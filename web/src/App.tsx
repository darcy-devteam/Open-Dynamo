import { ReactFlowProvider } from '@xyflow/react';
import { useEffect } from 'react';
import { Canvas, KINDS } from './canvas/Canvas';
import { Gallery, Palette, TopBar } from './panels/Chrome';
import { Dock } from './panels/Dock';
import { Inspector } from './panels/Inspector';
import { useStore } from './store';

/** Keyboard shortcuts, ignored while typing in a field. */
function useShortcuts() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (el.closest('input, textarea, select, [contenteditable]')) return;
      const s = useStore.getState(), mod = e.ctrlKey || e.metaKey, key = e.key.toLowerCase();
      if (mod && key === 'z') { e.preventDefault(); if (e.shiftKey) s.redo(); else s.undo(); }
      else if (mod && key === 'y') { e.preventDefault(); s.redo(); }
      else if (mod && key === 'c') s.copy();
      else if (mod && key === 'v') s.paste();
      else if (mod && key === 'd') { e.preventDefault(); s.copy(); s.paste(); }
      else if (mod && key === 'a') { e.preventDefault(); s.selectAll(); }
      else if (key === 'escape') useStore.setState({ tool: 'select', gallery: false });
      else if (!mod && !e.altKey) {
        if (key === 'v') useStore.setState({ tool: 'select' });
        const kind = KINDS.find(k => k.key.toLowerCase() === key);
        if (kind) useStore.setState({ tool: kind.kind });
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
}

export function App() {
  const gallery = useStore(s => s.gallery);
  useShortcuts();
  return (
    <ReactFlowProvider>
      <div className="app">
        <TopBar />
        <div className="work">
          <div className="stage"><Canvas /><Palette /></div>
          <Inspector />
        </div>
        <Dock />
        {gallery && <Gallery />}
      </div>
    </ReactFlowProvider>
  );
}
