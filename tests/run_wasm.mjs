// Runs a kernel the way the browser does: node run_wasm.mjs <kernel.wasm> <job.json>
// job: { cfg: number[16], params: number[], rec: number[] } -> prints { out: number[][] } (one row per recorded slot)
import { readFileSync } from 'node:fs';

const [wasmPath, jobPath] = process.argv.slice(2);
const job = JSON.parse(readFileSync(jobPath, 'utf8'));
const module = await WebAssembly.compile(readFileSync(wasmPath));
const imports = {};
for (const imp of WebAssembly.Module.imports(module)) {
  (imports[imp.module] ??= {})[imp.name] = () => 0;          // the kernel does no I/O; any WASI import is a stub
}
const { exports: k } = await WebAssembly.instantiate(module, imports);
if (k._initialize) k._initialize();

const nOut = Math.floor(job.cfg[2] / Math.max(1, job.cfg[4])) + 1;
const nRec = job.rec.length;
const pCfg = k.malloc(job.cfg.length * 8), pP = k.malloc(Math.max(1, job.params.length) * 8);
const pIx = k.malloc((nRec + 1) * 4), pTgt = k.malloc(8), pOut = k.malloc(Math.max(1, nRec) * nOut * 8), pMet = k.malloc(8);
new Float64Array(k.memory.buffer, pCfg, job.cfg.length).set(job.cfg);
new Float64Array(k.memory.buffer, pP, job.params.length).set(job.params);
new Int32Array(k.memory.buffer, pIx, nRec).set(job.rec);
const code = k.run(0n, 1n, pCfg, pP, pIx, pTgt, pOut, pMet);
const flat = new Float64Array(k.memory.buffer, pOut, nRec * nOut);
const out = job.rec.map((_, r) => Array.from(flat.subarray(r * nOut, (r + 1) * nOut)));
console.log(JSON.stringify({ code, imports: WebAssembly.Module.imports(module).map(i => `${i.module}.${i.name}`), out }));
