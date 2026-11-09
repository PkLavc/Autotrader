import { createHash } from 'node:crypto';

const TERMINAL = new Set(['FINAL_CONFIRMED', 'FINAL_FAILED', 'UNSENT', 'EXPIRED_UNSENT']);

function operationKey(index) {
  return `autotrader_s1_competitive_${String(Math.floor(index / 2) + 1).padStart(2, '0')}_${index % 2 ? 'sell' : 'buy'}`;
}

function journalHash(key) {
  const canonical = JSON.stringify({ key });
  return createHash('sha256')
    .update('audacefi.offline.canonical.v1\n' + canonical)
    .digest('hex');
}

/** Only allow handoff after every persisted competitive plan reached a terminal event. */
export function competitiveJournalIsQuiescent(files) {
  if (!files || typeof files !== 'object' || Array.isArray(files)) return false;
  const plans = Object.keys(files).filter(name => name.startsWith('operation-'));
  for (const name of plans) {
    const match = /^operation-(\d{2})\.json$/.exec(name);
    if (!match || typeof files[name] !== 'string') return false;
    const index = Number(match[1]);
    let plan;
    try { plan = JSON.parse(Buffer.from(files[name], 'base64').toString('utf8')); }
    catch { return false; }
    const key = operationKey(index);
    if (plan?.index !== index || !['buy', 'sell'].includes(plan?.side) ||
        !['SOL', 'ETH'].includes(plan?.symbol) ||
        plan?.operation?.operationKey !== key) return false;
    const encodedEvents = files[journalHash(key) + '.events'];
    if (typeof encodedEvents !== 'string') return false;
    let events;
    try {
      const text = Buffer.from(encodedEvents, 'base64').toString('utf8');
      if (!text.endsWith('\n')) return false;
      events = text.trimEnd().split('\n').map(line => JSON.parse(line));
    } catch { return false; }
    if (!events.length || !TERMINAL.has(events.at(-1)?.stage)) return false;
  }
  return true;
}
