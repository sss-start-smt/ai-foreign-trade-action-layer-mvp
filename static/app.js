const stateNames={
  DO_NOW:"立即处理",DO_TODAY:"今天处理",WAITING_EXTERNAL:"等待他人",
  NEEDS_CONFIRMATION:"待确认",SCHEDULED:"已安排",ESCALATE:"主管介入",
  NOT_MY_RESPONSIBILITY:"非本人责任",DONE:"已完成"
};
const riskNames={critical:"关键",high:"高",medium:"中",low:"低",none:"无"};
let allItems=[],activeFilter="ALL",selectedId=null;

const toast=(msg)=>{const el=document.getElementById("toast");el.textContent=msg;el.classList.add("show");setTimeout(()=>el.classList.remove("show"),2200)};
const api=async(url,opts={})=>{
  const res=await fetch(url,{headers:{"Content-Type":"application/json",...(opts.headers||{})},...opts});
  if(!res.ok)throw new Error(await res.text()); return res.json();
};
const fmt=(v)=>v?String(v).replace("T"," ").replace("+08:00",""):"—";

async function load(){
  const data=await api("/api/dashboard");
  allItems=data.items;
  renderKpis(data.summary);
  renderTabs();
  renderCards();
  document.getElementById("health").textContent="已连接 · "+data.current_time.slice(11,16);
  if(selectedId)selectCard(selectedId);
}
function renderKpis(s){
  const list=[["立即处理",s.do_now],["今天处理",s.do_today],["等待他人",s.waiting],["待确认",s.needs_confirmation],["已安排",s.scheduled],["需升级",s.escalate]];
  document.getElementById("kpis").innerHTML=list.map(x=>`<div class="kpi"><div class="label">${x[0]}</div><div class="num">${x[1]}</div></div>`).join("");
}
function renderTabs(){
  const tabs=[["ALL","全部"],["DO_NOW","立即处理"],["DO_TODAY","今天处理"],["WAITING_EXTERNAL","等待他人"],["NEEDS_CONFIRMATION","待确认"],["SCHEDULED","已安排"],["ESCALATE","需升级"]];
  document.getElementById("tabs").innerHTML=tabs.map(([k,n])=>`<button class="tab ${activeFilter===k?"active":""}" data-k="${k}">${n}</button>`).join("");
  document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>{activeFilter=b.dataset.k;renderTabs();renderCards()});
}
function renderCards(){
  const items=activeFilter==="ALL"?allItems:allItems.filter(x=>x.action_state===activeFilter);
  document.getElementById("cards").innerHTML=items.map(x=>`
    <article class="card ${selectedId===x.task_id?"selected":""}" data-id="${x.task_id}">
      <div class="card-top">
        <div><div class="order-no">${x.order?.order_no||x.related_order_id||"未关联订单"}</div><h3>${x.title}</h3></div>
        <span class="state ${x.action_state}">${stateNames[x.action_state]||x.action_state}</span>
      </div>
      <div class="meta">
        <div>动作<br><strong>${x.recommended_action}</strong></div>
        <div>对象<br><strong>${x.target||"待确认"}</strong></div>
        <div>下次处理<br><strong>${fmt(x.next_action_at)}</strong></div>
        <div>风险<br><strong>${riskNames[x.risk_level]||x.risk_level}</strong></div>
      </div>
      <div class="reason">${x.priority_reasons.join("；")}</div>
      <span class="score">排序分 ${x.priority_score}</span>
    </article>`).join("")||"<div class='empty-state'><h3>当前分组没有任务</h3></div>";
  document.querySelectorAll(".card").forEach(c=>c.onclick=()=>selectCard(c.dataset.id));
}
async function selectCard(id){
  selectedId=id;renderCards();
  const x=allItems.find(t=>t.task_id===id);if(!x)return;
  let detail={order:x.order,tasks:[],risks:[],commitments:[],messages:[]};
  if(x.related_order_id)detail=await api(`/api/orders/${x.related_order_id}`);
  const ev=(x.evidence||[]).map(e=>`<div class="evidence">${e}</div>`).join("")||"<span>暂无证据</span>";
  const risks=(detail.risks||[]).map(r=>`<div class="evidence">${r.risk_type} · ${r.evidence||"无原文"}</div>`).join("")||"<span>暂无风险记录</span>";
  const btn=x.action_state==="DONE"?"":`<button onclick="completeTask('${x.task_id}')">标记完成</button>`;
  const contacted=x.target==="factory"||x.waiting_on==="factory"?`<button class="secondary" onclick="recordContact('${x.task_id}')">记录已联系</button>`:"";
  document.getElementById("sidepanel").className="sidepanel";
  document.getElementById("sidepanel").innerHTML=`
    <div class="panel-head"><div><div class="order-no">${detail.order?.order_no||x.related_order_id||""}</div><h2>${x.title}</h2></div><span class="state ${x.action_state}">${stateNames[x.action_state]}</span></div>
    <div class="panel-section"><h4>行动判断</h4>
      <div class="kv"><span>推荐动作</span><strong>${x.recommended_action}</strong></div>
      <div class="kv"><span>处理对象</span><span>${x.target||"—"}</span></div>
      <div class="kv"><span>等待对象</span><span>${x.waiting_on||"—"}</span></div>
      <div class="kv"><span>承诺回复</span><span>${fmt(x.promised_reply_at)}</span></div>
      <div class="kv"><span>下一处理</span><span>${fmt(x.next_action_at)}</span></div>
      <div class="kv"><span>判断依据</span><span>${x.priority_reasons.join("；")}</span></div>
    </div>
    <div class="panel-section"><h4>订单信息</h4>
      <div class="kv"><span>客户</span><span>${detail.order?.customer_name||"—"}</span></div>
      <div class="kv"><span>产品</span><span>${detail.order?.product_name||"—"}</span></div>
      <div class="kv"><span>包装</span><span>${detail.order?.packaging_method||"—"}</span></div>
      <div class="kv"><span>客户交期</span><span>${detail.order?.requested_delivery_date||"—"}</span></div>
      <div class="kv"><span>工厂承诺</span><span>${detail.order?.latest_supplier_commitment||"—"}</span></div>
      <div class="kv"><span>当前进度</span><span>${detail.order?.current_progress!=null?Math.round(detail.order.current_progress*100)+"%":"—"}</span></div>
    </div>
    <div class="panel-section"><h4>事实证据</h4>${ev}</div>
    <div class="panel-section"><h4>风险记录</h4>${risks}</div>
    <div class="panel-actions">${contacted}${btn}</div>`;
}
async function recordContact(id){
  const d=new Date(Date.now()+3*60*60*1000);
  const promised=new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,19)+"+08:00";
  await api(`/api/tasks/${id}/contacted`,{method:"POST",body:JSON.stringify({waiting_on:"factory",promised_reply_at:promised,operator_id:"USER-1"})});
  document.getElementById("step2").disabled=true;toast("已记录联系，任务转为等待工厂");await load();
}
async function completeTask(id){await api(`/api/tasks/${id}/complete`,{method:"POST"});toast("任务已完成");selectedId=null;await load()}
document.getElementById("refreshBtn").onclick=load;
document.getElementById("resetBtn").onclick=async()=>{await api("/api/reset",{method:"POST"});selectedId=null;document.getElementById("step2").disabled=true;toast("演示数据已重置");await load()};
document.getElementById("step1").onclick=async()=>{await api("/api/demo/apply-ft01",{method:"POST"});document.getElementById("step2").disabled=false;toast("客户消息已确认写回，新增联系工厂任务");await load()};
document.getElementById("step2").onclick=()=>recordContact("TASK-PO1001-CONFIRM");
document.getElementById("step3").onclick=async()=>{await api("/api/demo/apply-ft02",{method:"POST"});toast("工厂回复已确认写回，任务重新排序");await load()};
load().catch(e=>{document.getElementById("health").textContent="连接失败";toast(e.message)});
