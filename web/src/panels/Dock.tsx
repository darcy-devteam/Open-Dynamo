// The bottom panel: sliders on the left; charts, experiments and problems on the right.
import { useMemo, useRef, useState } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { fmt } from '../api';
import { Chart, SERIES, type Line } from '../Chart';
import { Icon } from '../icons';
import { useStore } from '../store';
import type { ChartSpec, DNode } from '../types';
import { Experiments } from './Experiments';

const uid = () => 'c' + Math.random().toString(36).slice(2, 8);

function Slider({ node }: { node: DNode }) {
  const d = node.data, v = d.value ?? 0;
  const lo = d.min ?? Math.min(0, v * 2), hi = d.max ?? (v === 0 ? 1 : Math.max(v * 2, 0));
  const set = (value: number) => useStore.getState().patchNode(node.id, { value }, `slide:${node.id}`);
  return (
    <div className="slider">
      <div className="slider-head">
        <span title={d.doc || undefined}>{d.name}{d.units && <em> {d.units}</em>}</span>
        <input className="mono" value={Number.isFinite(v) ? String(+v.toPrecision(6)) : ''} inputMode="decimal"
          onChange={e => { const x = Number(e.target.value); if (e.target.value.trim() !== '' && Number.isFinite(x)) set(x); }} />
      </div>
      <input type="range" min={lo} max={hi} step={(hi - lo) / 400 || 0.01} value={v} onChange={e => set(Number(e.target.value))} />
      <div className="slider-range"><span>{fmt(lo)}</span><span>{fmt(hi)}</span></div>
    </div>
  );
}

function Controls() {
  const params = useStore(useShallow(s => s.nodes.filter(n => n.data.kind === 'parameter')));
  const stocks = useStore(useShallow(s => s.nodes.filter(n => n.data.kind === 'stock' && n.data.initial !== undefined && Number.isFinite(Number(n.data.initial)) && String(n.data.initial).trim() !== '')));
  return (
    <div className="controls">
      <h4>Controls</h4>
      {params.length === 0 && stocks.length === 0 && <p className="muted">Parameters you add to the model appear here as sliders.</p>}
      {params.map(n => <Slider key={n.id} node={n} />)}
      {stocks.length > 0 && <h4>Starting values</h4>}
      {stocks.map(n => (
        <label key={n.id} className="start">
          <span>{n.data.name}</span>
          <input className="mono" defaultValue={String(n.data.initial)} key={String(n.data.initial)} inputMode="decimal"
            onChange={e => { const x = Number(e.target.value); if (e.target.value.trim() !== '' && Number.isFinite(x)) useStore.getState().patchNode(n.id, { initial: e.target.value.trim() }, `init:${n.id}`); }} />
        </label>
      ))}
    </div>
  );
}

function ChartCard({ spec, onChange, onRemove }: { spec: ChartSpec; onChange: (c: ChartSpec) => void; onRemove: () => void }) {
  const result = useStore(s => s.result), unit = useStore(s => s.sim.timeUnit);
  const variables = useStore(useShallow(s => s.compiled?.variables ?? []));
  const label = (name: string) => variables.find(v => v.name === name)?.label || name;
  const names = variables.map(v => v.name);
  const lines: Line[] = useMemo(() => {
    if (!result) return [];
    if (spec.mode === 'phase') {
      const x = result.series[spec.x ?? ''], y = result.series[spec.y ?? ''];
      return x && y ? [{ name: `${label(spec.y!)} against ${label(spec.x!)}`, x, y, color: SERIES[0] }] : [];
    }
    return spec.vars.filter(v => result.series[v]).map((v, i) => ({ name: label(v), x: result.t, y: result.series[v], color: SERIES[i % 8] }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result, spec, variables]);
  const pick = (value: string, apply: (v: string) => ChartSpec) => value && onChange(apply(value));
  return (
    <div className="card-chart">
      <div className="chart-head">
        <div className="segmented small">
          <button className={spec.mode === 'time' ? 'on' : ''} onClick={() => onChange({ ...spec, mode: 'time', vars: spec.vars.length ? spec.vars : [spec.y, spec.x].filter(Boolean) as string[] })}>Over time</button>
          <button className={spec.mode === 'phase' ? 'on' : ''} title="Plot one variable against another"
            onClick={() => onChange({ ...spec, mode: 'phase', x: spec.x ?? spec.vars[0] ?? names[0], y: spec.y ?? spec.vars[1] ?? names[1] ?? names[0] })}>Phase</button>
        </div>
        {spec.mode === 'time' ? (
          <>
            {spec.vars.map((v, i) => (
              <span key={v} className="legend-chip"><i style={{ background: `var(${SERIES[i % 8]})` }} />{label(v)}
                <button aria-label={`Remove ${label(v)}`} onClick={() => onChange({ ...spec, vars: spec.vars.filter(x => x !== v) })}>×</button></span>
            ))}
            {spec.vars.length < 8 && (
              <select value="" aria-label="Add a variable" onChange={e => pick(e.target.value, v => ({ ...spec, vars: [...spec.vars, v] }))}>
                <option value="">+ Add variable</option>
                {variables.filter(v => !spec.vars.includes(v.name)).map(v => <option key={v.name} value={v.name}>{v.label || v.name}</option>)}
              </select>
            )}
          </>
        ) : (
          <>
            <select value={spec.y ?? ''} aria-label="Vertical axis" onChange={e => pick(e.target.value, y => ({ ...spec, y }))}>{variables.map(v => <option key={v.name} value={v.name}>{v.label || v.name}</option>)}</select>
            <span className="muted">against</span>
            <select value={spec.x ?? ''} aria-label="Horizontal axis" onChange={e => pick(e.target.value, x => ({ ...spec, x }))}>{variables.map(v => <option key={v.name} value={v.name}>{v.label || v.name}</option>)}</select>
          </>
        )}
        <div className="spacer" />
        <button className="ghost" title="Remove this chart" onClick={onRemove}><Icon name="close" size={14} /></button>
      </div>
      {lines.length ? (
        <Chart lines={lines} mode={spec.mode === 'phase' ? 'xy' : 'time'} xLabel={spec.mode === 'phase' ? label(spec.x!) : unit ? `time (${unit})` : 'time'}
          yLabel={spec.mode === 'phase' ? label(spec.y!) : undefined} />
      ) : <div className="chart-empty">{result ? 'Choose what to plot.' : 'Nothing to plot yet.'}</div>}
    </div>
  );
}

/** The charts on show: the model's own, or (until the user makes one) a chart of every stock. */
function useCharts() {
  const charts = useStore(s => s.charts);
  const stocks = useStore(useShallow(s => (s.compiled?.variables ?? []).filter(v => v.kind === 'stock').map(v => v.name)));
  const shown: ChartSpec[] = charts.length ? charts : stocks.length ? [{ id: 'auto', mode: 'time', vars: stocks.slice(0, 8) }] : [];
  const update = (next: ChartSpec[]) => useStore.getState().setCharts(next.map(c => (c.id === 'auto' ? { ...c, id: uid() } : c)));
  return { shown, update, add: () => update([...shown, { id: uid(), mode: 'time', vars: stocks.slice(0, 1) }]) };
}

function Charts() {
  const { shown, update } = useCharts();
  if (!shown.length) return <div className="chart-empty">Charts of your model appear here as soon as it has a stock.</div>;
  return (
    <div className="charts">
      {shown.map(c => <ChartCard key={c.id} spec={c} onChange={n => update(shown.map(x => (x.id === c.id ? n : x)))} onRemove={() => update(shown.filter(x => x.id !== c.id))} />)}
    </div>
  );
}

function Problems() {
  const list = useStore(useShallow(s => s.compiled?.diagnostics ?? []));
  const nodes = useStore(s => s.nodes);
  const go = (node: string | null, link: string | null) => useStore.setState(s => ({
    nodes: s.nodes.map(n => ({ ...n, selected: n.id === node })), edges: s.edges.map(e => ({ ...e, selected: e.id === link })),
  }));
  if (!list.length) return <div className="chart-empty">No problems. The model checks out.</div>;
  return (
    <ul className="problems">
      {list.map((d, i) => (
        <li key={i} className={d.level}>
          <button onClick={() => go(d.node, d.link)}>
            <b>{d.level === 'error' ? 'Fix' : 'Note'}</b>
            <span>{d.node ? `${nodes.find(n => n.id === d.node)?.data.name || 'Unnamed node'}: ` : d.link ? 'Link: ' : ''}{d.message}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

export function Dock() {
  const tab = useStore(s => s.dock);
  const counts = useStore(useShallow(s => [s.compiled?.diagnostics.filter(d => d.level === 'error').length ?? 0, s.compiled?.diagnostics.length ?? 0]));
  const [height, setHeight] = useState(() => Number(localStorage.getItem('open-dynamo:dock')) || 320);
  const drag = useRef<{ y: number; h: number } | null>(null);
  const { add } = useCharts();
  const tabs: ['charts' | 'experiments' | 'problems', string][] = [['charts', 'Charts'], ['experiments', 'Experiments'], ['problems', counts[1] ? `Problems (${counts[1]})` : 'Problems']];
  return (
    <section className="dock" style={{ height }}>
      <div className="dock-grip" title="Drag to resize"
        onPointerDown={e => { drag.current = { y: e.clientY, h: height }; e.currentTarget.setPointerCapture(e.pointerId); }}
        onPointerMove={e => { if (drag.current) setHeight(Math.max(140, Math.min(window.innerHeight - 160, drag.current.h + drag.current.y - e.clientY))); }}
        onPointerUp={() => { drag.current = null; localStorage.setItem('open-dynamo:dock', String(height)); }} />
      <Controls />
      <div className="dock-main">
        <div className="tabs">
          {tabs.map(([key, label]) => (
            <button key={key} className={`${tab === key ? 'on' : ''}${key === 'problems' && counts[0] ? ' bad' : ''}`} onClick={() => useStore.setState({ dock: key })}>{label}</button>
          ))}
          <div className="spacer" />
          {tab === 'charts' && <button className="add-chart" onClick={add}><Icon name="plus" size={14} /> Add chart</button>}
        </div>
        <div className="dock-body">{tab === 'charts' ? <Charts /> : tab === 'experiments' ? <Experiments /> : <Problems />}</div>
      </div>
    </section>
  );
}
