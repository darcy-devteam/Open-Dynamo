import {
  Background, BackgroundVariant, ConnectionMode, Controls, MiniMap, ReactFlow, SelectionMode, useReactFlow,
  type Connection, type FinalConnectionState,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useCallback, useEffect, useRef, useState, type DragEvent, type MouseEvent as RMouseEvent } from 'react';
import { Icon } from '../icons';
import { useStore } from '../store';
import type { DEdge, DNode, NodeKind } from '../types';
import { edgeTypes } from './edges';
import { nodeTypes } from './nodes';

export const KINDS: { kind: NodeKind; label: string; key: string; hint: string }[] = [
  { kind: 'stock', label: 'Stock', key: 'S', hint: 'A quantity that accumulates' },
  { kind: 'flow', label: 'Flow', key: 'F', hint: 'A rate that fills or drains a stock' },
  { kind: 'variable', label: 'Variable', key: 'A', hint: 'Anything calculated from other things' },
  { kind: 'parameter', label: 'Parameter', key: 'P', hint: 'A constant you can tune with a slider' },
  { kind: 'table', label: 'Table', key: 'T', hint: 'A curve drawn through points' },
  { kind: 'note', label: 'Note', key: 'N', hint: 'A sticky note' },
  { kind: 'frame', label: 'Group', key: 'G', hint: 'A labelled frame around part of the model' },
];
const HALF: Partial<Record<NodeKind, [number, number]>> = { stock: [75, 32], flow: [22, 22], variable: [50, 18], parameter: [60, 18], table: [50, 18], note: [100, 60], frame: [180, 120] };

export function Canvas() {
  const nodes = useStore(s => s.nodes), edges = useStore(s => s.edges);
  const tool = useStore(s => s.tool), theme = useStore(s => s.theme), fit = useStore(s => s.fit);
  const store = useStore.getState;
  const rf = useReactFlow<DNode, DEdge>();
  const [menu, setMenu] = useState<{ x: number; y: number; from: string } | null>(null);

  // Re-centre only when a model is opened (never while someone is drawing).
  const view = useRef(rf);
  view.current = rf;
  useEffect(() => {
    const t = setTimeout(() => {       // with nothing on the canvas a fit would wait, then jump when the first node lands
      if (store().nodes.length) void view.current.fitView({ padding: 0.2, maxZoom: 1.15, duration: fit ? 250 : 0 });
    }, 60);
    return () => clearTimeout(t);
  }, [fit, store]);

  const place = useCallback((kind: NodeKind, clientX: number, clientY: number): string => {
    const p = rf.screenToFlowPosition({ x: clientX, y: clientY }), [hw, hh] = HALF[kind] ?? [20, 20];
    const id = store().addNode(kind, { x: p.x - hw, y: p.y - hh });
    if (kind !== 'cloud') useStore.setState({ editing: id });
    return id;
  }, [rf, store]);

  const onConnect = useCallback((c: Connection) => store().connect(c.source, c.target), [store]);
  const onConnectEnd = useCallback((ev: MouseEvent | TouchEvent, state: FinalConnectionState) => {
    if (state.isValid || !state.fromNode) return;
    const at = 'changedTouches' in ev ? ev.changedTouches[0] : ev;
    setMenu({ x: at.clientX, y: at.clientY, from: state.fromNode.id });   // dropped on empty canvas: offer to create what it leads to
  }, []);
  const onPaneClick = useCallback((ev: RMouseEvent) => {
    setMenu(null);
    if (store().tool !== 'select') place(store().tool as NodeKind, ev.clientX, ev.clientY);
  }, [place, store]);
  const onDrop = useCallback((ev: DragEvent) => {
    const kind = ev.dataTransfer.getData('application/open-dynamo') as NodeKind;
    if (kind) { ev.preventDefault(); place(kind, ev.clientX, ev.clientY); }
  }, [place]);

  return (
    <div className={`canvas${tool !== 'select' ? ' placing' : ''}`}>
      <ReactFlow<DNode, DEdge>
        nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
        onNodesChange={store().onNodesChange} onEdgesChange={store().onEdgesChange}
        onConnect={onConnect} onConnectEnd={onConnectEnd} onPaneClick={onPaneClick}
        onNodeDragStart={() => store().commit()} onSelectionDragStart={() => store().commit()}
        onDrop={onDrop} onDragOver={ev => { ev.preventDefault(); ev.dataTransfer.dropEffect = 'copy'; }}
        connectionMode={ConnectionMode.Loose} connectionRadius={26}
        panOnScroll selectionOnDrag panOnDrag={[1, 2]} selectionMode={SelectionMode.Partial}
        snapToGrid snapGrid={[10, 10]} minZoom={0.15} maxZoom={3} zoomOnDoubleClick={false}
        deleteKeyCode={['Backspace', 'Delete']} multiSelectionKeyCode={['Shift', 'Meta', 'Control']}
        colorMode={theme} elevateEdgesOnSelect
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1.4} />
        <MiniMap pannable zoomable nodeStrokeWidth={3} />
        <Controls showInteractive={false} orientation="horizontal" />
      </ReactFlow>
      {nodes.length === 0 && (
        <div className="empty">
          <h2>Start your model</h2>
          <p>Pick a building block on the left and click the canvas, or open a worked example.</p>
          <button className="primary" onClick={() => useStore.setState({ gallery: true })}><Icon name="grid" /> Browse examples</button>
        </div>
      )}
      {menu && (
        <div className="quick" style={{ left: menu.x, top: menu.y }} onPointerLeave={() => setMenu(null)}>
          <div className="quick-title">Link to a new…</div>
          {KINDS.filter(k => k.kind !== 'frame').map(k => (
            <button key={k.kind} onClick={() => { const id = place(k.kind, menu.x, menu.y); store().connect(menu.from, id); setMenu(null); }}>
              <Icon name={k.kind} /> {k.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
