// The frame around the canvas: top bar, floating tool palette and the example gallery.
import { useEffect, useRef, useState } from 'react';
import { api, fmt } from '../api';
import { KINDS } from '../canvas/Canvas';
import { engineMessage } from '../engine';
import { Icon } from '../icons';
import { toFile, useStore, type LinkMode } from '../store';
import type { ModelFile } from '../types';

function Status() {
  const status = useStore(s => s.status), result = useStore(s => s.result);
  const errors = useStore(s => s.compiled?.diagnostics.filter(d => d.level === 'error').length ?? 0);
  const open = () => useStore.setState({ dock: 'problems' });
  if (status === 'compiling') return <span className="status busy"><i />Checking the model…</span>;
  if (status === 'offline') return <span className="status bad" title={engineMessage()}><i />{engineMessage() || 'Server unavailable'}</span>;
  if (status === 'error') {
    return <button className="status bad" onClick={open}><i />{errors ? `${errors} problem${errors > 1 ? 's' : ''} to fix` : engineMessage()}</button>;
  }
  if (status === 'ready' && result) {
    return result.diverged
      ? <span className="status warn" title="Some values became infinite. A smaller time step or the RK4 method usually fixes this."><i />Values blew up: try a smaller time step</span>
      : <span className="status ok" title={`Simulated ${result.where === 'browser' ? 'in this browser' : 'on the server'}`}><i />Ran in {fmt(result.ms, 2)} ms {result.where === 'browser' ? 'in your browser' : 'on the server'}</span>;
  }
  return <span className="status"><i />Ready</span>;
}

export function TopBar() {
  const name = useStore(s => s.name), theme = useStore(s => s.theme);
  const canUndo = useStore(s => s.past.length > 0), canRedo = useStore(s => s.future.length > 0);
  const file = useRef<HTMLInputElement>(null);
  const s = useStore.getState;

  const save = () => {
    const blob = new Blob([JSON.stringify(toFile(s()), null, 1)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${s().name.replace(/[^\w -]+/g, '').trim() || 'model'}.dynamo.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  const open = async (f: File | undefined) => {
    if (!f) return;
    try {
      const model = JSON.parse(await f.text()) as ModelFile;
      if (!Array.isArray(model.nodes) || !Array.isArray(model.links)) throw new Error();
      s().load(model);
    } catch { alert('That file is not an Open-Dynamo model.'); }
  };
  const toggle = () => {
    const next = theme === 'dark' ? 'light' : 'dark';
    localStorage.setItem('open-dynamo:theme', next);
    useStore.setState({ theme: next });
  };

  return (
    <header className="topbar">
      <div className="brand">
        <svg viewBox="0 0 32 32" width="26" height="26"><rect width="32" height="32" rx="8" fill="var(--accent)" /><path d="M6 21c4 0 4-10 8-10s4 10 8 10 3-5 4-7" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" /></svg>
        <b>Open-Dynamo</b>
      </div>
      <input className="title" value={name} aria-label="Model name" placeholder="Untitled model" spellCheck={false}
        onChange={e => { s().commit('name'); useStore.setState({ name: e.target.value }); }} />
      <div className="group">
        <button title="Undo (Ctrl+Z)" disabled={!canUndo} onClick={() => s().undo()}><Icon name="undo" /></button>
        <button title="Redo (Ctrl+Shift+Z)" disabled={!canRedo} onClick={() => s().redo()}><Icon name="redo" /></button>
      </div>
      <div className="group">
        <button onClick={() => useStore.setState({ gallery: true })}><Icon name="grid" /> Examples</button>
        <button title="Start an empty model" onClick={() => { if (!s().nodes.length || confirm('Start a new, empty model? You can undo this.')) s().clear(); }}><Icon name="plus" /> New</button>
        <button title="Open a saved model file" onClick={() => file.current?.click()}><Icon name="folder" /> Open</button>
        <button title="Download this model as a file" onClick={save}><Icon name="save" /> Save</button>
        <input ref={file} type="file" accept=".json,application/json" hidden onChange={e => { void open(e.target.files?.[0]); e.target.value = ''; }} />
      </div>
      <div className="spacer" />
      <Status />
      <button className="ghost" title={theme === 'dark' ? 'Switch to light' : 'Switch to dark'} onClick={toggle}><Icon name={theme === 'dark' ? 'sun' : 'moon'} /></button>
    </header>
  );
}

const LINKS: { mode: LinkMode; label: string; hint: string }[] = [
  { mode: 'auto', label: 'Auto', hint: 'Choose the link type from what you connect' },
  { mode: 'flow', label: 'Flow', hint: 'Material moving between stocks' },
  { mode: 'influence', label: 'Influence', hint: 'One thing is an input to another' },
  { mode: 'relation', label: 'Relation', hint: 'A labelled line with no effect on the maths' },
];

export function Palette() {
  const tool = useStore(s => s.tool), linkMode = useStore(s => s.linkMode);
  return (
    <aside className="palette">
      <button className={tool === 'select' ? 'on' : ''} title="Select and move (V)" onClick={() => useStore.setState({ tool: 'select' })}><Icon name="select" /></button>
      <hr />
      {KINDS.map(k => (
        <button key={k.kind} className={tool === k.kind ? 'on' : ''} title={`${k.label} (${k.key}): ${k.hint}. Click, then click the canvas; or drag it there.`}
          draggable onDragStart={e => { e.dataTransfer.setData('application/open-dynamo', k.kind); e.dataTransfer.effectAllowed = 'copy'; }}
          onClick={() => useStore.setState({ tool: tool === k.kind ? 'select' : k.kind })}>
          <Icon name={k.kind} /><span>{k.label}</span>
        </button>
      ))}
      <hr />
      <div className="palette-label">Links</div>
      {LINKS.map(l => (
        <button key={l.mode} className={`text${linkMode === l.mode ? ' on' : ''}`} title={l.hint} onClick={() => useStore.setState({ linkMode: l.mode })}>{l.label}</button>
      ))}
    </aside>
  );
}

interface Listing { id: string; name: string; description: string; level: string }

export function Gallery() {
  const [items, setItems] = useState<Listing[] | null>(null), [error, setError] = useState('');
  useEffect(() => { api<Listing[]>('examples').then(setItems).catch(e => setError(e.message)); }, []);
  const close = () => useStore.setState({ gallery: false });
  const pick = async (id: string) => {
    try { useStore.getState().load(await api<ModelFile>(`examples/${id}`)); } catch (e) { setError((e as Error).message); }
  };
  return (
    <div className="scrim" onPointerDown={e => { if (e.target === e.currentTarget) close(); }}>
      <div className="dialog" role="dialog" aria-label="Examples">
        <div className="dialog-head">
          <div><h2>Open-Dynamo</h2><p>Draw a system and watch it run as you build it. Start from a worked example or a blank canvas.</p></div>
          <button className="ghost" onClick={close} title="Close"><Icon name="close" /></button>
        </div>
        {error && <p className="error">{error}</p>}
        <div className="cards">
          <button className="card blank" onClick={() => { if (useStore.getState().nodes.length) useStore.getState().clear(); else close(); }}>
            <b>Blank canvas</b><span>Build your own model from stocks, flows and variables.</span>
          </button>
          {(items ?? []).map(m => (
            <button key={m.id} className="card" onClick={() => void pick(m.id)}>
              <i className={`level ${m.level.toLowerCase()}`}>{m.level}</i><b>{m.name}</b><span>{m.description}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
