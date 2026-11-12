/// <reference lib="webworker" />
// Runs the model's WebAssembly kernel off the main thread, so the canvas stays smooth while a run is in progress.

interface Kernel {
  memory: WebAssembly.Memory;
  malloc(bytes: number): number;
  free(ptr: number): void;
  run(b0: bigint, b1: bigint, cfg: number, p: number, ix: number, tgt: number, out: number, met: number): number;
  _initialize?: () => void;
}

let kernel: Kernel | null = null;

self.onmessage = async (ev: MessageEvent) => {
  const m = ev.data;
  try {
    if (m.type === 'load') {
      const module = await WebAssembly.compile(m.bytes);
      const imports: Record<string, Record<string, () => number>> = {};
      for (const imp of WebAssembly.Module.imports(module)) (imports[imp.module] ??= {})[imp.name] = () => 0; // the kernel does no I/O
      kernel = (await WebAssembly.instantiate(module, imports)).exports as unknown as Kernel;
      kernel._initialize?.();
      postMessage({ type: 'loaded', digest: m.digest });
    } else if (m.type === 'run') {
      if (!kernel) throw new Error('no kernel loaded');
      const k = kernel, started = performance.now();
      const cfg: Float64Array = m.cfg, params: Float64Array = m.params, rec: Int32Array = m.rec;
      const nOut = Math.floor(cfg[2] / Math.max(1, cfg[4])) + 1, nRec = rec.length;
      const ptrs = [cfg.length * 8, Math.max(1, params.length) * 8, (nRec + 1) * 4, 8, Math.max(1, nRec) * nOut * 8, 8].map(n => k.malloc(n));
      if (ptrs.some(p => p === 0)) throw new Error('This run needs more memory than the browser allows. Use a larger time step or a shorter run.');
      new Float64Array(k.memory.buffer, ptrs[0], cfg.length).set(cfg);
      new Float64Array(k.memory.buffer, ptrs[1], params.length).set(params);
      new Int32Array(k.memory.buffer, ptrs[2], nRec).set(rec);
      const code = k.run(0n, 1n, ptrs[0], ptrs[1], ptrs[2], ptrs[3], ptrs[4], ptrs[5]);
      if (code !== 0) throw new Error('This run needs more memory than the browser allows. Use a larger time step or a shorter run.');
      const out = new Float64Array(k.memory.buffer, ptrs[4], nRec * nOut).slice(); // the buffer may have moved while running
      ptrs.forEach(p => k.free(p));
      postMessage({ type: 'result', seq: m.seq, out, nOut, ms: performance.now() - started }, [out.buffer]);
    }
  } catch (e) {
    postMessage({ type: 'error', seq: m.seq, message: e instanceof Error ? e.message : String(e) });
  }
};
