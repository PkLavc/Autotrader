// Conservative AudaceFi adaptive worker handoff: only replace an old worker
// after checking the encrypted journal and official wallet are completely flat,
// with the active 15-minute decision window already evaluated by that worker.
// This does not hold or sign any wallet key and does not submit an order.
import { createDecipheriv } from 'node:crypto';
import { competitiveJournalIsQuiescent } from './handoff-journal.mjs';

const repo = process.env.GITHUB_REPOSITORY;
const token = process.env.GITHUB_TOKEN;
const key = process.env.AUDACEFI_API_KEY;
const walletAddress = process.env.AUDACEFI_WALLET_ADDRESS;
const journalKey = process.env.AUDACEFI_JOURNAL_KEY;
const branch = 'audacefi-competitive-journal';
const file = 'journal.json.enc';
if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo ?? '') ||
    !token || !key || !walletAddress || !journalKey) throw Error('HANDOFF_CONFIG_MISSING');

const repoApi = 'https://api.github.com/repos/' + repo;
async function call(url, bearer, method = 'GET', body) {
  const response = await fetch(url, {
    method, headers: {
      Authorization: 'Bearer ' + bearer, Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      ...(body ? {'Content-Type':'application/json'} : {}),
    }, ...(body ? {body:JSON.stringify(body)} : {}),
    redirect:'error', cache:'no-store', signal:AbortSignal.timeout(15000),
  });
  if (!response.ok && response.status !== 202 && response.status !== 204)
    throw Error('HANDOFF_REQUEST_HTTP_' + response.status);
  return response.status === 202 || response.status === 204 ? null : response.json();
}
async function audace(path) {
  const response = await fetch('https://sandbox.audacefi.app/api' + path, {
    headers:{Authorization:'Bearer '+key,Accept:'application/json'},
    redirect:'error', cache:'no-store', signal:AbortSignal.timeout(12000),
  });
  if (!response.ok) throw Error('HANDOFF_AUDACE_HTTP_' + response.status);
  return response.json();
}

async function journalSnapshot() {
  const info = await call(repoApi + '/contents/' + file + '?ref=' + branch, token);
  if (info?.type !== 'file' || !/^[0-9a-f]{40}$/.test(info.sha ?? ''))
    throw Error('HANDOFF_JOURNAL_NOT_FOUND');
  const blob = await call(repoApi + '/git/blobs/' + info.sha, token);
  if (blob?.encoding !== 'base64' || blob?.sha !== info.sha || typeof blob.content !== 'string')
    throw Error('HANDOFF_JOURNAL_BLOB_INVALID');
  const bytes = Buffer.from(blob.content.replace(/\s/g,''), 'base64');
  const secret = Buffer.from(journalKey.trim(),'base64');
  if (secret.length !== 32 || secret.toString('base64') !== journalKey.trim() || bytes.length < 29)
    throw Error('HANDOFF_JOURNAL_FORMAT_INVALID');
  const decipher = createDecipheriv('aes-256-gcm',secret,bytes.subarray(0,12));
  decipher.setAAD(Buffer.from('audacefi-journal-v1:' + repo + ':' + branch + ':' + file));
  decipher.setAuthTag(bytes.subarray(12,28));
  const plaintext = Buffer.concat([decipher.update(bytes.subarray(28)),decipher.final()]);
  const value = JSON.parse(plaintext.toString('utf8'));
  plaintext.fill(0);
  if (value.version !== 1 || !value.files || typeof value.files !== 'object' ||
      Array.isArray(value.files)) throw Error('HANDOFF_JOURNAL_VERSION_INVALID');
  if (!competitiveJournalIsQuiescent(value.files)) return {flat:false};
  const encoded = value.files['risk-state.json'];
  if (typeof encoded !== 'string') return {flat:false};
  const risk = JSON.parse(Buffer.from(encoded,'base64').toString('utf8'));
  return {flat:true, signalWindow:risk?.signalWindow};
}
async function seasonFlat() {
  const state = await audace('/v1/me/state?category=crypto-bro&mode=real');
  const season = state?.season?.active;
  if (season?.status !== 'active' || season?.executionMode !== 'devnet_onchain' ||
      state?.wallet?.walletAddress !== walletAddress || state?.wallet?.state !== 'ok')
    return false;
  const balance = await audace('/v1/me/wallet?category=crypto_bro');
  return balance?.seasonId === season.id && balance?.walletAddress === walletAddress &&
    Number.isInteger(balance?.swapCount) && balance.swapCount >= 2 &&
    Array.isArray(balance.holdings) &&
    balance.holdings.every(row => row.symbol === 'USDC' || Number(row.amount) === 0);
}

const now=Date.now(), signalWindow=Math.floor(now/900000);
const offset=now % 900000;
// Sufficient time before the next signal boundary to cancel the idle runner.
// The worker itself will HOLD while its journal signalWindow equals current.
if (offset < 120000 || offset > 540000) {
  console.log('AUDACEFI_HANDOFF=WAIT_SAFE_CANDLE_WINDOW');
  process.exit(0);
}
const [runList, latest] = await Promise.all([
  call(repoApi+'/actions/workflows/audacefi-direct-pilot.yml/runs?per_page=20',token),
  call(repoApi+'/commits/main',token),
]);
const head=latest?.sha;
if (!/^[0-9a-f]{40}$/.test(head ?? '') || !Array.isArray(runList?.workflow_runs))
  throw Error('HANDOFF_GITHUB_STATE_INVALID');
const current=runList.workflow_runs.find(row=>row.status==='in_progress' && row.head_branch==='main');
const queued=runList.workflow_runs.filter(row=>['pending','queued','waiting','requested'].includes(row.status) && row.head_branch==='main');
if (current?.head_sha===head) {console.log('AUDACEFI_HANDOFF=LATEST_ALREADY_RUNNING');process.exit(0);}
if (current) {
  const snapshot=await journalSnapshot();
  if (!snapshot.flat || snapshot.signalWindow!==signalWindow || !(await seasonFlat())) {
    console.log('AUDACEFI_HANDOFF=HOLD_POSITION_OR_UNCHECKED_WINDOW');
    process.exit(0);
  }
  // Observe again immediately before the cancellation to minimize race risk.
  if (!(await seasonFlat())) {console.log('AUDACEFI_HANDOFF=HOLD_WALLET_CHANGED');process.exit(0);}
  await call(repoApi+'/actions/runs/'+current.id+'/cancel',token,'POST');
  console.log('AUDACEFI_HANDOFF=OLD_IDLE_WORKER_CANCEL_REQUESTED');
}
for (const entry of queued) {
  if (entry.head_sha!==head) await call(repoApi+'/actions/runs/'+entry.id+'/cancel',token,'POST');
}
if (!queued.some(row=>row.head_sha===head)) {
  await call(repoApi+'/actions/workflows/audacefi-direct-pilot.yml/dispatches',
    token,'POST',{ref:'main', inputs:{mode:'auto',session_minutes:'330'}});
  console.log('AUDACEFI_HANDOFF=LATEST_DISPATCHED');
} else console.log('AUDACEFI_HANDOFF=LATEST_ALREADY_QUEUED');
