// Links attach to the edge of whatever they join and re-route as nodes move, so any node can link to any node.
import { EdgeLabelRenderer, useInternalNode, type EdgeProps, type InternalNode } from '@xyflow/react';
import { memo } from 'react';
import { useStore } from '../store';
import type { DEdge, DNode } from '../types';

interface Pt { x: number; y: number }
interface Box extends Pt { w: number; h: number; round: boolean }

function boxOf(n: InternalNode<DNode>): Box {
  const kind = n.data.kind;
  return { x: n.internals.positionAbsolute.x, y: n.internals.positionAbsolute.y, w: n.measured.width ?? 0, h: n.measured.height ?? 0,
    round: kind === 'flow' || kind === 'cloud' };
}
const centre = (b: Box): Pt => ({ x: b.x + b.w / 2, y: b.y + b.h / 2 });

/** Where the line from a node's centre towards `to` leaves the node. */
function exit(b: Box, to: Pt, gap: number): Pt {
  const c = centre(b), dx = to.x - c.x, dy = to.y - c.y, len = Math.hypot(dx, dy) || 1;
  if (b.round) { const r = Math.max(b.w, b.h) / 2 + gap; return { x: c.x + (dx / len) * r, y: c.y + (dy / len) * r }; }
  const s = Math.min((b.w / 2 + gap) / Math.abs(dx || 1e-9), (b.h / 2 + gap) / Math.abs(dy || 1e-9));
  return { x: c.x + dx * s, y: c.y + dy * s };
}

export const LinkEdge = memo(({ id, source, target, data, selected }: EdgeProps<DEdge>) => {
  const a = useInternalNode<DNode>(source), b = useInternalNode<DNode>(target);
  const kind = data?.kind ?? 'influence';
  const problem = useStore(s => s.compiled?.diagnostics.find(d => d.link === id && (kind === 'flow' || d.level === 'error')));
  const unused = useStore(s => kind === 'influence' && !data?.auto && !!s.compiled?.uses[target] && !s.compiled.uses[target].includes(source)
    && s.nodes.some(n => n.id === source && n.data.kind !== 'note' && n.data.kind !== 'frame' && n.data.kind !== 'cloud'));
  if (!a || !b) return null;
  const A = boxOf(a), B = boxOf(b), ca = centre(A), cb = centre(B);
  const dx = cb.x - ca.x, dy = cb.y - ca.y, dist = Math.hypot(dx, dy) || 1;
  let p0: Pt, p1: Pt, path: string, angle: number, mid: Pt, near: Pt;
  if (kind === 'influence') {
    // a gentle arc; a pair of links in opposite directions bow apart instead of overlapping
    const bow = Math.min(60, dist * 0.18);
    const ctrl = { x: (ca.x + cb.x) / 2 - (dy / dist) * bow, y: (ca.y + cb.y) / 2 + (dx / dist) * bow };
    p0 = exit(A, ctrl, 2); p1 = exit(B, ctrl, 5);
    path = `M${p0.x},${p0.y} Q${ctrl.x},${ctrl.y} ${p1.x},${p1.y}`;
    angle = Math.atan2(p1.y - ctrl.y, p1.x - ctrl.x);
    const q = (t: number): Pt => ({ x: (1 - t) ** 2 * p0.x + 2 * t * (1 - t) * ctrl.x + t * t * p1.x, y: (1 - t) ** 2 * p0.y + 2 * t * (1 - t) * ctrl.y + t * t * p1.y });
    mid = q(0.5); near = q(0.78);
  } else {
    p0 = exit(A, cb, kind === 'flow' ? 0 : 3); p1 = exit(B, ca, kind === 'flow' ? 1 : 5);
    angle = Math.atan2(p1.y - p0.y, p1.x - p0.x);
    const stop = kind === 'flow' ? { x: p1.x - Math.cos(angle) * 9, y: p1.y - Math.sin(angle) * 9 } : p1;   // the pipe ends under its arrowhead
    path = `M${p0.x},${p0.y} L${stop.x},${stop.y}`;
    mid = { x: (p0.x + p1.x) / 2, y: (p0.y + p1.y) / 2 }; near = mid;
  }
  const head = kind === 'flow' ? 'M0,0 L-13,-7.5 L-13,7.5 Z' : 'M0,0 L-9,-4 L-9,4 Z';
  const cls = `link ${kind}${selected ? ' selected' : ''}${problem ? ' problem' : ''}${unused ? ' unused' : ''}${data?.auto ? ' auto' : ''}`;
  const label = kind === 'flow' ? (data?.rate ? `ƒ ${data.rate}` : '') : kind === 'relation' ? data?.label : '';
  return (
    <g className={cls}>
      {kind === 'flow' ? <><path d={path} className="pipe-outer" /><path d={path} className="pipe-inner" /></> : <path d={path} className="wire" />}
      <path d={head} className="head" transform={`translate(${p1.x},${p1.y}) rotate(${(angle * 180) / Math.PI})`} />
      <path d={path} className="react-flow__edge-interaction" fill="none" stroke="transparent" strokeWidth={22} />
      {kind === 'influence' && data?.polarity && (
        <g transform={`translate(${near.x - (dy / dist) * 11},${near.y + (dx / dist) * 11})`} className="polarity">
          <circle r="7.5" /><text dy="0.34em" textAnchor="middle">{data.polarity === '-' ? '−' : '+'}</text>
        </g>
      )}
      {(label || problem) && (
        <EdgeLabelRenderer>
          <div className={`link-label${problem ? ' problem' : ''}`} style={{ transform: `translate(-50%, -50%) translate(${mid.x}px, ${mid.y}px)` }}
            title={problem?.message}>{label || '!'}</div>
        </EdgeLabelRenderer>
      )}
    </g>
  );
});

export const edgeTypes = { link: LinkEdge };
