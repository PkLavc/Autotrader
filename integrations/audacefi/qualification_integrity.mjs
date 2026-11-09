import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';

export async function readVerifiedQualification(path) {
  const bytes = await readFile(path);
  const checksum = (await readFile(path + '.sha256', 'utf8')).trim();
  if (!/^[0-9a-f]{64}$/.test(checksum) ||
      createHash('sha256').update(bytes).digest('hex') !== checksum)
    throw Error('QUALIFICATION_CHECKSUM_INVALID');
  const value = JSON.parse(bytes.toString('utf8'));
  if (value?.schemaVersion !== 3 || !Number.isInteger(value.sourceRunId) ||
      value.sourceRunId <= 0 || !Array.isArray(value.approvedSymbols) ||
      !value.approvedBySymbol || typeof value.approvedBySymbol !== 'object')
    throw Error('QUALIFICATION_SCHEMA_INVALID');
  return value;
}
