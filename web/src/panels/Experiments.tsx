// Experiments that need many runs. These are sent to the server, which does the heavy computing.
import { useState } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { api, fmt } from '../api';
import { Chart, rampColor, type Band, type Line } from '../Chart';
import { currentParams } from '../engine';
import { toFile, useStore } from '../store';
import type { ParamInfo } from '../types';

type Mode = 'sweep' | 'uncertainty' | 'fit' | 'optimise';
interface Pick { on: boolean; min: string; max: string }
const AGGS: [string, string][] = [['last', 'final value'], ['max', 'peak'], ['min', 'lowest point'], ['mean', 'average'], ['integral', 'total over time']];

/** Sensible search limits for a parameter: its slider range if it has one, else around its current value. */
function limits(p: ParamInfo, value: number): [number, number] {
  const node = useStore.getState().nodes.find(n => n.id === p.node)?.data;
  if (p.role === 'parameter' && node?.min != null && node?.max != null && node.max > node.min) return [node.min, node.max];
  return value === 0 ? [-1, 1] : value > 0 ? [value / 5, value * 5] : [value * 5, value / 5];
}

function useModel() {
  // parameters first, then the starting values of stocks
  const params = useStore(useShallow(s => [...(s.compiled?.params ?? [])].sort((a, b) => Number(a.role === 'initial') - Number(b.role === 'initial'))));
  const variables = useStore(useShallow(s => s.compiled?.variables ?? []));
  return { params, variables, ok: useStore(s => !!s.compiled?.ok) };
}

/** Tick-list of parameters; with `ranges`, each ticked one also shows the limits to search between. */
function Decisions({ params, pickOf, put, ranges }: { params: ParamInfo[]; pickOf: (p: ParamInfo) => Pick; put: (p: ParamInfo, patch: Partial<Pick>) => void; ranges: boolean }) {
  if (!params.length) return <p className="muted">Add a parameter to the model first.</p>;
  return (
    <div className="decisions">
      {params.map(p => {
        const k = pickOf(p);
        return (
          <div key={p.name} className={`decision${k.on ? ' on' : ''}`}>
            <label><input type="checkbox" checked={k.on} onChange={e => put(p, { on: e.target.checked })} /> {p.label}</label>
            {k.on && ranges && <><input className="mono" value={k.min} aria-label="lowest" onChange={e => put(p, { min: e.target.value })} /><span>to</span>
              <input className="mono" value={k.max} aria-label="highest" onChange={e => put(p, { max: e.target.value })} /></>}
          </div>
        );
      })}
    </div>
  );
}

function parseData(text: string): { t: number[]; y: number[] } {
  const t: number[] = [], y: number[] = [];
  for (const line of text.split('\n')) {
    const cells = line.split(/[,;\t ]+/).filter(Boolean).map(Number);
    if (cells.length >= 2 && cells.every(Number.isFinite)) { t.push(cells[0]); y.push(cells[1]); }
  }
  return { t, y };
}

export function Experiments() {
  const { params, variables, ok } = useModel();
  const unit = useStore(s => s.sim.timeUnit);
  const [mode, setMode] = useState<Mode>('sweep');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [variable, setVariable] = useState(''), [param, setParam] = useState('');
  const [from, setFrom] = useState(''), [to, setTo] = useState(''), [runs, setRuns] = useState('9');
  const [picks, setPicks] = useState<Record<string, Pick>>({});
  const [spread, setSpread] = useState('20'), [draws, setDraws] = useState('500');
  const [data, setData] = useState(''), [agg, setAgg] = useState('last'), [goal, setGoal] = useState<'min' | 'max'>('min');
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [out, setOut] = useState<{ mode: Mode; r: any; variable: string } | null>(null);

  const v = variables.some(x => x.name === variable) ? variable : variables[0]?.name ?? '';
  const p = params.find(x => x.name === param) ?? params[0];
  const label = (name: string) => variables.find(x => x.name === name)?.label || params.find(x => x.name === name)?.label || name;
  const values = currentParams();
  const range = p ? limits(p, values[p.name] ?? p.default) : [0, 1];
  const lo = from === '' ? range[0] : Number(from), hi = to === '' ? range[1] : Number(to);
  const pickOf = (x: ParamInfo): Pick => {
    if (picks[x.name]) return picks[x.name];
    const [a, b] = limits(x, values[x.name] ?? x.default);
    return { on: false, min: String(+a.toPrecision(4)), max: String(+b.toPrecision(4)) };
  };
  const put = (x: ParamInfo, patch: Partial<Pick>) => setPicks({ ...picks, [x.name]: { ...pickOf(x), ...patch } });
  const chosen = params.filter(x => picks[x.name]?.on);
  const xLabel = unit ? `time (${unit})` : 'time';

  async function go() {
    setBusy(true); setError('');
    const model = toFile(useStore.getState()), base = { model, params: values };
    try {
      let r;
      if (mode === 'sweep') {
        const n = Math.max(2, Math.min(60, Math.round(Number(runs) || 9)));
        r = await api('sweep', { ...base, param: p.name, values: Array.from({ length: n }, (_, i) => lo + ((hi - lo) * i) / (n - 1)), record: [v] });
      } else if (mode === 'uncertainty') {
        const s = Math.max(0, Number(spread) || 0) / 100;
        const uncertain = chosen.map(x => { const c = values[x.name] ?? x.default, w = Math.abs(c) * s || s; return { param: x.name, dist: 'uniform', a: c - w, b: c + w }; });
        r = await api('montecarlo', { ...base, uncertain, n: Math.max(10, Math.min(20000, Math.round(Number(draws) || 500))), record: [v] });
      } else {
        const decisions = chosen.map(x => ({ param: x.name, min: Number(picks[x.name].min), max: Number(picks[x.name].max) }));
        if (!decisions.length) throw new Error('Tick at least one parameter to adjust.');
        const points = parseData(data);
        if (mode === 'fit' && points.t.length < 2) throw new Error('Paste at least two rows of data: a time and a value on each line.');
        const objective = mode === 'fit' ? { kind: 'fit', targets: [{ var: v, t: points.t, y: points.y }] } : { kind: goal, var: v, agg };
        r = await api<{ values: Record<string, number> }>('optimise', { ...base, decisions, objective });
        const after = await api<{ t: number[]; series: Record<string, number[]> }>('simulate', { model, params: { ...values, ...r.values }, record: [v], maxPoints: 600 });
        const before = await api<{ t: number[]; series: Record<string, number[]> }>('simulate', { ...base, record: [v], maxPoints: 600 });
        r = { ...r, after, before, points };
      }
      setOut({ mode, r, variable: v });
    } catch (e) { setError((e as Error).message); }
    setBusy(false);
  }

  /** Write the values an experiment found back into the model's parameters. */
  function apply(found: Record<string, number>) {
    const s = useStore.getState();
    s.commit();
    for (const [name, value] of Object.entries(found)) {
      const info = params.find(x => x.name === name);
      if (info) s.patchNode(info.node, info.role === 'parameter' ? { value: +value.toPrecision(6) } : { initial: String(+value.toPrecision(6)) }, 'apply');
    }
  }

  const modes: [Mode, string, string][] = [
    ['sweep', 'Sweep', 'Run the model across a range of one parameter and compare the curves.'],
    ['uncertainty', 'Uncertainty', 'Run it hundreds of times with parameters drawn at random, to see the spread of outcomes.'],
    ['fit', 'Fit to data', 'Find the parameter values that make a variable follow measurements you paste in.'],
    ['optimise', 'Optimise', 'Search for the parameter values that make a variable as small or as large as possible.'],
  ];
  const result = out?.mode === mode ? out : null;

  return (
    <div className="experiments">
      <div className="exp-form">
        <div className="segmented">{modes.map(([m, name]) => <button key={m} className={mode === m ? 'on' : ''} onClick={() => setMode(m)}>{name}</button>)}</div>
        <p className="muted">{modes.find(m => m[0] === mode)![2]}</p>
        {!ok ? <p className="muted">Fix the model's problems first.</p> : (
          <>
            <label className="field"><span>{mode === 'fit' ? 'Variable the data measures' : mode === 'optimise' ? 'Variable to optimise' : 'Variable to plot'}</span>
              <select value={v} onChange={e => setVariable(e.target.value)}>{variables.map(x => <option key={x.name} value={x.name}>{x.label || x.name}</option>)}</select></label>
            {mode === 'sweep' && (params.length === 0 ? <p className="muted">Add a parameter to the model to sweep it.</p> : (
              <>
                <label className="field"><span>Parameter to vary</span>
                  <select value={p?.name} onChange={e => { setParam(e.target.value); setFrom(''); setTo(''); }}>{params.map(x => <option key={x.name} value={x.name}>{x.label}</option>)}</select></label>
                <div className="row">
                  <label className="field"><span>From</span><input className="mono" value={from === '' ? String(+range[0].toPrecision(4)) : from} onChange={e => setFrom(e.target.value)} /></label>
                  <label className="field"><span>to</span><input className="mono" value={to === '' ? String(+range[1].toPrecision(4)) : to} onChange={e => setTo(e.target.value)} /></label>
                  <label className="field"><span>Runs</span><input className="mono" value={runs} onChange={e => setRuns(e.target.value)} /></label>
                </div>
              </>
            ))}
            {mode === 'uncertainty' && (
              <>
                <div className="row">
                  <label className="field"><span>Vary each by ± %</span><input className="mono" value={spread} onChange={e => setSpread(e.target.value)} /></label>
                  <label className="field"><span>Runs</span><input className="mono" value={draws} onChange={e => setDraws(e.target.value)} /></label>
                </div>
                <span className="field-label">Uncertain parameters</span>
                <Decisions params={params} pickOf={pickOf} put={put} ranges={false} />
              </>
            )}
            {mode === 'optimise' && (
              <div className="row">
                <label className="field"><span>Goal</span><select value={goal} onChange={e => setGoal(e.target.value as 'min' | 'max')}><option value="min">Minimise</option><option value="max">Maximise</option></select></label>
                <label className="field"><span>its</span><select value={agg} onChange={e => setAgg(e.target.value)}>{AGGS.map(([k, name]) => <option key={k} value={k}>{name}</option>)}</select></label>
              </div>
            )}
            {mode === 'fit' && (
              <label className="field"><span>Measurements<em>time, value on each line; paste from a spreadsheet</em></span>
                <textarea className="mono" rows={5} value={data} placeholder={'0, 10\n5, 46\n10, 143'} onChange={e => setData(e.target.value)} /></label>
            )}
            {(mode === 'fit' || mode === 'optimise') && (
              <><span className="field-label">Parameters it may adjust, and their limits</span>
                <Decisions params={params} pickOf={pickOf} put={put} ranges /></>
            )}
            <button className="primary" disabled={busy || !v || (mode === 'sweep' && !p)} onClick={() => void go()}>{busy ? 'Running on the server…' : 'Run experiment'}</button>
            {error && <p className="callout error">{error}</p>}
          </>
        )}
      </div>
      <div className="exp-result">
        {!result && <div className="chart-empty">Results appear here.</div>}
        {result?.mode === 'sweep' && (() => {
          const rows: number[][] = result.r.series[result.variable], vals: number[] = result.r.values;
          const lines: Line[] = rows.map((y, i) => ({ name: `${label(result.r.param)} = ${fmt(vals[i])}`, x: result.r.t, y, color: rampColor(i, rows.length), width: 1.75 }));
          return (
            <>
              <div className="exp-legend"><b>{label(result.variable)}</b> for {label(result.r.param)} from {fmt(vals[0])}
                <span className="ramp" /> to {fmt(vals.at(-1))} ({rows.length} runs)</div>
              <Chart lines={lines} xLabel={xLabel} />
            </>
          );
        })()}
        {result?.mode === 'uncertainty' && (() => {
          const b = result.r.bands[result.variable], f = result.r.final[result.variable], t = result.r.t;
          const bands: Band[] = [{ x: t, lo: b.p5, hi: b.p95, color: '--s1', alpha: 0.16 }, { x: t, lo: b.p25, hi: b.p75, color: '--s1', alpha: 0.28 }];
          return (
            <div className="split">
              <div>
                <div className="exp-legend"><b>{label(result.variable)}</b> over {result.r.n} runs <i className="sw line" /> median <i className="sw mid" /> middle half <i className="sw wide" /> 5% to 95%</div>
                <Chart lines={[{ name: 'median', x: t, y: b.p50, color: '--s1' }]} bands={bands} xLabel={xLabel} />
              </div>
              <div>
                <div className="exp-legend"><b>Where it ends up</b> mean {fmt(f.mean)}; 90% of runs between {fmt(f.p5)} and {fmt(f.p95)}</div>
                <Chart lines={[]} bars={{ edges: f.edges, counts: f.counts, color: '--s1' }} xLabel={`final ${label(result.variable)}`} yLabel="runs" />
              </div>
            </div>
          );
        })()}
        {(result?.mode === 'fit' || result?.mode === 'optimise') && (() => {
          const r = result.r, name = label(result.variable);
          const lines: Line[] = [{ name: 'before', x: r.before.t, y: r.before.series[result.variable], color: '--muted', width: 1.5 },
            { name: result.mode === 'fit' ? 'fitted model' : 'optimised', x: r.after.t, y: r.after.series[result.variable], color: '--s1' }];
          if (result.mode === 'fit') lines.push({ name: 'measurements', x: r.points.t, y: r.points.y, color: '--s2', dots: true });
          return (
            <div className="split">
              <div>
                <div className="exp-legend"><b>{name}</b> <i className="sw gray" /> before <i className="sw line" /> {result.mode === 'fit' ? 'fitted' : 'optimised'}
                  {result.mode === 'fit' && <><i className="sw dot" /> measurements</>}</div>
                <Chart lines={lines} xLabel={xLabel} />
              </div>
              <div className="found">
                <div className="exp-legend"><b>{result.mode === 'fit' ? 'Best fit' : 'Best found'}</b> after {r.evaluations.toLocaleString()} runs</div>
                <table>
                  <thead><tr><th>Parameter</th><th>Now</th><th>Found</th></tr></thead>
                  <tbody>{Object.entries(r.values as Record<string, number>).map(([k, x]) => <tr key={k}><td>{label(k)}</td><td>{fmt(values[k])}</td><td><b>{fmt(x)}</b></td></tr>)}</tbody>
                </table>
                <p className="muted">{result.mode === 'fit' ? `Typical miss (RMSE): ${fmt(r.rmse[result.variable])}` : `${AGGS.find(a => a[0] === agg)?.[1] ?? 'value'} of ${name}: ${fmt(r.start)} → ${fmt(r.score)}`}</p>
                <button className="primary" onClick={() => apply(r.values)}>Use these values in the model</button>
              </div>
            </div>
          );
        })()}
      </div>
    </div>
  );
}
