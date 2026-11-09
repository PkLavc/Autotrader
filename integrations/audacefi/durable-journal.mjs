// Encrypted GitHub branch checkpoint for the kit's append-only FileJournal.
// Every journal mutation is checkpointed before the next AudaceFi write.
import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto';
import { mkdir, readdir, readFile, writeFile, lstat } from 'node:fs/promises';
import { join } from 'node:path';

const BRANCH = 'audacefi-journal';
const FILE = 'journal.json.enc';
const VERSION = 1;

export async function openDurableJournal(directory, { repository, githubToken, encryptionKey, mainSha }) {
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository) ||
      !githubToken || !/^[0-9a-f]{40}$/.test(mainSha)) throw Error('JOURNAL_GITHUB_CONFIG_INVALID');
  const key = Buffer.from(encryptionKey?.trim() ?? '', 'base64');
  if (key.length !== 32 || key.toString('base64') !== encryptionKey?.trim()) throw Error('JOURNAL_KEY_INVALID');
  const aad = Buffer.from(`audacefi-journal-v1:${repository}:${BRANCH}:${FILE}`);
  const base = `https://api.github.com/repos/${repository}`;
  async function api(method, route, body) {
    const response = await fetch(base + route, {
      method,
      headers: {
        Authorization: `Bearer ${githubToken}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        ...(body ? { 'Content-Type': 'application/json' } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
      redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(15000),
    });
    if (response.status === 404) return { status: 404, data: null };
    if (!response.ok) throw Error(`JOURNAL_GITHUB_HTTP_${response.status}`);
    return { status: response.status, data: await response.json() };
  }
  const ref = await api('GET', `/git/ref/heads/${BRANCH}`);
  if (ref.status === 404) {
    await api('POST', '/git/refs', { ref: `refs/heads/${BRANCH}`, sha: mainSha });
  }
  const contentRoute = `/contents/${FILE}?ref=${BRANCH}`;
  const loaded = await api('GET', contentRoute);
  let sha = loaded.data?.sha;
  await mkdir(directory, { recursive: true, mode: 0o700 });
  if (loaded.status === 200) {
    if (loaded.data?.type !== 'file' || typeof loaded.data.content !== 'string') throw Error('JOURNAL_REMOTE_INVALID');
    const encrypted = Buffer.from(loaded.data.content.replace(/\s/g, ''), 'base64');
    if (encrypted.length < 29) throw Error('JOURNAL_CIPHERTEXT_INVALID');
    const nonce = encrypted.subarray(0, 12);
    const tag = encrypted.subarray(12, 28);
    const decipher = createDecipheriv('aes-256-gcm', key, nonce);
    decipher.setAAD(aad);
    decipher.setAuthTag(tag);
    const plain = Buffer.concat([decipher.update(encrypted.subarray(28)), decipher.final()]);
    const state = JSON.parse(plain.toString('utf8'));
    plain.fill(0);
    if (state.version !== VERSION || !state.files || typeof state.files !== 'object' || Array.isArray(state.files))
      throw Error('JOURNAL_STATE_INVALID');
    for (const [name, value] of Object.entries(state.files)) {
      if (!/^[A-Za-z0-9._-]{1,160}$/.test(name) || name === '.' || name === '..' ||
          typeof value !== 'string' || value.length > 1_000_000) throw Error('JOURNAL_FILE_INVALID');
      await writeFile(join(directory, name), Buffer.from(value, 'base64'), { flag: 'wx', mode: 0o600 });
    }
  }
  async function checkpoint() {
    const files = {};
    for (const name of await readdir(directory)) {
      if (!/^[A-Za-z0-9._-]{1,160}$/.test(name)) throw Error('JOURNAL_FILE_INVALID');
      const path = join(directory, name);
      const stat = await lstat(path);
      if (!stat.isFile() || stat.size > 750_000) throw Error('JOURNAL_FILE_INVALID');
      files[name] = (await readFile(path)).toString('base64');
    }
    const nonce = randomBytes(12);
    const cipher = createCipheriv('aes-256-gcm', key, nonce);
    cipher.setAAD(aad);
    const plain = Buffer.from(JSON.stringify({ version: VERSION, files }));
    // GCM tag becomes available only after final().
    const body = Buffer.concat([cipher.update(plain), cipher.final()]);
    const sealed = Buffer.concat([nonce, cipher.getAuthTag(), body]);
    plain.fill(0);
    const saved = await api('PUT', `/contents/${FILE}`, {
      message: 'checkpoint encrypted AudaceFi journal',
      content: sealed.toString('base64'),
      branch: BRANCH,
      ...(sha ? { sha } : {}),
    });
    if (typeof saved.data?.content?.sha !== 'string') throw Error('JOURNAL_CHECKPOINT_UNCONFIRMED');
    sha = saved.data.content.sha;
  }
  async function wrap(journal) {
    await checkpoint();
    for (const method of ['reserve', 'append']) {
      const original = journal[method].bind(journal);
      journal[method] = async (...args) => {
        const result = await original(...args);
        await checkpoint();
        return result;
      };
    }
    return { journal, checkpoint };
  }
  return { wrap, checkpoint, directory };
}
