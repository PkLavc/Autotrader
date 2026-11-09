import assert from 'node:assert/strict';
import { test } from 'node:test';

test('queues a successor only after reading an active matching season', async () => {
  const names = ['GITHUB_REPOSITORY', 'GITHUB_TOKEN', 'GITHUB_RUN_ID',
    'AUDACEFI_API_KEY', 'AUDACEFI_WALLET_ADDRESS'];
  const previous = Object.fromEntries(names.map(name => [name, process.env[name]]));
  const originalFetch = globalThis.fetch;
  const calls = [];
  Object.assign(process.env, {
    GITHUB_REPOSITORY: 'Example/Autotrader', GITHUB_TOKEN: 'test-token',
    GITHUB_RUN_ID: '123', AUDACEFI_API_KEY: 'test-api-key',
    AUDACEFI_WALLET_ADDRESS: 'test-wallet',
  });
  globalThis.fetch = async (url, options) => {
    calls.push({ url, method: options?.method ?? 'GET', body: options?.body });
    if (url.endsWith('/actions/runs/123'))
      return Response.json({ created_at: new Date(Date.now() - 21 * 60_000).toISOString(),
        repository: { full_name: 'Example/Autotrader' } });
    if (url.includes('/api/v1/me/state?'))
      return Response.json({ season: { active: { status: 'active',
        executionMode: 'devnet_onchain', id: 'season',
        endsAt: new Date(Date.now() + 24 * 3600_000).toISOString() } },
        wallet: { state: 'ok', walletAddress: 'test-wallet', seasonId: 'season' } });
    if (url.endsWith('/actions/workflows/audacefi-direct-pilot.yml/dispatches'))
      return new Response(null, { status: 204 });
    throw Error('UNEXPECTED_TEST_REQUEST');
  };
  try {
    await import('./queue-next-session.mjs?test=' + Date.now());
    assert.equal(calls.length, 3);
    assert.equal(calls[2].method, 'POST');
    assert.deepEqual(JSON.parse(calls[2].body), {
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
