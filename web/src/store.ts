import { applyEdgeChanges, applyNodeChanges, type EdgeChange, type NodeChange, type XYPosition } from '@xyflow/react';
import { create } from 'zustand';
import {
  ident, MATH_KINDS, type ChartSpec, type Compiled, type DEdge, type DNode, type LinkData, type LinkKind,
  type ModelFile, type NodeData, type NodeKind, type Result, type SimSpec,
} from './types';

interface Snapshot { name: string; description: string; sim: SimSpec; nodes: DNode[]; edges: DEdge[]; charts: ChartSpec[] }
export type Tool = 'select' | NodeKind;
export type LinkMode = 'auto' | LinkKind;

export interface State extends Snapshot {
  past: Snapshot[];
  future: Snapshot[];
  lastCommit: { tag: string; at: number };
  tool: Tool;
  linkMode: LinkMode;
  editing: string | null; // node being renamed on the canvas
  theme: 'light' | 'dark';
  status: 'idle' | 'compiling' | 'ready' | 'error' | 'offline';
  compiled: Compiled | null;
  result: Result | null;
  gallery: boolean;
  clipboard: { nodes: DNode[]; edges: DEdge[] } | null;
  fit: number; // bumped when the view should re-centre on the model
  dock: 'charts' | 'experiments' | 'problems';

  commit: (tag?: string) => void;
  undo: () => void;
  redo: () => void;
  onNodesChange: (changes: NodeChange<DNode>[]) => void;
  onEdgesChange: (changes: EdgeChange<DEdge>[]) => void;
  addNode: (kind: NodeKind, at: XYPosition, patch?: Partial<NodeData>) => string;
  patchNode: (id: string, patch: Partial<NodeData>, tag?: string) => void;
  renameNode: (id: string, name: string) => void;
  patchEdge: (id: string, patch: Partial<LinkData>, tag?: string) => void;
  reverseEdge: (id: string) => void;
  connect: (source: string, target: string) => void;
  remove: (ids: string[]) => void;
  copy: () => void;
  paste: (offset?: number) => void;
  selectAll: () => void;
  setSim: (patch: Partial<SimSpec>) => void;
  setCharts: (charts: ChartSpec[]) => void;
  load: (file: ModelFile) => void;
  clear: () => void;
  set: (patch: Partial<State>) => void;
}

const SAVE_KEY = 'open-dynamo:model';
const uid = (p: string) => p + Math.random().toString(36).slice(2, 9);
const DEFAULT_SIM: SimSpec = { start: 0, stop: 100, dt: 0.25, method: 'rk4', timeUnit: '' };
const BASE_NAME: Record<NodeKind, string> = {
  stock: 'Stock', flow: 'flow', variable: 'variable', parameter: 'parameter', table: 'table', cloud: '', note: '', frame: 'Group',
};

export function fromFile(file: ModelFile): Snapshot {
  const nodes: DNode[] = file.nodes.map(({ id, x, y, w, h, ...data }) => ({
    id, type: data.kind, position: { x, y }, data: data as NodeData,
    ...(w && h ? { width: w, height: h } : {}), ...(data.kind === 'frame' ? { zIndex: -1 } : {}),
  }));
  const ids = new Set(nodes.map(n => n.id));
  const edges: DEdge[] = file.links.filter(l => ids.has(l.source) && ids.has(l.target)).map(l => ({
    id: l.id, source: l.source, target: l.target, type: 'link',
    data: { kind: (['flow', 'influence'].includes(l.kind) ? l.kind : 'relation') as LinkKind, rate: l.rate ?? '',
      polarity: (l.polarity ?? '') as LinkData['polarity'], label: l.label ?? '', auto: !!l.auto },
  }));
  return { name: file.name ?? 'Untitled model', description: file.description ?? '', sim: { ...DEFAULT_SIM, ...file.sim }, nodes, edges, charts: file.charts ?? [] };
}

export function toFile(s: Snapshot): ModelFile {
  return {
    format: 'open-dynamo/1', name: s.name, description: s.description, sim: s.sim,
    nodes: s.nodes.map(n => ({ ...n.data, id: n.id, x: Math.round(n.position.x), y: Math.round(n.position.y),
      ...(n.width && n.height ? { w: Math.round(n.width), h: Math.round(n.height) } : {}) })),
    links: s.edges.map(e => ({ id: e.id, source: e.source, target: e.target, kind: e.data?.kind ?? 'influence', rate: e.data?.rate || undefined,
      polarity: e.data?.polarity || undefined, label: e.data?.label || undefined, auto: e.data?.auto || undefined })),
    charts: s.charts,
  };
}

const snap = (s: State): Snapshot => ({ name: s.name, description: s.description, sim: s.sim, nodes: s.nodes, edges: s.edges, charts: s.charts });
const blank: Snapshot = { name: 'Untitled model', description: '', sim: DEFAULT_SIM, nodes: [], edges: [], charts: [] };

function saved(): Snapshot | null {
  try {
    const raw = localStorage.getItem(SAVE_KEY);
    return raw ? fromFile(JSON.parse(raw)) : null;
  } catch { return null; }
}

/** Replace whole-word uses of an identifier in a formula. */
const reword = (text: string | undefined, from: string, to: string) =>
  text ? text.replace(new RegExp(`(?<![A-Za-z0-9_])${from}(?![A-Za-z0-9_])`, 'g'), to) : text;

function uniqueName(nodes: DNode[], base: string): string {
  if (!base) return '';
  const taken = new Set(nodes.map(n => ident(n.data.name)));
  if (!taken.has(ident(base)) && /\d$/.test(base)) return base;
  const stem = base.replace(/\s*\d+$/, '');
  for (let k = 1; ; k++) if (!taken.has(ident(`${stem} ${k}`))) return `${stem} ${k}`;
}

const initial = saved();

export const useStore = create<State>((set, get) => ({
  ...(initial ?? blank),
  past: [], future: [], lastCommit: { tag: '', at: 0 },
  tool: 'select', linkMode: 'auto', editing: null,
  theme: (localStorage.getItem('open-dynamo:theme') as 'light' | 'dark' | null) ?? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'),
  status: 'idle', compiled: null, result: null, gallery: !initial, clipboard: null, fit: 0, dock: 'charts',

  set: patch => set(patch),

  // Record the current model so the next change can be undone. Edits with the same tag made in quick succession share one step.
  commit: (tag = '') => {
    const s = get(), now = Date.now();
    if (tag && tag === s.lastCommit.tag && now - s.lastCommit.at < 1200) return set({ lastCommit: { tag, at: now } });
    set({ past: [...s.past.slice(-99), snap(s)], future: [], lastCommit: { tag, at: now } });
  },
  undo: () => {
    const s = get(), prev = s.past.at(-1);
    if (prev) set({ ...prev, past: s.past.slice(0, -1), future: [snap(s), ...s.future], lastCommit: { tag: '', at: 0 } });
  },
  redo: () => {
    const s = get(), next = s.future[0];
    if (next) set({ ...next, future: s.future.slice(1), past: [...s.past, snap(s)], lastCommit: { tag: '', at: 0 } });
  },

  onNodesChange: changes => {
    if (changes.some(c => c.type === 'remove')) get().commit();
    set({ nodes: applyNodeChanges(changes, get().nodes) });
  },
  onEdgesChange: changes => {
    if (changes.some(c => c.type === 'remove')) get().commit();
    set({ edges: applyEdgeChanges(changes, get().edges) });
  },

  addNode: (kind, at, patch = {}) => {
    const s = get();
    s.commit();
    const id = uid('n');
    const data: NodeData = {
      kind, name: uniqueName(s.nodes, BASE_NAME[kind]),
      ...(kind === 'stock' ? { initial: '0' } : {}), ...(kind === 'flow' || kind === 'variable' ? { equation: '' } : {}),
      ...(kind === 'parameter' ? { value: 1, min: 0, max: 10 } : {}),
      ...(kind === 'table' ? { input: 'time', points: [[0, 0], [10, 1]] as [number, number][] } : {}),
      ...(kind === 'note' ? { text: '', color: 'yellow' } : {}), ...patch,
    };
    const size = kind === 'note' ? { width: 200, height: 120 } : kind === 'frame' ? { width: 360, height: 240, zIndex: -1 } : {};
    const node: DNode = { id, type: kind, position: { x: Math.round(at.x / 10) * 10, y: Math.round(at.y / 10) * 10 }, data, selected: true, ...size };
    set({ nodes: [...s.nodes.map(n => (n.selected ? { ...n, selected: false } : n)), node],
      edges: s.edges.map(e => (e.selected ? { ...e, selected: false } : e)), tool: 'select' });
    return id;
  },
  patchNode: (id, patch, tag) => {
    get().commit(tag ?? `node:${id}:${Object.keys(patch).join()}`);
    set({ nodes: get().nodes.map(n => (n.id === id ? { ...n, data: { ...n.data, ...patch } } : n)) });
  },
  // Renaming a node rewrites every formula and chart that refers to it.
  renameNode: (id, name) => {
    const s = get(), node = s.nodes.find(n => n.id === id);
    if (!node || node.data.name === name) return;
    s.commit(`rename:${id}`);
    const from = ident(node.data.name), to = ident(name);
    const fix = from && to && from !== to && MATH_KINDS.includes(node.data.kind);
    set({
      nodes: s.nodes.map(n => n.id === id ? { ...n, data: { ...n.data, name } } : !fix ? n : {
        ...n, data: { ...n.data, equation: reword(n.data.equation, from, to), input: reword(n.data.input, from, to),
          initial: reword(n.data.initial, from, to) },
      }),
      edges: !fix ? s.edges : s.edges.map(e => (e.data?.rate ? { ...e, data: { ...e.data, rate: reword(e.data.rate, from, to) } } : e)),
      charts: !fix ? s.charts : s.charts.map(c => ({ ...c, vars: c.vars.map(v => (v === from ? to : v)), x: c.x === from ? to : c.x, y: c.y === from ? to : c.y })),
    });
  },
  patchEdge: (id, patch, tag) => {
    get().commit(tag ?? `edge:${id}:${Object.keys(patch).join()}`);
    set({ edges: get().edges.map(e => (e.id === id ? { ...e, data: { ...e.data!, ...patch, auto: false } } : e)) });
  },
  reverseEdge: id => {
    get().commit();
    set({ edges: get().edges.map(e => (e.id === id ? { ...e, source: e.target, target: e.source } : e)) });
  },

  // Join two nodes. In "auto" mode the kind of link follows from what is being joined.
  connect: (source, target) => {
    const s = get();
    if (source === target) return;
    const a = s.nodes.find(n => n.id === source), b = s.nodes.find(n => n.id === target);
    if (!a || !b) return;
    const ka = a.data.kind, kb = b.data.kind;
    const pipeEnd = (k: NodeKind) => k === 'stock' || k === 'cloud';
    let kind: LinkKind = s.linkMode === 'auto'
      ? (ka === 'note' || kb === 'note' || ka === 'frame' || kb === 'frame') ? 'relation'
        : (pipeEnd(ka) && (pipeEnd(kb) || kb === 'flow')) || (ka === 'flow' && pipeEnd(kb)) ? 'flow' : 'influence'
      : s.linkMode;
    if (ka === 'cloud' && kb === 'cloud') kind = 'relation';
    if (s.edges.some(e => e.source === source && e.target === target && e.data?.kind === kind)) return;
    s.commit();
    const edge = (src: string, tgt: string): DEdge => ({ id: uid('l'), source: src, target: tgt, type: 'link', data: { kind, rate: '', polarity: '', label: '' } });
    if (kind === 'flow' && s.linkMode === 'auto' && pipeEnd(ka) && pipeEnd(kb)) {
      // stock to stock: put a valve in the middle of the pipe, as stock-and-flow diagrams do
      const mid = { x: (a.position.x + (a.measured?.width ?? 150) / 2 + b.position.x + (b.measured?.width ?? 150) / 2) / 2 - 22,
        y: (a.position.y + (a.measured?.height ?? 64) / 2 + b.position.y + (b.measured?.height ?? 64) / 2) / 2 - 22 };
      const valve: DNode = { id: uid('n'), type: 'flow', position: mid, data: { kind: 'flow', name: uniqueName(s.nodes, 'flow'), equation: '' }, selected: true };
      set({ nodes: [...s.nodes.map(n => ({ ...n, selected: false })), valve], edges: [...s.edges, edge(source, valve.id), edge(valve.id, target)], editing: valve.id });
    } else set({ edges: [...s.edges, edge(source, target)] });
  },

  remove: ids => {
    const gone = new Set(ids);
    get().commit();
    set({ nodes: get().nodes.filter(n => !gone.has(n.id)), edges: get().edges.filter(e => !gone.has(e.id) && !gone.has(e.source) && !gone.has(e.target)) });
  },
  copy: () => {
    const s = get(), nodes = s.nodes.filter(n => n.selected), ids = new Set(nodes.map(n => n.id));
    if (nodes.length) set({ clipboard: { nodes, edges: s.edges.filter(e => ids.has(e.source) && ids.has(e.target)) } });
  },
  paste: (offset = 40) => {
    const s = get(), clip = s.clipboard;
    if (!clip) return;
    s.commit();
    const map = new Map<string, string>();
    let all = s.nodes.map(n => ({ ...n, selected: false }));
    for (const n of clip.nodes) {
      const id = uid('n');
      map.set(n.id, id);
      all = [...all, { ...n, id, selected: true, position: { x: n.position.x + offset, y: n.position.y + offset },
        data: { ...n.data, name: uniqueName(all, n.data.name) } }];
    }
    const edges = clip.edges.map(e => ({ ...e, id: uid('l'), source: map.get(e.source)!, target: map.get(e.target)!, selected: false }));
    set({ nodes: all, edges: [...s.edges.map(e => ({ ...e, selected: false })), ...edges],
      clipboard: { nodes: clip.nodes.map(n => ({ ...n, position: { x: n.position.x + offset, y: n.position.y + offset } })), edges: clip.edges } });
  },
  selectAll: () => set({ nodes: get().nodes.map(n => ({ ...n, selected: true })), edges: get().edges.map(e => ({ ...e, selected: true })) }),

  setSim: patch => { get().commit('sim'); set({ sim: { ...get().sim, ...patch } }); },
  setCharts: charts => { get().commit('charts'); set({ charts }); },
  load: file => { get().commit(); set({ ...fromFile(file), result: null, compiled: null, gallery: false, editing: null, fit: get().fit + 1 }); },
  clear: () => { get().commit(); set({ ...blank, result: null, compiled: null, gallery: false, fit: get().fit + 1 }); },
}));

// The theme is applied the moment it changes, before anything redraws (charts read their colours from CSS).
document.documentElement.dataset.theme = useStore.getState().theme;
useStore.subscribe((s, prev) => { if (s.theme !== prev.theme) document.documentElement.dataset.theme = s.theme; });

// Keep the working model in this browser so a refresh or a closed tab loses nothing.
let saveTimer = 0;
useStore.subscribe((s, prev) => {
  if (s.nodes === prev.nodes && s.edges === prev.edges && s.sim === prev.sim && s.name === prev.name && s.charts === prev.charts) return;
  clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => {
    try { localStorage.setItem(SAVE_KEY, JSON.stringify(toFile(useStore.getState()))); } catch { /* storage full or blocked */ }
  }, 400);
});

export const identOf = (n: DNode) => ident(n.data.name);
