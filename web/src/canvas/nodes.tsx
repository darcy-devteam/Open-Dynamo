// What each kind of node looks like on the canvas.
import { Handle, NodeResizer, NodeToolbar, Position, useConnection, type NodeProps } from '@xyflow/react';
import { memo, useMemo, type ReactNode } from 'react';
import { fmt } from '../api';
import { useStore } from '../store';
import { ident, type DNode } from '../types';
import { Icon } from '../icons';

const SIDES = [Position.Top, Position.Right, Position.Bottom, Position.Left];

/** Connection points: four dots to drag from, and (while a link is being drawn) the whole node as somewhere to drop it. */
function Ports() {
  const connecting = useConnection(c => c.inProgress);
  return (
    <>
      <Handle type="target" id="body" position={Position.Top} className={`port-body${connecting ? ' on' : ''}`} isConnectableStart={false} />
      {SIDES.map(p => <Handle key={p} type="source" id={p} position={p} className="port" />)}
    </>
  );
}

function Badge({ id }: { id: string }) {
  const d = useStore(s => s.compiled?.diagnostics.find(x => x.node === id && x.level === 'error') ?? s.compiled?.diagnostics.find(x => x.node === id));
  if (!d) return null;
  return <span className={`badge ${d.level}`} title={d.message}>!</span>;
}

function Tools({ id, selected }: { id: string; selected?: boolean }) {
  const only = useStore(s => !!selected && s.nodes.filter(n => n.selected).length === 1);
  return (
    <NodeToolbar isVisible={only} position={Position.Top} offset={10} className="node-tools">
      <button title="Rename (double-click)" onClick={() => useStore.setState({ editing: id })}><Icon name="edit" /></button>
      <button title="Duplicate (Ctrl+D)" onClick={() => { useStore.getState().copy(); useStore.getState().paste(); }}><Icon name="copy" /></button>
      <button title="Delete (Del)" onClick={() => useStore.getState().remove([id])}><Icon name="trash" /></button>
    </NodeToolbar>
  );
}

/** Focus a field as soon as it can take it: a node that was just created is invisible until the canvas has measured it. */
function focusSoon(el: HTMLInputElement | HTMLTextAreaElement | null) {
  if (!el) return;
  let tries = 0;
  const attempt = () => {
    el.focus();
    if (document.activeElement === el) el.select();
    else if (tries++ < 30) requestAnimationFrame(attempt);
  };
  attempt();
}

/** The node's name: double-click to rename in place. */
function Name({ id, name, placeholder }: { id: string; name: string; placeholder: string }) {
  const editing = useStore(s => s.editing === id);
  if (!editing) {
    return <span className={`name${name ? '' : ' empty'}`} onDoubleClick={e => { e.stopPropagation(); useStore.setState({ editing: id }); }}>{name || placeholder}</span>;
  }
  const done = (value: string) => { useStore.getState().renameNode(id, value.trim()); useStore.setState({ editing: null }); };
  return (
    <input className="name-edit nodrag nopan" ref={focusSoon} defaultValue={name} size={Math.max(6, name.length + 1)}
      onBlur={e => done(e.currentTarget.value)}
      onKeyDown={e => { e.stopPropagation(); if (e.key === 'Enter') done(e.currentTarget.value); if (e.key === 'Escape') useStore.setState({ editing: null }); }} />
  );
}

/** A faint trace of the node's last run, drawn inside it. */
function Spark({ name }: { name: string }) {
  const y = useStore(s => s.result?.series[name]);
  const d = useMemo(() => {
    if (!y || y.length < 2) return '';
    let lo = Infinity, hi = -Infinity;
    for (let i = 0; i < y.length; i++) if (Number.isFinite(y[i])) { lo = Math.min(lo, y[i]); hi = Math.max(hi, y[i]); }
    if (lo === Infinity) return '';
    const n = 60, span = hi - lo || 1;
    let path = '';
    for (let k = 0; k <= n; k++) {
      const v = y[Math.round((k / n) * (y.length - 1))];
      if (Number.isFinite(v)) path += `${path ? 'L' : 'M'}${((k / n) * 100).toFixed(1)},${(22 - ((v - lo) / span) * 20).toFixed(1)}`;
    }
    return path;
  }, [y]);
  if (!d) return null;
  return <svg className="spark" viewBox="0 0 100 24" preserveAspectRatio="none"><path d={`${d}L100,24L0,24Z`} className="spark-fill" /><path d={d} className="spark-line" /></svg>;
}

function Last({ name, units }: { name: string; units?: string }) {
  const v = useStore(s => { const y = s.result?.series[name]; return y ? y[y.length - 1] : undefined; });
  if (v === undefined) return null;
  return <span className="readout">{fmt(v)}{units ? ` ${units}` : ''}</span>;
}

type P = NodeProps<DNode>;
const frame = (id: string, selected: boolean | undefined, cls: string, body: ReactNode) => (
  <div className={`node ${cls}${selected ? ' selected' : ''}`}>
    <Tools id={id} selected={selected} />
    {body}
    <Badge id={id} />
    <Ports />
  </div>
);

export const StockNode = memo(({ id, data, selected }: P) => frame(id, selected, 'stock', (
  <>
    <Spark name={ident(data.name)} />
    <Name id={id} name={data.name} placeholder="Stock" />
    <Last name={ident(data.name)} units={data.units} />
  </>
)));

export const FlowNode = memo(({ id, data, selected }: P) => frame(id, selected, 'flow', (
  <>
    <svg viewBox="0 0 44 44" className="glyph">
      <circle cx="22" cy="22" r="19" className="glyph-bg" />
      <path d="M11 13 L33 31 L33 13 L11 31 Z" className="glyph-fg" />
    </svg>
    <div className="below"><Name id={id} name={data.name} placeholder="flow" /></div>
  </>
)));

export const VariableNode = memo(({ id, data, selected }: P) => frame(id, selected, 'variable', <Name id={id} name={data.name} placeholder="variable" />));

export const ParameterNode = memo(({ id, data, selected }: P) => frame(id, selected, 'parameter', (
  <>
    <Name id={id} name={data.name} placeholder="parameter" />
    <span className="chip">{fmt(data.value ?? 0)}{data.units ? ` ${data.units}` : ''}</span>
  </>
)));

export const TableNode = memo(({ id, data, selected }: P) => {
  const pts = data.points ?? [];
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const d = pts.length > 1 ? [...pts].sort((a, b) => a[0] - b[0]).map((p, i) =>
    `${i ? 'L' : 'M'}${(2 + ((p[0] - x0) / (x1 - x0 || 1)) * 20).toFixed(1)},${(14 - ((p[1] - y0) / (y1 - y0 || 1)) * 12).toFixed(1)}`).join('') : '';
  return frame(id, selected, 'table', (
    <>
      <svg viewBox="0 0 24 16" className="mini"><path d={d} /></svg>
      <Name id={id} name={data.name} placeholder="table" />
    </>
  ));
});

export const CloudNode = memo(({ id, selected }: P) => frame(id, selected, 'cloud', (
  <svg viewBox="0 0 40 40" className="glyph">
    <path d="M11 28a6.5 6.5 0 0 1 .8-12.9A8.5 8.5 0 0 1 28 13.6 7.3 7.3 0 0 1 29 28Z" className="glyph-bg" />
  </svg>
)));

export const NoteNode = memo(({ id, data, selected }: P) => {
  const editing = useStore(s => s.editing === id);
  return (
    <div className={`node note ${data.color ?? 'yellow'}${selected ? ' selected' : ''}`} onDoubleClick={() => useStore.setState({ editing: id })}>
      <NodeResizer isVisible={!!selected} minWidth={120} minHeight={60} onResizeStart={() => useStore.getState().commit()} />
      {editing
        ? <textarea className="nodrag nopan nowheel" ref={focusSoon} defaultValue={data.text ?? ''} placeholder="Write a note"
            onKeyDown={e => { e.stopPropagation(); if (e.key === 'Escape') e.currentTarget.blur(); }}
            onBlur={e => { useStore.getState().patchNode(id, { text: e.currentTarget.value }); useStore.setState({ editing: null }); }} />
        : <div className="note-text">{data.text || <em>Double-click to write</em>}</div>}
      <Ports />
    </div>
  );
});

export const FrameNode = memo(({ id, data, selected }: P) => (
  <div className={`node frame${selected ? ' selected' : ''}`}>
    <NodeResizer isVisible={!!selected} minWidth={160} minHeight={100} onResizeStart={() => useStore.getState().commit()} />
    <div className="frame-title"><Name id={id} name={data.name} placeholder="Group" /></div>
  </div>
));

export const nodeTypes = {
  stock: StockNode, flow: FlowNode, variable: VariableNode, parameter: ParameterNode, table: TableNode,
  cloud: CloudNode, note: NoteNode, frame: FrameNode,
};
