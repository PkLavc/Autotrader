// Independent Actions watchdog. It never signs or submits an AudaceFi order.
const repository = process.env.GITHUB_REPOSITORY;
const token = process.env.GITHUB_TOKEN;
const key = process.env.AUDACEFI_API_KEY;
const address = process.env.AUDACEFI_WALLET_ADDRESS;
if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository ?? '') ||
    !token || !key || !address) throw Error('WATCHDOG_CONFIG_INVALID');

const api = `https://api.github.com/repos/${repository}`;
async function request(url, bearer, method = 'GET', body) {
  const response = await fetch(url, {
    method,
    headers: { Authorization: `Bearer ${bearer}`, Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      ...(body ? { 'Content-Type': 'application/json' } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
    redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(15000),
  });
  if (!response.ok) throw Error('WATCHDOG_HTTP_' + response.status);
  return response.status === 204 || response.status === 202 ? null : response.json();
}

const now = Date.now();
const state = await request(
  'https://sandbox.audacefi.app/api/v1/me/state?category=crypto-bro&mode=real', key);
const season = state?.season?.active;
const end = Date.parse(season?.endsAt);
if (!season || season.status !== 'active' || season.executionMode !== 'devnet_onchain' ||
    state?.wallet?.state !== 'ok' || state.wallet.walletAddress !== address ||
    state.wallet.seasonId !== season.id || !Number.isFinite(end))
  throw Error('WATCHDOG_SEASON_INVALID');
if (end <= now + 10 * 60_000) {
  console.log('AUDACEFI_WATCHDOG=SEASON_CLOSING');
  process.exit(0);
}

const workflow = 'audacefi-direct-pilot.yml';
const list = await request(`${api}/actions/workflows/${workflow}/runs?per_page=20`, token);
if (!Array.isArray(list?.workflow_runs)) throw Error('WATCHDOG_RUN_LIST_INVALID');
const runs = list.workflow_runs.filter(run => run?.head_branch === 'main');
const running = runs.find(run => run.status === 'in_progress');
const pending = runs.find(run => ['queued', 'pending', 'requested', 'waiting'].includes(run.status));
if (running) {
  const started = Date.parse(running.created_at);
  if (!Number.isInteger(running.id) || !Number.isFinite(started) || started > now + 60_000)
    throw Error('WATCHDOG_RUN_INVALID');
  if (now - started < 8 * 60_000) {
    console.log('AUDACEFI_WATCHDOG=OPERATOR_STARTING');
    process.exit(0);
  }
  const commits = await request(`${api}/commits?sha=audacefi-competitive-journal&per_page=1`, token);
  const heartbeat = Date.parse(commits?.[0]?.commit?.committer?.date);
  if (Number.isFinite(heartbeat) && heartbeat <= now + 60_000 &&
      now - heartbeat < 12 * 60_000) {
    console.log('AUDACEFI_WATCHDOG=OPERATOR_HEALTHY');
    process.exit(0);
  }
  await request(`${api}/actions/runs/${running.id}/cancel`, token, 'POST');
  console.log('AUDACEFI_WATCHDOG=STALE_OPERATOR_CANCEL_REQUESTED');
  if (pending) {
    console.log('AUDACEFI_WATCHDOG=REPLACEMENT_PENDING');
    process.exit(0);
  }
} else if (pending) {
  const queued = Date.parse(pending.created_at);
  if (Number.isFinite(queued) && now - queued < 10 * 60_000) {
    console.log('AUDACEFI_WATCHDOG=REPLACEMENT_PENDING');
    process.exit(0);
  }
}

await request(`${api}/actions/workflows/${workflow}/dispatches`, token, 'POST', {
  ref: 'main', inputs: { mode: 'auto', session_minutes: '330' },
});
console.log('AUDACEFI_WATCHDOG=REPLACEMENT_QUEUED');
