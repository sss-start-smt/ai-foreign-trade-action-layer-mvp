const stateNames = {
  DO_NOW: "立即处理",
  DO_TODAY: "今天处理",
  WAITING_EXTERNAL: "等待外部",
  NEEDS_CONFIRMATION: "待确认",
  SCHEDULED: "已安排",
  ESCALATE: "需主管介入",
  NOT_MY_RESPONSIBILITY: "非本人责任",
  DONE: "已完成"
};

const riskNames = {
  critical: "关键风险",
  high: "高风险",
  medium: "需关注",
  low: "低风险",
  none: "暂无风险"
};

const targetNames = {
  factory: "工厂",
  customer: "客户",
  manager: "主管",
  bank: "银行",
  logistics: "物流服务商"
};

const countryByOrder = {
  "PO-1001": ["🇺🇸", "美国"],
  "PO-1002": ["🇬🇧", "英国"],
  "PO-1003": ["🇩🇪", "德国"]
};

const iconPaths = {
  bolt: '<path d="M13 2 3 14h8l-1 8 10-12h-8z"/>',
  clipboard: '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V2h6v2M9 9h6M9 13h6M9 17h4"/>',
  box: '<path d="m4 7 8-4 8 4-8 4zM4 7v10l8 4 8-4V7M12 11v10"/>',
  message: '<path d="M21 15a4 4 0 0 1-4 4H8l-5 3 1.6-5A7 7 0 1 1 21 15Z"/><path d="M8 12h.01M12 12h.01M16 12h.01"/>',
  "check-square": '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="m8 12 3 3 6-7"/>',
  chart: '<path d="M4 19V9M10 19V5M16 19v-7M22 19H2"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.6v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/>',
  "chevrons-left": '<path d="m11 17-5-5 5-5M18 17l-5-5 5-5"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
  "chevron-down": '<path d="m6 9 6 6 6-6"/>',
  sparkles: '<path d="m12 3 1.4 3.6L17 8l-3.6 1.4L12 13l-1.4-3.6L7 8l3.6-1.4zM5 15l.8 2.2L8 18l-2.2.8L5 21l-.8-2.2L2 18l2.2-.8zM19 13l.7 1.8 1.8.7-1.8.7L19 18l-.7-1.8-1.8-.7 1.8-.7z"/>',
  "rotate-ccw": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/>',
  filter: '<path d="M4 5h16l-6 7v5l-4 2v-7z"/>',
  file: '<path d="M6 2h8l4 4v16H6z"/><path d="M14 2v5h5M9 13h6M9 17h6"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/>',
  "chevron-left": '<path d="m15 18-6-6 6-6"/>',
  "chevron-right": '<path d="m9 18 6-6-6-6"/>',
  "mouse-pointer": '<path d="m4 3 7.5 17 2.4-6.1L20 11.5z"/><path d="m14 14 5 5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  phone: '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 2 .7 2.9a2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.2-1.2a2 2 0 0 1 2.1-.5c1 .3 1.9.6 2.9.7a2 2 0 0 1 1.7 2Z"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  arrowup: '<path d="m18 15-6-6-6 6"/><path d="M12 9v12"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>'
};

let allItems = [];
let dashboardData = null;
let activeFilter = "ALL";
let selectedId = null;
let currentQuery = "";
let currentRisk = "ALL";
let currentTarget = "ALL";
let currentSort = "priority";
let waitTaskId = null;

function svg(name, cls = "") {
  return `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${iconPaths[name] || ""}</svg>`;
}

document.querySelectorAll("[data-icon]").forEach(el => {
  el.innerHTML = svg(el.dataset.icon);
});

const toast = (msg) => {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => el.classList.remove("show"), 2400);
};

const api = async (url, opts = {}) => {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
};

const parseDate = (value) => {
  if (!value) return null;
  const normalized = String(value).replace("Z", "+00:00");
  const d = new Date(normalized);
  return Number.isNaN(d.getTime()) ? null : d;
};

const fmtDateTime = (value) => {
  const d = parseDate(value);
  if (!d) return "—";
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  const prefix = sameDay ? "今天" : d.toDateString() === tomorrow.toDateString() ? "明天" : `${d.getMonth()+1}月${d.getDate()}日`;
  return `${prefix} ${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`;
};

const fmtShort = (value) => {
  const d = parseDate(value);
  if (!d) return "—";
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
};

const orderNo = item => item.order?.order_no || item.related_order_id || "未关联订单";

function currentItems() {
  let items = allItems.filter(item => {
    const matchState = activeFilter === "ALL" || item.action_state === activeFilter;
    const haystack = [
      orderNo(item), item.title, item.recommended_action,
      item.order?.customer_name, item.order?.product_name
    ].filter(Boolean).join(" ").toLowerCase();
    const matchSearch = !currentQuery || haystack.includes(currentQuery.toLowerCase());
    const matchRisk = currentRisk === "ALL" || item.risk_level === currentRisk;
    const target = item.waiting_on || item.target || "";
    const matchTarget = currentTarget === "ALL" || target === currentTarget;
    return matchState && matchSearch && matchRisk && matchTarget;
  });

  const riskWeight = { critical: 5, high: 4, medium: 3, low: 2, none: 1 };
  items.sort((a, b) => {
    if (currentSort === "time") {
      return (parseDate(a.next_action_at)?.getTime() || Infinity) - (parseDate(b.next_action_at)?.getTime() || Infinity);
    }
    if (currentSort === "risk") {
      return (riskWeight[b.risk_level] || 0) - (riskWeight[a.risk_level] || 0);
    }
    return (b.priority_score || 0) - (a.priority_score || 0);
  });
  return items;
}

async function load({ preserveSelection = true } = {}) {
  dashboardData = await api("/api/dashboard");
  allItems = dashboardData.items || [];
  renderKpis(dashboardData.summary || {});
  renderTabs();
  renderCards();
  document.getElementById("health").textContent = `已连接 · ${String(dashboardData.current_time || "").slice(11,16)}`;
  if (preserveSelection && selectedId && allItems.some(x => x.task_id === selectedId)) {
    await selectCard(selectedId, false);
  }
}

function renderKpis(summary) {
  const cards = [
    {
      label: "立即处理", value: summary.do_now || 0, trend: "+2", icon: "bolt",
      bg: "#eaf2ff", color: "#2868f0", glow: "#eef4ff", trendColor: "#ee5360"
    },
    {
      label: "今天处理", value: summary.do_today || 0, trend: "+3", icon: "check",
      bg: "#e8f8ef", color: "#23a862", glow: "#eefaf3", trendColor: "#23a862"
    },
    {
      label: "等待外部", value: summary.waiting || 0, trend: "-1", icon: "clock",
      bg: "#fff1dc", color: "#f39a32", glow: "#fff7eb", trendColor: "#23a862"
    },
    {
      label: "需主管介入", value: summary.escalate || 0, trend: "+1", icon: "arrowup",
      bg: "#ffeaec", color: "#ee5360", glow: "#fff1f2", trendColor: "#ee5360"
    }
  ];

  document.getElementById("kpis").innerHTML = cards.map(card => `
    <article class="summary-card" style="--icon-bg:${card.bg};--icon-color:${card.color};--card-glow:${card.glow};--trend:${card.trendColor}">
      <div class="summary-icon">${svg(card.icon)}</div>
      <div class="summary-copy">
        <span>${card.label}</span>
        <strong>${String(card.value).padStart(2,"0")}</strong>
        <small>较昨日 <b>${card.trend}</b></small>
      </div>
    </article>
  `).join("");
}

function renderTabs() {
  const counts = {
    ALL: allItems.length,
    DO_NOW: allItems.filter(x => x.action_state === "DO_NOW").length,
    DO_TODAY: allItems.filter(x => x.action_state === "DO_TODAY").length,
    WAITING_EXTERNAL: allItems.filter(x => x.action_state === "WAITING_EXTERNAL").length,
    NEEDS_CONFIRMATION: allItems.filter(x => x.action_state === "NEEDS_CONFIRMATION").length,
    SCHEDULED: allItems.filter(x => x.action_state === "SCHEDULED").length,
    ESCALATE: allItems.filter(x => x.action_state === "ESCALATE").length
  };
  const tabs = [
    ["ALL", "全部"], ["DO_NOW", "立即处理"], ["DO_TODAY", "今天处理"],
    ["WAITING_EXTERNAL", "等待外部"], ["NEEDS_CONFIRMATION", "待确认"],
    ["SCHEDULED", "已安排"], ["ESCALATE", "需升级"]
  ];
  document.getElementById("tabs").innerHTML = tabs.map(([key, name]) => `
    <button class="tab ${activeFilter === key ? "active" : ""}" data-key="${key}">
      ${name}<span class="tab-count">${counts[key]}</span>
    </button>
  `).join("");
  document.querySelectorAll(".tab").forEach(btn => {
    btn.addEventListener("click", () => {
      activeFilter = btn.dataset.key;
      renderTabs();
      renderCards();
    });
  });
}

function cardAccent(item) {
  if (item.action_state === "DO_NOW" || item.action_state === "ESCALATE") return "#2868f0";
  if (item.action_state === "WAITING_EXTERNAL") return "#f39a32";
  if (item.action_state === "NEEDS_CONFIRMATION") return "#7868e6";
  if (item.action_state === "DO_TODAY") return "#23a862";
  return "#4b82f5";
}

function timeClass(item) {
  if (item.action_state === "DO_NOW" || item.action_state === "ESCALATE") return "urgent";
  if (item.action_state === "DO_TODAY") return "today";
  if (item.risk_level === "low") return "safe";
  return "";
}

function renderCards() {
  const items = currentItems();
  document.getElementById("taskCount").textContent = `(${items.length})`;
  document.getElementById("listSummary").textContent = `显示 ${items.length} 条，共 ${allItems.length} 条`;
  document.getElementById("cards").innerHTML = items.length ? items.map(item => {
    const no = orderNo(item);
    const country = countryByOrder[no] || ["🌐", "海外客户"];
    const evidenceCount = Math.max((item.evidence || []).length, 1);
    return `
      <article class="task-card ${selectedId === item.task_id ? "selected" : ""}" data-id="${item.task_id}" style="--accent:${cardAccent(item)}">
        <div class="order-cell">
          <div class="order-link">${no}</div>
          <div class="customer-name">${item.order?.customer_name || "未关联客户"}</div>
          <div class="country-line"><span class="country-flag">${country[0]}</span>${country[1]}</div>
          <span class="status-badge ${item.action_state}">${stateNames[item.action_state] || item.action_state}</span>
        </div>
        <div class="task-main">
          <h3>${item.title}</h3>
          <p class="recommended"><b>推荐动作：</b>${item.recommended_action || "待确认下一步动作"}</p>
          <div class="evidence-row">
            <span>建议证据：</span>
            <span class="evidence-icon">${svg("file")}</span>
            <span class="evidence-icon">${svg("file")}</span>
            <span class="evidence-icon">${svg("image")}</span>
            <span class="evidence-more">+${evidenceCount}</span>
          </div>
        </div>
        <div class="task-side">
          <span class="risk-badge risk-${item.risk_level || "none"}">${riskNames[item.risk_level] || "暂无风险"}</span>
          <div>
            <span class="next-label">下次行动时间</span>
            <strong class="next-time ${timeClass(item)}">${fmtDateTime(item.next_action_at || item.business_deadline)}</strong>
          </div>
        </div>
      </article>
    `;
  }).join("") : `
    <div class="list-empty">
      <h3>当前筛选条件下没有任务</h3>
      <p>可以清除搜索或切换任务状态。</p>
    </div>
  `;

  document.querySelectorAll(".task-card").forEach(card => {
    card.addEventListener("click", () => selectCard(card.dataset.id));
  });
}

async function selectCard(id, rerender = true) {
  selectedId = id;
  if (rerender) renderCards();

  const item = allItems.find(x => x.task_id === id);
  if (!item) return;

  let detail = { order: item.order, tasks: [], risks: [], messages: [], commitments: [], events: [] };
  if (item.related_order_id) {
    try { detail = await api(`/api/orders/${item.related_order_id}`); }
    catch (_) {}
  }
  const order = detail.order || item.order || {};
  const no = order.order_no || item.related_order_id || "未关联订单";
  const evidence = item.evidence || [];
  const risks = detail.risks || [];
  const promises = detail.commitments || [];

  const evidenceHtml = evidence.length
    ? evidence.map(e => `<div class="quote-card">${escapeHtml(String(e))}<span class="quote-meta">— 原始消息证据</span></div>`).join("")
    : `<div class="quote-card">暂无可展示的原文证据<span class="quote-meta">— 系统记录</span></div>`;

  const riskHtml = risks.length
    ? risks.map(r => `<div class="risk-item"><span class="risk-dot"></span><div><b>${riskTypeName(r.risk_type)}</b><br>${escapeHtml(r.evidence || "暂无风险原文")}</div></div>`).join("")
    : `<div class="risk-item"><span class="risk-dot" style="background:#9aa7b8"></span><div><b>${riskNames[item.risk_level] || "暂无风险"}</b><br>${escapeHtml((item.priority_reasons || []).join("；") || "当前没有额外风险记录")}</div></div>`;

  const commitmentText = promises[0]?.commitment_value || order.latest_supplier_commitment;
  const timeline = [
    item.last_contact_at ? { t: fmtDateTime(item.last_contact_at), text: `已记录联系${targetNames[item.waiting_on] || item.waiting_on || "外部对象"}` } : null,
    item.promised_reply_at ? { t: fmtDateTime(item.promised_reply_at), text: `${targetNames[item.waiting_on] || item.waiting_on || "对方"}承诺回复时间` } : null,
    { t: fmtDateTime(item.created_at), text: "系统创建行动并完成排序" }
  ].filter(Boolean);

  const panel = document.getElementById("sidepanel");
  panel.className = "detail-panel open";
  panel.innerHTML = `
    <div class="detail-head">
      <h2>行动详情</h2>
      <button class="detail-close" id="closeDetail" aria-label="关闭">×</button>
    </div>
    <div class="detail-scroll">
      <section class="detail-section">
        <div class="section-title"><h3>订单信息</h3><span class="order-id">${no}</span></div>
        <div class="detail-kv"><span>客户</span><strong>${order.customer_name || "—"}</strong></div>
        <div class="detail-kv"><span>产品</span><span>${order.product_name || "—"}</span></div>
        <div class="detail-kv"><span>包装方式</span><span>${order.packaging_method || "—"}</span></div>
        <div class="detail-kv"><span>当前节点</span><span>${order.current_node || "—"}</span></div>
        <div class="detail-kv"><span>客户交期</span><span>${fmtShort(order.requested_delivery_date)}</span></div>
        <div class="detail-kv"><span>当前进度</span><span>${order.current_progress != null ? Math.round(order.current_progress * 100) + "%" : "—"}</span></div>
      </section>

      <section class="detail-section">
        <div class="section-title"><h3>当前行动判断</h3><span class="status-badge ${item.action_state}">${stateNames[item.action_state]}</span></div>
        <div class="detail-kv"><span>推荐动作</span><strong>${item.recommended_action || "—"}</strong></div>
        <div class="detail-kv"><span>处理对象</span><span>${targetNames[item.target] || item.target || "—"}</span></div>
        <div class="detail-kv"><span>等待对象</span><span>${targetNames[item.waiting_on] || item.waiting_on || "—"}</span></div>
        <div class="detail-kv"><span>承诺回复</span><span>${fmtDateTime(item.promised_reply_at)}</span></div>
        <div class="detail-kv"><span>下一处理</span><strong>${fmtDateTime(item.next_action_at || item.business_deadline)}</strong></div>
      </section>

      <section class="detail-section">
        <div class="section-title"><h3>客户 / 工厂消息（证据）</h3></div>
        ${evidenceHtml}
      </section>

      <section class="detail-section">
        <div class="section-title"><h3>最新工厂承诺</h3></div>
        <div class="commitment-card">
          ${commitmentText ? `当前记录的工厂承诺日期为 <b>${fmtShort(commitmentText)}</b>。` : "尚未形成明确工厂承诺，需继续确认。"}
        </div>
      </section>

      <section class="detail-section">
        <div class="section-title"><h3>风险原因</h3><span class="risk-badge risk-${item.risk_level || "none"}">${riskNames[item.risk_level] || "暂无风险"}</span></div>
        <div class="risk-list">${riskHtml}</div>
      </section>

      <section class="detail-section">
        <div class="section-title"><h3>跟进时间线</h3></div>
        <div class="timeline">
          ${timeline.map(x => `<div class="timeline-item"><b>${x.t}</b>${x.text}</div>`).join("")}
        </div>
      </section>

      <section class="detail-section">
        <div class="section-title"><h3>下一步行动</h3></div>
        <div class="detail-actions">
          <button class="action-button primary" id="contactBtn">${svg("phone")} 记录已联系</button>
          <button class="action-button blue" id="waitBtn">${svg("clock")} 设置等待</button>
          <button class="action-button green" id="writebackBtn">${svg("check")} 确认写回</button>
          <button class="action-button red" id="escalateBtn">${svg("arrowup")} 升级主管</button>
          <button class="action-button gray" id="completeBtn">标记完成</button>
        </div>
      </section>
    </div>
  `;

  document.getElementById("closeDetail").onclick = closeDetail;
  document.getElementById("contactBtn").onclick = () => quickRecordContact(item.task_id);
  document.getElementById("waitBtn").onclick = () => openWaitDialog(item.task_id, item.waiting_on || item.target || "factory");
  document.getElementById("writebackBtn").onclick = () => toast("正式写回由已接通的 Coze FT03 执行；网页不伪造写回结果");
  document.getElementById("escalateBtn").onclick = () => toast("主管升级接口尚未接入，已保留产品交互入口");
  document.getElementById("completeBtn").onclick = () => completeTask(item.task_id);
}

function closeDetail() {
  const panel = document.getElementById("sidepanel");
  selectedId = null;
  renderCards();
  if (window.innerWidth <= 920) {
    panel.classList.remove("open");
    setTimeout(() => {
      panel.className = "detail-panel empty";
      panel.innerHTML = `<div class="empty-detail"><div class="empty-orbit">${svg("mouse-pointer")}</div><h3>选择一条行动</h3><p>查看订单信息、原文证据、风险原因和下一步操作。</p></div>`;
    }, 260);
  } else {
    panel.className = "detail-panel empty";
    panel.innerHTML = `<div class="empty-detail"><div class="empty-orbit">${svg("mouse-pointer")}</div><h3>选择一条行动</h3><p>查看订单信息、原文证据、风险原因和下一步操作。</p></div>`;
  }
}

async function quickRecordContact(id) {
  const d = new Date(Date.now() + 3 * 60 * 60 * 1000);
  const promised = toLocalIso(d);
  await api(`/api/tasks/${id}/contacted`, {
    method: "POST",
    body: JSON.stringify({ waiting_on: "factory", promised_reply_at: promised, operator_id: "USER-1" })
  });
  document.getElementById("step2").disabled = true;
  toast("已记录联系，任务进入等待工厂回复窗口");
  await load();
}

function openWaitDialog(id, target) {
  waitTaskId = id;
  document.getElementById("waitTarget").value = Object.keys(targetNames).includes(target) ? target : "factory";
  const d = new Date(Date.now() + 3 * 60 * 60 * 1000);
  document.getElementById("waitTime").value = toDateTimeLocal(d);
  document.getElementById("waitDialog").showModal();
}

async function saveWait() {
  if (!waitTaskId) return;
  const local = document.getElementById("waitTime").value;
  const target = document.getElementById("waitTarget").value;
  if (!local) return;
  const promised = toLocalIso(new Date(local));
  await api(`/api/tasks/${waitTaskId}/contacted`, {
    method: "POST",
    body: JSON.stringify({ waiting_on: target, promised_reply_at: promised, operator_id: "USER-1" })
  });
  toast(`已设置等待${targetNames[target] || target}，承诺前不重复催办`);
  waitTaskId = null;
  await load();
}

async function completeTask(id) {
  await api(`/api/tasks/${id}/complete`, { method: "POST" });
  toast("任务已标记完成");
  selectedId = null;
  closeDetail();
  await load({ preserveSelection: false });
}

function toDateTimeLocal(d) {
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function toLocalIso(d) {
  const pad = n => String(n).padStart(2, "0");
  const offset = -d.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const oh = pad(Math.floor(Math.abs(offset) / 60));
  const om = pad(Math.abs(offset) % 60);
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:00${sign}${oh}:${om}`;
}

function riskTypeName(type) {
  const map = {
    delivery_impact_unknown: "交期影响待确认",
    commitment_uncertain: "工厂承诺不明确",
    customer_cancellation: "客户取消风险",
    material_shortage: "物料短缺",
    document_risk: "文件风险"
  };
  return map[type] || type || "其他风险";
}

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, ch => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;" }[ch]));
}

document.getElementById("refreshBtn").onclick = () => load().then(() => toast("排序已刷新"));
document.getElementById("resetBtn").onclick = async () => {
  await api("/api/reset", { method: "POST" });
  selectedId = null;
  document.getElementById("step2").disabled = true;
  closeDetail();
  toast("演示数据已重置");
  await load({ preserveSelection: false });
};
document.getElementById("step1").onclick = async () => {
  await api("/api/demo/apply-ft01", { method: "POST" });
  document.getElementById("step2").disabled = false;
  toast("客户消息确认结果已应用，新增联系工厂任务");
  await load();
};
document.getElementById("step2").onclick = () => quickRecordContact("TASK-PO1001-CONFIRM");
document.getElementById("step3").onclick = async () => {
  await api("/api/demo/apply-ft02", { method: "POST" });
  toast("工厂回复确认结果已应用，订单与行动已重排");
  await load();
};

document.getElementById("searchInput").addEventListener("input", e => {
  currentQuery = e.target.value.trim();
  renderCards();
});
document.getElementById("sortSelect").addEventListener("change", e => {
  currentSort = e.target.value;
  renderCards();
});
document.getElementById("filterToggle").onclick = () => {
  document.getElementById("filterDrawer").classList.toggle("open");
};
document.getElementById("riskFilter").addEventListener("change", e => {
  currentRisk = e.target.value;
  renderCards();
});
document.getElementById("targetFilter").addEventListener("change", e => {
  currentTarget = e.target.value;
  renderCards();
});
document.getElementById("confirmWait").addEventListener("click", async e => {
  e.preventDefault();
  await saveWait();
  document.getElementById("waitDialog").close();
});
document.getElementById("collapseBtn").onclick = () => {
  document.querySelector(".sidebar").classList.toggle("collapsed");
  document.querySelector(".app-shell").classList.toggle("sidebar-collapsed");
};

document.querySelectorAll(".nav-item, .mobile-nav button").forEach(btn => {
  btn.addEventListener("click", () => {
    if (btn.classList.contains("active")) return;
    toast("当前交付先实现“今日行动工作台”；其他页面按高保真原型继续扩展");
  });
});

document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    document.getElementById("searchInput").focus();
  }
});

load().catch(err => {
  document.getElementById("health").textContent = "连接失败";
  toast(`页面加载失败：${err.message}`);
});
