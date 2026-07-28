"use strict";

let accessToken = null;
let csrfToken = null;
let oneTimeCode = null;
let pendingPreviewId = null;
let selectedUser = null;
let globalStopEnabled = false;
let usersCursor = null;
let championsCursor = null;
let tasksCursor = null;
let auditCursor = null;

const byId = (id) => document.getElementById(id);
const panelTitles = {
  "overview-panel": "运行总览",
  "users-panel": "用户与设备",
  "champions-panel": "冠军画像",
  "tasks-panel": "任务流水",
  "threshold-panel": "门槛策略",
  "authorization-panel": "授权管理",
  "audit-panel": "审计记录",
};

function showMessage(target, message, kind = "warning") {
  const element = typeof target === "string" ? byId(target) : target;
  if (!element) return;
  element.textContent = message;
  element.dataset.kind = kind;
  if (element.classList.contains("banner")) element.hidden = !message;
}

function setBusy(button, busy, busyText = "处理中…") {
  if (!button) return;
  if (busy) {
    button.dataset.originalText = button.textContent;
    button.textContent = busyText;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.originalText || button.textContent;
    button.disabled = false;
    delete button.dataset.originalText;
  }
}

async function readJson(response) {
  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.toLowerCase().includes("application/json")) {
    throw new Error("unexpected_response");
  }
  return response.json();
}

async function refreshSession() {
  if (!csrfToken) return false;
  const response = await fetch("/api/v1/admin/session/refresh", {
    method: "POST",
    headers: { "X-CSRF-Token": csrfToken },
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) return false;
  const body = await readJson(response);
  accessToken = body.access_token;
  csrfToken = body.csrf_token;
  return true;
}

async function api(path, options = {}, retry = true) {
  if (!accessToken) throw new Error("not_authenticated");
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${accessToken}`);
  const method = (options.method || "GET").toUpperCase();
  if (method !== "GET" && csrfToken) headers.set("X-CSRF-Token", csrfToken);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, {
    ...options,
    method,
    headers,
    credentials: "same-origin",
    cache: "no-store",
  });
  if (response.status === 401 && retry && (await refreshSession())) {
    return api(path, options, false);
  }
  if (!response.ok) {
    if (response.status === 401) resetSession();
    throw new Error(`request_failed_${response.status}`);
  }
  return readJson(response);
}

function resetSession() {
  accessToken = null;
  csrfToken = null;
  pendingPreviewId = null;
  selectedUser = null;
  oneTimeCode = null;
  byId("dashboard-shell").hidden = true;
  byId("login-panel").hidden = false;
  byId("login-password").value = "";
  byId("authorization-code-value").textContent = "";
}

function showDashboard() {
  byId("login-panel").hidden = true;
  byId("dashboard-shell").hidden = false;
}

function formatYuan(minor) {
  if (minor === null || minor === undefined) return "不可用";
  const numeric = Number(minor);
  if (!Number.isFinite(numeric)) return "不可用";
  return `${(numeric / 100).toFixed(2)} 元`;
}

function setPnl(element, minor) {
  element.classList.remove("profit", "loss");
  if (minor === null || minor === undefined) {
    element.textContent = "不可用";
    return;
  }
  const value = Number(minor);
  const label = value >= 0 ? "盈利" : "亏损";
  const sign = value >= 0 ? "+" : "";
  element.textContent = `${label} ${sign}${(value / 100).toFixed(2)} 元`;
  element.classList.add(value >= 0 ? "profit" : "loss");
}

function formatRate(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${(numeric * 100).toFixed(2)}%` : "—";
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : new Intl.DateTimeFormat("zh-CN", {
        dateStyle: "short",
        timeStyle: "medium",
        hour12: false,
      }).format(date);
}

function shortId(value) {
  if (!value) return "—";
  const text = String(value);
  return text.length > 10 ? `…${text.slice(-8)}` : text;
}

function makeCell(text, className = "") {
  const cell = document.createElement("td");
  cell.textContent = text;
  if (className) cell.className = className;
  return cell;
}

function emptyTable(body, columns, message = "暂无数据") {
  const row = document.createElement("tr");
  const cell = document.createElement("td");
  cell.colSpan = columns;
  cell.className = "empty-cell";
  cell.textContent = message;
  row.append(cell);
  body.replaceChildren(row);
}

function statusBadge(text, status) {
  const badge = document.createElement("span");
  badge.className = "status-badge";
  badge.textContent = text;
  if (["ACTIVE", "运行中", "OPEN", "BET"].includes(status)) {
    badge.classList.add("status-online");
  } else if (["DISABLED", "UNBOUND", "CANCEL"].includes(status)) {
    badge.classList.add("status-danger");
  } else {
    badge.classList.add("status-warning");
  }
  return badge;
}

function showPanel(panelId) {
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.hidden = panel.id !== panelId;
    panel.classList.toggle("active-panel", panel.id === panelId);
  });
  document.querySelectorAll(".nav-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.panel === panelId);
  });
  byId("page-title").textContent = panelTitles[panelId] || "管理中心";
}

async function login(event) {
  event.preventDefault();
  const button = byId("login-submit");
  setBusy(button, true, "正在验证…");
  showMessage("login-message", "");
  try {
    const response = await fetch("/api/v1/admin/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: byId("login-username").value.trim(),
        password: byId("login-password").value,
      }),
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!response.ok) throw new Error("login_failed");
    const body = await readJson(response);
    accessToken = body.access_token;
    csrfToken = body.csrf_token;
    byId("login-password").value = "";
    showDashboard();
    await loadDashboard();
  } catch (_error) {
    showMessage("login-message", "登录失败，请检查账号和密码。");
  } finally {
    byId("login-password").value = "";
    setBusy(button, false);
  }
}

async function logout() {
  try {
    await api("/api/v1/admin/session", { method: "DELETE" });
  } catch (_error) {
    // Session state is cleared locally even if the network is unavailable.
  }
  resetSession();
}

async function loadOverview() {
  const data = await api("/api/v1/admin/overview");
  byId("metric-users").textContent = String(data.user_count);
  byId("metric-devices").textContent = String(data.active_device_count);
  byId("metric-balance").textContent = formatYuan(data.current_balance_minor);
  byId("metric-turnover").textContent = formatYuan(data.periods.today.turnover_minor);
  setPnl(byId("metric-today-pnl"), data.periods.today.net_pnl_minor);
  setPnl(byId("metric-total-pnl"), data.periods.cumulative.net_pnl_minor);
  globalStopEnabled = data.global_stop_enabled;
  const badge = byId("global-status-badge");
  const button = byId("global-stop");
  badge.className = "status-badge";
  if (globalStopEnabled) {
    badge.textContent = "全局停止已启用";
    badge.classList.add("status-danger");
    button.textContent = "解除全局停止";
  } else {
    badge.textContent = "系统允许分配任务";
    badge.classList.add("status-online");
    button.textContent = "启用全局停止";
  }
  byId("last-updated").textContent = `更新于 ${formatTime(data.generated_at)}`;
}

async function loadUsers(cursor = null) {
  const path = cursor
    ? `/api/v1/admin/users?limit=30&cursor=${encodeURIComponent(cursor)}`
    : "/api/v1/admin/users?limit=30";
  const page = await api(path);
  const reports = await Promise.all(
    page.items.map(async (user) => {
      try {
        return await api(`/api/v1/admin/users/${user.account_id}/report`);
      } catch (_error) {
        return null;
      }
    }),
  );
  const body = byId("users-body");
  const rows = [];
  page.items.forEach((user, index) => {
    const report = reports[index];
    const row = document.createElement("tr");
    row.append(makeCell(user.username));
    const statusCell = document.createElement("td");
    statusCell.append(statusBadge(user.status === "ACTIVE" ? "启用" : "停用", user.status));
    row.append(statusCell);
    row.append(makeCell(formatYuan(report?.current_balance_minor)));
    row.append(makeCell(formatYuan(report?.periods?.today?.net_pnl_minor), report?.periods?.today?.net_pnl_minor >= 0 ? "profit" : "loss"));
    row.append(makeCell(formatYuan(report?.periods?.week?.net_pnl_minor), report?.periods?.week?.net_pnl_minor >= 0 ? "profit" : "loss"));
    row.append(makeCell(formatYuan(report?.periods?.month?.net_pnl_minor), report?.periods?.month?.net_pnl_minor >= 0 ? "profit" : "loss"));
    row.append(makeCell(formatYuan(report?.periods?.cumulative?.net_pnl_minor), report?.periods?.cumulative?.net_pnl_minor >= 0 ? "profit" : "loss"));
    const actionCell = document.createElement("td");
    const action = document.createElement("button");
    action.type = "button";
    action.className = "button button-secondary";
    action.textContent = "查看详情";
    action.addEventListener("click", () => selectUser(user.account_id));
    actionCell.append(action);
    row.append(actionCell);
    rows.push(row);
  });
  if (rows.length) body.replaceChildren(...rows);
  else emptyTable(body, 8);
  usersCursor = page.next_cursor;
  byId("users-next").hidden = !usersCursor;
}

function detailItem(label, value, className = "") {
  const item = document.createElement("div");
  item.className = "detail-item";
  const caption = document.createElement("span");
  caption.textContent = label;
  const content = document.createElement("strong");
  content.textContent = value;
  if (className) content.className = className;
  item.append(caption, content);
  return item;
}

async function selectUser(accountId) {
  const [detail, report] = await Promise.all([
    api(`/api/v1/admin/users/${accountId}`),
    api(`/api/v1/admin/users/${accountId}/report`),
  ]);
  selectedUser = { detail, report };
  byId("user-detail").hidden = false;
  byId("user-detail-title").textContent = detail.username;
  const status = byId("user-detail-status");
  status.className = "status-badge";
  status.textContent = detail.status === "ACTIVE" ? "账号启用" : "账号停用";
  status.classList.add(detail.status === "ACTIVE" ? "status-online" : "status-danger");
  const activeDevice = detail.devices.find((device) => device.status === "ACTIVE") || null;
  const latency = report.execution_latency;
  const metrics = [
    detailItem("当前余额", formatYuan(report.current_balance_minor)),
    detailItem("今日输赢", formatYuan(report.periods.today.net_pnl_minor), report.periods.today.net_pnl_minor >= 0 ? "profit" : "loss"),
    detailItem("累计输赢", formatYuan(report.periods.cumulative.net_pnl_minor), report.periods.cumulative.net_pnl_minor >= 0 ? "profit" : "loss"),
    detailItem("当前设备", activeDevice ? shortId(activeDevice.device_id) : "未绑定"),
    detailItem("起始金额 / 上限", `${formatYuan(report.base_minor)} / ${formatYuan(report.cap_minor)}`),
    detailItem("未追回本金", formatYuan(report.unrecovered_loss_minor), report.unrecovered_loss_minor > 0 ? "loss" : ""),
    detailItem("生效门槛版本", report.active_threshold_version ? `v${report.active_threshold_version}` : "未启用"),
    detailItem("确认延迟 P95", latency ? `${latency.p95_ms} ms` : "暂无样本"),
  ];
  byId("user-detail-metrics").replaceChildren(...metrics);
  byId("unbind-device-button").disabled = !activeDevice;
  byId("disable-account-button").disabled = detail.status !== "ACTIVE";
  byId("user-action-reason").value = "";
}

async function loadChampions(cursor = null) {
  const path = cursor
    ? `/api/v1/admin/champions?limit=50&cursor=${encodeURIComponent(cursor)}`
    : "/api/v1/admin/champions?limit=50";
  const page = await api(path);
  const rows = page.items.map((item) => {
    const row = document.createElement("tr");
    row.append(makeCell(item.actor_ref));
    row.append(makeCell(item.market));
    row.append(makeCell(item.direction));
    row.append(makeCell(item.user_level));
    row.append(makeCell(String(item.sample_count)));
    row.append(makeCell(formatRate(item.raw_win_rate)));
    row.append(makeCell(formatRate(item.conservative_win_rate)));
    row.append(makeCell(formatRate(item.conservative_unit_return), Number(item.conservative_unit_return) >= 0 ? "profit" : "loss"));
    const stateCell = document.createElement("td");
    stateCell.append(statusBadge(item.signal_state === "OPEN" ? "当前候选" : "已结算", item.signal_state));
    row.append(stateCell);
    return row;
  });
  const body = byId("champions-body");
  if (rows.length) body.replaceChildren(...rows);
  else emptyTable(body, 9);
  championsCursor = page.next_cursor;
  byId("champions-next").hidden = !championsCursor;
}

async function loadTasks(cursor = null) {
  const path = cursor
    ? `/api/v1/admin/tasks?limit=50&cursor=${encodeURIComponent(cursor)}`
    : "/api/v1/admin/tasks?limit=50";
  const page = await api(path);
  const rows = page.items.map((item) => {
    const row = document.createElement("tr");
    row.append(makeCell(formatTime(item.issued_at)));
    row.append(makeCell(item.period_id));
    row.append(makeCell(`r${item.revision}`));
    const actionCell = document.createElement("td");
    actionCell.append(statusBadge(item.action === "BET" ? "下注" : "取消", item.action));
    row.append(actionCell);
    row.append(makeCell(item.actor_ref || "—"));
    row.append(makeCell(item.ball ? `第 ${item.ball} 球` : "—"));
    row.append(makeCell(item.direction || "—"));
    return row;
  });
  const body = byId("tasks-body");
  if (rows.length) body.replaceChildren(...rows);
  else emptyTable(body, 7);
  tasksCursor = page.next_cursor;
  byId("tasks-next").hidden = !tasksCursor;
}

async function loadAudit(cursor = null) {
  const path = cursor
    ? `/api/v1/admin/audit?limit=50&cursor=${encodeURIComponent(cursor)}`
    : "/api/v1/admin/audit?limit=50";
  const page = await api(path);
  const rows = page.items.map((item) => {
    const row = document.createElement("tr");
    row.append(makeCell(formatTime(item.created_at)));
    row.append(makeCell(item.action));
    row.append(makeCell(`${item.target_type} ${shortId(item.target_id)}`));
    row.append(makeCell(item.reason));
    row.append(makeCell(item.request_id));
    return row;
  });
  const body = byId("audit-body");
  if (rows.length) body.replaceChildren(...rows);
  else emptyTable(body, 5);
  auditCursor = page.next_cursor;
  byId("audit-next").hidden = !auditCursor;
}

function thresholdPayload() {
  const deviceScope = byId("threshold-scope").value === "DEVICE";
  return {
    device_id: deviceScope ? byId("threshold-device").value.trim() : null,
    minimum_level: byId("threshold-level").value,
    minimum_conservative_win_rate: (Number(byId("threshold-win-rate").value) / 100).toFixed(10),
    minimum_conservative_roi: (Number(byId("threshold-roi").value) / 100).toFixed(10),
    minimum_followable_rate: (Number(byId("threshold-followable").value) / 100).toFixed(10),
  };
}

async function previewThreshold() {
  const button = byId("threshold-preview");
  setBusy(button, true, "正在计算…");
  showMessage("threshold-message", "");
  try {
    const preview = await api("/api/v1/admin/thresholds/preview", {
      method: "POST",
      body: JSON.stringify(thresholdPayload()),
    });
    pendingPreviewId = preview.preview_id;
    const rows = preview.windows.map((item) => {
      const row = document.createElement("tr");
      row.append(makeCell(`${item.days} 天`));
      row.append(makeCell(String(item.executable_signal_count)));
      row.append(makeCell(`${item.win_count} / ${item.loss_count}`));
      row.append(makeCell(formatRate(item.conservative_win_rate)));
      row.append(makeCell(`${(Number(item.unit_profit_micros) / 1000000).toFixed(2)} 单位`, item.unit_profit_micros >= 0 ? "profit" : "loss"));
      return row;
    });
    byId("threshold-preview-body").replaceChildren(...rows);
    byId("threshold-activate").disabled = false;
    showMessage("threshold-message", "预览完成。确认结果后填写原因并启用。", "success");
  } catch (_error) {
    pendingPreviewId = null;
    byId("threshold-activate").disabled = true;
    showMessage("threshold-message", "预览失败，请检查参数或稍后重试。");
  } finally {
    setBusy(button, false);
  }
}

async function activateThreshold() {
  if (!pendingPreviewId) return;
  const reason = byId("threshold-reason").value.trim();
  if (!reason) {
    showMessage("threshold-message", "启用前必须填写原因。");
    return;
  }
  const button = byId("threshold-activate");
  setBusy(button, true, "正在启用…");
  try {
    const body = {
      ...thresholdPayload(),
      preview_id: pendingPreviewId,
      reason,
    };
    const result = await api("/api/v1/admin/thresholds", {
      method: "POST",
      body: JSON.stringify(body),
    });
    pendingPreviewId = null;
    button.disabled = true;
    showMessage("threshold-message", `门槛版本 v${result.config_version} 已启用。`, "success");
    await Promise.all([loadOverview(), loadAudit()]);
  } catch (_error) {
    showMessage("threshold-message", "启用失败。请重新预览后再试。");
  } finally {
    setBusy(button, false);
    if (!pendingPreviewId) button.disabled = true;
  }
}

function invalidateThresholdPreview() {
  pendingPreviewId = null;
  byId("threshold-activate").disabled = true;
  emptyTable(byId("threshold-preview-body"), 5, "修改参数后请重新预览");
}

async function generateAuthorizationCode(event) {
  event.preventDefault();
  const button = byId("authorization-generate");
  const purpose = byId("authorization-purpose").value;
  const reason = byId("authorization-reason").value.trim();
  const target = byId("authorization-account").value.trim();
  if (!reason || (purpose === "REBIND" && !target)) {
    showMessage("authorization-message", "请填写完整的目标用户和发放原因。");
    return;
  }
  setBusy(button, true, "正在生成…");
  try {
    const result = await api("/api/v1/admin/authorization-codes", {
      method: "POST",
      body: JSON.stringify({
        purpose,
        target_account_id: purpose === "REBIND" ? target : null,
        reason,
      }),
    });
    oneTimeCode = result.authorization_code;
    byId("authorization-code-value").textContent = oneTimeCode;
    byId("authorization-code-dialog").showModal();
    byId("authorization-reason").value = "";
    showMessage("authorization-message", "授权码已生成并记录审计。", "success");
    await loadAudit();
  } catch (_error) {
    showMessage("authorization-message", "生成失败，请检查目标用户或稍后重试。");
  } finally {
    setBusy(button, false);
  }
}

async function copyAuthorizationCode() {
  if (!oneTimeCode) return;
  try {
    await navigator.clipboard.writeText(oneTimeCode);
    byId("copy-code-button").textContent = "已复制";
  } catch (_error) {
    byId("copy-code-button").textContent = "复制失败，请手动选择";
  }
}

function clearAuthorizationCode() {
  oneTimeCode = null;
  byId("authorization-code-value").textContent = "";
  byId("copy-code-button").textContent = "复制授权码";
}

async function setGlobalStop() {
  const reason = byId("global-stop-reason").value.trim();
  if (!reason) {
    showMessage("dashboard-message", "全局停止操作必须填写原因。");
    return;
  }
  const enabling = !globalStopEnabled;
  if (enabling && !window.confirm("确认停止所有尚未执行的下注任务？")) return;
  const button = byId("global-stop");
  setBusy(button, true, "正在提交…");
  try {
    await api("/api/v1/admin/global-stop", {
      method: "POST",
      body: JSON.stringify({ enabled: enabling, reason }),
    });
    byId("global-stop-reason").value = "";
    showMessage("dashboard-message", enabling ? "全局停止已启用。" : "全局停止已解除。", "success");
    await Promise.all([loadOverview(), loadTasks(), loadAudit()]);
  } catch (_error) {
    showMessage("dashboard-message", "操作失败，请刷新状态后重试。");
  } finally {
    setBusy(button, false);
  }
}

async function unbindSelectedDevice() {
  const reason = byId("user-action-reason").value.trim();
  const device = selectedUser?.detail?.devices?.find((item) => item.status === "ACTIVE");
  if (!device || !reason) {
    showMessage("dashboard-message", "解绑设备前必须填写原因。");
    return;
  }
  const button = byId("unbind-device-button");
  setBusy(button, true, "正在解绑…");
  try {
    await api(`/api/v1/admin/devices/${device.device_id}/unbind`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
    showMessage("dashboard-message", "设备已解绑，旧会话和未执行任务已撤销。", "success");
    byId("user-detail").hidden = true;
    selectedUser = null;
    await Promise.all([loadUsers(), loadOverview(), loadTasks(), loadAudit()]);
  } catch (_error) {
    showMessage("dashboard-message", "解绑失败，请刷新后重试。");
  } finally {
    setBusy(button, false);
  }
}

async function disableSelectedAccount() {
  const reason = byId("user-action-reason").value.trim();
  const accountId = selectedUser?.detail?.account_id;
  if (!accountId || !reason) {
    showMessage("dashboard-message", "停用账号前必须填写原因。");
    return;
  }
  if (!window.confirm("确认停用此账号并撤销其设备会话？")) return;
  const button = byId("disable-account-button");
  setBusy(button, true, "正在停用…");
  try {
    await api(`/api/v1/admin/accounts/${accountId}/disable`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
    showMessage("dashboard-message", "账号已停用，未执行任务已取消。", "success");
    byId("user-detail").hidden = true;
    selectedUser = null;
    await Promise.all([loadUsers(), loadOverview(), loadTasks(), loadAudit()]);
  } catch (_error) {
    showMessage("dashboard-message", "停用失败，请刷新后重试。");
  } finally {
    setBusy(button, false);
  }
}

async function loadDashboard() {
  showMessage("dashboard-message", "");
  const button = byId("refresh-button");
  setBusy(button, true, "刷新中…");
  try {
    await Promise.all([
      loadOverview(),
      loadUsers(),
      loadChampions(),
      loadTasks(),
      loadAudit(),
    ]);
  } catch (_error) {
    showMessage("dashboard-message", "部分数据暂时无法读取，请稍后刷新。");
  } finally {
    setBusy(button, false);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  byId("login-form").addEventListener("submit", login);
  byId("logout-button").addEventListener("click", logout);
  byId("refresh-button").addEventListener("click", loadDashboard);
  byId("global-stop").addEventListener("click", setGlobalStop);
  byId("users-next").addEventListener("click", () => loadUsers(usersCursor));
  byId("champions-next").addEventListener("click", () => loadChampions(championsCursor));
  byId("tasks-next").addEventListener("click", () => loadTasks(tasksCursor));
  byId("audit-next").addEventListener("click", () => loadAudit(auditCursor));
  byId("threshold-preview").addEventListener("click", previewThreshold);
  byId("threshold-activate").addEventListener("click", activateThreshold);
  byId("authorization-form").addEventListener("submit", generateAuthorizationCode);
  byId("copy-code-button").addEventListener("click", copyAuthorizationCode);
  byId("authorization-code-dialog").addEventListener("close", clearAuthorizationCode);
  byId("unbind-device-button").addEventListener("click", unbindSelectedDevice);
  byId("disable-account-button").addEventListener("click", disableSelectedAccount);

  document.querySelectorAll(".nav-button").forEach((button) => {
    button.addEventListener("click", () => showPanel(button.dataset.panel));
  });
  byId("threshold-scope").addEventListener("change", () => {
    byId("threshold-device-wrap").hidden = byId("threshold-scope").value !== "DEVICE";
    invalidateThresholdPreview();
  });
  ["threshold-device", "threshold-level", "threshold-win-rate", "threshold-roi", "threshold-followable"].forEach((id) => {
    byId(id).addEventListener("input", invalidateThresholdPreview);
  });
  byId("authorization-purpose").addEventListener("change", () => {
    byId("authorization-account-wrap").hidden = byId("authorization-purpose").value !== "REBIND";
  });
  emptyTable(byId("threshold-preview-body"), 5, "先填写门槛并运行预览");

  window.setInterval(() => {
    if (accessToken && !document.hidden) {
      loadOverview().catch(() => showMessage("dashboard-message", "实时状态刷新失败。"));
    }
  }, 15000);
});
