// Instrument the pinned reference verifier in a reconciliation-only Actions
// run. Emit only the failed gate or a fixed error code, never RPC payloads.
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const file = resolve(import.meta.dirname, '../../vendor/audacefi-integration-kit/dist/reference.js');
const source = await readFile(file, 'utf8');
const useTenBps = ['verify10', 'finalize10'].includes(process.argv[2]);
const start = source.indexOf('    async function verifyFinal(saved, expected) {');
const end = source.indexOf('    async function readSpotHoldings(', start);
if (start < 0 || end < 0) throw Error('VERIFIER_DIAGNOSTIC_SOURCE_CHANGED');
let body = source.slice(start, end);
let gate = 0;
body = body.replaceAll('return { status: "unknown" };', () => {
  gate += 1;
  return `return (console.log('DIRECT_PILOT_VERIFIER_GATE=${gate}'), { status: "unknown" });`;
});
if (gate !== 8 || !body.includes('        catch {\n            return (console.log'))
  throw Error('VERIFIER_DIAGNOSTIC_GATES_CHANGED');
body = body.replace('        catch {\n            return (console.log',
  "        catch (error) {\n            console.log('DIRECT_PILOT_VERIFIER_ERROR=' + (/^[A-Z_]{1,64}$/.test(error?.message) ? error.message : 'UNCLASSIFIED'));\n            return (console.log");
const loop = 'for (const { address, mint, owner, delta } of expected) {';
const mismatch = 'if (post - pre !== delta)\n            throw new Error("FINALIZED_BENCH_BALANCE_MISMATCH");';
if (!body.includes(loop) && source.includes(loop)) {
  const prefix = source.slice(0, start);
  if (!prefix.includes(loop) || !prefix.includes(mismatch)) throw Error('VERIFIER_BALANCE_DIAGNOSTIC_CHANGED');
  let changedPrefix = prefix.replace(loop,
    'for (const [position, { address, mint, owner, delta }] of expected.entries()) {').replace(mismatch,
    'if (post - pre !== delta) {\n            console.log("DIRECT_PILOT_BALANCE_ROLE=" + position);\n            const observed = post - pre;\n            const deviation = delta === 0n ? 0n : (observed - delta) * 10000n / (delta < 0n ? -delta : delta);\n            console.log("DIRECT_PILOT_BALANCE_DEVIATION_BPS=" + deviation.toString());\n            throw new Error("FINALIZED_BENCH_BALANCE_MISMATCH");\n        }');
  if (useTenBps) {
    const liveFee = 'secondary?.price, secondary?.expo, 20);';
    if (!changedPrefix.includes(liveFee)) throw Error('VERIFIER_FEE_DIAGNOSTIC_CHANGED');
    changedPrefix = changedPrefix.replace(liveFee, 'secondary?.price, secondary?.expo, 10);');
  }
  await writeFile(file, changedPrefix + body + source.slice(end));
} else throw Error('VERIFIER_BALANCE_DIAGNOSTIC_CHANGED');
