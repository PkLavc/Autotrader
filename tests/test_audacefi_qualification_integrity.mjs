import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { readVerifiedQualification } from '../integrations/audacefi/qualification_integrity.mjs';

test('qualification is accepted only with a matching checksum and schema', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'audacefi-gate-'));
  try {
    const path = join(dir, 'strategy-qualification.json');
    const payload = Buffer.from(JSON.stringify({ schemaVersion: 3, sourceRunId: 42,
      approvedSymbols: [], approvedBySymbol: {}, diagnosticsBySymbol: {} }) + '\n');
    await writeFile(path, payload);
    await writeFile(path + '.sha256', createHash('sha256').update(payload).digest('hex') + '\n');
    assert.equal((await readVerifiedQualification(path)).sourceRunId, 42);
    await writeFile(path, Buffer.from(payload.toString().replace('42', '43')));
    await assert.rejects(readVerifiedQualification(path), /QUALIFICATION_CHECKSUM_INVALID/);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
