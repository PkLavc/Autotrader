// Narrow direct API client. Uses the official kit's response parsers and
// preserves its one-transmission, ambiguous-write and no-raw-error rules.
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';

const kit = resolve(import.meta.dirname, '../../vendor/audacefi-integration-kit/dist');
const { ClientError, parseQuote, parseSubmission, parseReadState } =
  await import(pathToFileURL(resolve(kit, 'index.js')).href);
const { uuid, mint, rawAmount } = await import(pathToFileURL(resolve(kit, 'client.js')).href);

const ORIGIN = 'https://sandbox.audacefi.app';
const PROTOCOL = 'audacefi.bench.submission.v1';
const MAX_BYTES = 262144;

export function createDirectClient(key, expectedAccountId) {
  if (typeof key !== 'string' || !/^afk_[A-Za-z0-9_.-]{4,508}$/.test(key)) throw Error('DIRECT_API_KEY_INVALID');
  const accountId = uuid(expectedAccountId);
  async function request(method, route, body, beforeSend) {
    const write = method === 'POST';
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12000);
    let sent = false;
    try {
      if (write) {
        try { await beforeSend?.(); }
        catch { throw new ClientError('GUARD_REFUSED_BEFORE_SEND', null, false); }
      }
      if (controller.signal.aborted) throw new ClientError('REQUEST_TIMEOUT', null, false);
      sent = true;
      const url = ORIGIN + '/api' + route;
      const response = await fetch(url, {
        method, headers: { Authorization: `Bearer ${key}`, Accept: 'application/json',
          ...(body ? { 'Content-Type': 'application/json' } : {}) },
        ...(body ? { body: JSON.stringify(body) } : {}),
        redirect: 'error', cache: 'no-store', credentials: 'omit',
        referrerPolicy: 'no-referrer', signal: controller.signal,
      });
      if (response.redirected || response.url !== url) throw new ClientError('REDIRECT_REFUSED', response.status, write);
      if (!response.ok) {
        void response.body?.cancel().catch(() => {});
        throw new ClientError(`HTTP_${response.status}`, response.status, write);
      }
      if (!/^application\/(?:json|[a-z0-9.+-]+\+json)(?:;|$)/i.test(response.headers.get('content-type') ?? ''))
        throw new ClientError('JSON_RESPONSE_REQUIRED', response.status, write);
      const reader = response.body?.getReader();
      if (!reader) throw new ClientError('EMPTY_RESPONSE', response.status, write);
      const chunks = []; let length = 0;
      for (;;) {
        const part = await reader.read();
        if (part.done) break;
        length += part.value.length;
        if (length > MAX_BYTES) throw new ClientError('RESPONSE_TOO_LARGE', response.status, write);
        chunks.push(part.value);
      }
      const raw = Buffer.concat(chunks);
      try { return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw)); }
      catch { throw new ClientError('INVALID_JSON_RESPONSE', response.status, write); }
    } catch (error) {
      if (error instanceof ClientError) {
        if (write && sent && !error.ambiguous)
          throw new ClientError(error.code, error.status, true);
        throw error;
      }
      throw new ClientError(controller.signal.aborted ? 'REQUEST_TIMEOUT' : 'TRANSPORT_FAILURE', null, write && sent);
    } finally { clearTimeout(timeout); controller.abort(); }
  }
  async function assertExecuteScope() {
    const me = await request('GET', '/v1/me');
    const account = me?.account;
    if (account?.id !== accountId || account?.participantType !== 'ai_agent' ||
        account?.status !== 'active' || me?.onboardingComplete !== true)
      throw new ClientError('ACCOUNT_NOT_AUTHORIZED', null, false);
    // This live API omits credentialScopes from /me. The server enforces the
    // execute scope on each POST; no local substitute is invented.
    if (Array.isArray(me.credentialScopes) && !me.credentialScopes.includes('execute'))
      throw new ClientError('EXECUTE_SCOPE_REQUIRED', null, false);
  }
  return {
    origin: ORIGIN, apiBasePath: '/api', writesAuthorized: true,
    officialProxyIdentity: Object.freeze({ accountId, actor: 'ai_agent' }),
    assertExecuteScope,
    async readState(category, mode = 'real') {
      if (category !== 'crypto-bro' || mode !== 'real') throw Error('STATE_CONTEXT_INVALID');
      const state = parseReadState(await request('GET', '/v1/me/state?category=crypto-bro&mode=real'));
      if (state.category !== category || state.mode !== mode) throw Error('STATE_CONTEXT_MISMATCH');
      return state;
    },
    async readAllowlist(category, seasonId) {
      if (category !== 'crypto-bro') throw Error('ALLOWLIST_CATEGORY_INVALID');
      uuid(seasonId);
      const raw = await request('GET', '/v1/me/allowlist?category=crypto_bro&mode=real');
      if (raw?.seasonId !== seasonId || raw?.category !== 'crypto_bro' ||
          raw?.allowlistFrozen !== true || !Array.isArray(raw.tokens) || raw.tokens.length > 2048)
        throw Error('ALLOWLIST_CONTEXT_MISMATCH');
      const tokens = raw.tokens.map(item => ({
        mint: mint(item.mint),
        decimals: item.decimals === null ? null : item.decimals,
        exempt: item.exempt === true, eligible: item.eligible === true,
        ...(typeof item.symbol === 'string' ? { symbol: item.symbol } : {}),
      }));
      if (new Set(tokens.map(item => item.mint)).size !== tokens.length) throw Error('ALLOWLIST_DUPLICATE_MINT');
      return { seasonId, category: raw.category, allowlistFrozen: true, tokens };
    },
    async createIntent(input, beforeSend, requireExactBinding = false) {
      uuid(input.seasonId); mint(input.tokenInMint); mint(input.tokenOutMint);
      if (input.category !== 'crypto-bro' || !Number.isFinite(input.amountIn) ||
          input.amountIn <= 0 || !/^[A-Za-z0-9_-]{8,128}$/.test(input.idempotencyKey))
        throw Error('INTENT_INVALID');
      const raw = await request('POST', '/v1/swap-intents', {
        seasonId: input.seasonId, category: 'crypto_bro', tokenInMint: input.tokenInMint,
        tokenOutMint: input.tokenOutMint, amountIn: input.amountIn,
        idempotencyKey: input.idempotencyKey, surface: 'api',
        ...(requireExactBinding ? { inputBinding: 'audacefi.swap-intent-input.v1' } : {}),
      }, beforeSend);
      if (requireExactBinding && raw.inputBinding !== 'audacefi.swap-intent-input.v1')
        throw new ClientError('INTENT_INPUT_BINDING_UNAVAILABLE', null, true);
      if (raw.status !== 'built' || typeof raw.replayed !== 'boolean')
        throw new ClientError('INTENT_RESPONSE_INVALID', null, true);
      return { intentId: uuid(raw.intentId), status: 'built', replayed: raw.replayed };
    },
    async quote(intentId, amountInRaw, beforeSend) {
      const raw = await request('POST', `/v1/swap-intents/${uuid(intentId)}/quote`,
        { amountInRaw: rawAmount(amountInRaw) }, beforeSend);
      const quote = parseQuote(raw);
      if (quote.intentId !== intentId || quote.amountInRaw !== amountInRaw)
        throw new ClientError('QUOTE_CONTEXT_MISMATCH', null, true);
      return quote;
    },
    async recordSignature(intentId, signature, beforeSend) {
      const raw = await request('POST', `/v1/swap-intents/${uuid(intentId)}/signature`,
        { signature }, beforeSend);
      if (raw.intentId !== intentId || raw.status !== 'signed')
        throw new ClientError('SIGNATURE_CONTEXT_MISMATCH', null, true);
    },
    async submitDurable(intentId, input, beforeSend) {
      const raw = await request('POST', `/v1/swap-intents/${uuid(intentId)}/submit-durable`,
        { quoteId: uuid(input.quoteId), ...(input.quoteOutId ? { quoteOutId: uuid(input.quoteOutId) } : {}),
          transaction: input.transaction }, beforeSend);
      const result = parseSubmission(raw);
      if (result.intentId !== intentId || result.quoteId !== input.quoteId ||
          result.quoteOutId !== (input.quoteOutId ?? null))
        throw new ClientError('SUBMISSION_CONTEXT_MISMATCH', null, true);
      return result;
    },
    async readSubmissionStatus(intentId) {
      const raw = await request('GET', `/v1/swap-intents/${uuid(intentId)}/submission`);
      if (raw.protocol !== PROTOCOL) throw Error('SUBMISSION_PROTOCOL_INVALID');
      const submission = raw.submission === null ? null : parseSubmission(raw.submission);
      if (submission && submission.intentId !== intentId) throw Error('SUBMISSION_CONTEXT_MISMATCH');
      return { submission, resultStatus: Object.hasOwn(raw, 'resultStatus') ? raw.resultStatus : null,
        resultStatusKnown: Object.hasOwn(raw, 'resultStatus') };
    },
    async readSubmission(intentId) { return (await this.readSubmissionStatus(intentId)).submission; },
    async recordConfirmedResult(intentId, status, beforeSend) {
      if (status !== 'confirmed' && status !== 'failed') throw Error('RESULT_STATUS_INVALID');
      const raw = await request('POST', `/v1/swap-intents/${uuid(intentId)}/result`, { status }, beforeSend);
      if (raw.intentId !== intentId || raw.status !== status) throw new ClientError('RESULT_CONTEXT_MISMATCH', null, true);
    },
    async readRankedEvidence(intentId) {
      return request('GET', `/v1/swap-intents/${uuid(intentId)}/ranked-evidence`);
    },
  };
}
