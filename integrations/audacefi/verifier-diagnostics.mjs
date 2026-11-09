// Instrument the pinned reference verifier in a reconciliation-only Actions
// run. Emit only the failed gate or a fixed error code, never RPC payloads.
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const file = resolve(import.meta.dirname, '../../vendor/audacefi-integration-kit/dist/reference.js');
const source = await readFile(file, 'utf8');
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
await writeFile(file, source.slice(0, start) + body + source.slice(end));
