// Continue a long Devnet session if GitHub drops a scheduled trigger.
// The official season state, not a hard-coded calendar, controls the cutoff.
const origin = 'https://sandbox.audacefi.app';
const repository = process.env.GITHUB_REPOSITORY;
const token = process.env.GITHUB_TOKEN;
const key = process.env.AUDACEFI_API_KEY;
const address = process.env.AUDACEFI_WALLET_ADDRESS;
const runId = process.env.GITHUB_RUN_ID;
if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository ?? '') ||
    !token || !key || !address || !/^[1-9][0-9]{0,19}$/.test(runId ?? ''))
  throw Error('AUTO_CONTINUATION_CONFIG_INVALID');

async function checkedJson(url, bearer) {
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${bearer}`, Accept: 'application/json',
      ...(url.startsWith('https://api.github.com/') ?
        { 'X-GitHub-Api-Version': '2022-11-28' } : {}) },
    redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw Error('AUTO_CONTINUATION_READ_HTTP_' + response.status);
  return response.json();
}

const now = Date.now();
const run = await checkedJson(`https://api.github.com/repos/${repository}/actions/runs/${runId}`, token);
const started = Date.parse(run.created_at);
if (run.repository?.full_name !== repository || !Number.isFinite(started) ||
    started > now || started < now - 6 * 3600_000)
  throw Error('AUTO_CONTINUATION_RUN_INVALID');
// An immediate failure cannot be allowed to create a rapid dispatch loop.
if (now - started < 20 * 60_000) {
  console.log('AUDACEFI_CONTINUATION=WAIT_FOR_CRON_AFTER_SHORT_RUN');
  process.exit(0);
}

const state = await checkedJson(origin + '/api/v1/me/state?category=crypto-bro&mode=real', key);
const season = state?.season?.active;
const end = Date.parse(season?.endsAt);
if (!season || season.status !== 'active' || season.executionMode !== 'devnet_onchain' ||
    state?.wallet?.state !== 'ok' || state.wallet.walletAddress !== address ||
    state.wallet.seasonId !== season.id || !Number.isFinite(end))
  throw Error('AUTO_CONTINUATION_SEASON_INVALID');
if (end <= now + 10 * 60_000) {
  console.log('AUDACEFI_CONTINUATION=SEASON_CLOSING');
  process.exit(0);
}

const response = await fetch(
  `https://api.github.com/repos/${repository}/actions/workflows/audacefi-direct-pilot.yml/dispatches`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json',
      'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28' },
    body: JSON.stringify({ ref: 'main', inputs: { mode: 'auto', session_minutes: '330' } }),
    redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(15000),
  });
if (response.status !== 204) throw Error('AUTO_CONTINUATION_DISPATCH_HTTP_' + response.status);
console.log('AUDACEFI_CONTINUATION=QUEUED');
