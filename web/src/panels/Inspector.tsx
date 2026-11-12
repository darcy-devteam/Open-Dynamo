// The right-hand panel: properties of whatever is selected, or of the model when nothing is.
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { Icon } from '../icons';
import { identOf, useStore } from '../store';
import { ident, MATH_KINDS, type DEdge, type DNode, type LinkKind } from '../types';

const FUNCTIONS: [string, string][] = [
  ['min(a, b)  max(a, b)  abs(x)  sqrt(x)  exp(x)  log(x)', 'everyday maths; also sin cos tan, floor ceil round, x^2'],
  ['clip(x, low, high)', 'keep x between two limits'],
  ['a if condition else b', 'choose between two values; conditions use < > == and or not'],
  ['step(height, at)', '0 until time "at", then height'],
  ['ramp(slope, from, to)', 'rises at "slope" between two times'],
  ['pulse(at, width, every)', '1 for "width" starting at "at"; repeats if "every" is given'],
  ['smooth(x, time)', 'x, exponentially averaged over "time"'],
  ['delay(x, time)  delay3(x, time)', 'x as it was "time" ago; delay3 is the gradual, third-order version'],
  ['lookup(x, [x1, x2], [y1, y2])', 'read a value off a curve'],
  ['rand()  randn()', 'random numbers: uniform 0 to 1, and standard normal'],
  ['time  dt  pi', 'the current time, the time step and π'],
];

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="field"><span>{label}{hint && <em>{hint}</em>}</span>{children}</label>;
}

/** A text box that keeps its own draft and writes to the model as you type. */
function Text({ value, onChange, area, mono, placeholder }: { value: string; onChange: (v: string) => void; area?: boolean; mono?: boolean; placeholder?: string }) {
  const props = { value, placeholder, spellCheck: false, className: mono ? 'mono' : '', onChange: (e: { target: { value: string } }) => onChange(e.target.value) };
  return area ? <textarea rows={3} {...props} /> : <input {...props} />;
}

function Num({ value, onChange, placeholder }: { value: number | null | undefined; onChange: (v: number | null) => void; placeholder?: string }) {
  const [draft, setDraft] = useState(value === null || value === undefined ? '' : String(value));
  const last = useRef(value);
  useEffect(() => { if (value !== last.current) { last.current = value; setDraft(value === null || value === undefined ? '' : String(value)); } }, [value]);
  return (
    <input className="mono" inputMode="decimal" value={draft} placeholder={placeholder} onChange={e => {
      setDraft(e.target.value);
      const v = e.target.value.trim() === '' ? null : Number(e.target.value);
      if (v === null || Number.isFinite(v)) { last.current = v; onChange(v); }
    }} />
  );
}

function Equation({ node }: { node: DNode }) {
  const box = useRef<HTMLTextAreaElement>(null);
  const patch = useStore.getState().patchNode;
  const inputs = useStore(s => {
    const ids = new Set(s.edges.filter(e => e.target === node.id && e.data?.kind !== 'relation').map(e => e.source));
    return s.nodes.filter(n => ids.has(n.id) && MATH_KINDS.includes(n.data.kind)).map(identOf).filter(Boolean).join(' ');
  });
  const insert = (name: string) => {
    const el = box.current, text = node.data.equation ?? '';
    const at = el ? [el.selectionStart, el.selectionEnd] : [text.length, text.length];
    patch(node.id, { equation: text.slice(0, at[0]) + name + text.slice(at[1]) });
    requestAnimationFrame(() => { el?.focus(); el?.setSelectionRange(at[0] + name.length, at[0] + name.length); });
  };
  return (
    <>
      <Field label={`${ident(node.data.name) || 'value'} =`}>
        <textarea ref={box} className="mono eq" rows={3} spellCheck={false} value={node.data.equation ?? ''} placeholder="e.g. rate * Stock"
          onChange={e => patch(node.id, { equation: e.target.value })} />
      </Field>
      {inputs && <div className="chips"><span>Inputs</span>{inputs.split(' ').map(n => <button key={n} onClick={() => insert(n)} title="Insert into the equation">{n}</button>)}</div>}
      <details className="help">
        <summary>What can I write?</summary>
        <p>Use other nodes by name, with spaces written as underscores. Linking a node to this one lists it under Inputs; using a name draws the link for you.</p>
        <dl>{FUNCTIONS.map(([sig, what]) => <div key={sig}><dt>{sig}</dt><dd>{what}</dd></div>)}</dl>
      </details>
    </>
  );
}

function NodePanel({ node }: { node: DNode }) {
  const s = useStore.getState, d = node.data, patch = (p: Partial<DNode['data']>) => s().patchNode(node.id, p);
  const problems = useStore(useShallow(st => st.compiled?.diagnostics.filter(x => x.node === node.id) ?? []));
  const flows = useStore(st => {
    if (d.kind !== 'stock') return '';
    const name = (id: string) => { const n = st.nodes.find(x => x.id === id); return n && MATH_KINDS.includes(n.data.kind) ? identOf(n) : ''; };
    return st.edges.filter(e => e.data?.kind === 'flow' && (e.source === node.id || e.target === node.id))
      .map(e => (e.target === node.id ? '+ ' : '− ') + (e.data?.rate || name(e.target === node.id ? e.source : e.target))).filter(t => t.length > 2).join('  ');
  });
  const title = { stock: 'Stock', flow: 'Flow', variable: 'Variable', parameter: 'Parameter', table: 'Table', cloud: 'Source or sink', note: 'Note', frame: 'Group' }[d.kind];
  return (
    <>
      <h3><Icon name={d.kind === 'cloud' ? 'variable' : d.kind} /> {title}</h3>
      {problems.map((p, i) => <p key={i} className={`callout ${p.level}`}>{p.message}</p>)}
      {d.kind !== 'cloud' && d.kind !== 'note' && <Field label="Name"><Text value={d.name} onChange={v => s().renameNode(node.id, v)} /></Field>}
      {d.kind === 'stock' && (
        <>
          <Field label="Starts at" hint="a number, or a formula"><Text mono value={String(d.initial ?? '')} onChange={v => patch({ initial: v })} /></Field>
          <Field label="Changes by"><div className="derived mono">{flows || 'nothing yet: link a flow to this stock'}</div></Field>
          <label className="check"><input type="checkbox" checked={!!d.nonNegative} onChange={e => patch({ nonNegative: e.target.checked })} /> Cannot go below zero</label>
        </>
      )}
      {(d.kind === 'flow' || d.kind === 'variable') && <Equation node={node} />}
      {d.kind === 'parameter' && (
        <>
          <Field label="Value"><Num value={d.value} onChange={v => patch({ value: v ?? 0 })} /></Field>
          <div className="row">
            <Field label="Slider from"><Num value={d.min} onChange={v => patch({ min: v })} /></Field>
            <Field label="to"><Num value={d.max} onChange={v => patch({ max: v })} /></Field>
          </div>
        </>
      )}
      {d.kind === 'table' && (
        <>
          <Field label="Read at" hint="usually time"><Text mono value={d.input ?? 'time'} onChange={v => patch({ input: v })} /></Field>
          <Field label="Points" hint="one x, y pair per line">
            <Points points={d.points ?? []} onChange={points => patch({ points })} />
          </Field>
        </>
      )}
      {d.kind === 'note' && (
        <>
          <Field label="Text"><Text area value={d.text ?? ''} onChange={v => patch({ text: v })} /></Field>
          <Field label="Colour">
            <div className="swatches">{['yellow', 'blue', 'green', 'pink', 'gray'].map(c => (
              <button key={c} className={`note ${c}${d.color === c ? ' on' : ''}`} aria-label={c} onClick={() => patch({ color: c })} />
            ))}</div>
          </Field>
        </>
      )}
      {MATH_KINDS.includes(d.kind) && (
        <>
          <Field label="Units" hint="optional"><Text value={d.units ?? ''} onChange={v => patch({ units: v })} placeholder="e.g. people, m/s" /></Field>
          <Field label="Notes" hint="optional"><Text area value={d.doc ?? ''} onChange={v => patch({ doc: v })} placeholder="What does this represent?" /></Field>
        </>
      )}
    </>
  );
}

function Points({ points, onChange }: { points: [number, number][]; onChange: (p: [number, number][]) => void }) {
  const text = (p: [number, number][]) => p.map(([x, y]) => `${x}, ${y}`).join('\n');
  const [draft, setDraft] = useState(text(points));
  const sent = useRef(text(points));
  useEffect(() => { if (text(points) !== sent.current) { sent.current = text(points); setDraft(sent.current); } }, [points]);
  return (
    <textarea className="mono" rows={6} spellCheck={false} value={draft} onChange={e => {
      setDraft(e.target.value);
      const rows = e.target.value.split('\n').map(l => l.split(/[,\s;]+/).filter(Boolean).map(Number)).filter(r => r.length >= 2 && r.every(Number.isFinite));
      const next = rows.map(r => [r[0], r[1]] as [number, number]);
      sent.current = text(next);
      onChange(next);
    }} />
  );
}

function EdgePanel({ edge }: { edge: DEdge }) {
  const s = useStore.getState, d = edge.data!, patch = (p: Partial<DEdge['data'] & object>) => s().patchEdge(edge.id, p);
  const ends = useStore(st => [edge.source, edge.target].map(id => { const n = st.nodes.find(x => x.id === id); return n?.data.name || n?.data.kind || '?'; }).join(' → '));
  const problem = useStore(st => st.compiled?.diagnostics.find(x => x.link === edge.id));
  const kinds: [LinkKind, string][] = [['flow', 'Flow'], ['influence', 'Influence'], ['relation', 'Relation']];
  return (
    <>
      <h3>Link</h3>
      <p className="muted">{ends}</p>
      {problem && <p className={`callout ${problem.level}`}>{problem.message}</p>}
      <div className="segmented">{kinds.map(([k, label]) => <button key={k} className={d.kind === k ? 'on' : ''} onClick={() => patch({ kind: k })}>{label}</button>)}</div>
      {d.kind === 'flow' && (
        <>
          <p className="muted">Moves quantity out of the source and into the target, when they are stocks.</p>
          <Field label="Rate" hint="leave empty when one end is a flow node"><Text mono value={d.rate ?? ''} onChange={v => patch({ rate: v })} placeholder="e.g. k * Stock" /></Field>
        </>
      )}
      {d.kind === 'influence' && (
        <>
          <p className="muted">Says the source is an input of the target. The target's equation decides how.</p>
          <Field label="Effect">
            <div className="segmented">{([['', 'Unmarked'], ['+', '+ same way'], ['-', '− opposite']] as const).map(([p, label]) => (
              <button key={p} className={(d.polarity ?? '') === p ? 'on' : ''} onClick={() => patch({ polarity: p })}>{label}</button>
            ))}</div>
          </Field>
        </>
      )}
      {d.kind === 'relation' && (
        <>
          <p className="muted">A line for the diagram only. It does not change the simulation.</p>
          <Field label="Label"><Text value={d.label ?? ''} onChange={v => patch({ label: v })} placeholder="e.g. causes, owns, reports to" /></Field>
        </>
      )}
      <div className="row">
        <button onClick={() => s().reverseEdge(edge.id)}><Icon name="swap" /> Reverse</button>
        <button onClick={() => s().remove([edge.id])}><Icon name="trash" /> Delete</button>
      </div>
    </>
  );
}

function ModelPanel() {
  const s = useStore.getState, sim = useStore(st => st.sim), description = useStore(st => st.description);
  const steps = Math.round((sim.stop - sim.start) / sim.dt);
  return (
    <>
      <h3>Simulation</h3>
      <div className="row">
        <Field label="From"><Num value={sim.start} onChange={v => s().setSim({ start: v ?? 0 })} /></Field>
        <Field label="to"><Num value={sim.stop} onChange={v => s().setSim({ stop: v ?? 0 })} /></Field>
      </div>
      <div className="row">
        <Field label="Time step"><Num value={sim.dt} onChange={v => s().setSim({ dt: v ?? 0 })} /></Field>
        <Field label="Time unit"><Text value={sim.timeUnit} onChange={v => s().setSim({ timeUnit: v })} placeholder="e.g. day" /></Field>
      </div>
      <Field label="Method">
        <div className="segmented">
          <button className={sim.method === 'rk4' ? 'on' : ''} onClick={() => s().setSim({ method: 'rk4' })} title="Fourth-order Runge-Kutta: accurate, the default">RK4</button>
          <button className={sim.method === 'euler' ? 'on' : ''} onClick={() => s().setSim({ method: 'euler' })} title="Euler: simplest, needs a small time step">Euler</button>
        </div>
      </Field>
      <p className="muted">{Number.isFinite(steps) && steps > 0 ? `${steps.toLocaleString()} steps per run.` : 'Check the times above.'}</p>
      <h3>About this model</h3>
      <Field label="Description"><Text area value={description} onChange={v => { s().commit('description'); useStore.setState({ description: v }); }} placeholder="What is this a model of?" /></Field>
      <h3>Tips</h3>
      <ul className="tips">
        <li>Drag from the dots on a node to link it to anything else.</li>
        <li>Double-click a name to rename it; equations follow.</li>
        <li>Scroll to pan, Ctrl + scroll to zoom, drag to select.</li>
      </ul>
    </>
  );
}

export function Inspector() {
  const node = useStore(s => { const sel = s.nodes.filter(n => n.selected); return sel.length === 1 ? sel[0] : null; });
  const edge = useStore(s => { const sel = s.edges.filter(e => e.selected); return sel.length === 1 && !s.nodes.some(n => n.selected) ? sel[0] : null; });
  const count = useStore(s => s.nodes.filter(n => n.selected).length);
  return (
    <aside className="inspector">
      {node ? <NodePanel key={node.id} node={node} /> : edge ? <EdgePanel key={edge.id} edge={edge} />
        : count > 1 ? <><h3>{count} nodes selected</h3><p className="muted">Drag to move them together, Ctrl+D to duplicate, Delete to remove.</p></> : <ModelPanel />}
    </aside>
  );
}
