// Keeps the simulation in step with the diagram.
//
// A change to the model's structure (a node, an equation, a flow) is sent to the server, which checks it and
// returns the kernel as WebAssembly. A change to a number (a parameter, an initial value, the time settings) only
// re-runs the kernel already loaded, in this browser, which is why dragging a slider is instant.
import { api } from './api';
import { toFile, useStore, type State } from './store';
import { isNumber, MATH_KINDS, type Compiled, type DEdge, type Result } from './types';

const MAX_POINTS = 3000;      // samples kept per curve
const MAX_STEPS = 5_000_000;  // a run larger than this would freeze a modest laptop

let worker: Worker | null = null;
let loaded = '';              // digest of the kernel the worker holds
let lastKey = '', lastNumbers = '';
let compileSeq = 0, runSeq = 0, compileTimer = 0;
let running = false, again = false;
let message = '';

export const engineMessage = () => message;

/** Everything that changes the equations. Positions, numbers and annotation links are left out on purpose. */
function structure(s: State): string {
  return JSON.stringify([
    s.nodes.map(n => MATH_KINDS.includes(n.data.kind)
      ? [n.id, n.data.kind, n.data.name.trim(), n.data.equation ?? '', isNumber(n.data.initial) ? '#' : n.data.initial ?? '', n.data.input ?? '',
        n.data.points ?? [], !!n.data.nonNegative]
      : [n.id, n.data.kind]),
    s.edges.filter(e => e.data?.kind === 'flow').map(e => [e.source, e.target, e.data?.rate ?? '']),
  ]);
}

/** Draw an influence link for every name an equation mentions, and drop the automatic ones no longer needed. */
function syncLinks(uses: Record<string, string[]>) {
  const s = useStore.getState();
  let edges = s.edges.filter(e => !(e.data?.auto && e.data.kind === 'influence' && !uses[e.target]?.includes(e.source)));
  let changed = edges.length !== s.edges.length;
  for (const [target, sources] of Object.entries(uses)) {
    for (const source of sources) {
      const joined = edges.some(e => (e.source === source && e.target === target && e.data?.kind !== 'relation')
        || (e.data?.kind === 'flow' && e.source === target && e.target === source));
      if (joined) continue;
      const edge: DEdge = { id: `a_${source}_${target}`, source, target, type: 'link', data: { kind: 'influence', polarity: '', rate: '', label: '', auto: true } };
      edges = [...edges, edge];
      changed = true;
    }
  }
  if (changed) useStore.setState({ edges });
}

async function compile() {
  const seq = ++compileSeq;
  const s = useStore.getState();
  if (!s.nodes.some(n => MATH_KINDS.includes(n.data.kind))) {
    return useStore.setState({ status: 'idle', compiled: null, result: null });
  }
  useStore.setState({ status: 'compiling' });
  let c: Compiled;
  try {
    c = await api<Compiled>('compile', toFile(s));
  } catch (e) {
    if (seq === compileSeq) { message = (e as Error).message; useStore.setState({ status: 'offline' }); }
    return;
  }
  if (seq !== compileSeq) return;                       // the model changed while this was compiling
  message = '';
  useStore.setState({ compiled: c, status: c.ok ? 'ready' : 'error' });
  syncLinks(c.uses);
  if (!c.ok || !c.wasm) return;
  if (loaded === c.digest) return void run();
  const bytes = Uint8Array.from(atob(c.wasm), ch => ch.charCodeAt(0));
  try {
    worker ??= startWorker();
    worker.postMessage({ type: 'load', bytes, digest: c.digest }, [bytes.buffer]);
  } catch {
    void serverRun();                                   // no Web Workers or WebAssembly here: let the server run it
  }
}

function startWorker(): Worker {
  const w = new Worker(new URL('./worker.ts', import.meta.url), { type: 'module' });
  w.onmessage = ev => {
    const m = ev.data;
    if (m.type === 'loaded') { loaded = m.digest; running = false; run(); }
    else if (m.type === 'result') { running = false; if (m.seq === runSeq) publish(m.out, m.nOut, m.ms, 'browser'); if (again) run(); }
    else { running = false; loaded = ''; void serverRun(); }
  };
  w.onerror = () => { running = false; loaded = ''; void serverRun(); };
  return w;
}

function inputs(s: State) {
  const c = s.compiled!;
  const byId = new Map(s.nodes.map(n => [n.id, n]));
  const values: Record<string, number> = {};
  for (const p of c.params) {
    const d = byId.get(p.node)?.data;
    const v = p.role === 'parameter' ? d?.value : Number(d?.initial);
    values[p.name] = Number.isFinite(v) ? (v as number) : p.default;
  }
  return values;
}

function simProblem(s: State): string {
  const { start, stop, dt } = s.sim;
  if (![start, stop, dt].every(Number.isFinite) || dt <= 0 || stop <= start) return 'The run needs an end time after its start, and a positive time step.';
  if ((stop - start) / dt > MAX_STEPS) return `That is ${Math.round((stop - start) / dt).toLocaleString()} time steps. Use a larger time step or a shorter run.`;
  return '';
}

/** Run the loaded kernel with the current numbers. If a run is in flight, run once more when it finishes. */
export function run() {
  const s = useStore.getState(), c = s.compiled;
  if (!c?.ok || c.digest !== loaded || !worker) return;
  if (running) { again = true; return; }
  again = false;
  message = simProblem(s);
  if (message) return useStore.setState({ status: 'error' });
  if (s.status !== 'ready') useStore.setState({ status: 'ready' });
  const steps = Math.max(1, Math.round((s.sim.stop - s.sim.start) / s.sim.dt));
  const cfg = new Float64Array(16);
  cfg.set([s.sim.start, s.sim.dt, steps, s.sim.method === 'rk4' ? 1 : 0, Math.max(1, Math.ceil(steps / MAX_POINTS)), c.variables.length, 0, 0, 0]);
  const values = inputs(s);
  const params = new Float64Array(c.params.length);
  for (const p of c.params) params[p.slot] = values[p.name];
  running = true;
  worker.postMessage({ type: 'run', seq: ++runSeq, cfg, params, rec: Int32Array.from(c.variables.map(v => v.slot)) });
}

function publish(out: Float64Array, nOut: number, ms: number, where: Result['where']) {
  const s = useStore.getState(), c = s.compiled;
  if (!c) return;
  const steps = Math.max(1, Math.round((s.sim.stop - s.sim.start) / s.sim.dt));
  const every = Math.max(1, Math.ceil(steps / MAX_POINTS));
  const t = Float64Array.from({ length: nOut }, (_, i) => s.sim.start + i * every * s.sim.dt);
  const series: Record<string, Float64Array> = {};
  let diverged = false;
  c.variables.forEach((v, i) => {
    series[v.name] = out.subarray(i * nOut, (i + 1) * nOut);
    if (!Number.isFinite(series[v.name][nOut - 1])) diverged = true;
  });
  useStore.setState({ result: { t, series, ms, where, diverged } });
}

/** Fallback when the browser cannot run the kernel itself. */
async function serverRun() {
  const s = useStore.getState();
  if (!s.compiled?.ok) return;
  const seq = ++runSeq, started = performance.now();
  try {
    const r = await api<{ t: number[]; series: Record<string, (number | null)[]> }>('simulate', { model: toFile(s), params: inputs(s), maxPoints: MAX_POINTS });
    if (seq !== runSeq) return;
    const names = s.compiled.variables.map(v => v.name), n = r.t.length;
    const out = new Float64Array(names.length * n);
    names.forEach((name, i) => out.set(r.series[name].map(x => x ?? NaN), i * n));
    publish(out, n, performance.now() - started, 'server');
  } catch (e) {
    message = (e as Error).message;
    useStore.setState({ status: 'offline' });
  }
}

export function startEngine() {
  const check = (s: State, prev?: State) => {
    if (prev && s.nodes === prev.nodes && s.edges === prev.edges && s.sim === prev.sim) return;
    const key = structure(s);
    if (key !== lastKey) {
      lastKey = key;
      clearTimeout(compileTimer);
      compileTimer = window.setTimeout(compile, prev ? 300 : 0);
    } else if (s.compiled?.ok) {
      const numbers = JSON.stringify([s.sim, inputs(s)]);          // moving a node changes nothing worth re-running
      if (numbers === lastNumbers) return;
      lastNumbers = numbers;
      if (loaded && s.compiled.digest === loaded) run(); else if (!worker) void serverRun();
    }
  };
  check(useStore.getState());
  useStore.subscribe(check);
}

/** Current parameter values by name, for experiments sent to the server. */
export const currentParams = () => (useStore.getState().compiled?.ok ? inputs(useStore.getState()) : {});
