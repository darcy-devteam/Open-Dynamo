// A small canvas chart: lines over time or against each other, shaded bands, bars, and a hover readout.
import { useEffect, useRef, useState } from 'react';
import { fmt } from './api';
import { useStore } from './store';

type Arr = ArrayLike<number>;
export interface Line { name: string; x: Arr; y: Arr; color: string; width?: number; dots?: boolean; quiet?: boolean }
export interface Band { x: Arr; lo: Arr; hi: Arr; color: string; alpha: number }
export interface Bars { edges: number[]; counts: number[]; color: string }
interface Props { lines: Line[]; bands?: Band[]; bars?: Bars; mode?: 'time' | 'xy'; xLabel?: string; yLabel?: string; height?: number | string }

export const SERIES = ['--s1', '--s2', '--s3', '--s4', '--s5', '--s6', '--s7', '--s8'];
export const RAMP = ['#86b6ef', '#6da7ec', '#5598e7', '#3987e5', '#2a78d6', '#256abf', '#1c5cab', '#184f95', '#104281'];
/** Colour for run i of n in a sweep: one hue, light to dark. */
export const rampColor = (i: number, n: number) => RAMP[n <= 1 ? 4 : Math.round((i / (n - 1)) * (RAMP.length - 1))];

function ticks(lo: number, hi: number, count: number): number[] {
  const span = hi - lo || Math.abs(hi) || 1;
  const raw = span / Math.max(1, count), mag = 10 ** Math.floor(Math.log10(raw)), f = raw / mag;
  const step = (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * mag;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return out;
}

function extent(arrays: Arr[]): [number, number] {
  let lo = Infinity, hi = -Infinity;
  for (const a of arrays) for (let i = 0; i < a.length; i++) { const v = a[i]; if (Number.isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; } }
  if (lo === Infinity) return [0, 1];
  if (lo === hi) { const pad = Math.abs(lo) * 0.1 || 1; return [lo - pad, hi + pad]; }
  return [lo, hi];
}

export function Chart({ lines, bands = [], bars, mode = 'time', xLabel, yLabel, height = '100%' }: Props) {
  const wrap = useRef<HTMLDivElement>(null), canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [hover, setHover] = useState<{ px: number; py: number } | null>(null);
  const theme = useStore(s => s.theme);
  const geo = useRef<{ x: (v: number) => number; y: (v: number) => number; ix: (p: number) => number; l: number; r: number; t: number; b: number } | null>(null);
  const [tip, setTip] = useState<{ left: number; top: number; title: string; rows: { color: string; name: string; value: string }[] } | null>(null);

  useEffect(() => {
    const el = wrap.current!;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const cv = canvas.current!, { w, h } = size;
    if (w < 40 || h < 40) return;
    const dpr = window.devicePixelRatio || 1;
    cv.width = w * dpr; cv.height = h * dpr;
    const g = cv.getContext('2d')!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    const css = getComputedStyle(cv);
    const col = (c: string) => (c.startsWith('--') ? css.getPropertyValue(c).trim() : c);
    g.font = '11px system-ui, -apple-system, "Segoe UI", sans-serif';

    let [x0, x1] = bars ? [bars.edges[0], bars.edges.at(-1)!] : extent([...lines.map(l => l.x), ...bands.map(b => b.x)]);
    let [y0, y1] = bars ? [0, Math.max(1, ...bars.counts)] : extent([...lines.map(l => l.y), ...bands.flatMap(b => [b.lo, b.hi])]);
    if (!bars) { const pad = (y1 - y0) * 0.06; y0 -= pad; y1 += pad; }
    if (mode === 'xy') { const pad = (x1 - x0) * 0.04; x0 -= pad; x1 += pad; }
    const yt = ticks(y0, y1, Math.max(2, Math.floor(h / 48))), labels = yt.map(v => fmt(v));
    const L = Math.max(34, Math.ceil(Math.max(...labels.map(s => g.measureText(s).width))) + 14) + (yLabel ? 14 : 0);
    const R = 12, T = 10, B = xLabel ? 38 : 24;
    const X = (v: number) => L + ((v - x0) / (x1 - x0)) * (w - L - R), Y = (v: number) => h - B - ((v - y0) / (y1 - y0)) * (h - T - B);
    geo.current = { x: X, y: Y, ix: p => x0 + ((p - L) / (w - L - R)) * (x1 - x0), l: L, r: w - R, t: T, b: h - B };

    g.lineWidth = 1;
    g.textBaseline = 'middle'; g.textAlign = 'right';
    yt.forEach((v, i) => {
      const py = Math.round(Y(v)) + 0.5;
      g.strokeStyle = col('--grid'); g.beginPath(); g.moveTo(L, py); g.lineTo(w - R, py); g.stroke();
      g.fillStyle = col('--muted'); g.fillText(labels[i], L - 8, py);
    });
    g.strokeStyle = col('--axis'); g.beginPath(); g.moveTo(L, h - B + 0.5); g.lineTo(w - R, h - B + 0.5); g.stroke();
    g.textAlign = 'center'; g.textBaseline = 'top';
    for (const v of ticks(x0, x1, Math.max(2, Math.floor((w - L - R) / 80)))) { g.fillStyle = col('--muted'); g.fillText(fmt(v), X(v), h - B + 6); }
    g.fillStyle = col('--ink-2');
    if (xLabel) g.fillText(xLabel, (L + w - R) / 2, h - 15);
    if (yLabel) { g.save(); g.translate(10, (T + h - B) / 2); g.rotate(-Math.PI / 2); g.textBaseline = 'middle'; g.fillText(yLabel, 0, 0); g.restore(); }

    g.save(); g.beginPath(); g.rect(L, T - 4, w - L - R, h - T - B + 4); g.clip();
    for (const b of bands) {
      g.fillStyle = col(b.color); g.globalAlpha = b.alpha; g.beginPath();
      for (let i = 0; i < b.x.length; i++) g[i ? 'lineTo' : 'moveTo'](X(b.x[i]), Y(b.hi[i]));
      for (let i = b.x.length - 1; i >= 0; i--) g.lineTo(X(b.x[i]), Y(b.lo[i]));
      g.closePath(); g.fill(); g.globalAlpha = 1;
    }
    if (bars) {
      g.fillStyle = col(bars.color);
      bars.counts.forEach((c, i) => {
        const a = X(bars.edges[i]) + 1, bw = Math.max(1, X(bars.edges[i + 1]) - a - 1), top = Y(c);
        g.beginPath(); g.roundRect(a, top, bw, h - B - top, [3, 3, 0, 0]); g.fill();
      });
    }
    g.lineJoin = 'round'; g.lineCap = 'round';
    for (const l of lines) {
      g.strokeStyle = g.fillStyle = col(l.color); g.lineWidth = l.width ?? 2; g.globalAlpha = l.quiet ? 0.35 : 1;
      if (l.dots) { for (let i = 0; i < l.x.length; i++) { g.beginPath(); g.arc(X(l.x[i]), Y(l.y[i]), 3.5, 0, 7); g.fill(); } continue; }
      g.beginPath();
      let pen = false;
      for (let i = 0; i < l.x.length; i++) {
        if (!Number.isFinite(l.y[i]) || !Number.isFinite(l.x[i])) { pen = false; continue; }
        g[pen ? 'lineTo' : 'moveTo'](X(l.x[i]), Y(l.y[i])); pen = true;
      }
      g.stroke();
    }
    g.globalAlpha = 1; g.restore();

    // hover: a crosshair and the values under it
    if (!hover || !lines.length || hover.px < L || hover.px > w - R) return setTip(null);
    const named = lines.filter(l => !l.quiet);
    const rows: { color: string; name: string; value: string }[] = [];
    let title = '', anchor = hover.px;
    if (mode === 'xy') {
      let best = Infinity, bi = 0, bl = named[0];
      for (const l of named) for (let i = 0; i < l.x.length; i++) {
        const d = (X(l.x[i]) - hover.px) ** 2 + (Y(l.y[i]) - hover.py) ** 2;
        if (d < best) { best = d; bi = i; bl = l; }
      }
      if (!bl || best > 40 ** 2) return setTip(null);
      anchor = X(bl.x[bi]);
      g.fillStyle = col(bl.color); g.strokeStyle = col('--surface'); g.lineWidth = 2;
      g.beginPath(); g.arc(anchor, Y(bl.y[bi]), 5, 0, 7); g.fill(); g.stroke();
      title = bl.name;
      rows.push({ color: '', name: xLabel ?? 'x', value: fmt(bl.x[bi]) }, { color: '', name: yLabel ?? 'y', value: fmt(bl.y[bi]) });
    } else {
      const xv = geo.current.ix(hover.px);
      const ref = named[0] ?? lines[0];
      let lo = 0, hi = ref.x.length - 1;
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (ref.x[mid] < xv) lo = mid; else hi = mid; }
      const xi = Math.abs(ref.x[lo] - xv) < Math.abs(ref.x[hi] - xv) ? lo : hi;
      anchor = X(ref.x[xi]);
      g.strokeStyle = col('--axis'); g.lineWidth = 1; g.beginPath(); g.moveTo(Math.round(anchor) + 0.5, T); g.lineTo(Math.round(anchor) + 0.5, h - B); g.stroke();
      title = `${xLabel ?? 'x'} = ${fmt(ref.x[xi])}`;
      for (const l of named.slice(0, 12)) {
        let i = xi;
        if (l.x !== ref.x) { let d = Infinity; for (let j = 0; j < l.x.length; j++) { const dj = Math.abs(l.x[j] - ref.x[xi]); if (dj < d) { d = dj; i = j; } } }
        if (!Number.isFinite(l.y[i])) continue;
        g.fillStyle = col(l.color); g.strokeStyle = col('--surface'); g.lineWidth = 2;
        g.beginPath(); g.arc(X(l.x[i]), Y(l.y[i]), 4, 0, 7); g.fill(); g.stroke();
        rows.push({ color: col(l.color), name: l.name, value: fmt(l.y[i]) });
      }
    }
    setTip({ left: anchor > w / 2 ? anchor - 12 : anchor + 12, top: Math.max(4, Math.min(hover.py - 10, h - 30 - rows.length * 18)), title, rows });
  }, [lines, bands, bars, mode, xLabel, yLabel, size, hover, theme]);

  return (
    <div ref={wrap} className="chart" style={{ height }}
      onPointerMove={e => { const r = e.currentTarget.getBoundingClientRect(); setHover({ px: e.clientX - r.left, py: e.clientY - r.top }); }}
      onPointerLeave={() => { setHover(null); setTip(null); }}>
      <canvas ref={canvas} style={{ width: size.w, height: size.h }} />
      {tip && !bars && (
        <div className="tip" style={{ left: tip.left, top: tip.top, transform: tip.left < size.w / 2 ? undefined : 'translateX(-100%)' }}>
          <div className="tip-title">{tip.title}</div>
          {tip.rows.map((r, i) => (
            <div key={i} className="tip-row">{r.color && <i style={{ background: r.color }} />}<span>{r.name}</span><b>{r.value}</b></div>
          ))}
        </div>
      )}
    </div>
  );
}
