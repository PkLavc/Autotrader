import { orderPayload, parseCookies, planRebalance, validateLimits, validateSignal } from "./core.js";

const API_PREFIXES = ["/autotrader", "/Autotrader/api"];
const SESSION_COOKIE = "at_session";
const SESSION_DAYS = 30;

function base64Url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function fromBase64Url(value) {
  const normalized = value.replaceAll("-", "+").replaceAll("_", "/");
  const binary = atob(normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "="));
  return Uint8Array.from(binary, character => character.charCodeAt(0));
}

async function sha256(value) {
  return base64Url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value))));
}

function randomToken(bytes = 32) {
  return base64Url(crypto.getRandomValues(new Uint8Array(bytes)));
}

function nowIso() {
  return new Date().toISOString();
}

function addDays(days) {
  return new Date(Date.now() + days * 86400000).toISOString();
}

function json(payload, status = 200, headers = {}) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "private, no-store, max-age=0",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
      ...headers,
    },
  });
}

function errorResponse(error, status = 400) {
  const message = error instanceof Error ? error.message : String(error);
  return json({ error: message }, status);
}

async function readJson(request) {
  const length = Number(request.headers.get("content-length") || 0);
  if (length > 1_000_000) throw new Error("request body is too large");
  try {
    return await request.json();
  } catch {
    throw new Error("request body must be valid JSON");
  }
}

async function secureEqual(left, right) {
  if (!left || !right) return false;
  const [a, b] = await Promise.all([sha256(left), sha256(right)]);
  if (a.length !== b.length) return false;
  let difference = 0;
  for (let index = 0; index < a.length; index += 1) difference |= a.charCodeAt(index) ^ b.charCodeAt(index);
  return difference === 0;
}

function bearer(request) {
  const value = request.headers.get("authorization") || "";
  return value.startsWith("Bearer ") ? value.slice(7).trim() : "";
}

async function requireBearer(request, configured, label) {
  if (!configured) throw new HttpError(503, `${label} is not configured`);
  const supplied = bearer(request);
  const expected = String(configured).trim();
  if (!(await secureEqual(supplied, expected))) {
    console.warn("bearer_rejected", { label, supplied_length: supplied.length, expected_length: expected.length });
    throw new HttpError(401, "unauthorized");
  }
}

class HttpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function encryptionKey(env) {
  if (!env.ACCOUNT_ENCRYPTION_KEY) throw new Error("account encryption is not configured");
  const raw = fromBase64Url(String(env.ACCOUNT_ENCRYPTION_KEY).trim());
  if (raw.byteLength !== 32) throw new Error("ACCOUNT_ENCRYPTION_KEY must contain 32 bytes");
  return crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
}

async function encryptCredentials(env, accountId, credentials) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await encryptionKey(env);
  const plaintext = new TextEncoder().encode(JSON.stringify(credentials));
  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: new TextEncoder().encode(accountId) },
    key,
    plaintext,
  );
  return { ciphertext: base64Url(new Uint8Array(ciphertext)), iv: base64Url(iv) };
}

async function decryptCredentials(env, account) {
  const key = await encryptionKey(env);
  const plaintext = await crypto.subtle.decrypt(
    {
      name: "AES-GCM",
      iv: fromBase64Url(account.credentials_iv),
      additionalData: new TextEncoder().encode(account.id),
    },
    key,
    fromBase64Url(account.credentials_ciphertext),
  );
  return JSON.parse(new TextDecoder().decode(plaintext));
}

function credentialHeaders(account, credentials) {
  if (account.auth_type === "oauth") return { Authorization: `Bearer ${credentials.access_token}` };
  return {
    "APCA-API-KEY-ID": credentials.key_id,
    "APCA-API-SECRET-KEY": credentials.secret_key,
  };
}

async function alpaca(account, credentials, path, options = {}) {
  const base = account.broker_mode === "paper"
    ? "https://paper-api.alpaca.markets"
    : "https://api.alpaca.markets";
  const response = await fetch(base + path, {
    ...options,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "User-Agent": "PkLavc-Autotrader-Fleet/1",
      ...credentialHeaders(account, credentials),
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch { payload = null; }
  if (!response.ok) {
    const detail = String(payload?.message || payload?.code || `HTTP ${response.status}`).slice(0, 300);
    throw new HttpError(response.status === 404 ? 404 : 502, `Alpaca ${response.status}: ${detail}`);
  }
  return payload;
}

function limitsFromAccount(account) {
  return {
    maxOrderNotional: account.max_order_notional,
    maxTotalNotional: account.max_total_notional,
    rebalanceThreshold: account.rebalance_threshold,
    maxTargetWeight: account.max_target_weight,
    maxGrossTarget: account.max_gross_target,
  };
}

function brokerPositions(rows) {
  return rows.map(row => ({
    symbol: String(row.symbol).toUpperCase(),
    qty: Number(row.qty),
    marketValue: Number(row.market_value),
    currentPrice: Number(row.current_price),
  }));
}

function sanitizeOrder(order) {
  return {
    id: order.id,
    client_order_id: order.client_order_id,
    symbol: order.symbol,
    side: order.side,
    type: order.type || order.order_type,
    status: order.status,
    qty: order.qty,
    notional: order.notional,
    filled_qty: order.filled_qty,
    filled_avg_price: order.filled_avg_price,
    submitted_at: order.submitted_at,
    filled_at: order.filled_at,
  };
}

function snapshotPayload(account, positions, orders) {
  const equity = Number(account.equity || 0);
  const lastEquity = Number(account.last_equity || 0);
  return {
    status: account.status,
    currency: account.currency || "USD",
    equity,
    cash: Number(account.cash || 0),
    buying_power: Number(account.buying_power || 0),
    last_equity: lastEquity,
    day_change: equity - lastEquity,
    day_return: lastEquity ? equity / lastEquity - 1 : null,
    trading_blocked: Boolean(account.trading_blocked || account.account_blocked),
    positions: positions.map(position => ({
      symbol: position.symbol,
      qty: Number(position.qty),
      market_value: Number(position.market_value),
      avg_entry_price: Number(position.avg_entry_price),
      current_price: Number(position.current_price),
      unrealized_pl: Number(position.unrealized_pl),
      unrealized_plpc: Number(position.unrealized_plpc),
    })),
    recent_orders: orders.slice(0, 100).map(sanitizeOrder),
  };
}

async function syncSnapshot(env, account, credentials) {
  const [brokerAccount, positions, orders] = await Promise.all([
    alpaca(account, credentials, "/v2/account"),
    alpaca(account, credentials, "/v2/positions"),
    alpaca(account, credentials, "/v2/orders?status=all&limit=100&direction=desc"),
  ]);
  const snapshot = snapshotPayload(brokerAccount, positions, orders);
  const updatedAt = nowIso();
  await env.DB.prepare(`
    INSERT INTO account_snapshots (account_id, snapshot_json, updated_at) VALUES (?, ?, ?)
    ON CONFLICT(account_id) DO UPDATE SET snapshot_json = excluded.snapshot_json, updated_at = excluded.updated_at
  `).bind(account.id, JSON.stringify(snapshot), updatedAt).run();
  return { ...snapshot, updated_at: updatedAt };
}

function accountView(account) {
  return {
    id: account.id,
    display_name: account.display_name,
    broker: account.broker,
    broker_mode: account.broker_mode,
    auth_type: account.auth_type,
    active: Boolean(account.active),
    execution_enabled: Boolean(account.execution_enabled),
    limits: limitsFromAccount(account),
    created_at: account.created_at,
    updated_at: account.updated_at,
  };
}

async function createAccount(request, env) {
  const body = await readJson(request);
  const displayName = String(body.display_name || "").trim();
  const authType = String(body.auth_type || "api_key");
  const mode = String(body.broker_mode || "paper");
  if (!displayName || displayName.length > 100) throw new HttpError(400, "display_name is required");
  if (mode !== "paper") throw new HttpError(400, "fleet v1 accepts paper accounts only");
  if (!['api_key', 'oauth'].includes(authType)) throw new HttpError(400, "unsupported auth_type");
  const credentials = body.credentials || {};
  if (authType === "api_key" && (!credentials.key_id || !credentials.secret_key)) {
    throw new HttpError(400, "key_id and secret_key are required");
  }
  if (authType === "oauth" && !credentials.access_token) throw new HttpError(400, "access_token is required");

  const limits = validateLimits({
    maxOrderNotional: body.limits?.max_order_notional || 100,
    maxTotalNotional: body.limits?.max_total_notional || 500,
    rebalanceThreshold: body.limits?.rebalance_threshold || 0.03,
    maxTargetWeight: body.limits?.max_target_weight || 0.18,
    maxGrossTarget: body.limits?.max_gross_target || 0.90,
  });

  const id = crypto.randomUUID();
  const provisional = { id, broker_mode: mode, auth_type: authType };
  const brokerAccount = await alpaca(provisional, credentials, "/v2/account");
  if (String(brokerAccount.status || "").toUpperCase() !== "ACTIVE") {
    throw new HttpError(400, `broker account is not active: ${brokerAccount.status || "unknown"}`);
  }
  const encrypted = await encryptCredentials(env, id, credentials);
  const createdAt = nowIso();
  await env.DB.prepare(`
    INSERT INTO accounts (
      id, display_name, broker, broker_mode, auth_type, credentials_ciphertext, credentials_iv,
      active, execution_enabled, max_order_notional, max_total_notional, rebalance_threshold,
      max_target_weight, max_gross_target, created_at, updated_at
    ) VALUES (?, ?, 'alpaca', ?, ?, ?, ?, 1, 0, ?, ?, ?, ?, ?, ?, ?)
  `).bind(
    id, displayName, mode, authType, encrypted.ciphertext, encrypted.iv,
    limits.maxOrderNotional,
    limits.maxTotalNotional,
    limits.rebalanceThreshold,
    limits.maxTargetWeight,
    limits.maxGrossTarget,
    createdAt, createdAt,
  ).run();
  const account = await env.DB.prepare("SELECT * FROM accounts WHERE id = ?").bind(id).first();
  await syncSnapshot(env, account, credentials);
  return json({ account: accountView(account) }, 201);
}

async function listAccounts(env) {
  const rows = await env.DB.prepare(`
    SELECT a.*, s.updated_at AS snapshot_updated_at
    FROM accounts a LEFT JOIN account_snapshots s ON s.account_id = a.id
    ORDER BY a.created_at
  `).all();
  return json({ accounts: rows.results.map(row => ({ ...accountView(row), snapshot_updated_at: row.snapshot_updated_at })) });
}

async function patchAccount(request, env, id) {
  const body = await readJson(request);
  const account = await env.DB.prepare("SELECT * FROM accounts WHERE id = ?").bind(id).first();
  if (!account) throw new HttpError(404, "account not found");
  const updates = [];
  const values = [];
  const fields = {
    display_name: value => String(value).trim().slice(0, 100),
    active: value => value ? 1 : 0,
    execution_enabled: value => value ? 1 : 0,
    max_order_notional: Number,
    max_total_notional: Number,
    rebalance_threshold: Number,
    max_target_weight: Number,
    max_gross_target: Number,
  };
  for (const [field, convert] of Object.entries(fields)) {
    const source = field in body ? body : (body.limits || {});
    if (field in source) {
      updates.push(`${field} = ?`);
      values.push(convert(source[field]));
    }
  }
  if (body.credentials) {
    const authType = String(body.auth_type || account.auth_type);
    const credentials = body.credentials;
    await alpaca({ ...account, auth_type: authType }, credentials, "/v2/account");
    const encrypted = await encryptCredentials(env, id, credentials);
    updates.push("auth_type = ?", "credentials_ciphertext = ?", "credentials_iv = ?");
    values.push(authType, encrypted.ciphertext, encrypted.iv);
  }
  if (!updates.length) throw new HttpError(400, "no supported account fields supplied");
  if ("display_name" in body && !String(body.display_name).trim()) throw new HttpError(400, "display_name cannot be empty");
  const effective = field => field in body
    ? body[field]
    : field in (body.limits || {}) ? body.limits[field] : account[field];
  validateLimits({
    maxOrderNotional: effective("max_order_notional"),
    maxTotalNotional: effective("max_total_notional"),
    rebalanceThreshold: effective("rebalance_threshold"),
    maxTargetWeight: effective("max_target_weight"),
    maxGrossTarget: effective("max_gross_target"),
  });
  updates.push("updated_at = ?");
  values.push(nowIso(), id);
  await env.DB.prepare(`UPDATE accounts SET ${updates.join(", ")} WHERE id = ?`).bind(...values).run();
  const updated = await env.DB.prepare("SELECT * FROM accounts WHERE id = ?").bind(id).first();
  return json({ account: accountView(updated) });
}

async function createInvite(request, env, accountId) {
  const account = await env.DB.prepare("SELECT * FROM accounts WHERE id = ? AND active = 1").bind(accountId).first();
  if (!account) throw new HttpError(404, "active account not found");
  let body = {};
  if (request.headers.get("content-length") !== "0") {
    try { body = await request.json(); } catch { body = {}; }
  }
  const validMinutes = Math.max(5, Math.min(1440, Number(body.valid_minutes || 60)));
  const token = randomToken();
  const expiresAt = new Date(Date.now() + validMinutes * 60000).toISOString();
  await env.DB.prepare(`
    INSERT INTO access_invites (id, account_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?)
  `).bind(crypto.randomUUID(), accountId, await sha256(token), expiresAt, nowIso()).run();
  return json({
    invite_url: `${String(env.PUBLIC_ACCOUNT_URL).replace(/\/$/, "")}/#${token}`,
    expires_at: expiresAt,
    one_time: true,
  }, 201);
}

async function exchangeInvite(request, env) {
  const body = await readJson(request);
  const token = String(body.token || "");
  if (token.length < 32) throw new HttpError(400, "invalid invitation");
  const tokenHash = await sha256(token);
  const invite = await env.DB.prepare(`
    SELECT i.id, i.account_id FROM access_invites i
    JOIN accounts a ON a.id = i.account_id
    WHERE i.token_hash = ? AND i.used_at IS NULL AND i.revoked_at IS NULL
      AND i.expires_at > ? AND a.active = 1
  `).bind(tokenHash, nowIso()).first();
  if (!invite) throw new HttpError(401, "invitation is invalid, expired, or already used");
  const consumed = await env.DB.prepare(`
    UPDATE access_invites SET used_at = ? WHERE id = ? AND used_at IS NULL AND revoked_at IS NULL
  `).bind(nowIso(), invite.id).run();
  if (Number(consumed.meta?.changes || 0) !== 1) throw new HttpError(409, "invitation was already used");

  const sessionToken = randomToken();
  await env.DB.prepare(`
    INSERT INTO sessions (token_hash, account_id, expires_at, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?)
  `).bind(await sha256(sessionToken), invite.account_id, addDays(SESSION_DAYS), nowIso(), nowIso()).run();
  const cookiePath = env.SESSION_COOKIE_PATH || "/";
  const cookie = `${SESSION_COOKIE}=${sessionToken}; Path=${cookiePath}; Max-Age=${SESSION_DAYS * 86400}; HttpOnly; Secure; SameSite=Strict`;
  return json({ ok: true }, 201, { "Set-Cookie": cookie });
}

async function currentSession(request, env) {
  const token = parseCookies(request.headers.get("cookie"))[SESSION_COOKIE];
  if (!token) throw new HttpError(401, "session required");
  const tokenHash = await sha256(token);
  const session = await env.DB.prepare(`
    SELECT s.token_hash, s.account_id, a.* FROM sessions s
    JOIN accounts a ON a.id = s.account_id
    WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > ? AND a.active = 1
  `).bind(tokenHash, nowIso()).first();
  if (!session) throw new HttpError(401, "session expired or revoked");
  return session;
}

async function me(request, env) {
  const session = await currentSession(request, env);
  const [snapshot, runs] = await Promise.all([
    env.DB.prepare("SELECT snapshot_json, updated_at FROM account_snapshots WHERE account_id = ?").bind(session.account_id).first(),
    env.DB.prepare(`
      SELECT status, order_count, result_json, error_message, started_at, finished_at
      FROM account_runs WHERE account_id = ? ORDER BY COALESCE(finished_at, started_at) DESC LIMIT 20
    `).bind(session.account_id).all(),
  ]);
  await env.DB.prepare("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?").bind(nowIso(), session.token_hash).run();
  return json({
    account: accountView(session),
    snapshot: snapshot ? { ...JSON.parse(snapshot.snapshot_json), updated_at: snapshot.updated_at } : null,
    runs: runs.results.map(run => ({
      status: run.status,
      order_count: run.order_count,
      result: run.result_json ? JSON.parse(run.result_json) : null,
      error: run.error_message,
      started_at: run.started_at,
      finished_at: run.finished_at,
    })),
  });
}

async function logout(request, env) {
  const token = parseCookies(request.headers.get("cookie"))[SESSION_COOKIE];
  if (token) await env.DB.prepare("UPDATE sessions SET revoked_at = ? WHERE token_hash = ?").bind(nowIso(), await sha256(token)).run();
  const cookiePath = env.SESSION_COOKIE_PATH || "/";
  return json({ ok: true }, 200, {
    "Set-Cookie": `${SESSION_COOKIE}=; Path=${cookiePath}; Max-Age=0; HttpOnly; Secure; SameSite=Strict`,
  });
}

async function syncAccount(env, id) {
  const account = await env.DB.prepare("SELECT * FROM accounts WHERE id = ? AND active = 1").bind(id).first();
  if (!account) throw new HttpError(404, "active account not found");
  const credentials = await decryptCredentials(env, account);
  return json({ snapshot: await syncSnapshot(env, account, credentials) });
}

async function revokeAccountAccess(env, id) {
  const account = await env.DB.prepare("SELECT id FROM accounts WHERE id = ?").bind(id).first();
  if (!account) throw new HttpError(404, "account not found");
  const revokedAt = nowIso();
  const results = await env.DB.batch([
    env.DB.prepare("UPDATE sessions SET revoked_at = ? WHERE account_id = ? AND revoked_at IS NULL").bind(revokedAt, id),
    env.DB.prepare("UPDATE access_invites SET revoked_at = ? WHERE account_id = ? AND revoked_at IS NULL AND used_at IS NULL").bind(revokedAt, id),
  ]);
  return json({
    ok: true,
    revoked_sessions: Number(results[0].meta?.changes || 0),
    revoked_invitations: Number(results[1].meta?.changes || 0),
  });
}

async function enqueueRebalance(request, env) {
  const body = await readJson(request);
  const signal = body.signal || body;
  const execute = Boolean(body.execute);
  const { signalDate, targets } = validateSignal(signal, Number(env.MAX_SIGNAL_AGE_DAYS || 7));
  const accounts = await env.DB.prepare("SELECT id FROM accounts WHERE active = 1 ORDER BY created_at").all();
  if (!accounts.results.length) throw new HttpError(409, "no active fleet accounts");
  const signalHash = await sha256(JSON.stringify({ signalDate, targets }));
  const runId = crypto.randomUUID();
  const createdAt = nowIso();
  const statements = [env.DB.prepare(`
    INSERT INTO fleet_runs (id, signal_date, signal_hash, execute_requested, account_count, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
  `).bind(runId, signalDate, signalHash, execute ? 1 : 0, accounts.results.length, createdAt)];
  for (const account of accounts.results) {
    statements.push(env.DB.prepare(`
      INSERT INTO account_runs (id, fleet_run_id, account_id, status) VALUES (?, ?, ?, 'queued')
    `).bind(crypto.randomUUID(), runId, account.id));
  }
  await env.DB.batch(statements);
  const messages = accounts.results.map(account => ({
    body: { runId, accountId: account.id, signalDate, targets, execute },
    contentType: "json",
  }));
  for (let index = 0; index < messages.length; index += 100) {
    await env.REBALANCE_QUEUE.sendBatch(messages.slice(index, index + 100));
  }
  return json({ run_id: runId, signal_date: signalDate, queued_accounts: messages.length, execute_requested: execute }, 202);
}

async function executeAccountJob(env, job) {
  const account = await env.DB.prepare("SELECT * FROM accounts WHERE id = ? AND active = 1").bind(job.accountId).first();
  if (!account) throw new Error("account is inactive or missing");
  await env.DB.prepare(`
    UPDATE account_runs SET status = 'processing', started_at = ? WHERE fleet_run_id = ? AND account_id = ?
  `).bind(nowIso(), job.runId, job.accountId).run();
  const credentials = await decryptCredentials(env, account);
  const [brokerAccount, positions, clock] = await Promise.all([
    alpaca(account, credentials, "/v2/account"),
    alpaca(account, credentials, "/v2/positions"),
    alpaca(account, credentials, "/v2/clock"),
  ]);
  if (String(brokerAccount.status || "").toUpperCase() !== "ACTIVE") throw new Error("broker account is not active");
  if (brokerAccount.trading_blocked || brokerAccount.account_blocked) throw new Error("broker account is blocked");

  const orders = planRebalance({
    equity: Number(brokerAccount.equity),
    cash: Math.max(0, Number(brokerAccount.cash)),
    positions: brokerPositions(positions),
    targets: job.targets,
    signalDate: job.signalDate,
    limits: limitsFromAccount(account),
  });
  const execute = Boolean(job.execute)
    && Boolean(account.execution_enabled)
    && String(env.PAPER_ORDER_SUBMISSION_ENABLED).toLowerCase() === "true";
  if (execute && !clock.is_open) throw new Error("broker market is closed; orders were not submitted");

  const results = [];
  for (const order of orders) {
    let existing = null;
    try {
      existing = await alpaca(account, credentials, `/v2/orders:by_client_order_id?client_order_id=${encodeURIComponent(order.clientOrderId)}`);
    } catch (error) {
      if (!(error instanceof HttpError) || error.status !== 404) throw error;
    }
    if (existing) {
      results.push({ symbol: order.symbol, side: order.side, status: "duplicate_prevented", broker_status: existing.status });
    } else if (execute) {
      const submitted = await alpaca(account, credentials, "/v2/orders", {
        method: "POST",
        body: JSON.stringify(orderPayload(order)),
      });
      results.push({
        symbol: order.symbol, side: order.side, estimated_notional: order.estimatedNotional,
        status: "submitted", broker_status: submitted.status, broker_order_id: submitted.id,
      });
    } else {
      results.push({
        symbol: order.symbol, side: order.side, estimated_notional: order.estimatedNotional,
        status: "dry_run", payload: orderPayload(order),
      });
    }
  }
  await syncSnapshot(env, account, credentials);
  const result = { execute_effective: execute, market_open: Boolean(clock.is_open), orders: results };
  await env.DB.prepare(`
    UPDATE account_runs SET status = 'completed', order_count = ?, result_json = ?, finished_at = ?
    WHERE fleet_run_id = ? AND account_id = ?
  `).bind(results.length, JSON.stringify(result), nowIso(), job.runId, job.accountId).run();
}

async function handleQueue(batch, env) {
  await Promise.all(batch.messages.map(async message => {
    const job = message.body;
    try {
      await executeAccountJob(env, job);
      message.ack();
    } catch (error) {
      const detail = (error instanceof Error ? error.message : String(error)).slice(0, 500);
      await env.DB.prepare(`
        UPDATE account_runs SET status = 'failed', error_message = ?, finished_at = ?
        WHERE fleet_run_id = ? AND account_id = ?
      `).bind(detail, nowIso(), job.runId, job.accountId).run();
      message.retry({ delaySeconds: Math.min(300, 15 * (message.attempts || 1)) });
    }
  }));
}

function corsHeaders(request, env) {
  const origin = request.headers.get("origin");
  if (!origin || origin !== env.PUBLIC_ORIGIN) return {};
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
    Vary: "Origin",
  };
}

function withCors(response, request, env) {
  const headers = new Headers(response.headers);
  for (const [key, value] of Object.entries(corsHeaders(request, env))) headers.set(key, value);
  return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
}

async function handleRequest(request, env) {
  const url = new URL(request.url);
  const prefix = API_PREFIXES.find(candidate => url.pathname === candidate || url.pathname.startsWith(`${candidate}/`));
  if (!prefix) throw new HttpError(404, "not found");
  const path = url.pathname.slice(prefix.length) || "/";
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders(request, env) });

  if (path === "/v1/health" && request.method === "GET") return json({ ok: true, environment: env.ENVIRONMENT });
  if (path === "/v1/session" && request.method === "POST") return exchangeInvite(request, env);
  if (path === "/v1/session" && request.method === "DELETE") return logout(request, env);
  if (path === "/v1/me" && request.method === "GET") return me(request, env);

  if (path.startsWith("/v1/admin/")) await requireBearer(request, env.FLEET_ADMIN_TOKEN, "fleet admin token");
  if (path === "/v1/admin/accounts" && request.method === "GET") return listAccounts(env);
  if (path === "/v1/admin/accounts" && request.method === "POST") return createAccount(request, env);
  let match = path.match(/^\/v1\/admin\/accounts\/([^/]+)$/);
  if (match && request.method === "PATCH") return patchAccount(request, env, match[1]);
  match = path.match(/^\/v1\/admin\/accounts\/([^/]+)\/invite$/);
  if (match && request.method === "POST") return createInvite(request, env, match[1]);
  match = path.match(/^\/v1\/admin\/accounts\/([^/]+)\/sync$/);
  if (match && request.method === "POST") return syncAccount(env, match[1]);
  match = path.match(/^\/v1\/admin\/accounts\/([^/]+)\/revoke-access$/);
  if (match && request.method === "POST") return revokeAccountAccess(env, match[1]);

  if (path === "/v1/internal/rebalance" && request.method === "POST") {
    await requireBearer(request, env.FLEET_SERVICE_TOKEN, "fleet service token");
    return enqueueRebalance(request, env);
  }
  throw new HttpError(404, "not found");
}

export default {
  async fetch(request, env) {
    try {
      return withCors(await handleRequest(request, env), request, env);
    } catch (error) {
      console.error("request_failed", { path: new URL(request.url).pathname, message: error?.message });
      return withCors(errorResponse(error, error instanceof HttpError ? error.status : 500), request, env);
    }
  },
  queue: handleQueue,
};
