(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const uncertaintyWords = ['应该','大概','可能','估计','差不多','尽量','预计','也许','或许'];
  const pageTitles = {today:'今日跟单', confirm:'待确认', orders:'订单', review:'复盘', exceptions:'异常中心', agent:'Agent运行', settings:'管理设置'};
  const riskLevelRank = {none:0, low:1, medium:2, high:3, critical:4};
  const stateRank = {DO_NOW:100, ESCALATE:95, NEEDS_CONFIRMATION:90, DO_TODAY:80, SCHEDULED:60, WAITING_EXTERNAL:40, NOT_MY_RESPONSIBILITY:10, DONE:0};
  const stateLabel = {DO_NOW:'现在做', ESCALATE:'主管介入', NEEDS_CONFIRMATION:'需要确认', DO_TODAY:'今天做', SCHEDULED:'已计划', WAITING_EXTERNAL:'等待外部', NOT_MY_RESPONSIBILITY:'非本人负责', DONE:'已完成'};
  const statePill = {DO_NOW:'pill-red', ESCALATE:'pill-red', NEEDS_CONFIRMATION:'pill-amber', DO_TODAY:'pill-blue', SCHEDULED:'pill-blue', WAITING_EXTERNAL:'pill-green', NOT_MY_RESPONSIBILITY:'pill-blue', DONE:'pill-green'};
  const stageOrder = ['接单','备货','生产','出货','交付'];

  const state = {
    token: null,
    profile: null,
    identity: null,
    view: 'today',
    settings: {},
    dashboard: null,
    orders: [],
    queue: [],
    waiting: [],
    reviews: [],
    d12Reviews: [],
    reviewSummary: null,
    selectedSource: '',
    currentOrder: null,
    currentOrderDetail: null,
    currentTask: null,
    saveSettingsTimer: null,
  };

  function toast(message, type = '') {
    const el = $('#toast');
    if (!el) return;
    el.textContent = message;
    el.className = `toast ${type}`.trim();
    el.classList.remove('hidden');
    clearTimeout(window.__flowToast);
    window.__flowToast = setTimeout(() => el.classList.add('hidden'), 2600);
  }

  function setLoading(btn, loading, label = '') {
    if (!btn) return;
    if (loading) {
      btn.dataset.oldText = btn.textContent;
      btn.textContent = label || '处理中…';
      btn.disabled = true;
      btn.classList.add('is-loading');
    } else {
      btn.textContent = btn.dataset.oldText || btn.textContent;
      btn.disabled = false;
      btn.classList.remove('is-loading');
    }
  }

  function formatDate(v) {
    if (!v) return '—';
    const d = new Date(v);
    if (Number.isNaN(d.getTime())) return String(v).slice(0, 10).replaceAll('-', '/');
    return new Intl.DateTimeFormat('zh-CN', {year:'numeric', month:'2-digit', day:'2-digit'}).format(d);
  }

  function formatTime(v) {
    if (!v) return '—';
    const d = new Date(v);
    if (Number.isNaN(d.getTime())) return String(v);
    return new Intl.DateTimeFormat('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}).format(d);
  }

  function relativeTime(v) {
    if (!v) return '未设置时间';
    const d = new Date(v);
    if (Number.isNaN(d.getTime())) return String(v);
    const diff = d.getTime() - Date.now();
    const abs = Math.abs(diff);
    if (abs < 3600000) return diff >= 0 ? `${Math.max(1, Math.round(diff/60000))} 分钟后` : `${Math.max(1, Math.round(-diff/60000))} 分钟前`;
    if (abs < 86400000) return diff >= 0 ? `${Math.max(1, Math.round(diff/3600000))} 小时后` : `${Math.max(1, Math.round(-diff/3600000))} 小时前`;
    return diff >= 0 ? `${Math.max(1, Math.round(diff/86400000))} 天后` : `${Math.max(1, Math.round(-diff/86400000))} 天前`;
  }

  function authHeaders(extra = {}) {
    return {'Content-Type':'application/json', ...(state.token ? {'X-Auth-Token':state.token} : {}), ...extra};
  }

  async function request(url, options = {}) {
    const res = await fetch(url, {...options, headers:authHeaders(options.headers || {})});
    const text = await res.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch { data = {detail:text}; }
    if (!res.ok) {
      if (res.status === 401 && url !== '/auth/login') {
        logout(false);
        throw new Error('登录状态已失效，请重新登录');
      }
      const detail = typeof data.detail === 'string' ? data.detail : (data.detail?.message || data.message || data.detail?.code || `请求失败 ${res.status}`);
      const err = new Error(detail);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  function sessionSave() {
    sessionStorage.setItem('floworder_d19_session', JSON.stringify({token:state.token, profile:state.profile}));
  }

  function sessionLoad() {
    try { return JSON.parse(sessionStorage.getItem('floworder_d19_session') || 'null'); } catch { return null; }
  }

  function applyRole() {
    const role = state.profile?.ui_role || state.identity?.role || 'operator';
    $$('.role-manager-up').forEach((el) => el.classList.toggle('role-hidden', !['manager','admin'].includes(role)));
    $$('.role-admin-only').forEach((el) => el.classList.toggle('role-hidden', role !== 'admin'));
    const name = state.profile?.display_name || state.identity?.name || state.identity?.user_id || '用户';
    const label = state.profile?.role_label || (role === 'manager' ? '主管' : role === 'admin' ? '管理员' : '跟单员');
    const avatar = state.profile?.avatar || name.slice(0, 1);
    ['topUserName','menuUserName'].forEach((id) => { const el = document.getElementById(id); if (el) el.textContent = name; });
    ['topUserRole','menuUserRole'].forEach((id) => { const el = document.getElementById(id); if (el) el.textContent = label; });
    ['topAvatar','menuAvatar'].forEach((id) => { const el = document.getElementById(id); if (el) el.textContent = avatar; });
  }

  function showLogin() {
    $('#authenticatedApp')?.classList.add('hidden');
    $('#loginScreen')?.classList.remove('hidden');
    $('#loginError')?.classList.add('hidden');
  }

  async function enterApp() {
    applyRole();
    // Keep the static shell hidden until real API data is loaded. This avoids
    // flashing the HTML design-time sample orders before the backend response.
    await loadCoreData();
    $('#loginScreen')?.classList.add('hidden');
    $('#authenticatedApp')?.classList.remove('hidden');
    navigate('today');
  }

  function logout(showMessage = true) {
    state.token = null; state.profile = null; state.identity = null;
    sessionStorage.removeItem('floworder_d19_session');
    showLogin();
    if (showMessage) toast('已退出登录');
  }

  async function login(username, password) {
    const data = await request('/auth/login', {method:'POST', body:JSON.stringify({username, password}), headers:{}});
    state.token = data.access_token;
    state.profile = data.profile;
    state.identity = data.identity;
    sessionSave();
    await enterApp();
  }

  async function restoreSession() {
    const saved = sessionLoad();
    if (!saved?.token) { showLogin(); return; }
    state.token = saved.token; state.profile = saved.profile || null;
    try {
      const me = await request('/auth/me');
      state.identity = me.identity;
      await enterApp();
    } catch {
      logout(false);
    }
  }

  async function loadCoreData() {
    let [settingsRes, dashboardRes, ordersRes] = await Promise.all([
      request('/api/settings'), request('/api/dashboard'), request('/api/orders')
    ]);

    // Railway can serve the app before the background startup seed has finished
    // (or after a seed warning). In demo mode, an empty order set gets one safe,
    // idempotent ensure attempt against the SAME database used by this request.
    if (!Array.isArray(ordersRes.items) || ordersRes.items.length === 0) {
      try {
        const ensured = await request('/api/d19/demo/ensure', {method:'POST', body:'{}'});
        if (ensured?.enabled && Number(ensured?.order_count || 0) > 0) {
          [dashboardRes, ordersRes] = await Promise.all([
            request('/api/dashboard'), request('/api/orders')
          ]);
        }
      } catch (e) {
        console.warn('[d19-demo-ensure]', e);
        toast(e?.message || '演示数据初始化失败', 'error');
      }
    }

    state.settings = settingsRes.settings || {};
    state.dashboard = dashboardRes;
    state.orders = ordersRes.items || [];
    buildQueue();
  }

  function mergeSettings(patch) {
    state.settings = {...state.settings, ...patch};
    clearTimeout(state.saveSettingsTimer);
    state.saveSettingsTimer = setTimeout(async () => {
      try { await request('/api/settings', {method:'PUT', body:JSON.stringify({settings:state.settings})}); }
      catch (e) { toast(`偏好保存失败：${e.message}`); }
    }, 250);
  }

  function buildQueue() {
    const items = state.dashboard?.items || [];
    const byOrder = new Map();
    for (const task of items) {
      if (!task.order || task.action_state === 'DONE') continue;
      if (task.action_state === 'WAITING_EXTERNAL') continue;
      const oid = task.order.order_id;
      const current = byOrder.get(oid);
      const score = Number(task.priority_score || 0) + (stateRank[task.action_state] || 0) + (riskLevelRank[String(task.risk_level || 'none').toLowerCase()] || 0) * 20;
      if (!current || score > current.__score) byOrder.set(oid, {...task, __score:score});
    }
    let queue = [...byOrder.values()].sort((a,b) => b.__score - a.__score);
    const pref = Array.isArray(state.settings.d19_manual_order_ids) ? state.settings.d19_manual_order_ids : [];
    if (pref.length) {
      const rank = new Map(pref.map((id, idx) => [id, idx]));
      queue.sort((a,b) => {
        const ar = rank.has(a.order.order_id) ? rank.get(a.order.order_id) : 9999;
        const br = rank.has(b.order.order_id) ? rank.get(b.order.order_id) : 9999;
        return ar === br ? b.__score - a.__score : ar - br;
      });
    }
    state.queue = queue;
    state.waiting = items.filter((t) => t.action_state === 'WAITING_EXTERNAL' && t.order);
  }

  function navButton(view) { return $(`.nav-item[data-view="${view}"]`); }

  async function navigate(view) {
    const btn = navButton(view);
    if (btn?.classList.contains('role-hidden')) view = 'today';
    state.view = view;
    $$('.nav-item').forEach((el) => el.classList.toggle('active', el.dataset.view === view));
    $$('.view').forEach((el) => el.classList.toggle('active', el.id === `view-${view}`));
    if ($('#pageTitle')) $('#pageTitle').textContent = pageTitles[view] || 'FlowOrder';
    try {
      if (view === 'today') await renderToday();
      if (view === 'confirm') await renderConfirm();
      if (view === 'orders') await renderOrders();
      if (view === 'review') await renderReview();
      if (view === 'exceptions') await renderExceptions();
      if (view === 'agent') await renderAgentTrace();
      if (view === 'settings') await renderSettings();
    } catch (e) { toast(e.message); }
  }

  function taskReason(task) {
    const reasons = task.priority_reasons || [];
    return reasons[0] || task.recommended_action || task.title || '需要跟进';
  }

  function queueRow(task, idx, compact = false) {
    const o = task.order || {};
    const stateName = task.action_state || 'DO_TODAY';
    const due = task.next_action_at || task.business_deadline || task.promised_reply_at;
    if (compact) {
      return `<button class="order-row draggable-order" draggable="true" data-queue-id="${esc(o.order_id)}" data-order="${esc(o.order_id)}">
        <span class="drag-handle" title="拖动调整顺序">⋮⋮</span><span class="rank">${idx+1}</span>
        <span class="order-main"><strong>${esc(o.order_no || o.order_id)} · ${esc(o.customer_name || '未知客户')}</strong><small>${esc(taskReason(task))}</small></span>
        <span class="order-side"><span class="pill ${statePill[stateName] || 'pill-blue'}">${esc(stateLabel[stateName] || stateName)}</span><small>${esc(task.risk_level || 'none')} · ${esc(relativeTime(due))}</small></span>
      </button>`;
    }
    return `<button class="queue-row draggable-queue-row" draggable="true" data-queue-id="${esc(o.order_id)}" data-order="${esc(o.order_id)}">
      <span class="queue-drag-handle" title="拖动调整顺序">⋮⋮</span><span>${idx+1}</span>
      <div><strong>${esc(o.order_no || o.order_id)} · ${esc(o.customer_name || '未知客户')}</strong><small>${esc(taskReason(task))}</small></div>
      <span class="pill ${statePill[stateName] || 'pill-blue'}">${esc(stateLabel[stateName] || stateName)}</span>
    </button>`;
  }

  function bindQueueDnD(container) {
    if (!container) return;
    let draggedId = null;
    container.querySelectorAll('[data-queue-id]').forEach((row) => {
      row.addEventListener('dragstart', (e) => { draggedId = row.dataset.queueId; row.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; });
      row.addEventListener('dragend', () => row.classList.remove('dragging'));
      row.addEventListener('dragover', (e) => { e.preventDefault(); row.classList.add('drag-over'); });
      row.addEventListener('dragleave', () => row.classList.remove('drag-over'));
      row.addEventListener('drop', (e) => {
        e.preventDefault(); row.classList.remove('drag-over');
        const targetId = row.dataset.queueId;
        if (!draggedId || draggedId === targetId) return;
        const from = state.queue.findIndex((x) => x.order.order_id === draggedId);
        let to = state.queue.findIndex((x) => x.order.order_id === targetId);
        const [moved] = state.queue.splice(from,1);
        const rect = row.getBoundingClientRect();
        if (e.clientY > rect.top + rect.height/2) to += 1;
        state.queue.splice(Math.max(0, Math.min(to, state.queue.length)), 0, moved);
        mergeSettings({d19_manual_order_ids:state.queue.map((x) => x.order.order_id)});
        renderQueueViews();
      });
    });
  }

  function bindOrderOpen(root = document) {
    $$('[data-order]', root).forEach((el) => {
      if (el.dataset.boundOrder) return;
      el.dataset.boundOrder = '1';
      el.addEventListener('click', (e) => {
        if (e.target.closest('.drag-handle,.queue-drag-handle')) return;
        openOrderDrawer(el.dataset.order);
      });
    });
  }

  function renderQueueViews() {
    const p = $('#priorityList'), full = $('#fullQueueList');
    if (p) p.innerHTML = state.queue.slice(0,5).map((x,i) => queueRow(x,i,true)).join('') || '<div class="empty-state"><strong>当前没有需要立即处理的订单</strong></div>';
    if (full) full.innerHTML = state.queue.map((x,i) => queueRow(x,i,false)).join('') || '<div class="empty-state"><strong>当前行动队列为空</strong></div>';
    bindQueueDnD(p); bindQueueDnD(full); bindOrderOpen(p); bindOrderOpen(full);
    const footer = $('.list-footer > span'); if (footer) footer.textContent = `还有 ${Math.max(0, state.queue.length - 5)} 项今日行动`;
    const title = $('#view-today .section-head p'); if (title) title.textContent = `完整待办行动队列 ${state.queue.length} 项；首页先展示当前注意力窗口。`;
    const pills = $$('#view-today .pills .pill');
    if (pills[0]) pills[0].textContent = `今日行动 ${state.queue.length}`;
    if (pills[1]) pills[1].textContent = `高优先 ${state.queue.filter((x)=>['high','critical'].includes(String(x.risk_level||'').toLowerCase()) || ['DO_NOW','ESCALATE'].includes(x.action_state)).length}`;
  }

  async function renderToday() {
    state.dashboard = await request('/api/dashboard');
    if (!state.orders.length) state.orders = (await request('/api/orders')).items || [];
    buildQueue(); renderQueueViews();
    const waitingHead = $('#view-today .waiting-head');
    let waitingCard = $('#view-today .waiting-card');
    if (!state.waiting.length) {
      if (waitingCard) waitingCard.outerHTML = '<div class="waiting-card"><span class="pill pill-green">等待</span><div><strong>当前没有等待中的订单</strong><small>新的外部等待会自动出现在这里。</small></div></div>';
    } else {
      const t = state.waiting[0], o = t.order;
      if (waitingCard) {
        waitingCard.outerHTML = `<button class="waiting-card waiting-card-button" data-order="${esc(o.order_id)}"><span class="pill pill-green">等待外部</span><div><strong>${esc(o.order_no)} · ${esc(t.title)}</strong><small>${esc(t.promised_reply_at ? `约定 ${formatTime(t.promised_reply_at)} 检查` : '等待外部回复')}</small></div></button>`;
        bindOrderOpen($('#view-today'));
      }
    }
    try {
      const summary = await request('/api/d19/review-summary');
      const change = summary.changes?.[0];
      const alert = $('#view-today .change-alert');
      if (alert) alert.innerHTML = change ? `<div class="change-title">${esc(change.order_no)}：${esc(change.text)}</div><div class="change-desc">风险等级：${esc(change.risk_level || '—')}</div><div class="change-link">→ 查看订单上下文与下一步</div>` : '<div class="change-title">当前没有新的关键变化</div><div class="change-desc">系统会持续根据订单事实更新行动优先级。</div>';
    } catch {}
  }

  function sourceToIntake(source) {
    if (source === '供应商消息') return {source_channel:'supplier_message', sender_role:'factory'};
    if (source === '客户消息') return {source_channel:'customer_message', sender_role:'customer'};
    if (source === '电话记录') return {source_channel:'phone', sender_role:'factory'};
    if (source === 'ERP') return {source_channel:'erp', sender_role:'internal'};
    return {source_channel:'manual', sender_role:'customer'};
  }

  function candidateSummary(candidate = {}) {
    const fields = Array.isArray(candidate.fields) ? candidate.fields : [];
    const risks = Array.isArray(candidate.risk_signals) ? candidate.risk_signals : [];
    const actions = Array.isArray(candidate.action_candidates) ? candidate.action_candidates : [];
    const fact = fields.length ? fields.map((f) => `${f.field_name}: ${f.old_value ?? '—'} → ${f.normalized_value ?? '—'}`).join('；') : '已识别消息，但没有可直接写入的结构化字段';
    const certainty = fields.some((f)=>Number(f.confidence||1)<0.8) ? '存在低置信度字段，需要人工核对' : '候选结果仍需人工确认后才能改变正式业务状态';
    const impact = risks.length ? risks.map((r)=>`${r.risk_level || ''} ${r.evidence || r.type || ''}`).join('；') : '未识别到明确新增风险';
    const suggestion = actions[0]?.recommended_action || actions[0]?.title || '查看候选事实并决定是否采用';
    return {fact, certainty, impact, suggestion};
  }

  async function pollIntake(jobId, timeoutMs = 90000) {
    const start = Date.now();
    while (Date.now() - start < timeoutMs) {
      const job = await request(`/api/intake/jobs/${encodeURIComponent(jobId)}`);
      if (job.status === 'COMPLETED') return job.result;
      if (job.status === 'FAILED') throw new Error(job.error?.message || job.progress_message || '识别失败');
      await sleep(550);
    }
    throw new Error('识别仍在后台进行，请稍后到待确认页面查看');
  }

  async function analyzeCopilot() {
    const input = $('#copilotInput');
    const text = input?.value.trim();
    if (!text) return toast('请先输入或说出一条新信息。');
    const btn = $('#analyzeBtn'); setLoading(btn,true,'分析中…');
    try {
      const source = sourceToIntake(state.selectedSource);
      const queued = await request('/api/intake/jobs', {method:'POST', body:JSON.stringify({raw_content:text, ...source})});
      const result = await pollIntake(queued.job_id);
      const summary = candidateSummary(result.candidate || {});
      $('#analysisSourceBadge').textContent = state.selectedSource || '手工补充';
      $('#analysisFact').textContent = summary.fact;
      $('#analysisCertainty').textContent = summary.certainty;
      $('#analysisImpact').textContent = summary.impact;
      $('#analysisSuggestion').value = summary.suggestion;
      $('#copilotAnalysisResult').dataset.reviewId = result.review_id || '';
      $('#copilotAnalysisResult').classList.remove('hidden');
    } catch (e) { toast(e.message); }
    finally { setLoading(btn,false); }
  }

  function inspectUncertainty(text, hintEl) {
    const hits = uncertaintyWords.filter((w) => text.includes(w));
    if (!hintEl) return;
    if (hits.length) {
      hintEl.innerHTML = `检测到可能存在不确定表达：<strong>${[...new Set(hits)].join('、')}</strong>。系统不会自动把它升级为正式承诺。`;
      hintEl.classList.remove('hidden');
    } else hintEl.classList.add('hidden');
  }

  async function runAgentQuestion(question, orderId = null) {
    const q = orderId ? `${question}。当前订单ID：${orderId}` : question;
    const created = await request('/api/agent/chat/jobs', {method:'POST', body:JSON.stringify({question:q, top_n:7})});
    const start = Date.now();
    while (Date.now() - start < 90000) {
      const job = await request(`/api/agent/chat/jobs/${encodeURIComponent(created.job_id)}`);
      if (job.status === 'COMPLETED') return job.result || {};
      if (job.status === 'FAILED') throw new Error(job.error_message || 'Agent 运行失败');
      await sleep(650);
    }
    throw new Error('Agent 仍在后台运行，请稍后到 Agent运行 查看');
  }

  function candidateRequiresManagerFrontend(item) {
    const fields = Array.isArray(item?.candidate?.fields) ? item.candidate.fields : [];
    const managerFields = new Set(['requested_delivery_date','customer_delivery_date','formal_delivery_date','formal_customer_commitment','customer_commitment']);
    return fields.some((f) => managerFields.has(String(f.field_name || '')) && String(f.old_value ?? '') !== String(f.normalized_value ?? ''));
  }

  function confirmationSeverity(item) {
    if (item.status === 'APPROVAL_PENDING') return 'high';
    const c = item.candidate || {};
    const risks = Array.isArray(c.risk_signals) ? c.risk_signals : [];
    if (risks.some((r)=>['high','critical'].includes(String(r.risk_level||'').toLowerCase()))) return 'high';
    const fields = Array.isArray(c.fields) ? c.fields : [];
    const formal = fields.some((f)=>['requested_delivery_date','customer_delivery_date','formal_delivery_date','formal_customer_commitment','customer_commitment'].includes(String(f.field_name||'')) && String(f.old_value??'') !== String(f.normalized_value??''));
    if (formal) return 'high';
    if (fields.length >= 2) return 'important';
    return 'normal';
  }

  function reviewDescription(item) {
    const c = item.candidate || {};
    const fields = Array.isArray(c.fields) ? c.fields : [];
    if (fields.length) return fields.map((f)=>`${f.field_name}: ${f.old_value ?? '—'} → ${f.normalized_value ?? '—'}`).join('；');
    return item.raw_content || '存在一条需要人工确认的候选变化。';
  }

  function reviewCard(item) {
    const risk = confirmationSeverity(item);
    const pendingApproval = item.status === 'APPROVAL_PENDING';
    const isManager = ['manager','admin'].includes(state.profile?.ui_role);
    const managerRequired = candidateRequiresManagerFrontend(item);
    const badge = pendingApproval ? '<span class="confirm-risk approval-badge">审批中</span>' : managerRequired ? '<span class="confirm-risk risk-high">高风险 · 需主管审批</span>' : risk === 'high' ? '<span class="confirm-risk risk-high">高风险 · 需单独确认</span>' : risk === 'important' ? '<span class="confirm-risk risk-important">重要</span>' : '<span class="confirm-risk risk-normal">普通</span>';
    const confirmLabel = pendingApproval ? (isManager ? '主管确认' : '审批中') : managerRequired ? (isManager ? '主管确认' : '提交主管审批') : risk === 'high' ? '单独确认' : '确认并继续';
    return `<div class="confirm-card selectable ${risk==='high'?'high-risk':''} ${pendingApproval?'approval-pending':''}" data-review-id="${esc(item.review_id)}" data-order-id="${esc(item.order_id || '')}" data-risk="${risk}" data-manager-required="${managerRequired?'1':'0'}" data-status="${esc(item.status)}">
      <label class="confirm-select"><input class="confirm-checkbox" type="checkbox" ${pendingApproval&&!isManager?'disabled':''}></label>
      <div class="confirm-content"><span class="pill ${risk==='high'?'pill-amber':'pill-red-soft'}">${pendingApproval?'主管审批':'待确认'}</span>${badge}<h3>${esc(item.order_no || item.order_id || '未匹配订单')} · ${esc(item.customer_name || '待确认')}</h3><p>${esc(reviewDescription(item))}</p><small>来源：${esc(item.source_channel || item.workflow_source || '业务信息')}</small></div>
      <div class="confirm-actions"><button class="btn btn-ghost single-reject" ${pendingApproval&&!isManager?'disabled':''}>${pendingApproval&&isManager?'主管拒绝':'暂不采用'}</button><button class="btn btn-primary single-confirm" ${pendingApproval&&!isManager?'disabled':''}>${confirmLabel}</button></div>
    </div>`;
  }

  function d12Card(item) {
    const isManager = ['manager','admin'].includes(state.profile?.ui_role);
    return `<div class="confirm-card selectable high-risk" data-d12-id="${esc(item.review_id)}" data-risk="high"><label class="confirm-select"><input class="confirm-checkbox" type="checkbox" disabled></label><div class="confirm-content"><span class="pill pill-amber">正式动作审批</span><span class="confirm-risk risk-high">Human Review</span><h3>${esc(item.target_id || item.task_id || item.review_id)}</h3><p>${esc(item.reason || item.action_type || '关键业务动作需要主管审批')}</p><small>${esc(item.action_type || '')}</small></div><div class="confirm-actions"><button class="btn btn-ghost d12-reject" ${isManager?'':'disabled'}>拒绝</button><button class="btn btn-primary d12-confirm" ${isManager?'':'disabled'}>${isManager?'批准并继续':'等待主管'}</button></div></div>`;
  }

  async function renderConfirm() {
    const [candidateRes, d12Res] = await Promise.all([request('/api/reviews?status=ALL'), request('/api/d12/reviews?status=PENDING')]);
    state.reviews = (candidateRes.items || []).filter((r)=>['PENDING','APPROVAL_PENDING'].includes(r.status));
    state.d12Reviews = d12Res.items || [];
    $('#confirmList').innerHTML = state.reviews.map(reviewCard).join('') + state.d12Reviews.map(d12Card).join('');
    $('#confirmEmpty').classList.toggle('hidden', state.reviews.length + state.d12Reviews.length > 0);
    $('#confirmPendingCount').textContent = state.reviews.filter((r)=>r.status==='PENDING').length;
    $('#approvalPendingCount').textContent = state.reviews.filter((r)=>r.status==='APPROVAL_PENDING').length + state.d12Reviews.length;
    const badge = navButton('confirm')?.querySelector('.badge'); if (badge) { const n=state.reviews.filter((r)=>r.status==='PENDING').length; badge.textContent=n; badge.classList.toggle('hidden',n===0); }
    refreshConfirmSelection();
  }

  function selectedReviewCards() { return $$('#confirmList .confirm-card').filter((c)=>c.querySelector('.confirm-checkbox')?.checked); }
  function refreshConfirmSelection() {
    const selected = selectedReviewCards();
    $('#selectedConfirmCount').textContent = `已选 ${selected.length} 项`;
    $('#bulkConfirm').disabled = !selected.length;
    $('#bulkReject').disabled = !selected.length;
    const counts = {normal:0,important:0,high:0}; selected.forEach((c)=>counts[c.dataset.risk || 'normal']++);
    $('#normalSelected').textContent=counts.normal; $('#importantSelected').textContent=counts.important; $('#highSelected').textContent=counts.high;
  }

  function showSingleRisk(card) {
    const item = state.reviews.find((r)=>r.review_id===card.dataset.reviewId);
    $('#singleRiskTitle').textContent = `${item?.order_no || '高风险事项'} · ${item?.status==='APPROVAL_PENDING'?'主管审批':'高风险确认'}`;
    $('#singleRiskDesc').textContent = reviewDescription(item || {});
    const managerRequired = card.dataset.managerRequired === '1' || item?.status === 'APPROVAL_PENDING';
    $('#singleRiskBody').textContent = item?.status==='APPROVAL_PENDING' ? '主管确认后才会继续候选确认流程；普通跟单员不能绕过审批状态。' : managerRequired ? '该事项涉及正式业务事实变更。提交后将进入主管审批，不会直接生效。' : '该事项风险较高，不能与普通事项一起静默批量确认。请单独检查影响范围后再确认。';
    $('#singleRiskModal').dataset.reviewId = card.dataset.reviewId;
    $('#singleRiskModal').dataset.managerRequired = managerRequired ? '1' : '0';
    $('#singleRiskBackdrop').classList.remove('hidden'); $('#singleRiskModal').classList.remove('hidden');
    $('#submitSingleRisk').textContent = managerRequired ? (['manager','admin'].includes(state.profile?.ui_role) ? '主管确认' : '提交主管审批') : '我已检查，确认并继续';
  }

  function closeSingleRisk() { $('#singleRiskBackdrop').classList.add('hidden'); $('#singleRiskModal').classList.add('hidden'); }

  async function confirmCandidate(reviewId) {
    await request(`/api/reviews/${encodeURIComponent(reviewId)}/confirm`, {method:'POST', body:JSON.stringify({})});
  }
  async function rejectCandidate(reviewId) {
    await request(`/api/reviews/${encodeURIComponent(reviewId)}/reject`, {method:'POST', body:JSON.stringify({reason:'user_rejected'})});
  }
  async function submitManager(reviewId) {
    await request(`/api/d19/reviews/${encodeURIComponent(reviewId)}/submit-manager-review`, {method:'POST', body:'{}'});
  }

  function classifyOrderGroup(o) {
    if ((riskLevelRank[String(o.max_risk||'none').toLowerCase()]||0) >= 2 || o.action_readiness === 'NEEDS_STATUS') return 'attention';
    if (Number(o.waiting_task_count || 0) > 0) return 'waiting';
    return 'normal';
  }

  function stageLabel(node) {
    const x=String(node||'');
    if (/出货|物流|发货/.test(x)) return '出货';
    if (/生产|裁剪|加工|印刷/.test(x)) return '生产';
    if (/备|采购|物料/.test(x)) return '备货';
    if (/交付|完成|签收/.test(x)) return '交付';
    return '接单';
  }

  function orderGroupRow(o) {
    const group=classifyOrderGroup(o);
    const status = group==='attention' ? (['high','critical'].includes(String(o.max_risk)) ? '需要关注' : '待处理') : group==='waiting' ? '等待反馈' : '正常';
    const pill = group==='attention'?'pill-red':group==='waiting'?'pill-green':'pill-blue';
    const desc = group==='attention' ? (o.risk_count ? `${o.risk_count} 个开放风险 · ${o.open_task_count} 个待办` : '需要补充最新进展') : group==='waiting' ? `${o.waiting_task_count} 个等待中的事项` : '按当前计划推进';
    return `<button class="group-order" data-order="${esc(o.order_id)}" data-group="${group}" data-stage="${esc(stageLabel(o.current_node))}" data-owner="${esc(o.owner||'')}" data-search="${esc([o.order_no,o.customer_name,o.product_name,o.current_node,desc].join(' '))}"><span><strong>${esc(o.order_no||o.order_id)}</strong><small>${esc(o.customer_name||'未知客户')}</small></span><span>${esc(stageLabel(o.current_node))}</span><span>${esc(desc)}</span><span class="pill ${pill}">${status}</span></button>`;
  }

  async function renderOrders() {
    const res=await request('/api/orders'); state.orders=res.items||[];
    const title=$('#view-orders .orders-toolbar p'); if(title) title.textContent=`共 ${state.orders.length} 张进行中订单`;
    const groups={attention:[],waiting:[],normal:[]}; state.orders.forEach((o)=>groups[classifyOrderGroup(o)].push(o));
    ['attention','waiting','normal'].forEach((g)=>{
      const section=$(`#view-orders .order-group[data-group="${g}"]`); if(!section)return;
      section.dataset.total=String(groups[g].length);
      section.querySelector('.group-count').textContent=`${groups[g].length} 张`;
      const body=section.querySelector('.group-body'); body.innerHTML=groups[g].map(orderGroupRow).join('') || '<div class="collapsed-note">当前没有订单</div>';
    });
    const ownerSelect=$('#filterOwner'); if(ownerSelect){const cur=ownerSelect.value;ownerSelect.innerHTML='<option value="">全部</option>'+[...new Set(state.orders.map((o)=>o.owner).filter(Boolean))].map((x)=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');ownerSelect.value=cur;}
    bindOrderOpen($('#view-orders')); applyOrderFilters();
  }

  function applyOrderFilters() {
    const q=($('#ordersSearch')?.value||'').trim().toLowerCase(); const stage=$('#filterStage')?.value||''; const status=$('#filterStatus')?.value||''; const owner=$('#filterOwner')?.value||'';
    let total=0;
    $$('#view-orders .order-group').forEach((section)=>{
      let visible=0; $$('.group-order',section).forEach((row)=>{const show=(!q||String(row.dataset.search||'').toLowerCase().includes(q))&&(!stage||row.dataset.stage===stage)&&(!status||row.dataset.group===status)&&(!owner||row.dataset.owner===owner);row.classList.toggle('search-hidden',!show);if(show)visible++;});
      total+=visible; section.querySelector('.group-count').textContent=(q||stage||status||owner)?`${visible} / ${section.dataset.total} 张`:`${section.dataset.total} 张`;
    }); $('#ordersEmpty').classList.toggle('hidden',total>0);
  }

  function stageIndex(node) { return stageOrder.indexOf(stageLabel(node)); }

  async function openOrderDrawer(orderId) {
    try {
      const detail = await request(`/api/orders/${encodeURIComponent(orderId)}`);
      state.currentOrderDetail=detail; state.currentOrder=detail.order;
      const o=detail.order||{}; const tasks=(detail.tasks||[]).filter((t)=>t.status!=='DONE'); const risks=detail.risks||[]; const events=detail.events||[];
      state.currentTask=tasks.slice().sort((a,b)=>(riskLevelRank[String(b.risk_level||'none')]||0)-(riskLevelRank[String(a.risk_level||'none')]||0))[0]||null;
      $('#drawerTitle').textContent=`${o.order_no||o.order_id} · ${o.customer_name||'未知客户'}`; $('#drawerStatus').textContent=o.status||'订单详情'; $('#drawerDueDate').textContent=formatDate(o.requested_delivery_date); $('#drawerStage').textContent=stageLabel(o.current_node); $('#drawerAttention').textContent=risks[0]?.evidence|| (tasks[0]?.title||'正常推进');
      const idx=Math.max(0,stageIndex(o.current_node)); $('#drawerProcessProgress').classList.remove('hidden'); $('#drawerProcessStageLabel').textContent=`当前节点：${stageOrder[idx]}`; $('#drawerProcessStepLabel').textContent=`${idx+1} / 5`; $('#drawerProcessFill').style.width=`${(idx+1)*20}%`; $$('.process-progress-steps span').forEach((el,i)=>{el.classList.toggle('done',i<idx);el.classList.toggle('current',i===idx);});
      const currentTask=state.currentTask; const latest=events[0];
      $('#drawerTimeline').classList.remove('hidden'); $('#drawerTimelineTitle').classList.remove('hidden'); $('#drawerDataNotice').classList.add('hidden'); $('#drawerTimelineStage').textContent=stageOrder[idx]; $('#drawerCurrentState').textContent=`${stageOrder[idx]} · ${risks[0]?.risk_level?`${risks[0].risk_level}风险`:'进行中'}`; $('#drawerLatestEvent').textContent=latest ? `${latest.event_type || '更新'} · ${formatTime(latest.created_at)}` : (tasks[0]?.title||'暂无新增事件'); $('#drawerWhyAttention').textContent=risks[0]?.evidence || currentTask?.evidence?.[0] || currentTask?.evidence_json || '当前没有新增高风险事实，按现有计划推进。'; $('#drawerSuggestedAction').textContent=currentTask?.recommended_action || currentTask?.title || '继续按当前计划推进';
      const wait=tasks.find((t)=>t.waiting_on); $('#drawerWaitingNote').textContent=wait ? `等待对象：${wait.waiting_on}；下一检查：${formatTime(wait.promised_reply_at||wait.next_action_at)}` : '当前未进入等待；记录已联系后可设置等待对象和下一检查时间。';
      $('#draftEditor').classList.add('hidden'); $('#waitingConfig').classList.add('hidden'); $('#drawerAnalysisResult').classList.add('hidden');
      $('#drawerBackdrop').classList.remove('hidden'); $('#orderDrawer').classList.add('open');
    } catch(e) { toast(e.message); }
  }

  function closeDrawer(){ $('#drawerBackdrop').classList.add('hidden'); $('#orderDrawer').classList.remove('open'); }

  async function generateDraft() {
    if(!state.currentOrder) return;
    const btn=$('#generateDraftBtn');setLoading(btn,true,'生成中…');
    try{const result=await runAgentQuestion(`请为订单 ${state.currentOrder.order_no} 根据当前最新事实起草一条简洁的供应商或客户跟进消息，只输出可编辑草稿。`, state.currentOrder.order_id); const answer=result.answer || result.message || result.diagnosis?.summary || '您好，想跟您确认一下当前订单的最新进度和预计完成时间，烦请确认后回复，谢谢。'; $('#draftText').value=String(answer);$('#draftEditor').classList.remove('hidden');}
    catch(e){toast(e.message);}finally{setLoading(btn,false);}
  }

  async function recordContact() {
    if(!state.currentTask) return toast('当前订单没有可记录联系状态的开放任务。');
    $('#waitingConfig').classList.remove('hidden');
  }

  async function saveWaiting() {
    if(!state.currentTask) return;
    const party=$('#waitingParty').value; const dt=$('#waitingUntil').value;
    if(!dt) return toast('请设置下一检查时间。');
    const target=party==='客户'?'customer':party==='供应商'?'factory':'internal';
    const btn=$('#saveWaiting');setLoading(btn,true,'保存中…');
    try{await request(`/api/tasks/${encodeURIComponent(state.currentTask.task_id)}/contacted`,{method:'POST',body:JSON.stringify({waiting_on:target,promised_reply_at:new Date(dt).toISOString()})});toast('已记录联系并进入等待。');await openOrderDrawer(state.currentOrder.order_id);}
    catch(e){toast(e.message);}finally{setLoading(btn,false);}
  }

  function reviewTone(risk){return ['critical','high'].includes(String(risk||'').toLowerCase())?'pill-red':String(risk).toLowerCase()==='medium'?'pill-amber':'pill-green';}

  async function renderReview() {
    const summary=await request('/api/d19/review-summary'); state.reviewSummary=summary;
    const stats=$$('#view-review .compact-stat strong'); const vals=[summary.today_completed,summary.waiting,summary.unclosed,summary.key_changes]; stats.forEach((el,i)=>{if(vals[i]!==undefined)el.textContent=vals[i];});
    const trend=$('#view-review .trend-card'); if(trend){const data=summary.daily_handled||[];const max=Math.max(1,...data.map((x)=>Number(x.count||0)));const pts=data.map((x,i)=>`${18+i*47},${58-(Number(x.count||0)/max)*38}`).join(' ');trend.innerHTML=`<div class="trend-card-head"><small>近 5 天处理</small><strong>${data.reduce((a,b)=>a+Number(b.count||0),0)}</strong></div><svg class="trend-chart" viewBox="0 0 220 76"><line x1="14" y1="58" x2="208" y2="58" class="trend-grid-line"/><line x1="14" y1="35" x2="208" y2="35" class="trend-grid-line"/><polyline points="${pts}" class="trend-line"/>${data.map((x,i)=>`<circle cx="${18+i*47}" cy="${58-(Number(x.count||0)/max)*38}" r="3.5" class="trend-point"><title>${esc(x.date.slice(5))}：${Number(x.count||0)} 单</title></circle><text x="${18+i*47}" y="72" text-anchor="middle">${esc(x.date.slice(-2))}</text>`).join('')}</svg>`;bindTrendTooltips();}
    const list=$('#view-review .review-list-v4');if(list)list.innerHTML=(summary.unclosed_orders||[]).map((x)=>`<button class="review-item review-order-item" data-order="${esc(x.order_id)}"><strong>${esc(x.order_no)}</strong><span>${esc(x.title)}</span><span class="pill ${x.waiting_on?'pill-green':reviewTone(x.risk_level)}">${x.waiting_on?'等待中':'待处理'}</span></button>`).join('')||'<div class="empty-state"><strong>今天没有未收口订单</strong></div>';
    const changes=$('#view-review .changes-card-v4');if(changes)changes.innerHTML=(summary.changes||[]).map((x)=>`<button class="review-change-row" data-order="${esc(x.order_id)}">• ${esc(x.order_no)}：${esc(x.text)}</button>`).join('')||'<p>今天没有新的关键变化。</p>';bindOrderOpen($('#view-review'));renderTomorrowPlans();
  }

  function renderTomorrowPlans(){const list=$('#tomorrowPlanList');if(!list)return;const plans=Array.isArray(state.settings.d19_tomorrow_plan)?state.settings.d19_tomorrow_plan:[];const seed=plans.length?plans:['上午确认最高优先订单的最新进度','10:00 检查今日未收口事项','15:00 检查等待回复是否到期'];list.innerHTML=seed.map((x)=>`<div class="plan-edit-row"><input type="checkbox"><input class="plan-text" type="text" value="${esc(typeof x==='string'?x:x.text||'')}"><button class="plan-delete" title="删除">×</button></div>`).join('');}

  async function renderExceptions(){const [obs,erp]=await Promise.all([request('/api/d16/observability/summary'),request('/api/integrations/erpnext/status')]);const cards=$('#view-exceptions .system-cards');const uncertain=obs.d15_execution?.result_uncertain||0,human=obs.d15_execution?.human_required||0;cards.innerHTML=`<div class="system-card ${uncertain?'danger-card':''}"><div><span class="state-dot state-red"></span><strong>RESULT_UNCERTAIN</strong></div><h3>${uncertain} 项结果待核对</h3><p>${uncertain?'自动重试已暂停。请先核对外部动作结果，避免重复副作用。':'当前没有结果未知的外部动作。'}</p></div><div class="system-card warning-card"><div><span class="state-dot state-amber"></span><strong>ERP 数据状态</strong></div><h3>${esc(erp.freshness||'UNKNOWN')}</h3><p>${erp.configured?'已连接只读 ERP，同步状态会显示数据新鲜度。':'当前组织尚未配置 ERPNext 连接。'}</p></div><div class="system-card"><div><span class="state-dot state-purple"></span><strong>HUMAN_REQUIRED</strong></div><h3>${human} 项需要人工接管</h3><p>${human?'自动流程已停止，等待主管处理。':'当前没有需要人工接管的执行故障。'}</p></div>`;}

  async function renderAgentTrace(){const overview=await request('/api/agent/overview');const table=$('#view-agent .trace-table');const calls=overview.latest_tool_calls||[];table.innerHTML='<div class="trace-row trace-head"><span>时间</span><span>对象</span><span>动作</span><span>结果</span><span>模型/工具</span></div>'+calls.slice(0,12).map((c)=>`<div class="trace-row"><span>${esc(formatTime(c.created_at))}</span><span>${esc(c.order_id||c.run_id||'—')}</span><span>${esc(c.tool_name||c.action||'受控工具')}</span><span class="${String(c.status).toUpperCase()==='SUCCESS'?'trace-ok':'trace-wait'}">${esc(c.status||'—')}</span><span>${esc(c.provider||c.model||c.tool_name||'Controlled Tool')}</span></div>`).join('')+(calls.length?'':'<div class="trace-row"><span>—</span><span>—</span><span>当前没有最近 Agent Tool 调用</span><span class="trace-ok">正常</span><span>—</span></div>');}

  async function renderSettings(){const flags=await request('/api/d16/flags');const list=$('#view-settings .settings-list');const labels={agent_assist_enabled:['Agent Assist','关闭后人工工作台仍可使用'],attention_dashboard_enabled:['Attention Dashboard','关闭后保留普通订单/工作台'],erp_readonly_sync_enabled:['ERP Read-only Sync','关闭后保留最近快照并显示新鲜度'],external_action_dispatch_enabled:['External Action Dispatch','外部执行能力需单独验收后开启']};list.innerHTML=(flags.items||[]).map((f)=>{const l=labels[f.flag_key]||[f.flag_key,f.safe_off_behavior];const locked=f.flag_key==='external_action_dispatch_enabled'&&!f.effective_enabled;return `<label class="setting-row ${locked?'locked-setting':''}"><div><strong>${esc(l[0])}</strong><span>${esc(l[1])}</span></div><input class="flag-toggle" data-flag="${esc(f.flag_key)}" type="checkbox" ${f.effective_enabled?'checked':''} ${locked?'disabled':''}></label>`;}).join('')+`<label class="setting-row locked-setting"><div><strong>Human Review</strong><span>安全控制，不属于 Feature Flag</span></div><span class="locked-badge">始终开启</span></label>`;}

  function bindTrendTooltips(){let tip=$('#trendTooltip');if(!tip){tip=document.createElement('div');tip.id='trendTooltip';tip.className='trend-tooltip hidden';document.body.appendChild(tip);}$$('.trend-point').forEach((p)=>{const title=p.querySelector('title')?.textContent||'';p.addEventListener('mouseenter',()=>{tip.textContent=title;tip.classList.remove('hidden');});p.addEventListener('mousemove',(e)=>{tip.style.left=`${e.clientX+10}px`;tip.style.top=`${e.clientY+10}px`;});p.addEventListener('mouseleave',()=>tip.classList.add('hidden'));});}

  function setupSpeech(buttonId, textareaId, statusId, hintId){const btn=document.getElementById(buttonId),ta=document.getElementById(textareaId),status=document.getElementById(statusId),hint=document.getElementById(hintId);if(!btn||!ta)return;const SR=window.SpeechRecognition||window.webkitSpeechRecognition;if(!SR){btn.addEventListener('click',()=>toast('当前浏览器不支持语音识别，请使用 Chrome / Edge 或文字输入。'));return;}const rec=new SR();rec.lang='zh-CN';rec.continuous=false;rec.interimResults=true;let listening=false;rec.onstart=()=>{listening=true;btn.classList.add('recording');if(status){status.textContent='● 正在录音…';status.classList.remove('hidden');}};rec.onresult=(e)=>{let final='',interim='';for(let i=e.resultIndex;i<e.results.length;i++){const t=e.results[i][0].transcript;if(e.results[i].isFinal)final+=t;else interim+=t;}ta.value=final||interim;inspectUncertainty(ta.value,hint);};rec.onend=()=>{listening=false;btn.classList.remove('recording');if(status){status.textContent='✓ 已转成文字，可修改后继续。';status.classList.remove('hidden');}};rec.onerror=()=>toast('语音识别失败，可继续文字输入。');btn.addEventListener('click',()=>listening?rec.stop():rec.start());}

  function applyGlobalSearch(){const q=($('#globalSearch')?.value||'').trim().toLowerCase();let selector='';if(state.view==='today')selector='#view-today .order-row,#view-today .waiting-card';if(state.view==='confirm')selector='#view-confirm .confirm-card';if(state.view==='review')selector='#view-review .review-order-item,#view-review .review-change-row';if(!selector)return;let visible=0;$$(selector).forEach((el)=>{const show=!q||el.innerText.toLowerCase().includes(q);el.classList.toggle('search-hidden',!show);if(show)visible++;});}

  function toggleGroup(btn){const section=btn.closest('.order-group'),body=section?.querySelector('.group-body');if(!section||!body)return;const expanded=btn.getAttribute('aria-expanded')==='true';btn.setAttribute('aria-expanded',String(!expanded));body.classList.toggle('hidden',expanded);section.classList.toggle('collapsed',expanded);btn.querySelector('.group-chevron').textContent=expanded?'›':'⌄';}

  // Login / account
  $('#loginForm')?.addEventListener('submit',async(e)=>{e.preventDefault();const btn=$('#loginSubmit'),u=$('#loginUsername').value.trim(),p=$('#loginPassword').value;$('#loginError').classList.add('hidden');setLoading(btn,true,'登录中…');try{await login(u,p);}catch(err){$('#loginError').textContent=err.message;$('#loginError').classList.remove('hidden');}finally{setLoading(btn,false);}});
  $('#togglePassword')?.addEventListener('click',()=>{const input=$('#loginPassword');const show=input.type==='password';input.type=show?'text':'password';$('#togglePassword').textContent=show?'隐藏':'显示';});
  $$('.demo-account').forEach((b)=>b.addEventListener('click',()=>{$('#loginUsername').value=b.dataset.demoUser;$('#loginPassword').value='demo123';$('#loginPassword').focus();}));
  $('#accountButton')?.addEventListener('click',(e)=>{e.stopPropagation();const menu=$('#accountMenu'),open=menu.classList.contains('hidden');menu.classList.toggle('hidden');$('#accountButton').setAttribute('aria-expanded',String(open));});
  $('#logoutButton')?.addEventListener('click',()=>logout());
  $('#profileAction')?.addEventListener('click',()=>toast(`${state.profile?.display_name||''} · ${state.profile?.role_label||''}`));

  // Navigation
  $$('.nav-item').forEach((b)=>b.addEventListener('click',()=>navigate(b.dataset.view)));
  $('#closeDrawer')?.addEventListener('click',closeDrawer);$('#drawerBackdrop')?.addEventListener('click',closeDrawer);
  $('#openFullQueue')?.addEventListener('click',()=>{$('#fullQueueBackdrop').classList.remove('hidden');$('#fullQueueModal').classList.remove('hidden');renderQueueViews();});
  $('#closeFullQueue')?.addEventListener('click',()=>{$('#fullQueueBackdrop').classList.add('hidden');$('#fullQueueModal').classList.add('hidden');});$('#fullQueueBackdrop')?.addEventListener('click',()=>$('#closeFullQueue').click());

  // Copilot
  $$('.quick-action').forEach((b)=>b.addEventListener('click',async()=>{const btn=b;setLoading(btn,true,'正在问 Agent…');try{const r=await runAgentQuestion(b.dataset.prompt||b.textContent);$('#copilotAnalysisResult').classList.remove('hidden');$('#analysisSourceBadge').textContent='Agent';$('#analysisFact').textContent=b.dataset.prompt||b.textContent;$('#analysisCertainty').textContent='以下是 Agent 建议，不会自动改变正式业务事实';$('#analysisImpact').textContent=r.answer||r.message||'Agent 已完成分析';$('#analysisSuggestion').value=r.answer||r.message||'请结合当前订单上下文处理。';}catch(e){toast(e.message);}finally{setLoading(btn,false);}}));
  $('#sourcePickerBtn')?.addEventListener('click',(e)=>{e.stopPropagation();$('#sourcePicker').classList.toggle('hidden');});$$('#sourcePicker [data-source]').forEach((b)=>b.addEventListener('click',()=>{state.selectedSource=b.dataset.source;$('#sourcePickerBtn').textContent=state.selectedSource;$('#sourcePicker').classList.add('hidden');}));
  $('#copilotInput')?.addEventListener('input',(e)=>inspectUncertainty(e.target.value,$('#uncertaintyHint')));$('#analyzeBtn')?.addEventListener('click',analyzeCopilot);$('#rejectAnalysis')?.addEventListener('click',()=>$('#copilotAnalysisResult').classList.add('hidden'));$('#editAnalysis')?.addEventListener('click',()=>{$('#analysisSuggestion').readOnly=false;$('#analysisSuggestion').focus();});$('#adoptAnalysis')?.addEventListener('click',()=>{const id=$('#copilotAnalysisResult').dataset.reviewId;if(id){navigate('confirm');toast('已进入待确认。正式事实仍需人工确认后生效。');}else toast('已保留为当前建议。');});

  // Confirm page delegated actions
  $('#confirmList')?.addEventListener('change',(e)=>{if(e.target.matches('.confirm-checkbox'))refreshConfirmSelection();});
  $('#confirmList')?.addEventListener('click',async(e)=>{const card=e.target.closest('.confirm-card');if(!card)return;try{if(e.target.closest('.single-reject')){if(card.dataset.d12Id){await request(`/api/d12/reviews/${card.dataset.d12Id}/decision`,{method:'POST',body:JSON.stringify({decision:'REJECT'})});}else await rejectCandidate(card.dataset.reviewId);await renderConfirm();return;}if(e.target.closest('.single-confirm')){if(card.dataset.risk==='high' || card.dataset.status==='APPROVAL_PENDING'){showSingleRisk(card);return;}await confirmCandidate(card.dataset.reviewId);await renderConfirm();}if(e.target.closest('.d12-reject')){await request(`/api/d12/reviews/${card.dataset.d12Id}/decision`,{method:'POST',body:JSON.stringify({decision:'REJECT'})});await renderConfirm();}if(e.target.closest('.d12-confirm')){await request(`/api/d12/reviews/${card.dataset.d12Id}/decision`,{method:'POST',body:JSON.stringify({decision:'APPROVE'})});await request(`/api/d12/reviews/${card.dataset.d12Id}/submit`,{method:'POST',body:'{}'});await renderConfirm();}}catch(err){toast(err.message);}});
  $('#selectAllConfirm')?.addEventListener('change',(e)=>{$$('#confirmList .confirm-checkbox:not(:disabled)').forEach((x)=>x.checked=e.target.checked);refreshConfirmSelection();});
  $('#bulkReject')?.addEventListener('click',async()=>{for(const c of selectedReviewCards()){if(c.dataset.reviewId)try{await rejectCandidate(c.dataset.reviewId);}catch(e){toast(e.message);}}await renderConfirm();});
  $('#bulkConfirm')?.addEventListener('click',()=>{const cards=selectedReviewCards();const high=cards.filter((c)=>c.dataset.risk==='high');const normal=cards.filter((c)=>c.dataset.risk!=='high');$('#riskModalList').innerHTML=cards.map((c)=>`<div class="risk-modal-item"><div><strong>${esc(c.querySelector('h3')?.textContent||'')}</strong><span>${esc(c.querySelector('p')?.textContent||'')}</span></div><span class="confirm-risk ${c.dataset.risk==='high'?'risk-high':'risk-important'}">${c.dataset.risk==='high'?'高风险':'可批量'}</span></div>`).join('');$('#riskModalWarning').innerHTML=high.length?`包含 <strong>${high.length}</strong> 项高风险事项，不会被批量静默确认；需要逐条检查后处理。`:'请确认已经检查所选事项的影响范围。';$('#riskConfirmModal').dataset.ids=normal.map((c)=>c.dataset.reviewId).filter(Boolean).join(',');$('#riskModalBackdrop').classList.remove('hidden');$('#riskConfirmModal').classList.remove('hidden');});
  $('#cancelRiskBatch')?.addEventListener('click',()=>{$('#riskModalBackdrop').classList.add('hidden');$('#riskConfirmModal').classList.add('hidden');});$('#riskModalBackdrop')?.addEventListener('click',()=>$('#cancelRiskBatch').click());
  $('#confirmRiskBatch')?.addEventListener('click',async()=>{const ids=($('#riskConfirmModal').dataset.ids||'').split(',').filter(Boolean);const btn=$('#confirmRiskBatch');setLoading(btn,true,'处理中…');try{for(const id of ids)await confirmCandidate(id);$('#cancelRiskBatch').click();await renderConfirm();}catch(e){toast(e.message);}finally{setLoading(btn,false);}});
  $('#cancelSingleRisk')?.addEventListener('click',closeSingleRisk);$('#singleRiskBackdrop')?.addEventListener('click',closeSingleRisk);$('#submitSingleRisk')?.addEventListener('click',async()=>{const id=$('#singleRiskModal').dataset.reviewId,manager=['manager','admin'].includes(state.profile?.ui_role),managerRequired=$('#singleRiskModal').dataset.managerRequired==='1';const btn=$('#submitSingleRisk');setLoading(btn,true,'处理中…');try{if(managerRequired){if(manager)await confirmCandidate(id);else await submitManager(id);}else{await confirmCandidate(id);}closeSingleRisk();await renderConfirm();toast(managerRequired?(manager?'主管确认完成':'已提交主管审批'):'高风险事项已单独确认');}catch(e){toast(e.message);}finally{setLoading(btn,false);}});

  // Orders page
  $('#ordersFilterBtn')?.addEventListener('click',()=>$('#ordersFilterPanel').classList.toggle('hidden'));$('#ordersSearch')?.addEventListener('input',applyOrderFilters);['filterStage','filterStatus','filterOwner'].forEach((id)=>document.getElementById(id)?.addEventListener('change',applyOrderFilters));$('#resetFilters')?.addEventListener('click',()=>{$('#ordersSearch').value='';$('#filterStage').value='';$('#filterStatus').value='';$('#filterOwner').value='';applyOrderFilters();});$('#view-orders')?.addEventListener('click',(e)=>{const t=e.target.closest('.group-toggle');if(t)toggleGroup(t);});

  // Drawer actions
  $('#generateDraftBtn')?.addEventListener('click',generateDraft);$('#recordContactBtn')?.addEventListener('click',recordContact);$('#cancelDraft')?.addEventListener('click',()=>$('#draftEditor').classList.add('hidden'));$('#saveDraft')?.addEventListener('click',()=>toast('草稿已保存；当前不会自动发送消息。'));$('#cancelWaiting')?.addEventListener('click',()=>$('#waitingConfig').classList.add('hidden'));$('#saveWaiting')?.addEventListener('click',saveWaiting);$('#drawerCopilotInput')?.addEventListener('input',(e)=>inspectUncertainty(e.target.value,$('#drawerUncertaintyHint')));$('#clearDrawerInput')?.addEventListener('click',()=>{$('#drawerCopilotInput').value='';$('#drawerAnalysisResult').classList.add('hidden');});$('#analyzeDrawerInfo')?.addEventListener('click',async()=>{const text=$('#drawerCopilotInput').value.trim();if(!text)return toast('请先输入或说出最新进展。');const btn=$('#analyzeDrawerInfo');setLoading(btn,true,'分析中…');try{const queued=await request('/api/intake/jobs',{method:'POST',body:JSON.stringify({raw_content:text,source_channel:'phone',sender_role:'factory',order_id:state.currentOrder?.order_id})});const result=await pollIntake(queued.job_id);const sum=candidateSummary(result.candidate||{});$('#drawerAnalysisFact').textContent=sum.fact;$('#drawerAnalysisImpact').textContent=sum.impact;$('#drawerAnalysisAction').textContent=sum.suggestion;$('#drawerAnalysisResult').classList.remove('hidden');}catch(e){toast(e.message);}finally{setLoading(btn,false);}});

  // Review plans
  $('#addTomorrowPlan')?.addEventListener('click',()=>{$('#tomorrowPlanList').insertAdjacentHTML('beforeend','<div class="plan-edit-row"><input type="checkbox"><input class="plan-text" type="text" value="" placeholder="输入新的明日计划"><button class="plan-delete" title="删除">×</button></div>');$('#tomorrowPlanList .plan-edit-row:last-child .plan-text').focus();});$('#tomorrowPlanList')?.addEventListener('click',(e)=>{if(e.target.closest('.plan-delete'))e.target.closest('.plan-edit-row').remove();});$('#saveTomorrowPlan')?.addEventListener('click',()=>{const plans=$$('#tomorrowPlanList .plan-text').map((x)=>x.value.trim()).filter(Boolean);mergeSettings({d19_tomorrow_plan:plans});toast(`已保存 ${plans.length} 条明日计划。`);});

  // Settings flags
  $('#view-settings')?.addEventListener('change',async(e)=>{const input=e.target.closest('.flag-toggle');if(!input)return;try{await request(`/api/d16/flags/${encodeURIComponent(input.dataset.flag)}`,{method:'PUT',body:JSON.stringify({scope_type:'ORG',enabled:input.checked,rollout_percent:100,reason:'D19 UI manager setting'})});toast(input.checked?'能力已开启':'能力已安全关闭');}catch(err){input.checked=!input.checked;toast(err.message);}});

  // Global search/account menus/escape
  $('#globalSearch')?.addEventListener('input',(e)=>{if(state.view==='orders'){$('#ordersSearch').value=e.target.value;applyOrderFilters();}else applyGlobalSearch();});$('#globalSearch')?.addEventListener('search',()=>{if(!$('#globalSearch').value){$$('.search-hidden').forEach((el)=>el.classList.remove('search-hidden'));}});
  document.addEventListener('click',(e)=>{if(!e.target.closest('#accountButton')&&!e.target.closest('#accountMenu')){$('#accountMenu')?.classList.add('hidden');$('#accountButton')?.setAttribute('aria-expanded','false');}if(!e.target.closest('.source-picker-wrap'))$('#sourcePicker')?.classList.add('hidden');});
  document.addEventListener('keydown',(e)=>{if(e.key!=='Escape')return;closeDrawer();closeSingleRisk();$('#riskModalBackdrop')?.classList.add('hidden');$('#riskConfirmModal')?.classList.add('hidden');$('#fullQueueBackdrop')?.classList.add('hidden');$('#fullQueueModal')?.classList.add('hidden');$('#sourcePicker')?.classList.add('hidden');$('#ordersFilterPanel')?.classList.add('hidden');$('#accountMenu')?.classList.add('hidden');});

  setupSpeech('voiceBtn','copilotInput','voiceStatus','uncertaintyHint');setupSpeech('drawerVoiceBtn','drawerCopilotInput','drawerVoiceStatus','drawerUncertaintyHint');
  restoreSession();
})();
