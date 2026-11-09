import assert from 'node:assert/strict';
import { test } from 'node:test';

test('cancels a stale operator and queues one replacement', async () => {
  const names = ['GITHUB_REPOSITORY', 'GITHUB_TOKEN', 'AUDACEFI_API_KEY',
    'AUDACEFI_WALLET_ADDRESS'];
  const previous = Object.fromEntries(names.map(name => [name, process.env[name]]));
  const originalFetch = globalThis.fetch;
  const calls = [];
  Object.assign(process.env, {
    GITHUB_REPOSITORY: 'Example/Autotrader', GITHUB_TOKEN: 'test-token',
    AUDACEFI_API_KEY: 'test-api-key', AUDACEFI_WALLET_ADDRESS: 'test-wallet',
  });
  globalThis.fetch = async (url, options) => {
    calls.push({ url, method: options?.method ?? 'GET', body: options?.body });
    if (url.includes('/api/v1/me/state?'))
      return Response.json({ season: { active: { status: 'active',
        executionMode: 'devnet_onchain', id: 'season',
        endsAt: new Date(Date.now() + 24 * 3600_000).toISOString() } },
        wallet: { state: 'ok', walletAddress: 'test-wallet', seasonId: 'season' } });
    if (url.includes('/actions/workflows/audacefi-direct-pilot.yml/runs?'))
      return Response.json({ workflow_runs: [{ id: 123, status: 'in_progress',
        head_branch: 'main', created_at: new Date(Date.now() - 20 * 60_000).toISOString() }] });
    if (url.includes('/commits?sha=audacefi-competitive-journal'))
      return Response.json([{ commit: { committer: {
        date: new Date(Date.now() - 15 * 60_000).toISOString(),
      } } }]);
    if (url.endsWith('/actions/runs/123/cancel'))
      return new Response(null, { status: 202 });
    if (url.endsWith('/actions/workflows/audacefi-direct-pilot.yml/dispatches'))
      return new Response(null, { status: 204 });
    throw Error('UNEXPECTED_TEST_REQUEST');
  };
  try {
    await import('./watchdog.mjs?test=' + Date.now());
    assert.equal(calls.length, 5);
    assert.equal(calls[3].method, 'POST');
    assert.equal(calls[4].method, 'POST');
    assert.deepEqual(JSON.parse(calls[4].body), {
      ref: 'main', inputs: { mode: 'auto', session_minutes: '330' },
    });
  } finally {
    globalThis.fetch = originalFetch;
    for (const name of names) {
      if (previous[name] === undefined) delete process.env[name];
      else process.env[name] = previous[name];
    }
  }
});
