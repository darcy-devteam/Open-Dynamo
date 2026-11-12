import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { startEngine } from './engine';
import { toFile, useStore } from './store';
import './styles.css';

startEngine();
// Handy in the browser console and in end-to-end tests: openDynamo.store.getState()
(window as unknown as { openDynamo: unknown }).openDynamo = { store: useStore, toFile };
createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
