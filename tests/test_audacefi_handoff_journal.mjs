import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { competitiveJournalIsQuiescent } from '../integrations/audacefi/handoff-journal.mjs';

const key = 'autotrader_s1_competitive_01_buy';
const hash = createHash('sha256')
  .update('audacefi.offline.canonical.v1\n' + JSON.stringify({ key })).digest('hex');
const encode = value => Buffer.from(value).toString('base64');
const files = stage => ({
  'operation-00.json': encode(JSON.stringify({index:0,side:'buy',symbol:'SOL',
    operation:{operationKey:key}})),
  [hash + '.events']: encode(JSON.stringify({stage}) + '\n'),
});

test('historical plans with terminal events allow handoff', () => {
  assert.equal(competitiveJournalIsQuiescent(files('FINAL_CONFIRMED')), true);
  assert.equal(competitiveJournalIsQuiescent(files('FINAL_FAILED')), true);
});

test('pending submissions and missing events block handoff', () => {
  assert.equal(competitiveJournalIsQuiescent(files('SUBMITTED')), false);
  assert.equal(competitiveJournalIsQuiescent({
    'operation-00.json': files('FINAL_CONFIRMED')['operation-00.json'],
  }), false);
});

test('malformed historical plans fail closed', () => {
  assert.equal(competitiveJournalIsQuiescent({'operation-old.json': encode('{}')}), false);
  assert.equal(competitiveJournalIsQuiescent({'operation-00.json': encode('{}')}), false);
  assert.equal(competitiveJournalIsQuiescent({'adaptive-state.json': encode('{}')}), true);
});
