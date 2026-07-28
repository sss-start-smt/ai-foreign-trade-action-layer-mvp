from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import Response, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

PATCH_VERSION = "3.0.0-unified-action-experience"

GLOBAL_CSS = r"""
:root{
  --uxa-canvas:#F4F4F3;
  --uxa-surface:#FFFFFF;
  --uxa-forest:#092923;
  --uxa-forest-soft:#143A33;
  --uxa-sage:#3F4D44;
  --uxa-bronze:#B38052;
  --uxa-sand:#D0C6B1;
  --uxa-sky:#5096B2;
  --uxa-blue:#2A68B2;
  --uxa-text:#17201D;
  --uxa-muted:#68736E;
  --uxa-line:#DDE2DC;
  --uxa-success:#2EAB4D;
  --uxa-warning:#E1B739;
  --uxa-danger:#B84C45;
  --uxa-radius:20px;
  --uxa-shadow:0 16px 44px rgba(9,41,35,.08);
  --uxa-shadow-soft:0 8px 24px rgba(9,41,35,.06);
  --primary:#092923;--primary-dark:#092923;--primary-2:#3F4D44;--accent:#B38052;
  --bg:#F4F4F3;--background:#F4F4F3;--surface:#FFFFFF;--card:#FFFFFF;
  --text:#17201D;--muted:#68736E;--border:#DDE2DC;--success:#2EAB4D;--warning:#E1B739;
}
html{background:var(--uxa-canvas)}
body.uxa-unified{
  background:var(--uxa-canvas)!important;
  color:var(--uxa-text)!important;
  font-family:Inter,"PingFang SC","Microsoft YaHei",system-ui,-apple-system,sans-serif!important;
}
body.uxa-unified *{box-sizing:border-box}
body.uxa-unified aside,
body.uxa-unified .sidebar,
body.uxa-unified [class*="side-nav"],
body.uxa-unified [class*="sidebar"]{
  background:var(--uxa-forest)!important;
  color:#fff!important;
  border-color:rgba(255,255,255,.08)!important;
}
body.uxa-unified aside a,
body.uxa-unified .sidebar a,
body.uxa-unified aside button,
body.uxa-unified .sidebar button{color:rgba(255,255,255,.72)!important}
body.uxa-unified aside a:hover,
body.uxa-unified .sidebar a:hover,
body.uxa-unified aside button:hover,
body.uxa-unified .sidebar button:hover,
body.uxa-unified aside .active,
body.uxa-unified .sidebar .active,
body.uxa-unified [aria-current="page"]{
  background:rgba(255,255,255,.11)!important;
  color:#fff!important;
  border-radius:12px!important;
}
body.uxa-unified main,
body.uxa-unified .main,
body.uxa-unified .main-content,
body.uxa-unified .content{background:var(--uxa-canvas)!important}
body.uxa-unified header,
body.uxa-unified .topbar,
body.uxa-unified .page-header{
  background:rgba(244,244,243,.92)!important;
  border-color:var(--uxa-line)!important;
  backdrop-filter:blur(16px);
}
body.uxa-unified h1,body.uxa-unified h2,body.uxa-unified h3{color:var(--uxa-text)!important;letter-spacing:-.025em}
body.uxa-unified .card,
body.uxa-unified .panel,
body.uxa-unified .surface,
body.uxa-unified .task-card,
body.uxa-unified .order-card,
body.uxa-unified .stat-card,
body.uxa-unified .metric-card,
body.uxa-unified .review-card,
body.uxa-unified .setting-section,
body.uxa-unified [data-card]{
  border-color:var(--uxa-line)!important;
  border-radius:var(--uxa-radius)!important;
  box-shadow:var(--uxa-shadow-soft)!important;
}
body.uxa-unified table{border-collapse:separate!important;border-spacing:0 8px!important}
body.uxa-unified th{color:var(--uxa-muted)!important;font-size:12px!important;font-weight:700!important;background:transparent!important}
body.uxa-unified td{background:#fff!important;border-top:1px solid var(--uxa-line)!important;border-bottom:1px solid var(--uxa-line)!important}
body.uxa-unified tr td:first-child{border-left:1px solid var(--uxa-line)!important;border-radius:13px 0 0 13px!important}
body.uxa-unified tr td:last-child{border-right:1px solid var(--uxa-line)!important;border-radius:0 13px 13px 0!important}
body.uxa-unified input,
body.uxa-unified textarea,
body.uxa-unified select{
  border:1px solid var(--uxa-line)!important;
  border-radius:12px!important;
  background:#fff!important;
  color:var(--uxa-text)!important;
  box-shadow:none!important;
}
body.uxa-unified input:focus,
body.uxa-unified textarea:focus,
body.uxa-unified select:focus{outline:3px solid rgba(80,150,178,.16)!important;border-color:var(--uxa-sky)!important}
body.uxa-unified button,
body.uxa-unified .btn,
body.uxa-unified [class*="button"]{border-radius:12px!important}
body.uxa-unified .primary,
body.uxa-unified .btn-primary,
body.uxa-unified button[type="submit"]{
  background:var(--uxa-forest)!important;color:#fff!important;border-color:var(--uxa-forest)!important
}
body.uxa-unified .badge,
body.uxa-unified .tag,
body.uxa-unified [class*="pill"],
body.uxa-unified [class*="chip"]{border-radius:999px!important}

.uxa-flow-rail{
  margin:10px 0 22px;padding:14px 16px;background:#fff;border:1px solid var(--uxa-line);
  border-radius:18px;box-shadow:var(--uxa-shadow-soft);display:flex;align-items:center;gap:8px;overflow:auto;
}
.uxa-flow-rail strong{font-size:12px;color:var(--uxa-muted);white-space:nowrap;margin-right:6px}
.uxa-flow-step{display:flex;align-items:center;gap:7px;white-space:nowrap;padding:7px 10px;border-radius:999px;color:var(--uxa-muted);font-size:12px;font-weight:700}
.uxa-flow-step i{width:8px;height:8px;border-radius:50%;background:var(--uxa-line)}
.uxa-flow-step.active{background:var(--uxa-forest);color:#fff}.uxa-flow-step.active i{background:var(--uxa-warning)}
.uxa-flow-arrow{color:#AAB3AE;font-size:12px}

.uxa-command{
  display:inline-flex;align-items:center;gap:8px;border:0;background:var(--uxa-bronze)!important;color:#fff!important;
  padding:10px 14px;border-radius:12px!important;font-weight:750;box-shadow:0 8px 20px rgba(179,128,82,.2);cursor:pointer
}
.uxa-command svg{width:16px;height:16px}
.uxa-floating-command{position:fixed;right:22px;bottom:24px;z-index:9990}
.uxa-inline-actions{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 0}
.uxa-inline-action{
  border:1px solid var(--uxa-line)!important;background:#fff!important;color:var(--uxa-forest)!important;
  padding:7px 10px!important;font-size:12px!important;font-weight:750!important;border-radius:10px!important;cursor:pointer
}
.uxa-inline-action:hover{border-color:var(--uxa-bronze)!important;color:var(--uxa-bronze)!important}
.uxa-context-dock{
  margin:14px 0 18px;padding:16px;background:linear-gradient(135deg,#fff 0%,#F0F3EF 100%);
  border:1px solid var(--uxa-line);border-radius:20px;display:grid;grid-template-columns:minmax(220px,1fr) 2fr;gap:16px;box-shadow:var(--uxa-shadow-soft)
}
.uxa-context-dock h3{margin:0 0 5px;font-size:15px}.uxa-context-dock p{margin:0;color:var(--uxa-muted);font-size:12px;line-height:1.6}
.uxa-context-actions{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:8px}
.uxa-context-actions button{border:1px solid var(--uxa-line)!important;background:#fff!important;text-align:left;padding:11px!important;color:var(--uxa-text)!important;font-weight:750!important;cursor:pointer}
.uxa-context-actions button small{display:block;color:var(--uxa-muted);font-weight:500;margin-top:3px}
.uxa-context-actions button:hover{border-color:var(--uxa-bronze)!important;transform:translateY(-1px)}

.uxa-overlay{position:fixed;inset:0;background:rgba(9,41,35,.34);backdrop-filter:blur(4px);z-index:10000;opacity:0;pointer-events:none;transition:.2s}
.uxa-overlay.open{opacity:1;pointer-events:auto}
.uxa-drawer{
  position:absolute;right:0;top:0;height:100%;width:min(720px,96vw);background:var(--uxa-canvas);
  box-shadow:-24px 0 64px rgba(9,41,35,.22);transform:translateX(100%);transition:.24s ease;display:flex;flex-direction:column
}
.uxa-overlay.open .uxa-drawer{transform:translateX(0)}
.uxa-drawer-head{padding:18px 22px;background:var(--uxa-forest);color:#fff;display:flex;justify-content:space-between;align-items:flex-start}
.uxa-drawer-head h2{color:#fff!important;margin:3px 0 0;font-size:21px}.uxa-drawer-head p{margin:6px 0 0;color:rgba(255,255,255,.68);font-size:12px}
.uxa-close{width:36px;height:36px;border:1px solid rgba(255,255,255,.2)!important;background:rgba(255,255,255,.08)!important;color:#fff!important;font-size:20px;cursor:pointer}
.uxa-drawer-body{padding:18px 22px 30px;overflow:auto;flex:1}
.uxa-stage-tabs{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px}
.uxa-stage-tab{padding:11px;border:1px solid var(--uxa-line)!important;background:#fff!important;color:var(--uxa-muted)!important;font-weight:750!important;cursor:pointer}
.uxa-stage-tab.active{background:var(--uxa-forest)!important;color:#fff!important;border-color:var(--uxa-forest)!important}
.uxa-context-card{padding:14px;background:#fff;border:1px solid var(--uxa-line);border-radius:16px;margin-bottom:14px}
.uxa-context-card strong{display:block;font-size:13px}.uxa-context-card p{margin:6px 0 0;color:var(--uxa-muted);font-size:12px;line-height:1.6}
.uxa-form-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.uxa-field{display:grid;gap:6px;margin:0 0 12px}.uxa-field>span{font-size:12px;font-weight:750;color:var(--uxa-sage)}
.uxa-field input,.uxa-field textarea,.uxa-field select{width:100%;padding:11px 12px;font:inherit}.uxa-field textarea{resize:vertical;min-height:110px}
.uxa-run{width:100%;padding:13px!important;background:var(--uxa-forest)!important;color:#fff!important;border:0!important;font-weight:800!important;cursor:pointer}
.uxa-result{margin-top:16px;padding:16px;background:#fff;border:1px solid var(--uxa-line);border-radius:18px}
.uxa-result-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:12px}.uxa-result h3{margin:0;font-size:16px}
.uxa-status{display:inline-flex;padding:5px 8px;border-radius:999px;background:#EEF2EF;color:var(--uxa-sage);font-size:11px;font-weight:800}
.uxa-status.good{background:#E9F6EC;color:#26733B}.uxa-status.warn{background:#FFF7D9;color:#7A6200}.uxa-status.bad{background:#FCECEA;color:#983B36}
.uxa-summary-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:10px 0}.uxa-summary{padding:10px;border-radius:12px;background:#F6F7F5}.uxa-summary span{display:block;color:var(--uxa-muted);font-size:10px}.uxa-summary b{font-size:12px}
.uxa-quote{padding:12px;border-left:3px solid var(--uxa-bronze);background:#F8F5F1;border-radius:0 10px 10px 0;font-size:12px;line-height:1.65}
.uxa-review-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.uxa-review-actions button{padding:10px 12px!important;font-weight:750!important;cursor:pointer}.uxa-review-actions .approve{background:var(--uxa-forest)!important;color:#fff!important;border:0!important}.uxa-review-actions .secondary{background:#fff!important;color:var(--uxa-forest)!important;border:1px solid var(--uxa-line)!important}.uxa-review-actions .danger{background:#fff!important;color:var(--uxa-danger)!important;border:1px solid #ECCBC8!important}
.uxa-risk{padding:11px;border:1px solid #E6D49B;background:#FFF9E8;border-radius:12px;color:#705A12;font-size:12px;line-height:1.6;margin:10px 0}
.uxa-key-row{display:flex;gap:8px;align-items:center;margin-top:10px}.uxa-key-row input{flex:1;padding:10px}.uxa-mini{font-size:11px;color:var(--uxa-muted)}
.uxa-toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%) translateY(20px);background:var(--uxa-forest);color:#fff;padding:11px 15px;border-radius:12px;z-index:11000;opacity:0;transition:.2s;pointer-events:none;box-shadow:var(--uxa-shadow)}
.uxa-toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.uxa-toast.error{background:#8F3732}

@media(max-width:900px){
  .uxa-context-dock{grid-template-columns:1fr}.uxa-context-actions{grid-template-columns:1fr 1fr}
  .uxa-flow-rail{margin-left:0;margin-right:0}.uxa-floating-command{right:14px;bottom:72px}.uxa-drawer{width:100vw}.uxa-form-grid{grid-template-columns:1fr}
}
@media(max-width:560px){.uxa-context-actions{grid-template-columns:1fr}.uxa-summary-grid{grid-template-columns:1fr 1fr}.uxa-drawer-body{padding:14px}.uxa-drawer-head{padding:16px}}
"""

GLOBAL_JS = r"""
(()=>{
  if(window.__UXA_UNIFIED_V3__) return;
  window.__UXA_UNIFIED_V3__=true;
  const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
  const esc=v=>String(v??"").replace(/[&<>\"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'\"':"&quot;","'":"&#39;"}[m]));
  const clean=t=>String(t||"").replace(/\s+/g," ").trim();
  const bodyText=()=>clean(document.body.innerText).slice(0,5000);
  let orders=[],currentContext={},currentCandidate=null,currentDraft=null,activeMode="ft06",observerTimer=null;

  function toast(message,type=""){
    let el=$("#uxa-toast"); if(!el){el=document.createElement("div");el.id="uxa-toast";el.className="uxa-toast";document.body.appendChild(el)}
    el.textContent=message;el.className=`uxa-toast ${type} show`;clearTimeout(el._t);el._t=setTimeout(()=>el.classList.remove("show"),2600);
  }
  function api(url,options={}){
    const key=sessionStorage.getItem("communicationAdminKey")||"";
    const headers={"Content-Type":"application/json",...(key?{"X-Communication-Key":key}:{}),...(options.headers||{})};
    return fetch(url,{...options,headers}).then(async r=>{let d={};try{d=await r.json()}catch(_){d={detail:await r.text()}};if(!r.ok)throw new Error(typeof d.detail==="string"?d.detail:JSON.stringify(d.detail||d.error||`请求失败 ${r.status}`));return d})
  }
  function currentPage(){
    const text=clean([$("h1")?.textContent,$("h2")?.textContent,$(".active")?.textContent,document.title].filter(Boolean).join(" "));
    if(/消息接入/.test(text))return "understand";
    if(/AI确认|候选确认/.test(text))return "confirm";
    if(/等待中心|全部任务/.test(text))return "wait";
    if(/订单详情/.test(text))return "order-detail";
    if(/订单中心|批量导入/.test(text))return "fact";
    if(/管理看板/.test(text))return "review";
    if(/设置/.test(text))return "system";
    return "action";
  }
  const stages=[
    ["fact","订单事实"],["understand","消息理解"],["confirm","人工确认"],["action","行动排序"],["communicate","沟通执行"],["wait","等待回复"],["review","回复重排"]
  ];
  function ensureFlowRail(){
    const page=currentPage();const active=page==="order-detail"?"fact":page==="system"?"review":page;
    const existing=$("#uxa-flow-rail");
    if(existing){existing.dataset.page=page;$$('.uxa-flow-step',existing).forEach((el,i)=>el.classList.toggle('active',stages[i]?.[0]===active));return}
    const main=$("main")||$(".main-content")||$(".content")||$("[role=main]");if(!main)return;
    const rail=document.createElement("div");rail.id="uxa-flow-rail";rail.className="uxa-flow-rail";rail.dataset.page=page;
    rail.innerHTML=`<strong>订单行动闭环</strong>${stages.map((s,i)=>`${i?'<span class="uxa-flow-arrow">→</span>':''}<span class="uxa-flow-step ${s[0]===active?'active':''}"><i></i>${s[1]}</span>`).join("")}`;
    const anchor=$(".page-header",main)||$("header",main)||$("h1",main)?.parentElement||main.firstElementChild;
    anchor?.insertAdjacentElement("afterend",rail) || main.prepend(rail);
  }
  function removeLegacyEntry(){
    $$('[data-communication-entry],a[href="/communication-assistant"]').forEach(el=>el.remove());
  }
  function contextFromElement(el={}){
    const holder=el?.closest?.('[data-task-id],[data-order-id],[data-order-no],.task-card,.action-card,.order-card,tr,article,.drawer')||el;
    const text=clean(holder?.innerText||"");
    const pick=(name)=>holder?.dataset?.[name]||holder?.querySelector?.(`[data-${name.replace(/[A-Z]/g,m=>'-'+m.toLowerCase())}]`)?.dataset?.[name]||"";
    const orderNo=pick("orderNo")||(text.match(/(?:PO|订单)[-：:\s]*([A-Z0-9-]{4,})/i)?.[1]||"");
    return {task_id:pick("taskId"),order_id:pick("orderId"),order_no:orderNo,text};
  }
  function recommendType(text){
    if(/工厂|供应商|生产进度|物料|到货/.test(text))return "SUPPLIER_PROGRESS_FOLLOWUP";
    if(/客户.*确认|包装|规格|版本/.test(text))return "CUSTOMER_CONFIRMATION_REMINDER";
    if(/交期|交付|延期/.test(text))return "DELIVERY_STATUS_REPLY";
    return "CUSTOMER_REPLY";
  }
  function ensureCommand(){
    if($("#uxa-global-command"))return;
    const btn=document.createElement("button");btn.id="uxa-global-command";btn.className="uxa-command uxa-floating-command";btn.type="button";
    btn.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 5h16v11H8l-4 4z"/><path d="M8 9h8M8 13h5"/></svg><span>处理沟通</span>';
    btn.addEventListener("click",()=>openDrawer("ft06",{}));document.body.appendChild(btn);
  }
  function ensureContextDock(){
    const page=currentPage(); if(!["action","wait","fact","order-detail","understand","confirm"].includes(page))return;
    const oldDock=$("#uxa-context-dock");if(oldDock&&oldDock.dataset.page===page)return;if(oldDock)oldDock.remove();
    const main=$("main")||$(".main-content")||$(".content")||$("[role=main]");if(!main)return;
    const dock=document.createElement("section");dock.id="uxa-context-dock";dock.className="uxa-context-dock";dock.dataset.page=page;
    let title="把沟通嵌进行动闭环",desc="根据当前订单、任务与等待状态生成下一步沟通，不再进入独立助手页面。";
    if(page==="understand"||page==="confirm"){title="识别后直接落到下一步";desc="消息完成理解或确认后，可立即转成任务，或生成需要人工审核的回复草稿。"}
    if(page==="wait"){title="等待不是终点";desc="在承诺回复窗口内保持等待；超时后从原任务直接生成再次催办消息。"}
    dock.innerHTML=`<div><h3>${title}</h3><p>${desc}</p></div><div class="uxa-context-actions">
      <button data-uxa-mode="ft05"><b>沟通转任务</b><small>FT05 · 生成候选后人工确认</small></button>
      <button data-uxa-mode="ft06" data-type="SUPPLIER_PROGRESS_FOLLOWUP"><b>催工厂进度</b><small>从订单与等待状态生成</small></button>
      <button data-uxa-mode="ft06" data-type="CUSTOMER_CONFIRMATION_REMINDER"><b>催客户确认</b><small>包装、规格、版本与交期</small></button>
      <button data-uxa-mode="ft06" data-type="DELIVERY_STATUS_REPLY"><b>回复客户交期</b><small>只使用已确认订单事实</small></button>
    </div>`;
    dock.addEventListener("click",e=>{const b=e.target.closest("[data-uxa-mode]");if(b)openDrawer(b.dataset.uxaMode,{draft_type:b.dataset.type||""})});
    const rail=$("#uxa-flow-rail");rail?.insertAdjacentElement("afterend",dock)||main.prepend(dock);
  }
  function scanCards(){
    const selectors=['[data-task-id]','.task-card','.action-card','.task-row','.order-card','tbody tr'];
    $$(selectors.join(',')).forEach(card=>{
      if(card.dataset.uxaActions)return;const text=clean(card.innerText);if(text.length<12)return;
      card.dataset.uxaActions="1";const row=document.createElement("div");row.className="uxa-inline-actions";
      const type=recommendType(text);row.innerHTML=`<button class="uxa-inline-action" data-uxa-mode="ft06" data-type="${type}">生成沟通草稿</button>${/消息|客户|工厂|回复/.test(text)?'<button class="uxa-inline-action" data-uxa-mode="ft05">转成任务</button>':''}`;
      row.addEventListener("click",e=>{e.stopPropagation();const b=e.target.closest("[data-uxa-mode]");if(b)openDrawer(b.dataset.uxaMode,{...contextFromElement(card),draft_type:b.dataset.type||type})});
      const target=card.matches?.('tr')?(card.lastElementChild||card):card;target.appendChild(row);
    });
  }
  function ensureDrawer(){
    if($("#uxa-overlay"))return;
    const overlay=document.createElement("div");overlay.id="uxa-overlay";overlay.className="uxa-overlay";
    overlay.innerHTML=`<aside class="uxa-drawer" aria-label="行动沟通工作区">
      <header class="uxa-drawer-head"><div><small>行动层 · 沟通执行</small><h2>当前行动的沟通处理</h2><p>订单事实 → 生成候选 → 人工确认 → 等待回复 → 动态重排</p></div><button class="uxa-close" type="button" aria-label="关闭">×</button></header>
      <div class="uxa-drawer-body">
        <div class="uxa-stage-tabs"><button class="uxa-stage-tab" data-tab="ft05">沟通转任务</button><button class="uxa-stage-tab active" data-tab="ft06">生成沟通草稿</button></div>
        <div id="uxa-context" class="uxa-context-card"><strong>尚未选择上下文</strong><p>系统会从当前订单、任务或消息页面带入关联信息。</p></div>
        <div class="uxa-key-row"><input id="uxa-key" type="password" placeholder="操作密钥（仅保存在本次会话）"><button id="uxa-key-save" class="uxa-inline-action" type="button">保存</button></div>
        <p class="uxa-mini">所有草稿均需人工审核，系统不会自动发送。</p>
        <div id="uxa-form"></div><div id="uxa-result"></div>
      </div></aside>`;
    document.body.appendChild(overlay);
    $(".uxa-close",overlay).onclick=closeDrawer;overlay.addEventListener("click",e=>{if(e.target===overlay)closeDrawer()});
    $$("[data-tab]",overlay).forEach(b=>b.onclick=()=>{activeMode=b.dataset.tab;renderDrawer()});
    $("#uxa-key",overlay).value=sessionStorage.getItem("communicationAdminKey")||"";
    $("#uxa-key-save",overlay).onclick=()=>{const v=$("#uxa-key",overlay).value.trim();if(v){sessionStorage.setItem("communicationAdminKey",v);toast("操作密钥已保存到本次会话")}};
  }
  async function loadOrders(){if(orders.length)return orders;try{orders=(await api('/api/communication/orders')).orders||[]}catch(e){toast(`订单加载失败：${e.message}`,'error')}return orders}
  function selectedOrder(){const v=$("#uxa-order")?.value;return orders.find(o=>String(o.order_id||o.order_no)===String(v))||orders.find(o=>o.order_no===currentContext.order_no)||{}}
  function orderOptions(){return orders.map(o=>`<option value="${esc(o.order_id||o.order_no)}" ${(o.order_id===currentContext.order_id||o.order_no===currentContext.order_no)?'selected':''}>${esc(o.order_no||o.order_id)} · ${esc(o.customer_name||'未命名客户')}</option>`).join('')}
  async function openDrawer(mode="ft06",context={}){ensureDrawer();activeMode=mode;currentContext={...currentContext,...context};await loadOrders();renderDrawer();$("#uxa-overlay").classList.add("open");document.body.style.overflow="hidden"}
  function closeDrawer(){$("#uxa-overlay")?.classList.remove("open");document.body.style.overflow=""}
  function renderDrawer(){
    $$(".uxa-stage-tab").forEach(b=>b.classList.toggle("active",b.dataset.tab===activeMode));
    const ctx=$("#uxa-context");ctx.innerHTML=`<strong>${esc(currentContext.order_no||"当前页面上下文")}</strong><p>${esc((currentContext.text||"将从所选订单加载交期、节点、工厂承诺与任务信息。").slice(0,220))}</p>`;
    const form=$("#uxa-form");$("#uxa-result").innerHTML="";
    if(activeMode==="ft05") form.innerHTML=`<div class="uxa-form-grid"><label class="uxa-field"><span>关联订单</span><select id="uxa-order"><option value="">自动识别/待确认</option>${orderOptions()}</select></label><label class="uxa-field"><span>消息来源</span><select id="uxa-sender"><option value="customer">客户</option><option value="supplier">工厂/供应商</option><option value="internal">内部同事</option></select></label></div><label class="uxa-field"><span>沟通原文</span><textarea id="uxa-message" placeholder="粘贴客户、工厂或内部沟通原文">${esc(currentContext.message||"")}</textarea></label><label class="uxa-field"><span>渠道</span><select id="uxa-channel"><option value="email">邮箱</option><option value="wechat">工作微信</option><option value="whatsapp">WhatsApp</option></select></label><button id="uxa-run" class="uxa-run" type="button">生成任务候选</button>`;
    else form.innerHTML=`<div class="uxa-form-grid"><label class="uxa-field"><span>关联订单</span><select id="uxa-order"><option value="">请选择订单</option>${orderOptions()}</select></label><label class="uxa-field"><span>沟通场景</span><select id="uxa-draft-type"><option value="CUSTOMER_REPLY">客户回复</option><option value="CUSTOMER_CONFIRMATION_REMINDER">催客户确认</option><option value="SUPPLIER_PROGRESS_FOLLOWUP">催工厂进度</option><option value="DELIVERY_STATUS_REPLY">回复客户交期</option><option value="CHANGE_HISTORY_SUMMARY">汇总客户变更</option></select></label></div><div class="uxa-form-grid"><label class="uxa-field"><span>接收对象</span><select id="uxa-recipient"><option value="customer">客户</option><option value="supplier">工厂/供应商</option><option value="internal">内部同事</option></select></label><label class="uxa-field"><span>渠道</span><select id="uxa-channel"><option value="email">邮箱</option><option value="wechat">工作微信</option><option value="whatsapp">WhatsApp</option></select></label></div><label class="uxa-field"><span>本次要完成的沟通</span><textarea id="uxa-instruction" placeholder="例如：确认拉链到货时间和补救方案；不要作出未经确认的交期承诺。">${esc(currentContext.instruction||"")}</textarea></label><button id="uxa-run" class="uxa-run" type="button">基于订单事实生成草稿</button>`;
    if(activeMode==="ft06"&&currentContext.draft_type)$("#uxa-draft-type").value=currentContext.draft_type;
    $("#uxa-run").onclick=activeMode==="ft05"?runFT05:runFT06;
  }
  async function runFT05(){
    const message=$("#uxa-message").value.trim();if(!message)return toast("请先粘贴沟通原文",'error');const order=selectedOrder();
    try{$("#uxa-run").disabled=true;$("#uxa-run").textContent="正在理解并校验…";const data=await api('/api/workflows/ft05/run',{method:'POST',body:JSON.stringify({communication_text:message,sender_role:$("#uxa-sender").value,channel:$("#uxa-channel").value,order_id:order.order_id||null,order_no:order.order_no||null,order_context:order.order_id||order.order_no?order:null,source_message_id:currentContext.source_message_id||null})});currentCandidate=data;renderFT05(data)}catch(e){toast(e.message,'error')}finally{$("#uxa-run").disabled=false;$("#uxa-run").textContent="生成任务候选"}
  }
  function renderFT05(data){const r=data.result||{},c=r.task_candidate||data.task_candidate||{},ready=r.run_status==='task_candidate_ready';$("#uxa-result").innerHTML=`<section class="uxa-result"><div class="uxa-result-head"><div><h3>${esc(c.task_title||'任务候选结果')}</h3><p class="uxa-mini">${esc(c.reason||r.technical?.error_message||'AI理解完成，等待人工确认。')}</p></div><span class="uxa-status ${ready?'good':'warn'}">${esc(r.run_status||'unknown')}</span></div><div class="uxa-summary-grid"><div class="uxa-summary"><span>订单</span><b>${esc(c.related_order_no||'待确认')}</b></div><div class="uxa-summary"><span>截止时间</span><b>${esc(c.due_at_candidate||'待确认')}</b></div><div class="uxa-summary"><span>优先级</span><b>${esc(c.priority_hint||'normal')}</b></div></div><label class="uxa-field"><span>任务标题</span><input id="uxa-candidate-title" value="${esc(c.task_title||'')}"></label><label class="uxa-field"><span>任务说明</span><textarea id="uxa-candidate-desc">${esc(c.task_description||'')}</textarea></label><div class="uxa-quote">${esc(c.source_quote||'无原文证据')}</div><div class="uxa-review-actions">${ready&&data.candidate_id?'<button class="approve" id="uxa-candidate-commit">人工确认并写回任务</button>':''}${data.candidate_id?'<button class="danger" id="uxa-candidate-reject">驳回候选</button>':''}</div></section>`;
    $("#uxa-candidate-commit")?.addEventListener('click',commitCandidate);$("#uxa-candidate-reject")?.addEventListener('click',rejectCandidate)}
  async function commitCandidate(){const c={...(currentCandidate.result?.task_candidate||{})};c.task_title=$("#uxa-candidate-title")?.value.trim()||c.task_title;c.task_description=$("#uxa-candidate-desc")?.value.trim()||c.task_description;try{const d=await api(`/api/communication/candidates/${currentCandidate.candidate_id}/commit`,{method:'POST',body:JSON.stringify({operator_id:'USER-1',edited_candidate:c,confirmation_version:'3',note:'全站融合行动面板人工确认'})});toast(d.message||'任务已写回并进入行动排序');setTimeout(()=>location.reload(),900)}catch(e){toast(e.message,'error')}}
  async function rejectCandidate(){try{await api(`/api/communication/candidates/${currentCandidate.candidate_id}/reject`,{method:'POST',body:JSON.stringify({operator_id:'USER-1',note:'全站融合行动面板人工驳回'})});toast('候选已驳回');$("#uxa-result").innerHTML=''}catch(e){toast(e.message,'error')}}
  function factCatalog(order){const map={order_no:'order_no',customer_delivery_date:'customer_delivery_date',supplier_completion_commitment_date:'supplier_completion_commitment_date',latest_supplier_commitment:'supplier_completion_commitment_date',current_progress:'current_progress',current_node:'current_node',factory_name:'factory_name',packaging_method:'packaging_method',product_name:'product_name',quantity:'quantity'};let i=1;return Object.entries(map).filter(([k])=>order[k]!==undefined&&order[k]!==null&&order[k]!=="").map(([k,t])=>({fact_id:`WEB-${String(i++).padStart(3,'0')}`,fact_type:t,value:order[k],confirmed:true}))}
  async function runFT06(){const order=selectedOrder();if(!order.order_id&&!order.order_no)return toast('请先选择关联订单','error');try{$("#uxa-run").disabled=true;$("#uxa-run").textContent='正在整理事实并生成…';const data=await api('/api/workflows/ft06/run',{method:'POST',body:JSON.stringify({draft_type:$("#uxa-draft-type").value,recipient_role:$("#uxa-recipient").value,channel:$("#uxa-channel").value,tone:'professional',user_instruction:$("#uxa-instruction").value,order_id:order.order_id||null,order_no:order.order_no||null,fact_catalog:factCatalog(order),order_context:order,task_context:currentContext.task_id?{task_id:currentContext.task_id,source_text:currentContext.text}:null,communication_history:[]})});currentDraft=data;renderFT06(data)}catch(e){toast(e.message,'error')}finally{$("#uxa-run").disabled=false;$("#uxa-run").textContent='基于订单事实生成草稿'}}
  function renderFT06(data){const r=data.result||{},d=data.draft_result||r.draft_result||{},blocked=String(r.approval_status||'').startsWith('BLOCKED');$("#uxa-result").innerHTML=`<section class="uxa-result"><div class="uxa-result-head"><div><h3>受控沟通草稿</h3><p class="uxa-mini">只使用引用事实；所有发送动作均需人工确认。</p></div><span class="uxa-status ${blocked?'bad':'good'}">${esc(r.approval_status||r.run_status||'待审核')}</span></div>${blocked?'<div class="uxa-risk">该草稿存在事实或承诺风险，需补充信息或人工复核后才能使用。</div>':''}<label class="uxa-field"><span>主题</span><input id="uxa-draft-subject" value="${esc(d.subject||'')}"></label><label class="uxa-field"><span>草稿正文</span><textarea id="uxa-draft-body" style="min-height:220px">${esc(d.draft||'')}</textarea></label><div class="uxa-mini">引用事实：${esc((d.facts_used||[]).join('、')||'—')}</div><div class="uxa-mini">待询问：${esc((d.questions_to_ask||[]).join('；')||'—')}</div>${blocked?'<label class="uxa-field"><span>高风险人工复核原因</span><textarea id="uxa-risk-note" placeholder="确需放行时说明核对依据"></textarea></label><label class="uxa-mini"><input type="checkbox" id="uxa-risk-override"> 我已核对交期、费用、赔偿与责任边界</label>':''}<div class="uxa-review-actions">${data.draft_id?'<button class="secondary" id="uxa-draft-save">保存修改</button><button class="approve" id="uxa-draft-approve">人工确认</button><button class="secondary" id="uxa-draft-copy">复制并记录已联系</button>':''}</div></section>`;$("#uxa-draft-save")?.addEventListener('click',()=>reviewDraft('save_edit'));$("#uxa-draft-approve")?.addEventListener('click',()=>reviewDraft('approve'));$("#uxa-draft-copy")?.addEventListener('click',()=>reviewDraft('copy_and_record'))}
  async function reviewDraft(action){const blocked=String(currentDraft.result?.approval_status||'').startsWith('BLOCKED'),override=Boolean($("#uxa-risk-override")?.checked),note=$("#uxa-risk-note")?.value||'';if(blocked&&['approve','copy_and_record'].includes(action)&&!override)return toast('该草稿已被阻断，请补充事实或完成高风险复核','error');if(blocked&&override&&!note.trim())return toast('高风险放行必须填写核对依据','error');const subject=$("#uxa-draft-subject").value,body=$("#uxa-draft-body").value;if(action==='copy_and_record')try{await navigator.clipboard.writeText([subject,body].filter(Boolean).join('\n\n'))}catch(_){};try{const d=await api(`/api/communication/drafts/${currentDraft.draft_id}/review`,{method:'POST',body:JSON.stringify({action,operator_id:'USER-1',edited_subject:subject,edited_draft:body,note,risk_override_confirmed:override,task_id:currentContext.task_id||null,waiting_on:$("#uxa-recipient")?.value==='supplier'?'factory':'customer'})});toast(d.message||'草稿状态已更新');if(action==='copy_and_record')setTimeout(()=>location.reload(),900)}catch(e){toast(e.message,'error')}}
  function apply(){document.body.classList.add('uxa-unified');removeLegacyEntry();ensureFlowRail();ensureCommand();ensureContextDock();scanCards();ensureDrawer()}
  function schedule(){clearTimeout(observerTimer);observerTimer=setTimeout(apply,120)}
  document.readyState==='loading'?document.addEventListener('DOMContentLoaded',apply):apply();
  new MutationObserver(schedule).observe(document.documentElement,{subtree:true,childList:true});
  window.addEventListener('popstate',schedule);document.addEventListener('click',e=>{const b=e.target.closest('[data-uxa-mode]');if(b&&!b.closest('#uxa-context-dock')&&!b.closest('.uxa-inline-actions'))openDrawer(b.dataset.uxaMode,{...contextFromElement(b),draft_type:b.dataset.type||''})});
  if(new URLSearchParams(location.search).get('action_comms')==='1')setTimeout(()=>openDrawer('ft06',{}),400);
})();
"""

class UnifiedActionExperienceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.method == "GET" and request.url.path == "/communication-assistant":
            return RedirectResponse(url="/?action_comms=1", status_code=307)
        response = await call_next(request)
        if request.method != "GET":
            return response
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type or request.url.path.startswith(("/docs", "/redoc")):
            return response
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        text = body.decode("utf-8", errors="replace")
        css_marker = "/api/action-experience/style.css"
        js_marker = "/api/action-experience/app.js"
        if css_marker not in text:
            tag = f'<link rel="stylesheet" href="{css_marker}?v={PATCH_VERSION}">'
            text = text.replace("</head>", tag + "</head>") if "</head>" in text else tag + text
        if js_marker not in text:
            tag = f'<script src="{js_marker}?v={PATCH_VERSION}" defer></script>'
            text = text.replace("</body>", tag + "</body>") if "</body>" in text else text + tag
        headers = dict(response.headers)
        headers.pop("content-length", None)
        return Response(content=text, status_code=response.status_code, headers=headers, media_type="text/html")


def register_unified_action_experience_patch(app: FastAPI) -> None:
    if getattr(app.state, "unified_action_experience_registered", False):
        return
    app.state.unified_action_experience_registered = True
    app.add_middleware(UnifiedActionExperienceMiddleware)

    @app.get("/api/action-experience/style.css")
    def action_experience_style():
        return Response(GLOBAL_CSS, media_type="text/css")

    @app.get("/api/action-experience/app.js")
    def action_experience_script():
        return Response(GLOBAL_JS, media_type="application/javascript")

    @app.get("/api/action-experience/status")
    def action_experience_status():
        return {
            "status": "ok",
            "patch_version": PATCH_VERSION,
            "legacy_communication_page_redirected": True,
            "communication_is_contextual": True,
            "automatic_send_enabled": False,
        }
