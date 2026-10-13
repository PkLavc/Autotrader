(() => {
  "use strict";
  const API = "https://api.pklavc.com/autotrader/v1";
  const $ = id => document.getElementById(id);
  const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "USD" });
  const number = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 6 });
  const dateTime = value => value ? new Date(value).toLocaleString("pt-BR") : "—";

  async function request(path, options = {}) {
    const response = await fetch(API + path, {
      credentials: "include",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      cache: "no-store",
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(payload.error || `Falha HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  function setStatus(id, enabled, yes = "SIM", no = "NÃO") {
    const node = $(id);
    node.textContent = enabled ? yes : no;
    node.className = enabled ? "positive" : "negative";
  }

  function cell(row, value, className = "") {
    const td = document.createElement("td");
    td.textContent = value;
    if (className) td.className = className;
    row.append(td);
  }

  function emptyRow(body, columns, message) {
    const row = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = columns;
    td.className = "empty";
    td.textContent = message;
    row.append(td);
    body.append(row);
  }

  function renderPositions(positions) {
    const body = $("positionsBody");
    body.replaceChildren();
    if (!positions.length) return emptyRow(body, 5, "A conta está sem posições no momento.");
    for (const position of positions) {
      const row = document.createElement("tr");
      const pnl = Number(position.unrealized_pl || 0);
      cell(row, position.symbol);
      cell(row, number.format(Number(position.qty || 0)));
      cell(row, money.format(Number(position.current_price || 0)));
      cell(row, money.format(Number(position.market_value || 0)));
      cell(row, `${pnl >= 0 ? "+" : ""}${money.format(pnl)} (${(Number(position.unrealized_plpc || 0) * 100).toFixed(2)}%)`, pnl >= 0 ? "positive" : "negative");
      body.append(row);
    }
  }

  function renderOrders(orders) {
    const body = $("ordersBody");
    body.replaceChildren();
    if (!orders.length) return emptyRow(body, 6, "Nenhuma ordem recente.");
    for (const order of orders) {
      const row = document.createElement("tr");
      const buy = String(order.side).toLowerCase() === "buy";
      cell(row, order.symbol || "—");
      cell(row, buy ? "COMPRA" : "VENDA", buy ? "positive" : "negative");
      cell(row, String(order.status || "—").toUpperCase());
      cell(row, number.format(Number(order.filled_qty || order.qty || 0)));
      cell(row, order.filled_avg_price ? money.format(Number(order.filled_avg_price)) : "—");
      cell(row, dateTime(order.filled_at || order.submitted_at));
      body.append(row);
    }
  }

  function renderRuns(runs) {
    const root = $("runsList");
    root.replaceChildren();
    if (!runs.length) {
      const p = document.createElement("p");
      p.className = "muted";
      p.textContent = "Nenhuma execução do robô foi registrada para esta conta.";
      return root.append(p);
    }
    for (const run of runs) {
      const item = document.createElement("article");
      item.className = "run";
      const status = document.createElement("strong");
      status.textContent = run.status;
      status.className = run.status === "completed" ? "positive" : run.status === "failed" ? "negative" : "";
      const detail = document.createElement("span");
      detail.textContent = run.error || `${run.order_count || 0} ordem(ns) planejada(s)`;
      const time = document.createElement("time");
      time.textContent = dateTime(run.finished_at || run.started_at);
      item.append(status, detail, time);
      root.append(item);
    }
  }

  function render(payload) {
    const { account, snapshot, runs = [] } = payload;
    if (!snapshot) throw new Error("A conta ainda não possui uma sincronização concluída.");
    $("accountName").textContent = account.display_name;
    $("brokerName").textContent = account.broker.toUpperCase();
    $("brokerMode").textContent = account.broker_mode === "paper" ? "Simulação (paper)" : account.broker_mode;
    $("modeBadge").textContent = account.broker_mode.toUpperCase();
    $("updatedAt").textContent = dateTime(snapshot.updated_at);
    $("equityValue").textContent = money.format(Number(snapshot.equity || 0));
    const dayReturn = Number(snapshot.day_return || 0);
    $("dayReturn").textContent = `${dayReturn >= 0 ? "▲" : "▼"} ${Math.abs(dayReturn * 100).toFixed(2)}% no dia`;
    $("dayReturn").className = dayReturn >= 0 ? "positive" : "negative";
    $("cashValue").textContent = money.format(Number(snapshot.cash || 0));
    $("cashShare").textContent = `${snapshot.equity ? (Number(snapshot.cash) / Number(snapshot.equity) * 100).toFixed(1) : "0.0"}% do patrimônio`;
    $("buyingPower").textContent = money.format(Number(snapshot.buying_power || 0));
    $("positionCount").textContent = snapshot.positions.length;
    setStatus("accountActive", account.active);
    setStatus("executionEnabled", account.execution_enabled, "HABILITADO", "SOMENTE PLANO");
    setStatus("tradingBlocked", !snapshot.trading_blocked, "NÃO", "SIM");
    $("orderLimit").textContent = money.format(Number(account.limits.maxOrderNotional || 0));
    $("runLimit").textContent = money.format(Number(account.limits.maxTotalNotional || 0));
    renderPositions(snapshot.positions || []);
    renderOrders(snapshot.recent_orders || []);
    renderRuns(runs);
    $("loadingView").hidden = true;
    $("errorView").hidden = true;
    $("accountView").hidden = false;
    $("modeBadge").hidden = false;
    $("refreshButton").hidden = false;
    $("logoutButton").hidden = false;
  }

  function fail(error) {
    $("loadingView").hidden = true;
    $("accountView").hidden = true;
    $("errorMessage").textContent = error.message || "Peça um novo convite ao administrador do Autotrader.";
    $("errorView").hidden = false;
  }

  async function load() {
    try {
      const token = location.hash.length > 1 ? location.hash.slice(1) : "";
      if (token) {
        history.replaceState(null, "", location.pathname + location.search);
        await request("/session", { method: "POST", body: JSON.stringify({ token }) });
      }
      render(await request("/me"));
    } catch (error) {
      fail(error);
    }
  }

  $("refreshButton").addEventListener("click", async () => {
    $("refreshButton").disabled = true;
    try { render(await request("/me")); } catch (error) { fail(error); }
    finally { $("refreshButton").disabled = false; }
  });
  $("logoutButton").addEventListener("click", async () => {
    try { await request("/session", { method: "DELETE" }); } finally { location.reload(); }
  });
  load();
})();
