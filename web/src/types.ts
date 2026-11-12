import type { Edge, Node } from '@xyflow/react';

export type NodeKind = 'stock' | 'flow' | 'variable' | 'parameter' | 'table' | 'cloud' | 'note' | 'frame';
export type LinkKind = 'flow' | 'influence' | 'relation';
export const MATH_KINDS: NodeKind[] = ['stock', 'flow', 'variable', 'parameter', 'table'];

export interface NodeData extends Record<string, unknown> {
  kind: NodeKind;
  name: string;
  equation?: string;
  initial?: string;
  value?: number;
  min?: number | null;
  max?: number | null;
  input?: string;
  points?: [number, number][];
  nonNegative?: boolean;
  units?: string;
  text?: string;
  color?: string;
  doc?: string;
}

export interface LinkData extends Record<string, unknown> {
  kind: LinkKind;
  rate?: string;
  polarity?: '' | '+' | '-';
  label?: string;
  auto?: boolean; // drawn by the editor because an equation mentions the source
}

export type DNode = Node<NodeData>;
export type DEdge = Edge<LinkData>;

export interface SimSpec {
  start: number;
  stop: number;
  dt: number;
  method: 'euler' | 'rk4';
  timeUnit: string;
}

export interface ChartSpec {
  id: string;
  mode: 'time' | 'phase';
  vars: string[];
  x?: string;
  y?: string;
}

/** The saved form of a model: what the server compiles and what "Save" downloads. */
export interface ModelFile {
  format: 'open-dynamo/1';
  id?: string;
  name: string;
  description: string;
  level?: string;
  sim: SimSpec;
  nodes: (NodeData & { id: string; x: number; y: number; w?: number; h?: number })[];
  links: { id: string; kind: string; source: string; target: string; rate?: string; polarity?: string; label?: string; auto?: boolean }[];
  charts: ChartSpec[];
}

export interface Diagnostic {
  level: 'error' | 'warning';
  message: string;
  node: string | null;
  link: string | null;
}

export interface ParamInfo {
  name: string;
  node: string;
  role: 'parameter' | 'initial';
  label: string;
  default: number;
  slot: number;
}

export interface VarInfo {
  name: string;
  node: string | null;
  link: string | null;
  kind: string;
  label: string;
  units?: string;
  slot: number;
}

export interface Compiled {
  ok: boolean;
  diagnostics: Diagnostic[];
  names: Record<string, string>;
  uses: Record<string, string[]>;
  params: ParamInfo[];
  variables: VarInfo[];
  wasm: string | null;
  digest: string | null;
  size: number;
}

export interface Result {
  t: Float64Array;
  series: Record<string, Float64Array>;
  ms: number;
  where: 'browser' | 'server';
  diverged: boolean;
}

/** The identifier equations use for a node called `name` ("Birth rate" -> "Birth_rate"). Mirrors the server. */
export function ident(name: string): string {
  const out = name.trim().replace(/[^A-Za-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');
  return /^[0-9]/.test(out) ? '_' + out : out;
}

export const isNumber = (text: unknown) => typeof text === 'number' || (typeof text === 'string' && text.trim() !== '' && Number.isFinite(Number(text)));
