const ICONS={
  agent:'<path d="M12 3a4 4 0 0 0-4 4v1H6a3 3 0 0 0-3 3v5a3 3 0 0 0 3 3h12a3 3 0 0 0 3-3v-5a3 3 0 0 0-3-3h-2V7a4 4 0 0 0-4-4z"/><path d="M8 13h.01M16 13h.01M9 16h6"/>' ,
  today:'<path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/><circle cx="12" cy="12" r="3.5"/>',
  tasks:'<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 3h6v4H9zM9 12h6M9 16h6"/>',
  orders:'<path d="M4 7.5 12 3l8 4.5v9L12 21l-8-4.5z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/>',
  message:'<path d="M4 5h16v11H8l-4 4z"/><path d="M8 9h8M8 12h5"/>',
  review:'<rect x="3" y="3" width="18" height="18" rx="3"/><path d="m8 12 2.5 2.5L16 9"/>',
  chart:'<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  settings:'<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 0-.4 1v.1h-4v-.1a1.7 1.7 0 0 0-1.4-1.7 1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 3.6 15a1.7 1.7 0 0 0-.6-1 1.7 1.7 0 0 0-1-.4h-.1v-4h.1a1.7 1.7 0 0 0 1.7-1.4 1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 8.2 3.6a1.7 1.7 0 0 0 1-.6 1.7 1.7 0 0 0 .4-1v-.1h4v.1a1.7 1.7 0 0 0 1.4 1.7 1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 20.4 8.2a1.7 1.7 0 0 0 .6 1 1.7 1.7 0 0 0 1 .4h.1v4h-.1a1.7 1.7 0 0 0-1.7 1.4z"/>',
  menu:'<path d="M4 7h16M4 12h16M4 17h16"/>',
  search:'<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
  refresh:'<path d="M20 12a8 8 0 1 1-2.34-5.66L20 8"/><path d="M20 3v5h-5"/>',
  chevron:'<path d="m9 10 3 3 3-3"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  upload:'<path d="M12 16V4M7 9l5-5 5 5"/><path d="M5 20h14"/>',
  spark:'<path d="m12 3 1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5zM5 15l.8 2.2L8 18l-2.2.8L5 21l-.8-2.2L2 18l2.2-.8z"/>',
  arrow:'<path d="M5 12h14M14 7l5 5-5 5"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  edit:'<path d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4"/>',
  external:'<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 13v6H5V6h6"/>',
  copy:'<rect x="8" y="8" width="11" height="11" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
  alert:'<path d="M12 3 2.5 20h19z"/><path d="M12 9v4M12 17h.01"/>',
  user:'<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  inbox:'<path d="M4 4h16v14H4z"/><path d="M4 13h4l2 3h4l2-3h4"/>',
  close:'<path d="m6 6 12 12M18 6 6 18"/>',
  mail:'<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
  factory:'<path d="M3 21V9l6 3V9l6 3V4h6v17z"/><path d="M7 17h2M13 17h2M18 8h1"/>',
  customer:'<path d="M3 21h18M5 21V8l7-5 7 5v13M9 21v-6h6v6"/>',
  more:'<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>'
};
const icon=name=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]||ICONS.more}</svg>`;
document.querySelectorAll('[data-icon]').forEach(el=>el.insertAdjacentHTML('afterbegin',icon(el.dataset.icon)));

const $=(s,r=document)=>r.querySelector(s);
const $$=(s,r=document)=>[...r.querySelectorAll(s)];
const esc=v=>String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const safeJson=(v,fallback={})=>{if(v==null||v==='')return fallback;if(typeof v==='object')return v;try{return JSON.parse(v)}catch{return fallback}};
const todayISO=()=>new Date().toISOString().slice(0,10);
const localGet=key=>{try{return window.localStorage.getItem(key)}catch{return null}};
const localSet=(key,value)=>{try{window.localStorage.setItem(key,value);return true}catch{return false}};
const sessionGet=key=>{try{return window.sessionStorage.getItem(key)}catch{return null}};
const sessionSet=(key,value)=>{try{window.sessionStorage.setItem(key,value);return true}catch{return false}};
const currentUser=()=>localGet('currentUserId')||'USER-1';
const DEMO_TOKEN_MAP={'USER-1':'tok-user-1','USER-2':'tok-user-2','USER-3':'tok-user-3','MANAGER-1':'tok-manager-1','OPERATOR-A1':'tok-operator-a1','MANAGER-A':'tok-manager-a','OPERATOR-B1':'tok-operator-b1','MANAGER-B':'tok-manager-b'};
const demoTokenForUser=(userId)=>DEMO_TOKEN_MAP[userId]||'tok-user-1';
const operatorNames={'USER-1':'李梅','USER-2':'王晓','USER-3':'陈琳','MANAGER-1':'周主管'};
const stateLabels={DO_NOW:'立即处理',DO_TODAY:'今天处理',WAITING_EXTERNAL:'等待外部',NEEDS_CONFIRMATION:'需要确认',SCHEDULED:'已计划',ESCALATE:'主管介入',NOT_MY_RESPONSIBILITY:'非本人负责',DONE:'已完成'};
const riskLabels={critical:'严重',high:'高风险',medium:'中风险',low:'低风险',none:'无明显风险'};
const readinessLabels={BASE_ONLY:'仅有基础订单',NEEDS_STATUS:'待补充进展',READY_FOR_RANKING:'可生成行动',ACTION_GENERATED:'已有行动',CLOSED:'已完成'};
const contactStatusLabels={NOT_CONTACTED:'尚未联系',WAITING_REPLY:'已联系，等待回复',REPLIED:'已收到回复',UNKNOWN:'不清楚'};
const draftTypeLabels={CUSTOMER_REPLY:'客户回复',CUSTOMER_CONFIRMATION_REMINDER:'催客户确认',SUPPLIER_PROGRESS_FOLLOWUP:'催工厂进度',DELIVERY_STATUS_REPLY:'回复交期状态',CHANGE_HISTORY_SUMMARY:'汇总客户变更'};
const FRONTEND_VERSION='6.1.4.11-action-workspace';
const pageMeta={
  today:['TODAY WORKBENCH','今日工作台'],
  agent:['AGENT ASSISTANT','Agent助手'],
  tasks:['TASK CENTER','任务中心'],
  orders:['ORDER CENTER','订单中心'],
  orderDetail:['ORDER DETAIL','订单详情'],
  activation:['FIRST VALUE','首次行动初始化'],
  intake:['MESSAGE CENTER','消息中心'],
  confirm:['CONFIRMATION HUB','确认中心'],
  manage:['MANAGEMENT VIEW','管理看板'],
  settings:['SYSTEM SETTINGS','设置与连接']
};
let route={name:'today',id:null,query:{}};
let settings={theme:'upstream',compact:false,current_user_id:'USER-1',notifications:{}};
let search='';
let searchTimer=null;
let activeOrderSort='ACTION';
let cache={};
let activeTaskFilter='ALL';
let selectedReviewId=null;
let communicationContext=null;
let communicationRun=null;

async function api(url,options={}){
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),options.timeoutMs||120000);
  const headers={'Content-Type':'application/json','X-Auth-Token':demoTokenForUser(currentUser()),...(options.headers||{})};
  const commKey=sessionGet('communicationAdminKey');
  if(commKey&&(url.startsWith('/api/workflows/ft05')||url.startsWith('/api/workflows/ft06')||url.startsWith('/api/communication/')))headers['X-Communication-Key']=commKey;
  try{
    const res=await fetch(url,{...options,headers,signal:controller.signal});
    const text=await res.text();
    let data={};try{data=text?JSON.parse(text):{}}catch{data={detail:text}}
    if(!res.ok){
      if(res.status===401&&url.includes('/communication/'))openCommunicationKeyModal();
      const detail=typeof data.detail==='string'?data.detail:(data.detail?.message||data.message||JSON.stringify(data.detail||data));
      throw new Error(detail||`请求失败 ${res.status}`);
    }
    return data;
  }catch(err){if(err.name==='AbortError')throw new Error('请求超时，请稍后重试；当前输入不会丢失');if(err instanceof TypeError&&/fetch/i.test(err.message||''))throw new Error('暂时无法连接服务器。请确认Render服务在线后再次点击；当前草稿不会丢失。');throw err}
  finally{clearTimeout(timeout)}
}
function toast(message,type=''){
  const el=document.createElement('div');el.className=`toast ${type}`;el.textContent=message;$('#toastRegion').appendChild(el);setTimeout(()=>el.remove(),3600)
}
function fdate(v){if(!v)return '—';const d=new Date(v);if(Number.isNaN(d.getTime()))return String(v);return new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',year:'numeric'}).format(d)}
function fdt(v){if(!v)return '—';const d=new Date(v);if(Number.isNaN(d.getTime()))return String(v);return new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(d)}
function relative(v){if(!v)return '未设置时间';const d=new Date(v),now=new Date();if(Number.isNaN(d.getTime()))return String(v);const diff=d-now,abs=Math.abs(diff);if(abs<3600000)return diff>=0?`${Math.max(1,Math.round(diff/60000))}分钟后`:`已过${Math.max(1,Math.round(-diff/60000))}分钟`;if(abs<86400000)return diff>=0?`${Math.round(diff/3600000)}小时后`:`已过${Math.round(-diff/3600000)}小时`;return diff>=0?`${Math.round(diff/86400000)}天后`:`已过${Math.round(-diff/86400000)}天`}
function progressValue(v){const n=Number(v);if(Number.isNaN(n))return 0;return Math.max(0,Math.min(100,n<=1?n*100:n))}
function statusBadge(state){return `<span class="status ${esc(state)}">${esc(stateLabels[state]||state||'未知')}</span>`}
function riskBadge(risk='none'){return `<span class="risk-badge ${esc(risk)}">${esc(riskLabels[risk]||risk)}</span>`}
function readinessBadge(value='BASE_ONLY'){return `<span class="readiness-badge ${esc(value)}">${esc(readinessLabels[value]||value)}</span>`}
function readinessNeedsInput(value){return ['BASE_ONLY','NEEDS_STATUS'].includes(value||'BASE_ONLY')}
function readinessNextAction(o){const r=o.action_readiness||'BASE_ONLY';if(readinessNeedsInput(r))return '补充最新进展';if(r==='READY_FOR_RANKING')return '生成行动';if(r==='ACTION_GENERATED')return '查看已有行动';return '查看历史'}
function parseRoute(){const raw=location.hash.replace(/^#/,'')||'today';const [path,q='']=raw.split('?');const parts=path.split('/').filter(Boolean);return{name:parts[0]||'today',id:parts[1]||null,query:Object.fromEntries(new URLSearchParams(q))}}
function go(path){location.hash=path}
function bindRouteButtons(root=document){$$('[data-route]',root).forEach(b=>b.onclick=()=>{go(b.dataset.route);$('#sidebar').classList.remove('open')});$$('[data-go]',root).forEach(b=>b.onclick=()=>go(b.dataset.go));$$('[data-metric-go]',root).forEach(card=>{const open=()=>go(card.dataset.metricGo);card.onclick=open;card.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();open()}}})}
/* setPageMeta moved to V0.3 implementation at line 515 */
function matchTask(t){if(!search)return true;const o=t.order||{};return [t.title,t.recommended_action,t.action_state,o.order_no,o.customer_name,o.product_name].join(' ').toLowerCase().includes(search.toLowerCase())}
function matchOrder(o){if(!search)return true;return [o.order_no,o.customer_name,o.product_name,o.current_node,o.factory_name].join(' ').toLowerCase().includes(search.toLowerCase())}
function emptyState(title,desc,actions=''){return `<div class="empty-state"><div class="empty-icon">${icon('inbox')}</div><h3>${esc(title)}</h3><p>${esc(desc)}</p>${actions?`<div class="empty-actions">${actions}</div>`:''}</div>`}
function metric(label,value,kind,ico,help='实时业务数据',goTo=''){const attrs=goTo?` data-metric-go="${esc(goTo)}" role="button" tabindex="0"`:'';return `<article class="metric-card ${kind}${goTo?' clickable':''}"${attrs}><div class="metric-top"><span class="metric-icon">${icon(ico)}</span><small class="metric-delta">${esc(help)}</small></div><span>${esc(label)}</span><strong>${Number(value||0)}</strong>${goTo?'<small class="metric-link">点击查看明细 →</small>':''}</article>`}
function orderDisplay(o){return o?`${o.order_no||'未编号'} · ${o.customer_name||'未知客户'}`:'未关联订单'}
function automationLabel(value=''){const v=String(value||'').toUpperCase();if(v.includes('FT02')||v.includes('FACTORY'))return '工厂回复识别';if(v.includes('FT01')||v.includes('CUSTOMER'))return '客户消息识别';if(v.includes('IMPORT'))return '订单资料识别';return '消息识别'}
function currentOperator(){return{user_id:currentUser(),name:operatorNames[currentUser()]||currentUser(),role:currentUser()==='MANAGER-1'?'业务主管':'跟单专员'}}

async function init(){
  bindShell();
  try{
    const [health,settingData,reviews,d12Reviews,operators]=await Promise.all([api('/health'),api(`/api/settings?user_id=${currentUser()}`),api('/api/reviews?status=PENDING'),api('/api/d12/reviews?status=PENDING').catch(()=>({items:[],count:0})),api('/api/operators')]);
    settings=settingData.settings||settings;
    if(settings.current_user_id&&settings.current_user_id!==currentUser())localSet('currentUserId',settings.current_user_id);
    cache.operators=operators.items||[];
    updateProfile();updateHealth(health);updateBadges(null,Number(reviews.pending||0)+Number(d12Reviews.count||0));
  }catch(err){updateHealth(null,err.message)}
  await renderRoute();
}
function bindShell(){
  bindRouteButtons();
  window.addEventListener('hashchange',()=>renderRoute(true));
  $('#mobileMenu').onclick=()=>$('#sidebar').classList.toggle('open');
  $('#refreshBtn').onclick=()=>{cache={operators:cache.operators};renderRoute(false);toast('已刷新真实业务数据','success')};
  $('#profileButton').onclick=()=>go('settings');
  $('#drawerMask').onclick=closeDrawer;
  $('#globalSearch').oninput=e=>{search=e.target.value;clearTimeout(searchTimer);searchTimer=setTimeout(()=>renderRoute(false,true),160)};

  const modal=$('#modal');
  const modalForm=$('#modalForm');
  modalForm.addEventListener('submit',e=>e.preventDefault());
  $$('[data-close-modal]',modal).forEach(button=>button.onclick=closeModal);
  modal.addEventListener('cancel',e=>{e.preventDefault();closeModal()});
  modal.addEventListener('click',e=>{if(e.target===modal)closeModal()});

  document.addEventListener('keydown',e=>{
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){
      e.preventDefault();$('#globalSearch').focus();return;
    }
    if(e.key==='Escape'){
      if(modal.open){e.preventDefault();closeModal()}
      else closeDrawer();
    }
  });
}
function updateProfile(){const op=currentOperator();$('#profileName').textContent=op.name;$('#profileRole').textContent=`${op.role} · 演示`;$('#profileAvatar').textContent=op.name.slice(0,1);$$('[data-route="manage"]').forEach(el=>el.hidden=op.user_id!=='MANAGER-1');const settingsLabel=$('[data-route="settings"] span:last-of-type');if(settingsLabel)settingsLabel.textContent=op.user_id==='MANAGER-1'?'设置与连接':'身份与设置';document.body.dataset.role=op.user_id==='MANAGER-1'?'manager':'operator'}
async function updateHealth(h,error=''){
  const set=(dot,value,tone,text)=>{if(dot){dot.className=`status-dot ${tone||''}`.trim()}if(value)value.textContent=text};
  const systemDot=$('#systemStatusDot'),systemValue=$('#systemStatusValue');
  if(!h){set(systemDot,systemValue,'','数据异常');return}
  set(systemDot,systemValue,'ok','正常 · 最近更新 '+(h.latest_data_update_minutes||'10')+' 分钟前');
}
function updateBadges(todayCount,reviewCount,agentCount=null){if(todayCount!=null){$('#todayBadge').hidden=!todayCount;$('#todayBadge').textContent=todayCount}if(reviewCount!=null){$('#reviewBadge').hidden=!reviewCount;$('#reviewBadge').textContent=reviewCount}if(agentCount!=null&&$('#agentBadge')){$('#agentBadge').hidden=!agentCount;$('#agentBadge').textContent=agentCount}}
/* renderRoute moved to V0.3 implementation at line 522 */

async function dashboardData(){return cache.dashboard||(cache.dashboard=await api(`/api/dashboard?current_user_id=${encodeURIComponent(currentUser())}`))}
async function ordersData(){return cache.orders||(cache.orders=await api(`/api/orders?current_user_id=${encodeURIComponent(currentUser())}`))}

async function workspaceData(){return cache.workspace||(cache.workspace=await api('/api/action-workspace'))}
const d11TaskLabels={TODO:'待开始',IN_PROGRESS:'处理中',WAITING:'等待中',DONE:'已完成',CANCELLED:'已取消'};
const d11CaseLabels={ACTIONABLE:'可继续处理',WAITING_ONLY:'等待外部',NO_OPEN_TASK:'暂无开放任务',CLOSED:'已关闭'};
function d11TaskTone(status){return({TODO:'amber',IN_PROGRESS:'red',WAITING:'blue',DONE:'green',CANCELLED:'muted'}[status]||'muted')}
function d11CaseTone(state){return({ACTIONABLE:'red',WAITING_ONLY:'blue',NO_OPEN_TASK:'muted',CLOSED:'green'}[state]||'muted')}
function d11CaseTitle(w){const c=w.action_case||{};return c.title||c.latest_recommended_action||c.intent_type||'未命名行动案例'}
function d11EvidenceText(evidence){if(!Array.isArray(evidence)||!evidence.length)return'暂无额外证据';return evidence.map(x=>typeof x==='string'?x:(x.reason||x.message||x.rule_id||JSON.stringify(x))).filter(Boolean).join('；')}
function matchWorkspace(w){if(!search)return true;const c=w.action_case||{},o=w.order||{};const tasks=[...(w.actionable_tasks||[]),...(w.waiting_tasks||[]),...(w.history_tasks||[])];return [d11CaseTitle(w),c.intent_type,c.latest_recommended_action,o.order_no,o.customer_name,...tasks.flatMap(t=>[t.title,t.recommended_action])].join(' ').toLowerCase().includes(search.toLowerCase())}
function d11WaitingText(t){const w=t?.active_waiting;if(!w)return'—';return `${w.reason||w.waiting_type||'等待外部'} · ${fdt(w.due_at)}`}
function d11BusinessActionText(t){const a=t?.business_action,o=t?.outbox;if(!a)return'';const target=a.target_id||a.target_type||'业务对象';const status=a.status==='ACCEPTED'?'系统已接受，尚未确认外部执行':a.status;const ex=o?.durable_execution;if(ex){const map={SUCCESS:'已确认外部执行成功',FAILED_SAFE:'本次未执行',RETRYABLE:'可安全有限重试',RESULT_UNCERTAIN:'结果未知·已暂停自动重试',HUMAN_REQUIRED:'需要人工处理',PENDING:'待执行',IN_FLIGHT:'正在处理'};return `${a.action_type} · ${target} · ${status} · ${map[ex.state]||ex.state}`}const out=o?.status==='PENDING'?'待执行（不等于ERP成功）':o?.status||'无Outbox';return `${a.action_type} · ${target} · ${status} · ${out}`}
function d11CaseRow(w,buttonLabel='打开案例'){
  const c=w.action_case||{},o=w.order||{},state=w.workspace_state;const actionable=w.actionable_tasks||[],waiting=w.waiting_tasks||[];
  const next=actionable[0]||waiting[0];const waitingDue=waiting.map(t=>t.active_waiting?.due_at).filter(Boolean).sort()[0];
  const desc=actionable.length?`${actionable.length} 个可执行任务${actionable.length>1?'（不替你排序）':''}`:waiting.length?`正在等待 · ${waitingDue?relative(waitingDue):'未设置到期时间'}`:'暂无开放任务';
  return `<div class="action-row" style="display:flex;gap:14px;align-items:center;padding:14px 0;border-bottom:1px solid var(--line)"><span class="metric-icon" style="width:34px;height:34px;border-radius:9px;background:var(--surface-2);display:inline-flex;align-items:center;justify-content:center;color:var(--ink-2)">${icon(state==='WAITING_ONLY'?'clock':'tasks')}</span><div style="flex:1;min-width:0"><strong style="font-size:13.5px">${esc(d11CaseTitle(w))}</strong><br><small class="demo-note">${esc(o.order_no||'未关联订单')}${o.customer_name?` · ${esc(o.customer_name)}`:''} · ${esc(desc)}</small>${next?.title?`<br><small class="demo-note">当前：${esc(next.title)}</small>`:''}</div><span class="tag ${d11CaseTone(state)}">${esc(d11CaseLabels[state]||state)}</span><button class="btn link" data-case-detail="${esc(c.action_case_id)}">${esc(buttonLabel)}</button></div>`
}
function d11TaskRow(w,t){const c=w.action_case||{},o=w.order||{};return `<tr><td><strong>${esc(t.title||'未命名任务')}</strong><br><small class="demo-note">${esc(d11CaseTitle(w))}</small></td><td>${esc(o.order_no||'—')}</td><td>${esc(t.recommended_action||'—')}</td><td>${esc(d11WaitingText(t))}</td><td><span class="tag ${d11TaskTone(t.status)}">${esc(d11TaskLabels[t.status]||t.status)}</span></td><td><button class="btn link" data-case-detail="${esc(c.action_case_id)}">打开案例</button></td></tr>`}
function bindD11CaseButtons(root=document){$$('[data-case-detail]',root).forEach(b=>b.onclick=e=>{e.stopPropagation();openCaseDrawer(b.dataset.caseDetail)})}
function invalidateWorkspace(){cache.workspace=null;cache.dashboard=null}


function uiMetric(label,value,color,ic,delta='',up=false,goTo=''){
  return `<div class="metric-card ${color}"${goTo?` data-go="${esc(goTo)}" role="button" tabindex="0"`:''}><span class="metric-icon">${icon(ic)}</span><div class="metric-body"><span class="label">${esc(label)}</span><div class="metric-value-row"><strong>${Number(value||0)}</strong>${delta==='__reserve__'?`<span class="delta up" style="visibility:hidden" aria-hidden="true">▲ 0</span>`:delta?`<span class="delta ${up?'up':'down'}">${up?'▲':'▼'} ${esc(delta)}</span>`:''}</div></div></div>`
}
function taskTone(t){if(t.status==='DONE'||t.action_state==='DONE')return'green';if(['DO_NOW','ESCALATE'].includes(t.action_state))return'red';if(['DO_TODAY','NEEDS_CONFIRMATION'].includes(t.action_state))return'amber';if(['WAITING_EXTERNAL','SCHEDULED'].includes(t.action_state))return'blue';return'muted'}
function taskLabel(t){return stateLabels[t.action_state]||({DONE:'已完成',OPEN:'进行中',PENDING:'待处理'}[t.status])||t.action_state||t.status||'待处理'}
function riskToneValue(v){return({critical:'red',high:'red',medium:'amber',low:'blue',none:'muted'}[String(v||'none').toLowerCase()]||'muted')}
function riskTextValue(v){return riskLabels[String(v||'none').toLowerCase()]||v||'暂无风险'}
function orderWaitingText(o){if(o.waiting_on)return String(o.waiting_on);if(o.contact_status==='WAITING_REPLY')return'等待回复';return'—'}
function uiActionRow(t,buttonLabel='处理'){
  const o=t.order||{};const tone=taskTone(t);const due=t.promised_reply_at||t.next_action_at||t.business_deadline;const ico=t.action_state==='WAITING_EXTERNAL'?'clock':t.source_message_id?'message':'tasks';
  return `<div class="action-row" style="display:flex;gap:14px;align-items:center;padding:14px 0;border-bottom:1px solid var(--line)"><span class="metric-icon" style="width:34px;height:34px;border-radius:9px;background:var(--surface-2);display:inline-flex;align-items:center;justify-content:center;color:var(--ink-2)">${icon(ico)}</span><div style="flex:1"><strong style="font-size:13.5px">${esc(t.title||'未命名任务')}</strong><br><small class="demo-note">${esc(o.order_no||'未关联订单')}${o.customer_name?` · ${esc(o.customer_name)}`:''}</small></div><span class="tag ${tone}">${esc(taskLabel(t))}${due?` · ${esc(relative(due))}`:''}</span><button class="btn link" data-task-detail="${esc(t.task_id)}">${esc(buttonLabel)}</button></div>`
}
function todayReviewRow(x){return `<div class="action-row" style="display:flex;gap:14px;align-items:center;padding:14px 0;border-bottom:1px solid var(--line)"><span class="metric-icon" style="width:34px;height:34px;border-radius:9px;background:var(--surface-2);display:inline-flex;align-items:center;justify-content:center;color:var(--ink-2)">${icon('review')}</span><div style="flex:1"><strong style="font-size:13.5px">${esc(x.order_no||'待关联订单')} · 消息变化候选</strong><br><small class="demo-note">${esc((x.raw_content||'').slice(0,52))}</small></div><button class="btn link" data-go="confirm?review=${esc(x.review_id)}">查看</button></div>`}
function taskSourceInfo(t){const text=String(t.source||t.source_type||t.created_by||'').toUpperCase();if(text.includes('AGENT'))return{t:'Agent巡检生成',c:'agent'};if(t.source_message_id||text.includes('MESSAGE')||text.includes('FT0'))return{t:'消息生成',c:'msg'};if(t.action_state==='WAITING_EXTERNAL'||text.includes('WAIT'))return{t:'等待到期恢复',c:'wait'};return{t:'人工创建',c:'manual'}}
function taskTableRow(t){const src=taskSourceInfo(t);const due=t.next_action_at||t.business_deadline||t.promised_reply_at;return `<tr><td><strong>${esc(t.title||'未命名任务')}</strong></td><td><span class="src-tag ${src.c}">${src.t}</span></td><td>${esc(operatorNames[t.owner_user_id]||t.owner_user_id||'未分配')}</td><td>${esc(fdt(due))}</td><td>${esc(t.waiting_on||'—')}</td><td><span class="tag ${taskTone(t)}">${esc(taskLabel(t))}</span></td><td><button class="btn link" data-task-detail="${esc(t.task_id)}">打开</button></td></tr>`}
function orderTableRow(o){return `<tr><td><strong>${esc(o.order_no)}</strong><br><small class="demo-note">${esc(o.customer_name||'未知客户')}</small></td><td>${esc(o.current_node||'待补充')}</td><td>${esc(operatorNames[o.owner]||o.owner||'未分配')}</td><td>${esc(fdate(o.requested_delivery_date||o.customer_delivery_date))}</td><td><span class="tag ${riskToneValue(o.max_risk)}">${esc(riskTextValue(o.max_risk))}</span></td><td>${esc(readinessNextAction(o))}</td><td>${esc(orderWaitingText(o))}</td><td><button class="btn link" data-order-detail="${esc(o.order_id)}">详情</button></td></tr>`}
function orderTaskRow(t){const src=taskSourceInfo(t);return `<tr><td><strong>${esc(t.title||'未命名任务')}</strong></td><td><span class="src-tag ${src.c}">${src.t}</span></td><td>${esc(fdt(t.next_action_at||t.business_deadline||t.promised_reply_at))}</td><td><span class="tag ${taskTone(t)}">${esc(taskLabel(t))}</span></td></tr>`}
function uiLoadRow(name,n,total){const pct=total?Math.min(100,Math.round(Number(n||0)/total*100)):0;return `<div style="margin:10px 0"><div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:6px"><strong>${esc(name)}</strong><span class="demo-note">${Number(n||0)} 项</span></div><div style="height:8px;background:var(--surface-3);border-radius:999px;overflow:hidden"><div style="width:${pct}%;height:100%;background:var(--brand);border-radius:999px"></div></div></div>`}
function uiManagementRow(ref,title,tag,tone,ico,goTo=''){return `<div class="action-row" style="display:flex;gap:14px;align-items:center;padding:14px 0;border-bottom:1px solid var(--line)"><span class="metric-icon" style="width:34px;height:34px;border-radius:9px;background:var(--surface-2);display:inline-flex;align-items:center;justify-content:center;color:var(--ink-2)">${icon(ico)}</span><div style="flex:1"><strong style="font-size:13.5px">${esc(title)}</strong><br><small class="demo-note">${esc(ref||'')}</small></div><span class="tag ${tone}">${esc(tag)}</span>${goTo?`<button class="btn link" data-go="${esc(goTo)}">查看</button>`:''}</div>`}
function uiSpark(id,values=[]){const nums=(values.length?values:[0,0,0,0,0]).map(Number);const max=Math.max(1,...nums);const pts=nums.map((v,i)=>`${Math.round(i*(320/Math.max(1,nums.length-1)))},${Math.round(100-(v/max)*76)}`).join(' ');return `<svg class="mini-chart" viewBox="0 0 320 120" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="var(--brand)" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`}
function connectionRow(name,status,color='green'){return `<div style="display:flex;align-items:center;gap:10px;padding:12px 0;border-bottom:1px solid var(--line)"><span class="metric-icon" style="width:32px;height:32px;border-radius:8px;background:var(--surface-2);display:inline-flex;align-items:center;justify-content:center">${icon('factory')}</span><div style="flex:1"><strong style="font-size:13.5px">${esc(name)}</strong><br><small class="demo-note">${esc(status)}</small></div><span class="tag ${color}">${color==='green'?'正常':color==='amber'?'待验证':'异常'}</span></div>`}

function agentStoreKey(){return `floworderAgentConversations:${currentUser()}`}
function agentActiveKey(){return `floworderAgentActive:${currentUser()}`}
function newAgentConversation(){return{id:`LCONV-${Date.now()}-${Math.random().toString(16).slice(2,7)}`,title:'新会话',created_at:new Date().toISOString(),updated_at:new Date().toISOString(),messages:[],coze_conversation_id:null,last_job_id:null,last_run_id:null,metrics:null}}
function loadAgentConversations(){try{const v=JSON.parse(localGet(agentStoreKey())||'[]');return Array.isArray(v)?v:[]}catch{return[]}}
function saveAgentConversations(list){localSet(agentStoreKey(),JSON.stringify(list.slice(0,20).map(c=>({...c,messages:(c.messages||[]).slice(-12)}))))}
function saveOneConversation(conv){const list=loadAgentConversations();const i=list.findIndex(x=>x.id===conv.id);if(i>=0)list[i]=conv;else list.unshift(conv);list.sort((a,b)=>String(b.updated_at).localeCompare(String(a.updated_at)));saveAgentConversations(list)}
const agentPollingJobs=new Map();
async function pollConversationJob(root,conversationId,jobId,attempt=0){
  const key=`${conversationId}:${jobId}`;
  if(agentPollingJobs.has(key)&&attempt===0)return;
  agentPollingJobs.set(key,true);
  try{
    const job=await api(`/api/agent/chat/jobs/${encodeURIComponent(jobId)}`,{timeoutMs:12000});
    const conversations=loadAgentConversations();
    const active=conversations.find(x=>x.id===conversationId);
    if(!active){agentPollingJobs.delete(key);return}
    active.last_job_id=jobId;
    active.updated_at=job.updated_at||new Date().toISOString();
    const live=$('#agentLiveStatus',root);
    if(job.status==='COMPLETED'){
      const result=job.result||{};
      if(active.last_processed_job_id!==job.job_id){
        active.messages.push({role:'assistant',content:result.answer||'Agent已完成运行，请查看右侧结构化结果。',time:job.completed_at||new Date().toISOString()});
      }
      active.coze_conversation_id=job.conversation_id||result.conversation_id||active.coze_conversation_id;
      active.last_run_id=job.linked_run_id||result.run?.run_id||active.last_run_id;
      const diagnosis=result.diagnosis||{};
      if(result.diagnosis){active.metrics={screened:Number(diagnosis.screened_order_count||0),risk:Number(diagnosis.risk_order_count||0),gaps:Number(diagnosis.information_gap_order_count||0)}}
      active.last_processed_job_id=job.job_id;
      saveOneConversation(active);
      agentPollingJobs.delete(key);
      await refreshAgentJobUI(root,active,job);
      return;
    }
    if(job.status==='FAILED'){
      if(active.last_processed_job_id!==job.job_id){active.messages.push({role:'assistant',content:`本次运行未完成：${job.error_message||'未知错误'}`,time:job.completed_at||new Date().toISOString()})}
      active.last_processed_job_id=job.job_id;saveOneConversation(active);agentPollingJobs.delete(key);
      if(live)live.textContent=job.error_message||'Agent运行未完成';
      await refreshAgentJobUI(root,active,job);
      return;
    }
    saveOneConversation(active);
    if(live)live.textContent=job.message||(job.status==='QUEUED'?'Agent任务已进入后台队列':'Agent正在执行已识别的工具计划…');
    const statusMeta=agentJobStatusMeta(job,active),statusTag=$('#agentRunStatus',root);if(statusTag){statusTag.className=`tag ${statusMeta.tone}`;statusTag.textContent=statusMeta.label}
    setTimeout(()=>{agentPollingJobs.delete(key);pollConversationJob(root,conversationId,jobId,attempt+1)},attempt<10?1000:2000);
  }catch(err){
    if(attempt<4){setTimeout(()=>{agentPollingJobs.delete(key);pollConversationJob(root,conversationId,jobId,attempt+1)},1800);return}
    agentPollingJobs.delete(key);const live=$('#agentLiveStatus',root);if(live)live.textContent=`状态查询失败：${err.message}`;
  }
}
function agentSessionItem(c,active){const meta=[];if(c.last_run_id)meta.push('已运行');if(c.metrics?.risk!=null)meta.push(`${c.metrics.risk}笔风险`);if(!meta.length)meta.push('未开始');return `<button class="session-item ${active?'active':''}" data-agent-session="${esc(c.id)}"><div class="s-when">${esc(fdt(c.updated_at))}</div><div class="s-title">${esc(c.title||'新会话')}</div><div class="s-meta">${meta.map(x=>`<span>${esc(x)}</span>`).join('')}</div></button>`}
function agentMessagesHtml(messages=[]){if(!messages.length)return '<p class="demo-note">输入一个业务目标开始新会话。系统会保留本次会话ID，后续追问可以继续引用上一轮结果。</p>';return messages.map(m=>`<div class="chat-bubble ${m.role==='user'?'user':'agent'}">${esc(m.content)}<span class="c-time">${esc(fdt(m.time))}</span></div>`).join('')}
function toolDisplayName(name){return({diagnose_priority_orders:'分析订单风险并排序',create_task_draft:'生成任务草稿',create_approval_request:'创建人工审批',deterministic_rule_inspection:'执行规则巡检',backend_finalize_agent_run:'保存运行结果',start_agent_run:'创建运行记录',complete_agent_run:'完成运行记录'}[name]||name)}
function agentStructuredResultHtml({active,job,latest,latestRun,latestCalls,status,metrics}){const answer=job?.result?.answer||(active.messages||[]).filter(x=>x.role==='assistant').slice(-1)[0]?.content||'';const calls=latestCalls||[];const draftId=latestRun?.result?.task_draft_id||job?.result?.task_draft_id||job?.result?.task_draft?.task_draft_id||'—';const approvalId=latestRun?.result?.approval_id||job?.result?.approval_id||job?.result?.task_draft?.approval_id||'—';return `<div class="run-row"><span class="r-label">扫描范围</span><span class="r-val">未来14天 · ${Number(metrics.screened||0)} 笔订单</span></div><div class="run-row"><span class="r-label">风险订单</span><span class="r-val">${Number(metrics.risk||0)} 笔</span></div><div class="run-row"><span class="r-label">信息缺口</span><span class="r-val">${Number(metrics.gaps||0)} 笔，未计入风险排序</span></div><div class="run-row"><span class="r-label">执行过程</span><span class="r-val"><ul class="trace">${calls.map(c=>`<li>${esc(toolDisplayName(c.tool_name))}</li>`).join('')||'<li>尚无执行轨迹</li>'}</ul></span></div><div class="run-row"><span class="r-label">停止原因</span><span class="r-val">${esc(latestRun?.stop_reason||latestRun?.status||job?.status||'尚未运行')}</span></div><div class="run-row"><span class="r-label">任务草稿</span><span class="r-val">${esc(draftId)}</span></div><div class="run-row"><span class="r-label">审批</span><span class="r-val">${esc(approvalId)}</span></div>${answer?`<div class="cc-evidence">${esc(answer)}</div>`:''}<details class="tech-details"><summary>查看技术执行详情</summary><div class="tech-box">run_id：${esc(active.last_run_id||latestRun?.run_id||'—')}<br>conversation_id：${esc(active.coze_conversation_id||job?.conversation_id||job?.result?.conversation_id||'—')}<br>执行方式：${esc(job?.result?.execution_mode||'—')}<br>识别目标：${esc((job?.result?.route_plan?.intents||[]).map(x=>x.intent).join(' → ')||'—')}<br>Agent状态：${status.coze_agent?.configured?'已配置':'未配置'}<br>工具调用：${calls.map(c=>esc(c.tool_name)).join(' → ')||'—'}</div></details><div class="row-actions" style="margin-top:12px"><button class="btn secondary" id="runRuleInspection">仅运行规则巡检</button><button class="btn primary" data-go="confirm">查看审批</button></div>`}

function agentJobStatusMeta(job,active){if(job?.status==='RUNNING')return{label:'运行中',tone:'warning'};if(job?.status==='QUEUED')return{label:'排队中',tone:'warning'};if(job?.status==='FAILED')return{label:'未完成',tone:'danger'};if(active?.last_run_id||job?.status==='COMPLETED')return{label:'已完成',tone:'success'};return{label:'暂无运行',tone:'success'}}
function bindAgentResultActions(root){
  $('#runRuleInspection',root)?.addEventListener('click',async()=>{const b=$('#runRuleInspection',root);b.disabled=true;try{await api('/api/agent/inspection/run',{method:'POST',body:JSON.stringify({current_user_id:currentUser(),current_role:currentUser()==='MANAGER-1'?'manager':'operator',due_within_days:14,top_n:7,goal:'规则巡检未来14天订单',trigger_type:'MANUAL_RULE'}),timeoutMs:120000});cache={operators:cache.operators};toast('规则巡检完成','success');renderRoute(false)}catch(e){toast(e.message,'error')}finally{b.disabled=false}});
}
async function refreshAgentJobUI(root,active,job){
  if(route.name!=='agent'||localGet(agentActiveKey())!==active.id||!document.body.contains(root))return;
  const chat=$('#agentChat',root);if(chat){chat.innerHTML=agentMessagesHtml(active.messages);chat.scrollTop=chat.scrollHeight}
  const sessions=$('#agentSessionList',root);if(sessions){const list=loadAgentConversations();sessions.innerHTML=list.map(c=>agentSessionItem(c,c.id===active.id)).join('');$$('[data-agent-session]',sessions).forEach(b=>b.onclick=()=>{localSet(agentActiveKey(),b.dataset.agentSession);renderRoute(false)})}
  const live=$('#agentLiveStatus',root);if(live)live.textContent=job.status==='FAILED'?(job.error_message||'Agent运行未完成'):'';
  const ask=$('#askAgent',root);if(ask)ask.disabled=false;
  const statusMeta=agentJobStatusMeta(job,active),statusTag=$('#agentRunStatus',root);if(statusTag){statusTag.className=`tag ${statusMeta.tone}`;statusTag.textContent=statusMeta.label}
  try{
    const role=currentUser()==='MANAGER-1'?'manager':'operator';
    const [data,status,trace]=await Promise.all([
      api(`/api/agent/overview?current_user_id=${encodeURIComponent(currentUser())}&current_role=${role}`).catch(()=>({reports:[],latest_run:null,latest_tool_calls:[]})),
      api('/api/agent/status').catch(()=>({coze_agent:{configured:false}})),
      active.last_run_id?api(`/api/agent/runs/${encodeURIComponent(active.last_run_id)}/trace?current_user_id=${encodeURIComponent(currentUser())}&current_role=${role}`).catch(()=>null):Promise.resolve(null)
    ]);
    const latest=job?.result?.diagnosis||data.reports?.[0]?.report||null;
    const latestRun=trace?.run||data.latest_run;
    const latestCalls=trace?.tool_calls||data.latest_tool_calls||[];
    const metrics=active.metrics||{screened:Number(latest?.screened_order_count||0),risk:Number(latest?.risk_order_count||0),gaps:Number(latest?.information_gap_order_count||0)};
    const panel=$('#agentResultPanel',root);if(panel){panel.innerHTML=agentStructuredResultHtml({active,job,latest,latestRun,latestCalls,status,metrics});bindAgentResultActions(root);bindRouteButtons(root)}
  }catch(err){console.error('refreshAgentJobUI failed',err)}
}
function confirmCardShell(category,type,ref,suggest,evidence,fields,risk,by,after,actions,color='muted'){return `<div class="confirm-card" data-confirm-type="${esc(category)}"><div class="cc-head"><span class="tag ${color}">${esc(type)}</span><span class="demo-note">${esc(ref||'')}</span></div><div class="cc-grid"><span class="cc-k">系统建议</span><span class="cc-v">${esc(suggest||'—')}</span><span class="cc-k">影响字段</span><span class="cc-v">${esc(fields||'—')}</span><span class="cc-k">风险</span><span class="cc-v">${esc(risk||'—')}</span><span class="cc-k">发起方</span><span class="cc-v">${esc(by||'—')}</span><span class="cc-k">确认后</span><span class="cc-v">${esc(after||'—')}</span></div><div class="cc-evidence">${esc(evidence||'暂无证据摘要')}</div><div class="row-actions" style="margin-top:12px">${actions}</div></div>`}
function confirmReviewCard(x){const c=x.candidate||safeJson(x.candidate_json,{});const fields=(c.fields||[]).map(f=>fieldLabel(f.field_name)).join('、')||'订单字段候选';const suggest=(c.fields||[]).map(f=>`${fieldLabel(f.field_name)}：${f.normalized_value??'—'}`).join('；')||'查看AI提取的字段变化';const risk=(c.risk_signals||[])[0]?.risk_level||'需人工确认';const actions=x.status==='PENDING'?`<button class="btn primary" data-review-confirm="${esc(x.review_id)}">确认</button><button class="btn ghost" data-review-reject="${esc(x.review_id)}">驳回</button><button class="btn link" data-review-open="${esc(x.review_id)}">查看详情</button>`:`<button class="btn link" data-review-open="${esc(x.review_id)}">查看详情</button>`;return confirmCardShell('数据变更','消息字段变更',x.order_no||x.review_id,suggest,x.raw_content,fields,risk,automationLabel(x.workflow_source),'写回已确认字段并重新生成行动排序',actions,x.status==='PENDING'?'amber':'green')}
function confirmAnomalyCard(x){const evidence=(x.evidence||safeJson(x.evidence_json,[])).join('；');const actions=['ANOMALY_CANDIDATE','PENDING_CONFIRMATION'].includes(x.status)?`<button class="btn primary" data-anomaly-confirm="${esc(x.candidate_id)}">确认</button><button class="btn ghost" data-anomaly-reject="${esc(x.candidate_id)}">驳回</button><button class="btn link" data-go="orders/${esc(x.order_id)}">查看订单</button>`:`<button class="btn link" data-go="orders/${esc(x.order_id)}">查看订单</button>`;return confirmCardShell('异常确认','异常确认',x.order_no||x.candidate_id,x.recommended_action,evidence,anomalyLabels[x.anomaly_type]||x.anomaly_type,x.severity,'Agent巡检','保存异常决策，不自动执行对外动作',actions,'red')}
function confirmApprovalCard(x){const category=/MESSAGE|SEND|DRAFT/i.test(x.action_type||'')?'对外动作':'任务草稿';const orderAction=x.order_id?`<button class="btn link" data-go="orders/${esc(x.order_id)}">查看订单</button>`:'';const actions=x.status==='PENDING'?`<button class="btn primary" data-approval-approve="${esc(x.approval_id)}">批准</button><button class="btn ghost" data-approval-reject="${esc(x.approval_id)}">驳回</button>${orderAction}`:orderAction;return confirmCardShell(category,category==='对外动作'?'对外动作审批':'任务草稿确认',x.order_no||x.approval_id,x.action_type,(x.payload||safeJson(x.payload_json,{})).reason||x.approval_id,x.action_type,x.required_role==='manager'?'高 · 需要主管':'需要人工确认','Agent助手',category==='对外动作'?'只生成或放行待发送草稿，网站不会自动发送':'任务进入任务中心等待执行',actions,category==='对外动作'?'red':'blue')}
async function confirmReviewDirect(id){if(!confirm('确认后将写回正式订单并重新排序，确定继续吗？'))return;try{const x=await api(`/api/reviews/${encodeURIComponent(id)}`);await api(`/api/reviews/${encodeURIComponent(id)}/confirm`,{method:'POST',body:JSON.stringify({candidate:x.candidate||{},operator_id:currentUser()}),timeoutMs:240000});cache={operators:cache.operators};toast('候选已确认并写回','success');renderRoute(false)}catch(e){toast(e.message,'error')}}
async function rejectReviewDirect(id){if(!confirm('确定驳回这条候选吗？'))return;try{await api(`/api/reviews/${encodeURIComponent(id)}/reject`,{method:'POST',body:JSON.stringify({operator_id:currentUser()})});toast('候选已驳回','success');renderRoute(false)}catch(e){toast(e.message,'error')}}
async function openReviewEditor(id){try{const x=await api(`/api/reviews/${encodeURIComponent(id)}`),c=x.candidate||{};openModal({eyebrow:'HUMAN REVIEW',title:x.order_no||'消息变化候选',subtitle:'确认前可编辑字段值；确认后才写回订单。',body:`<div class="quote">${esc(x.raw_content||'无原文')}</div><div class="candidate-section" id="modalCandidateFields">${(c.fields||[]).map((f,i)=>`<label class="field"><span>${esc(fieldLabel(f.field_name))}</span><input data-index="${i}" value="${esc(f.normalized_value??'')}"></label>`).join('')||'<p>没有字段候选</p>'}</div>`,actions:x.status==='PENDING'?`<button class="btn" data-close-modal>取消</button><button class="btn primary" id="modalConfirmReview">确认并写回</button>`:'<button class="btn primary" data-close-modal>关闭</button>'});$('#modalConfirmReview')?.addEventListener('click',async()=>{const edited=JSON.parse(JSON.stringify(c));$$('#modalCandidateFields input').forEach(inp=>{const f=edited.fields[Number(inp.dataset.index)];f.normalized_value=['current_progress','current_progress_percentage'].includes(f.field_name)?Number(inp.value):inp.value});try{await api(`/api/reviews/${encodeURIComponent(id)}/confirm`,{method:'POST',body:JSON.stringify({candidate:edited,operator_id:currentUser()}),timeoutMs:240000});closeModal();cache={operators:cache.operators};toast('候选已确认并写回','success');renderRoute(false)}catch(e){toast(e.message,'error')}})}catch(e){toast(e.message,'error')}}
async function decideApproval(id,decision){if(!confirm(decision==='APPROVE'?'确定批准该动作吗？':'确定驳回该动作吗？'))return;try{const result=await api(`/api/agent/approvals/${encodeURIComponent(id)}/decision`,{method:'POST',body:JSON.stringify({decision,operator_id:currentUser(),current_role:currentUser()==='MANAGER-1'?'manager':'operator'})});if(decision==='APPROVE'&&result?.result?.d12_review_required){toast('旧审批已记录，但客户正式交期不会直接修改；请从订单当前行动发起主管审批','info')}else{toast(decision==='APPROVE'?'审批已通过':'审批已驳回','success')}renderRoute(false)}catch(e){toast(e.message,'error')}}


function taskRecommendedDraft(t){const text=`${t.title||''} ${t.recommended_action||''}`;if(t.target==='factory'||t.waiting_on==='factory'||/工厂|供应商|生产|物料|进度/.test(text))return 'SUPPLIER_PROGRESS_FOLLOWUP';if(/交期|交货|延期|按时/.test(text))return 'DELIVERY_STATUS_REPLY';if(/确认|版本|包装|样品|唛头|设计/.test(text))return 'CUSTOMER_CONFIRMATION_REMINDER';return 'CUSTOMER_REPLY'}
function taskActions(t,compact=false){
  const buttons=[];const order=t.order||{};
  if(order.order_id)buttons.push(`<button class="btn small soft" data-go="orders/${esc(order.order_id)}">查看订单</button>`);
  if(t.action_state==='NEEDS_CONFIRMATION')buttons.push(`<button class="btn small" data-go="confirm">去确认</button>`);
  if(['DO_NOW','DO_TODAY','SCHEDULED','ESCALATE'].includes(t.action_state)&&t.target!=='manager')buttons.push(`<button class="btn small bronze" data-task-draft="${esc(t.task_id)}">生成沟通</button>`);
  if(t.action_state==='WAITING_EXTERNAL'){
    const overdue=t.promised_reply_at&&new Date(t.promised_reply_at)<=new Date();
    if(overdue)buttons.push(`<button class="btn small bronze" data-task-draft="${esc(t.task_id)}">再次催办</button>`);
    buttons.push(`<button class="btn small" data-task-replied="${esc(t.task_id)}">记录已回复</button>`);
  }
  if(!compact&&t.action_state!=='DONE')buttons.push(`<button class="btn small" data-task-detail="${esc(t.task_id)}">详情</button>`);
  return buttons.join('')
}
function actionRow(t){const o=t.order||{};const next=t.next_action_at||t.business_deadline;return `<article class="action-row" data-task-id="${esc(t.task_id)}"><div class="action-primary"><span class="state-marker ${esc(t.action_state)}"></span><div><h3>${esc(t.title||'未命名任务')}</h3><p>${esc((t.priority_reasons||[])[0]||t.recommended_action||'等待补充下一步说明')}</p></div></div><div class="order-link"><strong>${esc(o.order_no||'未关联订单')}</strong><small>${esc(o.customer_name||'')}</small></div><div class="deadline"><strong>${esc(next?fdt(next):'未设置')}</strong><small>${esc(next?relative(next):'需要补充时间')}</small></div><div class="row-actions">${taskActions(t)}</div></article>`}
function bindTaskActions(root,tasks){
  $$('[data-task-detail]',root).forEach(b=>b.onclick=e=>{e.stopPropagation();openTaskDrawer(tasks.find(t=>t.task_id===b.dataset.taskDetail))});
  $$('[data-task-draft]',root).forEach(b=>b.onclick=e=>{e.stopPropagation();const t=tasks.find(x=>x.task_id===b.dataset.taskDraft);openCommunicationDrawer({mode:'ft06',task:t,order:t?.order,draftType:taskRecommendedDraft(t)})});
  $$('[data-task-replied]',root).forEach(b=>b.onclick=async e=>{e.stopPropagation();await markTaskReplied(b.dataset.taskReplied)});
}

async function pageTasks(root){
  const wd=await workspaceData();const workspaces=(wd.items||[]).filter(matchWorkspace);let rows=[];for(const w of workspaces){for(const t of [...(w.actionable_tasks||[]),...(w.waiting_tasks||[]),...(w.history_tasks||[]),...(w.blocked_open_tasks||[])])rows.push({w,t})}
  const filter=route.query.state||activeTaskFilter||'ALL';activeTaskFilter=filter;const filtered=rows.filter(({t})=>filter==='ALL'||t.status===filter);
  const todo=rows.filter(x=>x.t.status==='TODO').length,doing=rows.filter(x=>x.t.status==='IN_PROGRESS').length,waiting=rows.filter(x=>x.t.status==='WAITING').length,done=rows.filter(x=>x.t.status==='DONE').length;
  root.innerHTML=`<div class="page-stack"><div class="metric-grid">${uiMetric('待开始',todo,'amber','tasks')}${uiMetric('处理中',doing,'red','today')}${uiMetric('等待中',waiting,'blue','clock')}${uiMetric('已完成',done,'green','check')}</div><section class="panel"><div class="panel-head"><div><h3>正式 Task</h3><p class="demo-note">每个 Task 都属于一个 Action Case；不再展示 legacy tasks 表。</p></div><div class="row-actions"><input class="btn secondary" id="taskPageSearch" style="height:36px;width:170px;text-align:left" placeholder="搜索案例 / 任务 / 订单" value="${esc(search)}" /><select class="btn secondary" id="taskFilter"><option value="ALL">全部状态</option><option value="TODO" ${filter==='TODO'?'selected':''}>待开始</option><option value="IN_PROGRESS" ${filter==='IN_PROGRESS'?'selected':''}>处理中</option><option value="WAITING" ${filter==='WAITING'?'selected':''}>等待中</option><option value="DONE" ${filter==='DONE'?'selected':''}>已完成</option><option value="CANCELLED" ${filter==='CANCELLED'?'selected':''}>已取消</option></select></div></div><div class="panel-body" style="padding:0"><table class="data-table"><thead><tr><th>任务 / Action Case</th><th>订单</th><th>建议行动</th><th>等待</th><th>状态</th><th></th></tr></thead><tbody>${filtered.map(({w,t})=>d11TaskRow(w,t)).join('')||'<tr><td colspan="6">没有匹配的正式任务</td></tr>'}</tbody></table></div></section></div>`;
  $('#taskPageSearch',root).oninput=e=>{search=e.target.value;$('#globalSearch').value=search;clearTimeout(searchTimer);searchTimer=setTimeout(()=>renderRoute(false,true),160)};
  $('#taskFilter',root).onchange=e=>{activeTaskFilter=e.target.value;go(`tasks?state=${encodeURIComponent(e.target.value)}`)};bindD11CaseButtons(root)
}


async function pageActivation(root){
  const [a,d]=await Promise.all([api(`/api/activation/summary?current_user_id=${encodeURIComponent(currentUser())}`),ordersData()]);const recommended=(a.recommended_orders||[]).filter(matchOrder);
  root.innerHTML=`<div class="page-stack"><div class="metric-grid">${uiMetric('订单底座',a.total_orders,'blue','orders')}${uiMetric('待补充进展',a.needs_initialization,'amber','edit')}${uiMetric('已生成行动',a.action_generated,'green','tasks')}${uiMetric('首次体验目标',a.activation_target,'red','chart')}</div><section class="panel"><div class="panel-head"><h3>完成首次行动初始化</h3><a class="btn secondary" href="/import-orders">继续导入</a></div><div class="panel-body"><div class="flow"><span class="step">① 导入原始订单</span><span class="arrow">→</span><span class="step">② 补充1笔动态</span><span class="arrow">→</span><span class="step">③ 再补2笔形成排序</span></div>${recommended.map(o=>`<div class="action-row" style="display:flex;gap:14px;align-items:center;padding:14px 0;border-bottom:1px solid var(--line)"><div style="flex:1"><strong>${esc(o.order_no)} · ${esc(o.customer_name||'未知客户')}</strong><br><small class="demo-note">${esc(o.product_name||'未填写产品')} · 客户交期 ${esc(fdate(o.requested_delivery_date||o.customer_delivery_date))}</small></div><button class="btn primary" data-quick-init="${esc(o.order_id)}">快速补充状态</button><button class="btn secondary" data-intake-order="${esc(o.order_id)}">粘贴最近沟通</button></div>`).join('')||emptyState('没有待初始化订单','已完成初始化的订单会进入今日工作台和任务中心。')}</div></section></div>`;
  $$('[data-quick-init]',root).forEach(b=>b.onclick=()=>{const o=d.items.find(x=>x.order_id===b.dataset.quickInit);openQuickInitializeModal(o,()=>{cache={operators:cache.operators};renderRoute(false)})});$$('[data-intake-order]',root).forEach(b=>b.onclick=()=>go(`intake?order=${encodeURIComponent(b.dataset.intakeOrder)}`));
}

function orderPriorityScore(o){const risk={none:0,low:1,medium:2,high:3,critical:4}[o.max_risk||'none']||0;const readiness={ACTION_GENERATED:4,READY_FOR_RANKING:3,NEEDS_STATUS:2,BASE_ONLY:1,CLOSED:0}[o.action_readiness||'BASE_ONLY']||0;return risk*10000+Number(o.open_task_count||0)*1000+readiness*100}
function orderSortReason(o){if(['critical','high'].includes(o.max_risk))return `${riskLabels[o.max_risk]}，优先查看`;if(Number(o.open_task_count||0)>0)return `${o.open_task_count}项开放任务`;if(readinessNeedsInput(o.action_readiness))return '待补充进展';const date=o.requested_delivery_date||o.customer_delivery_date;return date?`客户交期 ${fdate(date)}`:'按最近更新时间'}
function sortOrders(items,mode){const rows=[...items];if(mode==='DELIVERY')return rows.sort((a,b)=>String(a.requested_delivery_date||a.customer_delivery_date||'9999').localeCompare(String(b.requested_delivery_date||b.customer_delivery_date||'9999')));if(mode==='UPDATED')return rows.sort((a,b)=>String(b.updated_at||'').localeCompare(String(a.updated_at||'')));if(mode==='ORDER_NO')return rows.sort((a,b)=>String(a.order_no||'').localeCompare(String(b.order_no||''),'zh-CN'));return rows.sort((a,b)=>orderPriorityScore(b)-orderPriorityScore(a)||String(a.next_action_at||a.requested_delivery_date||'9999').localeCompare(String(b.next_action_at||b.requested_delivery_date||'9999')))}

async function pageOrderDetail(root){
  const d=await api(`/api/orders/${encodeURIComponent(route.id)}?current_user_id=${encodeURIComponent(currentUser())}`);const o=d.order;const tasks=d.tasks.filter(t=>t.status!=='DONE').map(t=>({...t,action_state:deriveTaskStateForDetail(t),order:o}));const risks=d.risks||[];const p=progressValue(o.current_progress);const risk=maxRiskFromList(risks);const needsInput=readinessNeedsInput(o.action_readiness||'BASE_ONLY');
  root.innerHTML=`<div class="page-stack"><section class="panel"><div class="panel-head"><h3>${esc(o.order_no)} · ${esc(o.customer_name||'未知客户')}</h3><div class="row-actions"><span class="tag ${riskToneValue(risk)}">${esc(riskTextValue(risk))}</span><button class="btn primary" data-go="confirm">去确认中心</button></div></div><div class="panel-body"><div class="order-hero"><div class="stat"><small>产品</small><strong>${esc(o.product_name||'—')}</strong></div><div class="stat"><small>客户正式交期</small><strong>${esc(fdate(o.requested_delivery_date||o.customer_delivery_date))}</strong></div><div class="stat"><small>供应商完工承诺</small><strong>${esc(fdate(o.latest_supplier_commitment))}</strong></div><div class="stat"><small>当前进度</small><strong>${o.current_progress==null?'待补充':`${Math.round(p)}%`}</strong></div></div><div class="tabs" style="margin:18px -20px 0"><button class="tab active" data-detail-tab="overview">概览</button><button class="tab" data-detail-tab="comm">沟通与变化</button><button class="tab" data-detail-tab="tasks">任务与等待</button><button class="tab" data-detail-tab="risk">风险与历史</button></div></div><div class="panel-body"><div class="tab-panel active" data-detail-panel="overview"><div class="proto-2col"><div><p class="demo-note">核心事实</p><p style="font-size:13px;line-height:1.7">${esc(o.customer_name||'未知客户')} · ${esc(o.product_name||'未填写产品')} · ${esc(o.quantity!=null?`${o.quantity} ${o.unit||''}`:'数量待补充')}。当前节点：${esc(o.current_node||'待补充')}；负责人：${esc(operatorNames[o.owner]||o.owner||'未分配')}。</p></div><div><p class="demo-note">当前状态</p><ul class="timeline"><li><span class="dot"></span><div><strong>${esc(o.current_node||'待补充当前节点')}</strong><br><small class="demo-note">${needsInput?'动态信息不足，暂不生成风险结论':`已有 ${tasks.length} 项开放任务`}</small></div></li><li><span class="dot muted"></span><div><strong>下一步：${esc(readinessNextAction(o))}</strong><br><small class="demo-note">${needsInput?'补充当前进度或最近沟通后再进入排序':'按当前订单事实与任务状态继续推进'}</small></div></li></ul></div></div>${needsInput?`<div class="row-actions" style="margin-top:14px"><button class="btn primary" id="quickInitialize">快速补充状态</button><button class="btn secondary" id="orderIntake">录入消息</button></div>`:''}</div><div class="tab-panel" data-detail-panel="comm"><ul class="timeline">${(d.messages||[]).map(m=>`<li><span class="dot"></span><div><strong>${m.sender_role==='factory'?'工厂/供应商':'客户'} · ${esc(m.source_channel||'')}</strong><br><small class="demo-note">${esc(m.raw_content||'')} · ${esc(fdt(m.source_time||m.created_at))}</small></div></li>`).join('')||'<li><span class="dot muted"></span><div><strong>暂无沟通记录</strong><br><small class="demo-note">从消息中心录入真实客户或工厂消息。</small></div></li>'}</ul></div><div class="tab-panel" data-detail-panel="tasks"><table class="data-table"><thead><tr><th>任务</th><th>来源</th><th>截止</th><th>状态</th></tr></thead><tbody>${tasks.map(orderTaskRow).join('')||'<tr><td colspan="4">暂无开放任务</td></tr>'}</tbody></table></div><div class="tab-panel" data-detail-panel="risk">${risks.map(r=>`<div class="cc-evidence" style="border-left-color:var(--${r.risk_level==='high'||r.risk_level==='critical'?'red':r.risk_level==='medium'?'amber':'line-strong'})"><strong>${esc(r.risk_type)}</strong><br>${esc(r.evidence||'')}</div>`).join('')||'<div class="cc-evidence"><strong>暂无开放风险</strong><br>系统未发现已记录的风险信号。</div>'}<ul class="timeline" style="margin-top:14px">${(d.events||[]).slice(0,12).map(e=>`<li><span class="dot"></span><div><strong>${esc(e.event_type)}</strong><br><small class="demo-note">${esc(eventSummary(e.payload_json))} · ${esc(fdt(e.created_at))}</small></div></li>`).join('')}</ul></div></div></section></div>`;
  $$('[data-detail-tab]',root).forEach(tab=>tab.onclick=()=>{$$('[data-detail-tab]',root).forEach(x=>x.classList.toggle('active',x===tab));$$('[data-detail-panel]',root).forEach(x=>x.classList.toggle('active',x.dataset.detailPanel===tab.dataset.detailTab))});
  $('#orderIntake',root)?.addEventListener('click',()=>go(`intake?order=${encodeURIComponent(o.order_id)}`));$('#quickInitialize',root)?.addEventListener('click',()=>openQuickInitializeModal(o,()=>{cache={operators:cache.operators};renderRoute(false)}));bindTaskActions(root,tasks);bindRouteButtons(root)
}

function maxRiskFromList(list){const rank={none:0,low:1,medium:2,high:3,critical:4};return list.reduce((a,r)=>rank[r.risk_level]>rank[a]?r.risk_level:a,'none')}
function deriveTaskStateForDetail(t){if(t.status==='DONE')return'DONE';if(t.pending_confirmation)return'NEEDS_CONFIRMATION';if(t.waiting_on)return new Date(t.promised_reply_at)>new Date()?'WAITING_EXTERNAL':'DO_NOW';if(t.urgent||t.risk_level==='critical')return'ESCALATE';const due=new Date(t.next_action_at||t.business_deadline);if(!Number.isNaN(due.getTime())){const diff=due-new Date();if(diff<=0)return'DO_NOW';if(diff<86400000)return'DO_TODAY'}return'SCHEDULED'}
function orderFactCards(o){const fields=[['客户名称',o.customer_name],['产品',o.product_name],['SKU',o.sku],['数量',o.quantity!=null?`${o.quantity} ${o.unit||''}`:'—'],['包装方式',o.packaging_method],['当前节点',o.current_node],['工厂',o.factory_name],['客户正式交期',o.requested_delivery_date||o.customer_delivery_date],['工厂完成承诺',o.latest_supplier_commitment],['负责人',operatorNames[o.owner]||o.owner],['规格',o.specification],['材质/颜色',[o.material,o.color].filter(Boolean).join(' / ')]];return fields.map(([l,v])=>`<div class="info-card"><span>${esc(l)}</span><strong>${esc(v||'—')}</strong></div>`).join('')}
function timelineItem(title,body,time){return `<div class="timeline-item"><span class="timeline-dot"></span><div class="timeline-copy"><strong>${esc(title)}</strong><p>${esc(body||'')}</p><time>${esc(fdt(time))}</time></div></div>`}
function eventSummary(v){const p=safeJson(v,{});if(typeof p==='string')return p;const keys=Object.keys(p).slice(0,4);return keys.map(k=>`${k}: ${typeof p[k]==='object'?JSON.stringify(p[k]):p[k]}`).join('；')||'记录已更新'}

async function pageIntake(root){
  const orders=(await ordersData()).items;const preselect=route.query.order||'';
  root.innerHTML=`<div class="page-stack"><div class="proto-2col"><form class="panel" id="intakeForm"><div class="panel-head"><h3>录入消息</h3><span class="tag muted">草稿</span></div><div class="panel-body"><div class="flow"><span class="step">① 粘贴消息</span><span class="arrow">→</span><span class="step">② 选择来源</span><span class="arrow">→</span><span class="step">③ 关联订单</span><span class="arrow">→</span><span class="step">④ AI 提取候选</span><span class="arrow">→</span><span class="step">⑤ 展示证据</span><span class="arrow">→</span><span class="step">⑥ 提交确认</span></div><label class="demo-note">消息来源</label><div class="src-pick"><button type="button" class="src-tag msg" data-sender-role="customer" style="padding:6px 12px">客户消息</button><button type="button" class="src-tag manual" data-sender-role="factory" style="padding:6px 12px;opacity:.55">工厂消息</button></div><input type="hidden" name="sender_role" value="customer"><input type="hidden" name="source_channel" value="email"><label class="demo-note">关联订单</label><select class="btn secondary" name="order_id" style="height:36px;width:100%;text-align:left;margin:6px 0 12px"><option value="">由AI自动匹配</option>${orders.map(o=>`<option value="${esc(o.order_id)}" ${preselect===o.order_id?'selected':''}>${esc(o.order_no)} · ${esc(o.customer_name||'')}</option>`).join('')}</select><textarea class="btn" name="raw_content" style="width:100%;height:110px;text-align:left;padding:12px" placeholder="粘贴真实客户或工厂消息…" required></textarea><input type="hidden" name="source_time"><div class="row-actions" style="margin-top:12px"><button class="btn primary" type="submit" id="analyzeButton">分析消息</button></div></div></form><section class="panel"><div class="panel-head"><h3>变化候选（待提交确认）</h3><span class="tag info" id="intakeState">等待输入</span></div><div class="panel-body" id="intakeResult"><p class="demo-note">系统只生成候选，不直接修改订单。</p></div></section></div></div>`;
  const form=$('#intakeForm',root);form.source_time.value=new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16);$$('[data-sender-role]',root).forEach(b=>b.onclick=()=>{form.sender_role.value=b.dataset.senderRole;form.source_channel.value=b.dataset.senderRole==='factory'?'wechat':'email';$$('[data-sender-role]',root).forEach(x=>x.style.opacity=x===b?'1':'.55')});
  const renderCompleted=async(r,body)=>{cache.dashboard=null;let review=null;try{review=await api(`/api/reviews/${encodeURIComponent(r.review_id)}`)}catch{}const c=review?.candidate||{};const fields=(c.fields||[]).map(x=>fieldLabel(x.field_name)).join('、')||'等待人工查看';const risk=(c.risk_signals||[])[0];$('#intakeState',root).textContent='待确认';$('#intakeResult',root).innerHTML=`<p>AI已生成消息变化候选，尚未写入正式订单。</p><div class="cc-evidence">原文证据：「${esc(body.raw_content)}」</div><div class="run-row" style="margin-top:12px"><span class="r-label">影响字段</span><span class="r-val">${esc(fields)}</span></div><div class="run-row"><span class="r-label">衍生风险</span><span class="r-val">${esc(risk?.evidence||risk?.type||'由人工确认是否影响客户正式交期')}</span></div><div class="run-row"><span class="r-label">提交后</span><span class="r-val">进入确认中心，不写回业务数据</span></div><div class="run-row"><span class="r-label">人工确认后</span><span class="r-val">保存已确认变化并重新生成行动排序</span></div><div class="row-actions" style="margin-top:12px"><button class="btn primary" data-review-now="${esc(r.review_id)}">提交确认</button><button class="btn ghost" id="discardIntakeResult">丢弃</button></div><p class="demo-note" style="margin-top:10px">不在此处生成对外沟通草稿，也不直接写回业务数据。</p>`;$('#intakeResult [data-review-now]',root).onclick=()=>go(`confirm?review=${encodeURIComponent(r.review_id)}`);$('#discardIntakeResult',root).onclick=()=>{$('#intakeResult',root).innerHTML='<p class="demo-note">系统只生成候选，不直接修改订单。</p>';$('#intakeState',root).textContent='等待输入'};toast('消息识别完成，等待人工确认','success')};
  const pollJob=async(jobId,body,attempt=0)=>{if(!root.isConnected||route.name!=='intake')return;try{const j=await api(`/api/intake/jobs/${encodeURIComponent(jobId)}`,{timeoutMs:12000});if(j.status==='COMPLETED'){renderCompleted(j.result,body);return}if(j.status==='FAILED')throw new Error(j.error?.message||j.progress_message||'识别失败');$('#intakeState',root).textContent=j.status==='QUEUED'?'排队中':'分析中';$('#intakeResult',root).innerHTML=`<p class="demo-note">${esc(j.progress_message||'系统正在识别消息')}</p>`;setTimeout(()=>pollJob(jobId,body,attempt+1),attempt<10?1200:2000)}catch(err){$('#intakeState',root).textContent='未完成';$('#intakeResult',root).innerHTML=`<p>${esc(err.message)}</p>`;toast(err.message,'error')}};
  form.onsubmit=async e=>{e.preventDefault();const btn=$('#analyzeButton',root);const body=Object.fromEntries(new FormData(form));btn.disabled=true;btn.textContent='正在提交…';try{const job=await api('/api/intake/jobs',{method:'POST',body:JSON.stringify(body),timeoutMs:15000});$('#intakeState',root).textContent='排队中';$('#intakeResult',root).innerHTML='<p class="demo-note">消息已进入后台识别队列。</p>';pollJob(job.job_id,body)}catch(err){toast(err.message,'error')}finally{btn.disabled=false;btn.textContent='分析消息'}}
}


function fieldLabel(name){return({packaging_method:'包装方式',requested_delivery_date:'客户正式交期',latest_supplier_commitment:'工厂完成承诺',current_progress:'当前进度',current_node:'当前节点',customer_name:'客户名称',product_name:'产品名称'}[name]||name)}


const anomalyLabels={SUPPLIER_COMMITMENT_OVERDUE:'供应商承诺超时',CUSTOMER_CONFIRMATION_BLOCKING:'客户确认阻塞',DELIVERY_RISK:'交期风险',LOGISTICS_EXCEPTION:'物流异常',INFORMATION_GAP:'信息不足'};
const anomalyStatusLabels={ANOMALY_CANDIDATE:'异常候选',PENDING_CONFIRMATION:'待确认',CONFIRMED:'已确认',REJECTED:'已驳回',RESOLVED:'已解决'};
function severityBadge(v='LOW'){const map={CRITICAL:'严重',HIGH:'高',MEDIUM:'中',LOW:'低'};return `<span class="risk-badge ${v==='CRITICAL'?'critical':v.toLowerCase()}">${map[v]||v}</span>`}
function agentCandidateCard(c){
  const evidence=c.evidence||safeJson(c.evidence_json,[]),missing=c.missing_information||safeJson(c.missing_information_json,[]);
  const secondary=(c.secondary_anomaly_types||[]).map(x=>anomalyLabels[x]||x);
  return `<article class="agent-candidate-card"><div class="agent-candidate-head"><div><span class="agent-rank">${esc(c.rank||'')}</span><strong>${esc(c.order_no||c.order_id)} · ${esc(anomalyLabels[c.primary_anomaly_type||c.anomaly_type]||c.primary_anomaly_type||c.anomaly_type)}</strong><small>${esc(c.customer_name||'')}${c.order_anomaly_count>1?` · 共${Number(c.order_anomaly_count)}类异常`:''}</small></div>${severityBadge(c.severity)}</div>${secondary.length?`<div class="secondary-anomalies">次要异常：${secondary.map(esc).join('、')}</div>`:''}<p class="agent-action">${esc(c.recommended_action||'待补充处置建议')}</p><div class="evidence-list">${evidence.slice(0,4).map(x=>`<span>${esc(x)}</span>`).join('')||'<span>暂无证据摘要</span>'}</div>${missing.length?`<div class="missing-note">仍需补充：${esc(missing.join('、'))}</div>`:''}<footer><span class="status ${esc(c.status)}">${esc(anomalyStatusLabels[c.status]||c.status||'待确认')}</span><div class="button-row">${['ANOMALY_CANDIDATE','PENDING_CONFIRMATION'].includes(c.status)?`<button class="btn small primary" data-anomaly-confirm="${esc(c.candidate_id)}">确认异常</button><button class="btn small" data-anomaly-reject="${esc(c.candidate_id)}">驳回</button>`:''}${c.status==='CONFIRMED'?`<button class="btn small" data-anomaly-resolve="${esc(c.candidate_id)}">标记解决</button>`:''}<button class="btn small soft" data-go="orders/${esc(c.order_id)}">查看订单</button></div></footer></article>`
}
function agentConnectionCard(label,state,detail,tone='green'){return `<div class="agent-connection-card ${esc(tone)}"><span>${esc(label)}</span><strong>${esc(state)}</strong><small>${esc(detail)}</small></div>`}
function agentRunTrace(run,calls=[]){
  if(!run)return emptyState('还没有运行轨迹','启动一次Coze Agent诊断或规则巡检后，这里会显示可审计步骤。');
  const result=run.result||{},mode=result.execution_mode||(/RULE/.test(run.trigger_type||'')?'RULE_INSPECTION':'COZE_AGENT');
  const labels={start_agent_run:'创建Agent运行',diagnose_priority_orders:'调用组合诊断技能',deterministic_rule_inspection:'执行确定性规则巡检',create_task_draft:'创建任务草稿',create_approval_request:'创建人工审批',complete_agent_run:'完成Agent运行',backend_finalize_agent_run:'后端完成运行记录'};
  const steps=calls.map((c,i)=>`<li><b>${i+1}</b><div><strong>${esc(labels[c.tool_name]||c.tool_name)}</strong><small>${esc(c.status||'')}${c.duration_ms!=null?` · ${Number(c.duration_ms)}ms`:''}</small>${c.response?.task_draft_id?`<code>task_draft_id: ${esc(c.response.task_draft_id)}</code>`:''}${c.response?.approval_id?`<code>approval_id: ${esc(c.response.approval_id)}</code>`:''}</div></li>`).join('');
  return `<div class="agent-trace-head"><div><span>${mode==='COZE_AGENT'?'Coze Agent运行':'规则巡检运行'}</span><strong>${esc(run.run_id)}</strong></div><div><span>停止原因</span><strong>${esc(run.stop_reason||run.status||'运行中')}</strong></div><div><span>耗时</span><strong>${run.duration_ms==null?'—':`${Number(run.duration_ms)}ms`}</strong></div></div><ol class="agent-trace-list">${steps||'<li><b>1</b><div><strong>运行记录已创建</strong><small>暂无工具调用明细</small></div></li>'}</ol>`
}

async function pageAgent(root){
  const role=currentUser()==='MANAGER-1'?'manager':'operator';
  let conversations=loadAgentConversations();if(!conversations.length){conversations=[newAgentConversation()];saveAgentConversations(conversations)}
  let activeId=localGet(agentActiveKey())||conversations[0].id;let active=conversations.find(x=>x.id===activeId)||conversations[0];localSet(agentActiveKey(),active.id);
  const [data,status,job,trace]=await Promise.all([
    api(`/api/agent/overview?current_user_id=${encodeURIComponent(currentUser())}&current_role=${role}`),
    api('/api/agent/status'),
    active.last_job_id?api(`/api/agent/chat/jobs/${encodeURIComponent(active.last_job_id)}`).catch(()=>null):Promise.resolve(null),
    active.last_run_id?api(`/api/agent/runs/${encodeURIComponent(active.last_run_id)}/trace?current_user_id=${encodeURIComponent(currentUser())}&current_role=${role}`).catch(()=>null):Promise.resolve(null)
  ]);
  updateBadges(null,null,data.summary.candidate_count);
  const latest=data.reports?.[0]?.report||null;const latestRun=trace?.run||data.latest_run;const latestCalls=trace?.tool_calls||data.latest_tool_calls||[];const cozeConfigured=!!status.coze_agent?.configured;
  if(job?.status==='COMPLETED'&&active.last_processed_job_id!==job.job_id){const answer=job.result?.answer||'Agent已完成运行，请查看右侧结构化结果。';active.messages.push({role:'assistant',content:answer,time:job.completed_at||new Date().toISOString()});active.coze_conversation_id=job.conversation_id||job.result?.conversation_id||active.coze_conversation_id;active.last_run_id=job.linked_run_id||job.result?.run?.run_id||active.last_run_id;const diagnosis=job.result?.diagnosis||{};if(job.result?.diagnosis)active.metrics={screened:Number(diagnosis.screened_order_count||0),risk:Number(diagnosis.risk_order_count||0),gaps:Number(diagnosis.information_gap_order_count||0)};active.last_processed_job_id=job.job_id;active.updated_at=job.completed_at||new Date().toISOString();saveOneConversation(active);conversations=loadAgentConversations()}
  if(latestRun?.run_id&&active.last_run_id===latestRun.run_id&&latest){active.metrics={screened:Number(latest.screened_order_count||0),risk:Number(latest.risk_order_count||0),gaps:Number(latest.information_gap_order_count||0)};saveOneConversation(active)}
  const metrics=active.metrics||{screened:Number(latest?.screened_order_count||0),risk:Number(latest?.risk_order_count||0),gaps:Number(latest?.information_gap_order_count||0)};
  root.innerHTML=`<div class="page-stack"><div class="agent-3col">
    <section class="panel" style="display:flex;flex-direction:column"><div class="panel-head"><button class="btn primary" id="newAgentConversation" style="height:30px;padding:0 10px">+ 新建会话</button></div><div class="panel-body"><div class="session-list" id="agentSessionList">${conversations.map(c=>agentSessionItem(c,c.id===active.id)).join('')}</div></div></section>
    <section class="panel" style="display:flex;flex-direction:column"><div class="panel-head"><h3>对话与目标</h3></div><div class="panel-body" style="flex:1;display:flex;flex-direction:column"><div class="chat" id="agentChat">${agentMessagesHtml(active.messages)}</div><div id="agentLiveStatus" class="demo-note" style="margin-top:auto"></div><div class="chat-input"><input id="agentQuestion" type="text" placeholder="输入目标，例如：检查未来14天最需要处理的订单" /><button class="btn primary" id="askAgent">发送</button></div></div></section>
    <section class="panel" style="display:flex;flex-direction:column"><div class="panel-head"><h3>本次运行结果</h3><span id="agentRunStatus" class="tag ${job?.status==='FAILED'?'danger':job?.status==='RUNNING'||job?.status==='QUEUED'?'warning':'success'}">${esc(job?.status==='RUNNING'?'运行中':job?.status==='QUEUED'?'排队中':job?.status==='FAILED'?'未完成':active.last_run_id?'已完成':'暂无运行')}</span></div><div class="panel-body" id="agentResultPanel">${agentStructuredResultHtml({active,job,latest,latestRun,latestCalls,status,metrics})}</div></section>
  </div></div>`;
  $('#newAgentConversation',root).onclick=()=>{const list=loadAgentConversations();const c=newAgentConversation();list.unshift(c);saveAgentConversations(list);localSet(agentActiveKey(),c.id);renderRoute(false)};
  $$('[data-agent-session]',root).forEach(b=>b.onclick=()=>{localSet(agentActiveKey(),b.dataset.agentSession);renderRoute(false)});
  const send=async()=>{const input=$('#agentQuestion',root),question=input.value.trim();if(!question)return toast('请输入业务目标','error');active=loadAgentConversations().find(x=>x.id===active.id)||active;active.messages.push({role:'user',content:question,time:new Date().toISOString()});if(!active.title||active.title==='新会话')active.title=question.slice(0,24);active.updated_at=new Date().toISOString();saveOneConversation(active);const chat=$('#agentChat',root);chat.innerHTML=agentMessagesHtml(active.messages);chat.scrollTop=chat.scrollHeight;const sessions=$('#agentSessionList',root);if(sessions){sessions.innerHTML=loadAgentConversations().map(c=>agentSessionItem(c,c.id===active.id)).join('');$$('[data-agent-session]',sessions).forEach(b=>b.onclick=()=>{localSet(agentActiveKey(),b.dataset.agentSession);renderRoute(false)})}input.value='';$('#askAgent',root).disabled=true;$('#agentLiveStatus',root).textContent='正在创建Agent后台任务…';try{const created=await api('/api/agent/chat/jobs',{method:'POST',body:JSON.stringify({question,current_user_id:currentUser(),current_role:role,due_within_days:14,top_n:7,create_task_draft:true,create_approval_request:true,conversation_id:active.coze_conversation_id||null,previous_run_id:active.last_run_id||null}),timeoutMs:15000});active.last_job_id=created.job_id;active.updated_at=new Date().toISOString();saveOneConversation(active);pollConversationJob(root,active.id,created.job_id)}catch(e){$('#agentLiveStatus',root).textContent=e.message;$('#askAgent',root).disabled=false;toast(e.message,'error')}};
  $('#askAgent',root).onclick=send;$('#agentQuestion',root).onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send()}};
  if(job&&['QUEUED','RUNNING'].includes(job.status))pollConversationJob(root,active.id,job.job_id);
  bindAgentResultActions(root);
  bindRouteButtons(root)
}

async function decideAnomaly(id,decision){try{await api(`/api/agent/candidates/${encodeURIComponent(id)}/decision`,{method:'POST',body:JSON.stringify({decision,operator_id:currentUser(),current_role:currentUser()==='MANAGER-1'?'manager':'operator'})});toast(decision==='CONFIRM'?'异常已由人工确认':decision==='REJECT'?'候选已驳回':'异常已标记解决','success');renderRoute(false)}catch(e){toast(e.message,'error')}}
function openInfoModal(title,html){openModal({eyebrow:'AGENT POLICY',title,subtitle:'受控型Agent不会绕过人工责任边界',body:html,actions:'<button class="btn primary" data-close-modal>知道了</button>'});$$('[data-close-modal]',$('#modal')).forEach(b=>b.onclick=closeModal)}

async function pageManage(root){
  if(currentUser()!=='MANAGER-1'){root.innerHTML=`<section class="panel"><div class="panel-body"><p class="demo-note">当前身份没有团队管理权限。</p><button class="btn primary" data-route="today">返回今日工作台</button></div></section>`;bindRouteButtons(root);return}
  const [d,orders,agent]=await Promise.all([api('/api/management'),api('/api/orders?current_user_id=MANAGER-1').catch(()=>({items:[],summary:{}})),api('/api/agent/overview?current_user_id=MANAGER-1&current_role=manager').catch(()=>({reports:[]}))]);const orderItems=orders.items||[];const tasks=[...(d.escalations||[])];const riskCount=orderItems.filter(o=>!['none',null,undefined].includes(String(o.max_risk))).length;const overdueWait=tasks.filter(t=>t.action_state==='WAITING_EXTERNAL'&&t.promised_reply_at&&new Date(t.promised_reply_at)<new Date()).length;const totalWork=(d.workload||[]).reduce((s,x)=>s+Number(x.total||0),0);const assignedWork=(d.workload||[]).reduce((s,x)=>s+Number(x.total||0),0);const loadPct=totalWork?Math.min(100,Math.round(assignedWork/Math.max(totalWork,1)*100)):0;const unassigned=orderItems.filter(o=>!o.owner).slice(0,3);const stale=tasks.slice(0,2);const latest=agent.reports?.[0]?.report;const coverage=Number(orders.summary?.active||orderItems.length)?Math.round(Number(latest?.screened_order_count||0)/Math.max(1,Number(orders.summary?.active||orderItems.length))*100):0;const maxWork=Math.max(1,...(d.workload||[]).map(x=>Number(x.total||0)));const riskSeries=Object.values(d.state_distribution||{}).map(Number);
  root.innerHTML=`<div class="page-stack"><div class="metric-grid">${uiMetric('风险订单分布',riskCount,'red','alert')}${uiMetric('超时等待',overdueWait,'amber','clock')}${uiMetric('人员负载',loadPct,'blue','tasks')}${uiMetric('未分配订单',unassigned.length,'red','user')}${uiMetric('长时间未处理',stale.length,'amber','clock')}${uiMetric('巡检覆盖率',coverage,'green','spark','__reserve__')}</div><h4 class="section-title">团队状态</h4><div class="proto-2col"><section class="panel"><div class="panel-head"><h3>风险分布</h3><span class="tag info">实时</span></div><div class="panel-body">${uiSpark('management-risk',riskSeries)}<div style="display:flex;gap:16px;margin-top:12px;font-size:12px">${Object.entries(d.state_distribution||{}).slice(0,3).map(([k,v])=>`<span>${esc(stateLabels[k]||k)} ${Number(v||0)}</span>`).join('')}</div></div></section><section class="panel"><div class="panel-head"><h3>人员负载</h3></div><div class="panel-body">${(d.workload||[]).map(w=>uiLoadRow(w.name,w.total,maxWork)).join('')||'<p class="demo-note">暂无团队任务。</p>'}</div></section></div><h4 class="section-title">积压预警</h4><div class="proto-2col"><section class="panel"><div class="panel-head"><h3>未分配订单</h3><span class="tag danger">${unassigned.length} 笔</span></div><div class="panel-body">${unassigned.map(o=>uiManagementRow(o.order_no,'新导入订单待分配负责人','待分配','red','orders',`orders/${o.order_id}`)).join('')||'<p class="demo-note">当前没有未分配订单。</p>'}</div></section><section class="panel"><div class="panel-head"><h3>长时间未处理</h3><span class="tag warning">${stale.length} 项</span></div><div class="panel-body">${stale.map(t=>uiManagementRow(t.order?.order_no||'',t.title||'待处理事项','需关注','amber','clock',t.order_id?`orders/${t.order_id}`:'tasks')).join('')||'<p class="demo-note">当前没有长时间未处理事项。</p>'}</div></section></div></div>`;bindRouteButtons(root)
}

async function pageSettings(root){
  const [s,ops,agentStatus,importStatus,storage]=await Promise.all([api(`/api/settings?user_id=${encodeURIComponent(currentUser())}`),api('/api/operators'),api('/api/agent/status').catch(()=>({})),api('/api/import/capabilities').catch(()=>({})),api('/api/system/storage').catch(()=>({}))]);settings=s.settings||settings;cache.operators=ops.items||cache.operators||[];const agentConfigured=!!agentStatus.coze_agent?.configured;const scheduleVerified=!!agentStatus.daily_schedule?.scheduler_verified;const scheduleConfigured=!!agentStatus.daily_schedule?.configured;const importReady=Array.isArray(importStatus.supported_formats)&&importStatus.supported_formats.length>0;
  root.innerHTML=`<div class="page-stack"><section class="panel"><div class="panel-head"><h3>身份与权限（演示模式）</h3><span class="tag muted">演示</span></div><div class="panel-body"><div class="row-actions" style="margin-bottom:14px"><select class="btn secondary" id="currentUserSelect">${ops.items.map(o=>`<option value="${esc(o.user_id)}" ${o.user_id===currentUser()?'selected':''}>${esc(o.name)} · ${esc(o.role)}</option>`).join('')}</select><button class="btn primary" id="saveSettings">保存身份</button></div><p class="demo-note">切换身份仅用于MVP演示；跟单专员查看本人订单，主管查看团队数据。</p></div></section><h4 class="section-title">系统状态</h4><div class="proto-2col"><section class="panel"><div class="panel-head"><h3>集成与连接</h3><span class="tag ${importReady&&storage.on_persistent_path?'success':'warning'}">${importReady&&storage.on_persistent_path?'已连接':'部分待验证'}</span></div><div class="panel-body">${connectionRow('订单底座导入',importReady?`${importStatus.supported_formats.join(' / ')} · 最多${Number(importStatus.max_rows||0)}行`:'能力状态读取失败',importReady?'green':'red')}${connectionRow('数据持久化',storage.on_persistent_path?'持久化路径已启用':'需要检查持久化路径',storage.on_persistent_path?'green':'amber')}${connectionRow('Coze Agent',agentConfigured?'Agent已配置':'未配置，规则巡检仍可用',agentConfigured?'green':'amber')}<div class="row-actions" style="margin-top:10px"><a class="btn secondary" href="/import-orders">打开订单导入</a></div></div></section><section class="panel"><div class="panel-head"><h3>Agent 与巡检状态</h3><span class="tag ${agentConfigured&&scheduleVerified?'success':'warning'}">${agentConfigured&&scheduleVerified?'已验证':'部分待验证'}</span></div><div class="panel-body"><div class="run-row"><span class="r-label">系统服务</span><span class="r-val">在线</span></div><div class="run-row"><span class="r-label">Agent 服务</span><span class="r-val">${agentConfigured?'Coze Agent 已配置':'未配置'}</span></div><div class="run-row"><span class="r-label">定时巡检</span><span class="r-val">${scheduleVerified?'外部调度已验证':scheduleConfigured?'接口与密钥已配置；外部调度尚未验证':'未配置'}</span></div><div class="run-row"><span class="r-label">数据存储</span><span class="r-val">${storage.on_persistent_path?'持久化已启用':'待验证'}</span></div><div class="business-note">页面只展示后端状态接口返回的真实连接状态，不写死模型名称或运行结果。</div></div></section></div></div>`;
  $('#saveSettings',root).onclick=async()=>{const uid=$('#currentUserSelect',root).value;try{await api(`/api/settings?user_id=${encodeURIComponent(currentUser())}`,{method:'PATCH',body:JSON.stringify({current_user_id:uid})});localSet('currentUserId',uid);settings.current_user_id=uid;cache={};updateProfile();toast('身份已切换','success');go('today')}catch(e){toast(e.message,'error')}}
}

function integrationItem(name,desc,state,ok){return `<div class="integration-item"><div><strong>${esc(name)}</strong><small>${esc(desc)}</small></div><span class="integration-state ${ok?'':'bad'}">${esc(state)}</span></div>`}

function openModal({eyebrow='操作',title,subtitle='',body,actions}){
  const m=$('#modal');
  if(m.open)m.close();
  $('#modalEyebrow').textContent=eyebrow;
  $('#modalTitle').textContent=title;
  $('#modalSubtitle').textContent=subtitle;
  $('#modalBody').innerHTML=body;
  $('#modalActions').innerHTML=actions||'<button class="btn" type="button" data-close-modal>取消</button>';
  $$('[value="cancel"], [data-close-modal]',m).forEach(button=>{
    button.type='button';
    button.setAttribute('data-close-modal','');
    button.onclick=closeModal;
  });
  m.returnValue='';
  m.showModal();
  document.body.classList.add('modal-open');
  requestAnimationFrame(()=>$('#modalBody input, #modalBody select, #modalBody textarea, #modalActions button',m)?.focus());
  return m;
}
function closeModal(){
  const m=$('#modal');
  if(m.open)m.close('cancel');
  document.body.classList.remove('modal-open');
}
function openQuickInitializeModal(o,onDone=null){
  if(!o)return toast('订单不存在','error');const tomorrow=new Date(Date.now()+24*3600000-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16);const m=openModal({eyebrow:'QUICK INITIALIZATION',title:`补充 ${o.order_no} 当前进展`,subtitle:'只收集生成首次行动所需的最少信息；后续可通过真实沟通继续更新。',body:`<div class="boundary-note">原始订单只能建立事实底座。这里的快速标记用于生成一条初步行动，不会替代真实沟通证据。</div><div class="form-grid" style="margin-top:16px"><label class="field"><span>当前节点 *</span><select name="current_node"><option value="不清楚">不清楚</option><option value="尚未开始">尚未开始</option><option value="备料中">备料中</option><option value="生产中">生产中</option><option value="待确认">待确认</option><option value="待出运">待出运</option><option value="已出货">已出货</option><option value="已完成">已完成</option><option value="已取消">已取消</option></select></label><label class="field"><span>最近联系状态 *</span><select name="contact_status" id="initContactStatus"><option value="NOT_CONTACTED">尚未联系</option><option value="WAITING_REPLY">已联系，等待回复</option><option value="REPLIED">已收到回复</option><option value="UNKNOWN">不清楚</option></select></label><label class="field"><span>是否有已知异常 *</span><select name="issue_status"><option value="UNKNOWN">不清楚</option><option value="NONE">暂无已知异常</option><option value="KNOWN">存在已知异常</option></select></label><label class="field"><span>补充说明 <small>可选</small></span><input name="initialization_note" placeholder="例如：拉链到货时间不明确"></label></div><div class="form-grid init-wait-fields" id="initWaitFields" hidden><label class="field"><span>等待对象</span><select name="waiting_on"><option value="factory">工厂</option><option value="customer">客户</option></select></label><label class="field"><span>承诺回复时间</span><input name="promised_reply_at" type="datetime-local" value="${tomorrow}"></label></div><div class="activation-tip"><strong>更准确的方式：</strong>有最近客户或工厂消息时，取消本窗口并选择“粘贴最近沟通”，由AI提取进度、承诺和未回答事项。</div>`,actions:'<button class="btn" value="cancel">取消</button><button class="btn primary" type="button" id="initializeOrderSubmit">生成初步行动</button>'});const contact=$('#initContactStatus',m),wait=$('#initWaitFields',m);const toggle=()=>wait.hidden=contact.value!=='WAITING_REPLY';contact.onchange=toggle;toggle();$('#initializeOrderSubmit',m).onclick=async()=>{const body=Object.fromEntries($$('select,input',$('#modalBody',m)).map(el=>[el.name,el.value]));if(body.contact_status==='WAITING_REPLY'&&!body.promised_reply_at)return toast('请填写承诺回复时间','error');if(body.promised_reply_at)body.promised_reply_at=new Date(body.promised_reply_at).toISOString();const btn=$('#initializeOrderSubmit',m);btn.disabled=true;btn.textContent='正在生成行动…';try{const r=await api(`/api/orders/${o.order_id}/initialize`,{method:'POST',body:JSON.stringify({...body,operator_id:currentUser()})});cache={operators:cache.operators};closeModal();toast(r.action_readiness==='CLOSED'?'订单已标记完成':'已生成初步行动，可继续补充其他订单','success');onDone?onDone(r):go('activation')}catch(e){toast(e.message,'error')}finally{btn.disabled=false;btn.textContent='生成初步行动'}}
}

async function openNewOrderModal(){
  const m=openModal({eyebrow:'CREATE ORDER',title:'新建订单',subtitle:'建立真实订单底座后，任务和沟通才能获得可靠上下文。',body:`<div class="form-grid"><label class="field"><span>订单号 *</span><input name="order_no" required placeholder="例如 PO-2026-001"></label><label class="field"><span>客户名称 *</span><input name="customer_name" required></label><label class="field"><span>产品名称</span><input name="product_name"></label><label class="field"><span>SKU</span><input name="sku"></label><label class="field"><span>数量</span><input name="quantity" type="number" step="any"></label><label class="field"><span>单位</span><input name="unit" placeholder="pcs"></label><label class="field"><span>客户正式交期</span><input name="customer_delivery_date" type="date"></label><label class="field"><span>当前节点</span><input name="current_node" placeholder="例如 资料确认"></label><label class="field"><span>工厂名称</span><input name="factory_name"></label>${currentUser()==='MANAGER-1'?`<label class="field"><span>负责人</span><select name="owner"><option value="">待分配</option>${(cache.operators||[]).filter(o=>o.user_id!=='MANAGER-1').map(o=>`<option value="${esc(o.user_id)}">${esc(o.name)}</option>`).join('')}</select></label>`:`<label class="field"><span>负责人</span><input value="${esc(operatorNames[currentUser()]||currentUser())}" disabled><input name="owner" type="hidden" value="${esc(currentUser())}"></label>`}</div>`,actions:'<button class="btn" value="cancel">取消</button><button class="btn primary" type="button" id="createOrderSubmit">创建订单</button>'});$('#createOrderSubmit',m).onclick=async()=>{const body=Object.fromEntries($$('input,select',$('#modalBody')).map(el=>[el.name,el.value]));if(!body.order_no.trim()||!body.customer_name.trim())return toast('请填写订单号和客户名称','error');if(body.quantity!=='')body.quantity=Number(body.quantity);try{const r=await api('/api/orders',{method:'POST',body:JSON.stringify({...body,operator_id:currentUser()})});cache.orders=null;closeModal();toast('订单已创建','success');go(`orders/${r.order_id}`)}catch(e){toast(e.message,'error')}}
}
function openEditOrderModal(o){const m=openModal({eyebrow:'EDIT ORDER',title:`编辑 ${o.order_no}`,subtitle:'事实记录可以直接更新；客户正式交期属于对外承诺，需要从当前行动发起主管审批。',body:`<div class="form-grid"><label class="field"><span>订单号</span><input name="order_no" value="${esc(o.order_no||'')}"></label><label class="field"><span>客户名称</span><input name="customer_name" value="${esc(o.customer_name||'')}"></label><label class="field"><span>产品名称</span><input name="product_name" value="${esc(o.product_name||'')}"></label><label class="field"><span>SKU</span><input name="sku" value="${esc(o.sku||'')}"></label><label class="field"><span>包装方式</span><input name="packaging_method" value="${esc(o.packaging_method||'')}"></label><label class="field"><span>当前节点</span><input name="current_node" value="${esc(o.current_node||'')}"></label><label class="field"><span>客户正式交期</span><input type="date" disabled value="${esc((o.requested_delivery_date||o.customer_delivery_date||'').slice(0,10))}"><small>如需修改，请从订单当前行动发起主管审批。</small></label><label class="field"><span>工厂完成承诺</span><input name="supplier_completion_commitment_date" type="date" value="${esc((o.latest_supplier_commitment||'').slice(0,10))}"><small>这是事实记录，不等于修改客户正式承诺。</small></label><label class="field"><span>当前进度 %</span><input name="current_progress" type="number" min="0" max="100" value="${Math.round(progressValue(o.current_progress))}"></label><label class="field"><span>工厂名称</span><input name="factory_name" value="${esc(o.factory_name||'')}"></label>${currentUser()==='MANAGER-1'?`<label class="field"><span>负责人</span><select name="owner">${(cache.operators||[]).filter(op=>op.user_id!=='MANAGER-1').map(op=>`<option value="${esc(op.user_id)}" ${op.user_id===(o.owner||'')?'selected':''}>${esc(op.name)}</option>`).join('')}</select></label>`:''}</div>`,actions:'<button class="btn" value="cancel">取消</button><button class="btn primary" type="button" id="saveOrder">保存修改</button>'});$('#saveOrder',m).onclick=async()=>{const body=Object.fromEntries($$('input[name],select[name]',$('#modalBody')).map(el=>[el.name,el.value]));body.current_progress=body.current_progress===''?null:Number(body.current_progress)/100;try{await api(`/api/orders/${o.order_id}`,{method:'PATCH',body:JSON.stringify({...body,operator_id:currentUser()})});cache={operators:cache.operators};closeModal();toast('订单已更新','success');renderRoute(false)}catch(e){toast(e.message,'error')}}}
async function openTaskModal(order=null){const orders=(await ordersData()).items;const m=openModal({eyebrow:'CREATE TASK',title:'新建任务',subtitle:'任务会进入真实行动排序，并可在任务中生成沟通。',body:`<div class="form-grid"><label class="field full"><span>关联订单 *</span><select name="order_id"><option value="">请选择订单</option>${orders.map(o=>`<option value="${esc(o.order_id)}" ${order?.order_id===o.order_id?'selected':''}>${esc(o.order_no)} · ${esc(o.customer_name||'')}</option>`).join('')}</select></label><label class="field full"><span>任务标题 *</span><input name="title" placeholder="例如：确认面料到厂时间"></label><label class="field full"><span>建议行动</span><textarea name="recommended_action" rows="3"></textarea></label><label class="field"><span>沟通对象</span><select name="target"><option value="factory">工厂/供应商</option><option value="customer">客户</option><option value="manager">主管</option><option value="internal">内部</option></select></label>${currentUser()==='MANAGER-1'?`<label class="field"><span>负责人</span><select name="owner_user_id">${(cache.operators||[]).filter(o=>o.user_id!=='MANAGER-1').map(o=>`<option value="${esc(o.user_id)}">${esc(o.name)}</option>`).join('')}</select></label>`:`<label class="field"><span>负责人</span><input value="${esc(operatorNames[currentUser()]||currentUser())}" disabled><input name="owner_user_id" type="hidden" value="${esc(currentUser())}"></label>`}<label class="field"><span>下一行动时间</span><input name="next_action_at" type="datetime-local"></label><label class="field"><span>风险等级</span><select name="risk_level"><option value="low">低</option><option value="medium" selected>中</option><option value="high">高</option><option value="critical">严重</option></select></label></div>`,actions:'<button class="btn" value="cancel">取消</button><button class="btn primary" type="button" id="createTask">创建任务</button>'});$('#createTask',m).onclick=async()=>{const body=Object.fromEntries($$('input,select,textarea',$('#modalBody')).map(el=>[el.name,el.value]));if(!body.order_id||!body.title.trim())return toast('请选择订单并填写任务标题','error');if(body.next_action_at)body.next_action_at=new Date(body.next_action_at).toISOString();try{await api(`/api/orders/${body.order_id}/tasks`,{method:'POST',body:JSON.stringify({...body,operator_id:currentUser()})});cache.dashboard=null;closeModal();toast('任务已创建','success');renderRoute(false)}catch(e){toast(e.message,'error')}}}
function openCommunicationKeyModal(){const m=openModal({eyebrow:'SECURITY',title:'沟通工作流操作密钥',subtitle:'仅保存在当前浏览器会话，不会写入页面或日志。',body:`<label class="field"><span>COMMUNICATION_ADMIN_KEY</span><input id="communicationKeyInput" type="password" value="${esc(sessionGet('communicationAdminKey')||'')}" placeholder="Render中配置的密钥"></label>`,actions:'<button class="btn" value="cancel">取消</button><button class="btn primary" type="button" id="saveCommunicationKey">保存</button>'});$('#saveCommunicationKey',m).onclick=()=>{const v=$('#communicationKeyInput',m).value.trim();if(!v)return toast('请输入操作密钥','error');sessionSet('communicationAdminKey',v);closeModal();toast('操作密钥已保存到本次会话','success')}}

async function openTaskDrawer(task){if(!task)return;let detail=task;try{detail=(await api(`/api/tasks/${task.task_id}?current_user_id=${encodeURIComponent(currentUser())}`)).task}catch{}const o=detail.order||task.order||{};openDrawer(`<div class="drawer-head"><div><span>TASK CONTEXT</span><h2>${esc(detail.title)}</h2><p>${esc(orderDisplay(o))}</p></div><button class="drawer-close" data-close-drawer>×</button></div><div class="drawer-body"><div class="context-strip"><div class="context-chip"><span>状态</span><strong>${esc(stateLabels[detail.action_state]||detail.action_state)}</strong></div><div class="context-chip"><span>风险</span><strong>${esc(riskLabels[detail.risk_level]||detail.risk_level)}</strong></div><div class="context-chip"><span>下一时间</span><strong>${esc(fdt(detail.next_action_at||detail.business_deadline))}</strong></div></div><section class="ai-result"><div class="ai-result-head"><div><h3>系统为什么把它排在这里</h3><p>${esc((detail.priority_reasons||[]).join('；')||detail.recommended_action||'暂无排序原因')}</p></div>${statusBadge(detail.action_state)}</div><div class="quote">${esc((detail.evidence||[]).join('；')||'没有额外证据')}</div></section><div class="info-grid"><div class="info-card"><span>负责人</span><strong>${esc(operatorNames[detail.owner_user_id]||detail.owner_user_id||'未分配')}</strong></div><div class="info-card"><span>沟通对象</span><strong>${esc(detail.target||'—')}</strong></div><div class="info-card"><span>等待对象</span><strong>${esc(detail.waiting_on||'—')}</strong></div></div><div class="review-actions">${taskActions(detail,true)}${detail.action_state!=='DONE'?`<button class="btn primary" id="drawerComplete">完成任务</button>`:''}</div></div>`);bindRouteButtons($('#drawer'));bindTaskActions($('#drawer'),[detail]);$('#drawerComplete')?.addEventListener('click',()=>completeTask(detail.task_id))}
function openDrawer(html){$('#drawerBody').innerHTML=html;$('#drawer').classList.add('open');$('#drawer').setAttribute('aria-hidden','false');$('#drawerMask').hidden=false;requestAnimationFrame(()=>$('#drawerMask').classList.add('show'));$$('[data-close-drawer]',$('#drawer')).forEach(b=>b.onclick=closeDrawer)}
function closeDrawer(){$('#drawer').classList.remove('open');$('#drawer').setAttribute('aria-hidden','true');$('#drawerMask').classList.remove('show');setTimeout(()=>{$('#drawerMask').hidden=true;$('#drawerBody').innerHTML=''},220);communicationContext=null;communicationRun=null}

function factsFromOrder(o){if(!o)return[];const map=[['order_no',o.order_no],['customer_name',o.customer_name],['product_name',o.product_name],['customer_delivery_date',o.requested_delivery_date||o.customer_delivery_date],['supplier_completion_commitment_date',o.latest_supplier_commitment],['current_progress',o.current_progress],['current_node',o.current_node],['factory_name',o.factory_name],['packaging_method',o.packaging_method],['quantity',o.quantity],['unit',o.unit]];return map.filter(([,v])=>v!==null&&v!==undefined&&v!=='').map(([fact_type,value],i)=>({fact_id:`WEB-${String(i+1).padStart(3,'0')}`,fact_type,value,confirmed:true}))}
function communicationContextHtml(ctx){const o=ctx.order||ctx.task?.order||{};return `<div class="context-strip"><div class="context-chip"><span>订单</span><strong>${esc(o.order_no||'待选择')}</strong></div><div class="context-chip"><span>客户</span><strong>${esc(o.customer_name||'—')}</strong></div><div class="context-chip"><span>当前任务</span><strong>${esc(ctx.task?.title||'从订单发起')}</strong></div></div>`}
function openCommunicationDrawer(ctx){communicationContext=ctx;const o=ctx.order||ctx.task?.order||{};const mode=ctx.mode||'ft06';openDrawer(`<div class="drawer-head"><div><span>CONTEXTUAL COMMUNICATION</span><h2>在当前行动中处理沟通</h2><p>沟通不是独立助手；生成、确认和等待状态都与当前订单/任务关联。</p></div><button class="drawer-close" data-close-drawer>×</button></div><div class="drawer-body">${communicationContextHtml(ctx)}<div class="drawer-tabs"><button class="drawer-tab ${mode==='ft06'?'active':''}" data-comm-tab="ft06">生成沟通草稿</button><button class="drawer-tab ${mode==='ft05'?'active':''}" data-comm-tab="ft05">沟通转任务</button></div><div id="communicationPanel"></div></div>`);$$('[data-comm-tab]',$('#drawer')).forEach(b=>b.onclick=()=>{$$('[data-comm-tab]',$('#drawer')).forEach(x=>x.classList.toggle('active',x===b));renderCommunicationPanel(b.dataset.commTab)});renderCommunicationPanel(mode)}
function renderCommunicationPanel(mode){const p=$('#communicationPanel');if(mode==='ft05')renderFT05Panel(p);else renderFT06Panel(p)}
function renderFT05Panel(p){const msg=communicationContext.message||{};p.innerHTML=`<div class="page-stack"><label class="field"><span>发送方</span><select id="ft05Sender"><option value="customer" ${msg.sender_role==='customer'?'selected':''}>客户</option><option value="factory" ${msg.sender_role==='factory'?'selected':''}>工厂/供应商</option></select></label><label class="field"><span>沟通渠道</span><select id="ft05Channel"><option value="email" ${msg.source_channel==='email'?'selected':''}>邮件</option><option value="wechat" ${msg.source_channel==='wechat'?'selected':''}>企业微信</option><option value="internal">人工记录</option></select></label><label class="field"><span>沟通原文</span><textarea id="ft05Text" rows="8" placeholder="粘贴需要转成任务的真实沟通">${esc(msg.raw_content||'')}</textarea></label><div class="boundary-note">系统只生成任务候选。正式任务仍需人工确认后写回。</div><button class="btn primary" id="runFT05">生成任务候选</button><div id="ft05Result"></div></div>`;$('#runFT05',p).onclick=runFT05}
async function runFT05(){const p=$('#communicationPanel'),o=communicationContext.order||communicationContext.task?.order;const text=$('#ft05Text',p).value.trim();if(!text)return toast('请输入沟通原文','error');if(!o?.order_id&&!o?.order_no)return toast('请从订单或任务上下文发起沟通转任务','error');const b=$('#runFT05',p);b.disabled=true;b.textContent='正在理解沟通…';try{const r=await api('/api/workflows/ft05/run',{method:'POST',body:JSON.stringify({communication_text:text,sender_role:$('#ft05Sender',p).value,channel:$('#ft05Channel',p).value,order_id:o.order_id,order_no:o.order_no,order_context:o,source_message_id:communicationContext.message?.source_message_id||null})});communicationRun=r;renderFT05Result(r)}catch(e){toast(e.message,'error')}finally{b.disabled=false;b.textContent='生成任务候选'}}
function renderFT05Result(r){const p=$('#ft05Result'),result=r.result||{},c=result.task_candidate||{},ready=result.run_status==='task_candidate_ready';p.innerHTML=`<section class="ai-result"><div class="ai-result-head"><div><h3>${esc(c.task_title||'任务候选')}</h3><p>${esc(c.reason||result.technical?.error_message||'AI已完成理解')}</p></div>${statusBadge(ready?'NEEDS_CONFIRMATION':result.run_status)}</div><div class="form-grid"><label class="field full"><span>任务标题</span><input id="candidateTitle" value="${esc(c.task_title||'')}"></label><label class="field full"><span>任务说明</span><textarea id="candidateDesc" rows="4">${esc(c.task_description||'')}</textarea></label><label class="field"><span>截止时间</span><input id="candidateDue" value="${esc(c.due_at_candidate||'')}"></label><label class="field"><span>优先级</span><select id="candidatePriority"><option value="low" ${c.priority_hint==='low'?'selected':''}>低</option><option value="normal" ${c.priority_hint==='normal'?'selected':''}>普通</option><option value="medium" ${c.priority_hint==='medium'?'selected':''}>中</option><option value="high" ${c.priority_hint==='high'?'selected':''}>高</option></select></label></div><div class="quote">原文证据：${esc(c.source_quote||'—')}</div>${ready&&r.candidate_id?'<div class="review-actions"><button class="btn primary" id="commitFT05">人工确认并写回任务</button><button class="btn danger" id="rejectFT05">驳回候选</button></div>':''}</section>`;$('#commitFT05',p)?.addEventListener('click',commitFT05);$('#rejectFT05',p)?.addEventListener('click',rejectFT05)}
async function commitFT05(){const c={...(communicationRun.result?.task_candidate||{})};c.task_title=$('#candidateTitle').value;c.task_description=$('#candidateDesc').value;c.due_at_candidate=$('#candidateDue').value;c.priority_hint=$('#candidatePriority').value;try{await api(`/api/communication/candidates/${communicationRun.candidate_id}/commit`,{method:'POST',body:JSON.stringify({operator_id:currentUser(),edited_candidate:c,confirmation_version:'5.0',note:'在订单行动上下文中人工确认'}) ,timeoutMs:240000});cache.dashboard=null;toast('任务候选已写回并重新排序','success');closeDrawer();renderRoute(false)}catch(e){toast(e.message,'error')}}
async function rejectFT05(){try{await api(`/api/communication/candidates/${communicationRun.candidate_id}/reject`,{method:'POST',body:JSON.stringify({operator_id:currentUser(),note:'人工驳回'})});toast('候选已驳回','success');closeDrawer()}catch(e){toast(e.message,'error')}}
function renderFT06Panel(p){const ctx=communicationContext,o=ctx.order||ctx.task?.order||{};const defaultType=ctx.draftType||'CUSTOMER_REPLY';p.innerHTML=`<div class="page-stack"><label class="field"><span>沟通场景</span><select id="ft06Type">${Object.entries(draftTypeLabels).map(([k,l])=>`<option value="${k}" ${k===defaultType?'selected':''}>${l}</option>`).join('')}</select></label><div class="form-grid"><label class="field"><span>接收方</span><select id="ft06Recipient"><option value="customer">客户</option><option value="supplier" ${defaultType==='SUPPLIER_PROGRESS_FOLLOWUP'?'selected':''}>工厂/供应商</option><option value="internal" ${defaultType==='CHANGE_HISTORY_SUMMARY'?'selected':''}>内部</option></select></label><label class="field"><span>渠道</span><select id="ft06Channel"><option value="email">邮件</option><option value="wechat" ${defaultType==='SUPPLIER_PROGRESS_FOLLOWUP'?'selected':''}>企业微信</option><option value="internal">内部记录</option></select></label></div><label class="field"><span>本次要求</span><textarea id="ft06Instruction" rows="5" placeholder="补充你希望本次回复强调或询问的内容">${esc(defaultInstruction(defaultType,ctx.task))}</textarea></label><div class="boundary-note">系统只使用订单事实和沟通历史生成草稿。所有内容必须人工审核，网站不会自动发送。</div><button class="btn primary" id="runFT06">基于订单事实生成</button><div id="ft06Result"></div></div>`;$('#ft06Type',p).onchange=e=>{$('#ft06Instruction',p).value=defaultInstruction(e.target.value,ctx.task);if(e.target.value==='SUPPLIER_PROGRESS_FOLLOWUP'){$('#ft06Recipient',p).value='supplier';$('#ft06Channel',p).value='wechat'}else if(e.target.value==='CHANGE_HISTORY_SUMMARY'){$('#ft06Recipient',p).value='internal';$('#ft06Channel',p).value='internal'}else{$('#ft06Recipient',p).value='customer';$('#ft06Channel',p).value='email'}};$('#runFT06',p).onclick=runFT06}
function defaultInstruction(type,task){const base={CUSTOMER_REPLY:'根据已确认的订单事实回复客户，不作未经确认的交期、费用或责任承诺。',CUSTOMER_CONFIRMATION_REMINDER:'礼貌提醒客户确认仍待确认的事项，说明确认内容和期望回复时间。',SUPPLIER_PROGRESS_FOLLOWUP:'询问当前准确进度、关键物料到货时间、补救方案和明确完成时间。',DELIVERY_STATUS_REPLY:'区分客户正式交期与工厂生产完成承诺，事实不足时明确说明正在核实。',CHANGE_HISTORY_SUMMARY:'按时间顺序汇总已确认的客户变更，不新增未发生的变化。'};return `${base[type]||base.CUSTOMER_REPLY}${task?` 当前任务：${task.title}。`:''}`}
function startGenerationProgress(container){let seconds=0;container.innerHTML=`<div class="generation-progress"><div class="progress-spinner"></div><div><strong id="generationStage">正在读取订单事实</strong><p>已用时 <span id="generationSeconds">0</span> 秒。系统会依次核对交期、承诺与责任边界，再生成草稿。</p><div class="generation-steps"><i class="active">读取事实</i><i>核对边界</i><i>生成草稿</i></div></div></div>`;const timer=setInterval(()=>{seconds++;$('#generationSeconds',container).textContent=seconds;const steps=$$('.generation-steps i',container);if(seconds>=4){$('#generationStage',container).textContent='正在核对交期与承诺边界';steps[1].classList.add('active')}if(seconds>=10){$('#generationStage',container).textContent='正在组织可审核草稿';steps[2].classList.add('active')}},1000);return()=>clearInterval(timer)}
async function runFT06(){const p=$('#communicationPanel'),ctx=communicationContext,o=ctx.order||ctx.task?.order;if(!o?.order_id&&!o?.order_no)return toast('请从真实订单或任务上下文发起草稿','error');const b=$('#runFT06',p),resultBox=$('#ft06Result',p);b.disabled=true;b.textContent='正在生成，可继续查看上方事实';const stop=startGenerationProgress(resultBox);try{const r=await api('/api/workflows/ft06/run',{method:'POST',body:JSON.stringify({draft_type:$('#ft06Type',p).value,recipient_role:$('#ft06Recipient',p).value,channel:$('#ft06Channel',p).value,language:'zh-CN',tone:'professional',order_id:o.order_id,order_no:o.order_no,fact_catalog:factsFromOrder(o),order_context:o,task_context:ctx.task?{task_id:ctx.task.task_id,title:ctx.task.title,recommended_action:ctx.task.recommended_action,evidence:ctx.task.evidence}:null,user_instruction:$('#ft06Instruction',p).value}) ,timeoutMs:210000});communicationRun=r;renderFT06Result(r)}catch(e){resultBox.innerHTML=`<div class="risk-box"><strong>草稿生成未完成</strong><p>${esc(e.message)}</p></div>`;toast(e.message,'error')}finally{stop();b.disabled=false;b.textContent='基于订单事实生成'}}
function renderFT06Result(r){const p=$('#ft06Result'),result=r.result||{},d=r.draft_result||result.draft_result||{},blocked=String(result.approval_status||'').startsWith('BLOCKED');const flags=(d.blocking_risk_flags||d.risk_flags||[]);const reasonTemplate=`已核对以下事项：
1. 客户正式交期及订单事实与系统记录一致；
2. 草稿未新增价格、费用、赔偿、责任或接受延期等承诺；
3. 本次人工放行的具体依据：`;
  p.innerHTML=`<section class="ai-result"><div class="ai-result-head"><div><h3>受控沟通草稿</h3><p>${blocked?'系统已经完成风险识别并给出阻断原因。先修改草稿最安全；只有你仍决定绕过阻断时，才需要填写人工放行依据。':'草稿已通过自动规则检查，仍需人工确认后才能使用。'}</p></div><span class="status ${blocked?'DO_NOW':'NEEDS_CONFIRMATION'}">${esc(blocked?'已阻断，待处理':'待人工确认')}</span></div>${blocked?`<div class="risk-box"><strong>系统识别的高风险原因</strong><p>${esc(flags.join('；')||'存在未经确认的交期、费用、赔偿或责任表达')}</p><small>这是系统自动填写的风险结论，不需要你重复解释。</small></div>`:''}<label class="field"><span>主题</span><input id="draftSubject" value="${esc(d.subject||'')}"></label><label class="field"><span>草稿正文</span><textarea id="draftBody" rows="12">${esc(d.draft||'')}</textarea></label><div><span class="help">引用事实</span><div class="fact-pills">${(d.facts_used||[]).map(f=>`<span class="fact-pill">${esc(f)}</span>`).join('')||'<span class="help">无</span>'}</div></div><div><span class="help">需要对方回答</span><div class="fact-pills">${(d.questions_to_ask||[]).map(f=>`<span class="fact-pill">${esc(f)}</span>`).join('')||'<span class="help">无</span>'}</div></div>${blocked?`<div class="override-explainer"><strong>两种处理方式</strong><ol><li><b>推荐：</b>直接修改上方草稿，删除或改写高风险表达，再保存。</li><li><b>例外放行：</b>只有你确认业务事实无误、并愿意承担发送责任时，勾选下方选项并说明依据。</li></ol><label class="override-choice"><input id="riskOverride" type="checkbox"> 我仍决定人工放行这份被系统阻断的草稿</label><div id="riskOverrideFields" hidden><label class="field"><span>人工放行依据 <small>不是让你解释AI为什么阻断，而是记录你为什么决定绕过阻断</small></span><textarea id="riskNote" rows="5">${esc(reasonTemplate)}</textarea></label></div></div>`:''}<div class="form-grid"><label class="field"><span>记录联系后等待对象</span><select id="waitingOn"><option value="customer">客户</option><option value="factory" ${communicationContext.task?.target==='factory'?'selected':''}>工厂</option></select></label><label class="field"><span>承诺回复时间</span><input id="replyAt" type="datetime-local"></label></div><div class="review-actions"><button class="btn" id="saveDraft">保存修改</button><button class="btn primary" id="approveDraft">确认草稿（不发送）</button><button class="btn bronze" id="copyDraft">复制并记录已联系</button></div><p class="help action-feedback" id="draftActionFeedback">确认草稿只保存审核结果；复制并记录会同时把当前任务移入等待状态。</p></section>`;
  const input=$('#replyAt',p);input.value=new Date(Date.now()+24*3600000-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16);
  $('#riskOverride',p)?.addEventListener('change',e=>{$('#riskOverrideFields',p).hidden=!e.target.checked});
  $('#saveDraft',p).onclick=()=>reviewDraft('save_edit',$('#saveDraft',p));$('#approveDraft',p).onclick=()=>reviewDraft('approve',$('#approveDraft',p));$('#copyDraft',p).onclick=()=>reviewDraft('copy_and_record',$('#copyDraft',p))
}
async function reviewDraft(action,button){const p=$('#communicationPanel'),blocked=String(communicationRun.result?.approval_status||'').startsWith('BLOCKED'),override=Boolean($('#riskOverride',p)?.checked),note=$('#riskNote',p)?.value||'';if(blocked&&['approve','copy_and_record'].includes(action)&&!override)return toast('该草稿已被系统阻断。请先修改，或明确选择人工放行并填写依据','error');if(blocked&&override&&(!note.trim()||/具体依据：\s*$/.test(note)))return toast('请补充你决定人工放行的具体依据','error');const subject=$('#draftSubject',p).value,body=$('#draftBody',p).value;if(!body.trim())return toast('草稿正文不能为空','error');const reply=$('#replyAt',p).value;const original=button.textContent;button.disabled=true;button.textContent=action==='copy_and_record'?'正在复制并记录…':action==='approve'?'正在保存确认…':'正在保存…';$('#draftActionFeedback',p).textContent='正在保存到订单记录，请勿重复点击。';try{if(action==='copy_and_record'){try{await navigator.clipboard.writeText([subject,body].filter(Boolean).join('\n\n'))}catch{}}const result=await api(`/api/drafts/${communicationRun.draft_id}/review`,{method:'POST',body:JSON.stringify({action,operator_id:currentUser(),edited_subject:subject,edited_draft:body,note,risk_override_confirmed:override,task_id:communicationContext.task?.task_id||null,waiting_on:$('#waitingOn',p).value,promised_reply_at:reply?new Date(reply).toISOString():null,next_action_at:reply?new Date(reply).toISOString():null}),timeoutMs:60000});if(action==='copy_and_record'){cache.dashboard=null;$('#draftActionFeedback',p).textContent=result.task_update?.updated?'已复制，并将任务记录为等待回复。':'已复制并保存草稿；当前没有关联任务，因此未改变任务状态。';toast(result.duplicate_skipped?'该操作已经记录，无需重复提交':(result.task_update?.updated?'已复制并记录人工触达，任务进入等待状态':'草稿已复制并保存'),'success');setTimeout(()=>{closeDrawer();renderRoute(false)},650)}else{$('#draftActionFeedback',p).textContent=action==='approve'?'已保存人工确认，网站没有自动发送。':'修改已保存。';toast(action==='approve'?'草稿已人工确认，尚未发送':'修改已保存','success')}}catch(e){$('#draftActionFeedback',p).textContent=e.message;toast(e.message,'error')}finally{button.disabled=false;button.textContent=original}}

window.addEventListener('load',init);

/* ==========================================================
   D11 V0.2 — user-behaviour IA after real-user UAT
   The backend remains D8/D9/D10 canonical; operator UI translates
   it into: what changed -> what to do -> order flow -> daily recap.
   ========================================================== */
pageMeta.today=['TODAY FOLLOW-UP','今日跟单'];
pageMeta.confirm=['DECISIONS','待确认'];
pageMeta.orders=['ORDERS','订单'];
pageMeta.recap=['DAILY WRAP-UP','复盘'];
pageMeta.manage=['TEAM WORKBENCH','团队工作台'];

function setPageMeta(name){
  const meta=pageMeta[name]||pageMeta.today;
  $('#pageEyebrow').textContent=meta[0];$('#pageTitle').textContent=meta[1];
  $$('[data-route]').forEach(b=>b.classList.toggle('active',b.dataset.route===name||(name==='orderDetail'&&b.dataset.route==='orders')));
  $('#globalSearch').placeholder='搜索订单、客户或当前事项';
}

async function renderRoute(resetSearch=true,preserveSearchFocus=false){
  route=parseRoute();if(resetSearch){search='';$('#globalSearch').value=''}
  setPageMeta(route.name==='orders'&&route.id?'orderDetail':route.name);
  const root=$('#pageRoot');const cursor=$('#globalSearch').selectionStart||search.length;
  root.innerHTML='<div class="loading-state"><span></span><p>正在整理你的跟单信息…</p></div>';
  try{
    const pages={today:pageToday,confirm:pageConfirm,orders:route.id?pageOrderDetail:pageOrders,recap:pageRecap,manage:pageManage,settings:pageSettings,activation:pageActivation,intake:pageIntake,agent:pageAgent,tasks:pageTasks};
    await (pages[route.name]||pageToday)(root);bindRouteButtons(root);
    if(preserveSearchFocus){const input=$('#globalSearch');input.focus({preventScroll:true});try{input.setSelectionRange(cursor,cursor)}catch{}}else root.focus({preventScroll:true});
  }catch(err){root.innerHTML=`<section class="panel">${emptyState('页面加载失败',err.message,`<button class="btn primary" onclick="location.reload()">重新加载</button>`)}</section>`;console.error(err)}
}

function d11v2Evidence(w){const e=w?.risk_context?.evidence||[];return Array.isArray(e)?e.map(x=>typeof x==='string'?x:(x.reason||x.message||x.evidence||'')).filter(Boolean):[]}
function d11v2IssueTitle(w){
  const c=w?.action_case||{},order=w?.order||{};let title=String(c.title||c.latest_recommended_action||'当前事项');
  title=title.replace(/^解决\s*/,'').replace(new RegExp(String(order.order_no||'').replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'g'),'').replace(/^\s*[-·:]?\s*/,'').trim();
  if(!title||title.length>26){const intent=String(c.intent_type||'').toUpperCase();if(intent.includes('DELIVERY'))return'交期异常';if(intent.includes('SUPPLIER'))return'供应商进度异常';return'当前跟单事项'}
  return title;
}
function d11v2BucketScore(v){return({ESCALATE:700,DO_NOW:650,DO_TODAY:550,NEEDS_CONFIRMATION:520,SCHEDULED:350,WAITING_EXTERNAL:200}[String(v||'').toUpperCase()]||300)}
function d11v2SeverityScore(v){return({CRITICAL:80,HIGH:60,MEDIUM:35,LOW:10}[String(v||'').toUpperCase()]||0)}
function d11v2CaseScore(w){const c=w.action_case||{};let score=d11v2BucketScore(c.latest_action_bucket)+d11v2SeverityScore(c.latest_severity);if(w.workspace_state==='ACTIONABLE')score+=100;if((w.waiting_tasks||[]).some(t=>t.active_waiting?.due_at&&new Date(t.active_waiting.due_at)<=new Date()))score+=180;return score}
function d11v2SortCases(items){return [...items].sort((a,b)=>d11v2CaseScore(b)-d11v2CaseScore(a)||String(a.order?.requested_delivery_date||'9999').localeCompare(String(b.order?.requested_delivery_date||'9999')))}
function d11v2NextAction(w){const a=w.actionable_tasks||[],wt=w.waiting_tasks||[];if(a.length===1)return a[0].title||a[0].recommended_action||'继续推进';if(a.length>1)return `${a.length} 件事情都可以继续推进`;if(wt.length)return `等待${wt[0].active_waiting?.reason||wt[0].active_waiting?.waiting_type||'外部回复'}`;return '查看订单进展'}
function d11v2Reason(w){const e=d11v2Evidence(w);if(e.length)return e.join('；');return w?.risk_context?.recommended_action||w?.action_case?.latest_recommended_action||'系统已识别到需要继续关注的订单变化。'}
function d11v2WaitingDue(w){return (w.waiting_tasks||[]).map(t=>t.active_waiting?.due_at).filter(Boolean).sort()[0]||null}
function d11v2IsOverdue(w){return (w.waiting_tasks||[]).some(t=>t.active_waiting?.due_at&&new Date(t.active_waiting.due_at)<=new Date())}
function d11v2OrderStage(o,w){
  const text=String(o?.current_node||'').toLowerCase();
  if(/交付|完成|签收/.test(text))return 4;if(/出货|出运|物流|装船/.test(text))return 3;if(/生产|加工|排产/.test(text))return 2;if(/采购|备料|物料|打样/.test(text))return 1;
  const intent=String(w?.action_case?.intent_type||'').toUpperCase();if(intent.includes('DELIVERY')||intent.includes('SUPPLIER'))return 2;return 0;
}
function d11v2FlowHtml(o,w){const labels=['接单','备货/采购','生产','出货','交付'],idx=d11v2OrderStage(o,w);return `<div class="d11v2-flow">${labels.map((x,i)=>`<div class="d11v2-flow-step ${i<idx?'done':i===idx?'current':''}">${esc(x)}</div>`).join('')}</div>`}
function d11v2HumanStage(o,w){const idx=d11v2OrderStage(o,w);return ['接单','备货/采购','生产','出货','交付'][idx]||'接单'}
function d11v2FindOrder(w){return w?.order||{}}
function d11v2CaseButton(w,label='查看'){const id=w?.action_case?.action_case_id||'';return `<button class="btn link" data-case-detail="${esc(id)}">${esc(label)}</button>`}

function d11v2ChangeRow({time='最近',orderNo='',title='',body='',impact=''}){return `<div class="d11v2-change"><div class="meta"><span>${esc(time)}${orderNo?` · ${esc(orderNo)}`:''}</span></div><strong>${esc(title)}</strong><p>${esc(body)}</p>${impact?`<span class="d11v2-impact">${esc(impact)}</span>`:''}</div>`}
function d11v2ActionRow(w,index){const o=w.order||{},urgent=d11v2CaseScore(w)>=700,reason=d11v2Reason(w);return `<div class="d11v2-action ${urgent?'urgent':''}"><span class="d11v2-rank">${index+1}</span><div class="d11v2-action-copy"><strong>${esc(o.order_no||'未编号')} · ${esc(d11v2NextAction(w))}</strong><p>${esc(reason)}</p><small>${esc(o.customer_name||'未知客户')}${o.requested_delivery_date?` · 客户交期 ${esc(fdate(o.requested_delivery_date))}`:''}</small></div>${d11v2CaseButton(w,'继续处理')}</div>`}

function d11v4ChangeItem(c){return `<div class="d11v4-change"><div class="d11v4-change-meta"><span>${esc(c.time)}${c.orderNo?' · '+esc(c.orderNo):''}</span></div><strong>${esc(c.title)}</strong><p>${esc(c.body)}</p>${c.impact?`<span class="d11v4-change-impact">${esc(c.impact)}</span>`:''}</div>`}
function d11v4ActionItem(w,idx){const o=w.order||{},urgent=d11v2CaseScore(w)>=700;return `<div class="d11v4-action ${urgent?'urgent':''}"><span class="d11v4-rank">${idx+1}</span><div class="d11v4-action-copy"><strong>${esc(o.order_no||'未编号')} · ${esc(d11v2IssueTitle(w))}</strong><p>${esc(d11v2Reason(w))}</p><small>${esc(o.customer_name||'未知客户')}${o.requested_delivery_date?' · 客户交期 '+esc(fdate(o.requested_delivery_date)):''}</small></div>${d11v2CaseButton(w,'继续处理')}</div>`}

async function d11v4SubmitInfo(root,workspaces){
  const box=$('#d11v4InfoInput',root),btn=$('#d11v4InfoSubmit',root),status=$('#d11v4InfoStatus',root);const raw=(box?.value||'').trim();if(!raw)return toast('请先粘贴一条客户、供应商或电话沟通内容','error');
  const selectedRole=($('.d11v4-src-btn.active',root)?.dataset?.senderRole)||'customer';
  const sourceChannel=selectedRole==='factory'?'wechat':'email';
  btn.disabled=true;btn.textContent='正在识别…';status.textContent='正在识别这条信息影响哪个订单，以及是否需要你确认。';
  const body={sender_role:selectedRole,source_channel:sourceChannel,raw_content:raw,source_time:new Date().toISOString()};
  try{
    const job=await api('/api/intake/jobs',{method:'POST',body:JSON.stringify(body),timeoutMs:15000});
    for(let i=0;i<45;i++){
      await new Promise(r=>setTimeout(r,i<8?800:1400));const j=await api(`/api/intake/jobs/${encodeURIComponent(job.job_id)}`,{timeoutMs:12000});
      if(j.status==='COMPLETED'){
        const rid=j.result?.review_id;cache.dashboard=null;status.innerHTML=`已识别出一项订单变化。确认后会更新行动排序。${rid?` <button class="btn link" data-go="confirm?review=${esc(rid)}">去确认</button>`:''}`;box.value='';bindRouteButtons(status);toast('新信息已识别，等待你确认','success');return;
      }
      if(j.status==='FAILED'){
        const msg=j.error?.message||j.progress_message||'';
        if(msg.includes('Coze尚未配置')||msg.includes('COZE')){
          throw new Error('暂时无法分析这条信息，智能识别服务尚未就绪。内容已暂存，请稍后重试。');
        }
        throw new Error('暂时无法分析这条信息，请稍后重试。原始内容尚未写入订单。');
      }
      status.textContent=j.progress_message||'正在核对订单和变化…';
    }
    throw new Error('识别仍在后台进行，请稍后刷新查看待确认');
  }catch(e){
    const msg=e.message||'';
    if(msg.includes('Coze尚未配置')||msg.includes('COZE')||msg.includes('503')||msg.includes('Service Unavailable')){
      status.textContent='暂时无法分析这条信息，智能识别服务尚未就绪。内容已暂存，请稍后重试。';
    }else if(msg.includes('无法分析')||msg.includes('尚未写入')){
      status.textContent=msg;
    }else{
      status.textContent='暂时无法分析这条信息，请稍后重试。原始内容尚未写入订单。';
    }
    toast(status.textContent,'error');
  }finally{btn.disabled=false;btn.textContent='分析这条信息'}
}

async function pageToday(root){
  const [wd,reviews]=await Promise.all([workspaceData(),api('/api/reviews?status=PENDING').catch(()=>({items:[],pending:0}))]);
  const cases=d11v2SortCases((wd.items||[]).filter(matchWorkspace));const actionable=cases.filter(w=>w.workspace_state==='ACTIONABLE'||d11v2IsOverdue(w));const waiting=cases.filter(w=>w.workspace_state==='WAITING_ONLY'&&!d11v2IsOverdue(w));const pending=(reviews.items||[]).filter(x=>x.status==='PENDING');updateBadges(actionable.length,pending.length);
  const changes=[];
  pending.slice(0,5).forEach(x=>changes.push({time:fdt(x.created_at||x.source_time||new Date()),orderNo:x.order_no||'',title:'有一条新信息等待确认',body:(x.raw_content||'').slice(0,110),impact:'确认后才会更新订单与行动'}));
  const changeList=changes.slice(0,5);
  const completedToday=[];
  cases.forEach(w=>(w.history_tasks||[]).filter(t=>String(t.updated_at||'').slice(0,10)===todayISO()).forEach(t=>completedToday.push({w,t})));
  const todayCompletedCount=completedToday.length;const todayResolvedCount=completedToday.filter(({t})=>t.status==='COMPLETED').length;
  const recentCompleted=completedToday.slice(-3).reverse();
  const changesHtml=changeList.length?changeList.map(d11v4ChangeItem).join(''):'<div class="d11v4-empty-inline">当前没有新的关键变化。</div>';
  const copilotHtml=`<aside class="d11v4-copilot"><div class="d11v4-copilot-header"><strong>跟单 Copilot</strong></div><div class="d11v4-copilot-changes">${changesHtml}</div><div class="d11v4-copilot-input"><textarea id="d11v4InfoInput" placeholder="粘贴客户邮件、供应商回复或电话结果……"></textarea><div class="d11v4-source-pick"><button type="button" class="d11v4-src-btn active" data-sender-role="customer">客户</button><button type="button" class="d11v4-src-btn" data-sender-role="factory">工厂/供应商</button></div><div class="d11v4-input-actions"><small id="d11v4InfoStatus">系统会识别可能受影响的订单；确认变化后，再更新订单与行动排序。</small><button class="btn primary" id="d11v4InfoSubmit">分析这条信息</button></div></div></aside>`;
  const actionHtml=actionable.length?actionable.map((w,i)=>d11v4ActionItem(w,i)).join(''):'<div class="d11v4-empty-inline">当前没有需要你主动处理的订单。</div>';
  const waitingHtml=waiting.length?waiting.map(w=>{
    const due=fdt(d11v2WaitingDue(w));const reason=esc(w.waiting_tasks?.[0]?.active_waiting?.reason||'外部回复');
    return `<div class="d11v4-wait-row"><div><strong>${esc(w.order?.order_no||'未编号')} · ${reason}</strong><small>最晚 ${due}</small></div>${d11v2CaseButton(w,'查看')}</div>`;
  }).join(''):'<div class="d11v4-wait-zero" id="waitingZero">正在等待 0 当前没有等待中的订单</div>';
  const completedHtml=`<div class="d11v4-completed-row">今天已处理 <strong>${todayCompletedCount}</strong> 项${todayResolvedCount?` · 解决异常 <strong>${todayResolvedCount}</strong> 项`:''}${recentCompleted.length?` · 最近：${recentCompleted.map(({t})=>esc(t.title||'')).join('、')}`:''}</div>`;
  root.innerHTML=`<div class="d11v4-grid">${copilotHtml}<section class="d11v4-stack"><div class="d11v4-section d11v4-primary"><div class="d11v4-section-head"><div><h3>现在先做</h3></div><span class="tag danger">${actionable.length} 个订单需要行动</span></div><div class="d11v4-section-body">${actionHtml}</div></div><div class="d11v4-section d11v4-waiting"><div class="d11v4-section-head"><h3>正在等待</h3></div><div class="d11v4-section-body">${waitingHtml}</div></div><div class="d11v4-section d11v4-completed">${completedHtml}</div></section></div>`;
  bindD11CaseButtons(root);$('#d11v4InfoSubmit',root)?.addEventListener('click',()=>d11v4SubmitInfo(root,cases));
  $$('.d11v4-src-btn',root).forEach(btn=>btn.addEventListener('click',()=>{
    $$('.d11v4-src-btn',root).forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
  }));
  const wz=document.getElementById('waitingZero');if(wz)wz.addEventListener('click',()=>renderRoute(false,'recap'));
}

function d11v2ReviewDecisionCard(x){return `<div class="d11v2-decision" data-review-card="${esc(x.review_id)}"><div class="d11v2-decision-head"><div><h3>${esc(x.order_no||'待关联订单')} · 新信息带来的订单变化</h3><p>${esc((x.raw_content||'').slice(0,180))}</p></div><span class="tag warning">需要你确认</span></div><div class="d11v2-question">确认这条信息与订单的对应关系和变化内容后，系统才会更新事实并重新整理行动。</div><div class="review-actions" style="margin-top:11px"><button class="btn secondary" data-review-open="${esc(x.review_id)}">查看并修改</button><button class="btn primary" data-review-confirm="${esc(x.review_id)}">确认变化</button><button class="btn ghost" data-review-reject="${esc(x.review_id)}">不是这样</button></div></div>`}
function d11v2AgentDecisionCard(x,type='candidate'){const id=x.candidate_id||x.approval_id||'';const title=x.order_no?`${x.order_no} · ${x.recommended_action||'业务事项'}`:(x.title||'业务事项');const desc=x.reason||x.evidence||x.recommended_action||'这项业务决定需要人工确认后才能继续。';const actions=type==='approval'?`<button class="btn primary" data-approval-approve="${esc(id)}">同意</button><button class="btn ghost" data-approval-reject="${esc(id)}">不同意</button>`:`<button class="btn primary" data-anomaly-confirm="${esc(id)}">确认</button><button class="btn ghost" data-anomaly-reject="${esc(id)}">不是这样</button>`;return `<div class="d11v2-decision"><div class="d11v2-decision-head"><div><h3>${esc(title)}</h3><p>${esc(desc)}</p></div><span class="tag warning">需要决定</span></div><div class="review-actions" style="margin-top:11px">${actions}</div></div>`}

function d11v4ConfirmItem(item, type='review'){
  const id = item.review_id || item.candidate_id || item.approval_id || '';
  const orderNo = item.order_no || '';
  const customer = item.customer_name || '';
  const body = (item.raw_content || item.reason || '').slice(0, 200);
  const question = type === 'review' ? '确认这条信息与订单的对应关系和变化内容？' : (item.recommended_action ? `是否执行：${item.recommended_action}？` : '请确认是否继续推进？');
  const evidence = type === 'review' ? `原始消息：${esc(item.raw_content || '无')}<br>系统判断：${esc(item.summary || item.reason || '系统自动生成')}<br>可能影响：更新订单事实并重新排序行动` : '';
  const actions = type === 'review' 
    ? `<button class="btn primary" data-review-confirm="${esc(id)}">接受</button><button class="btn secondary" data-review-reject="${esc(id)}">继续协调</button><button class="btn link d11v4-evidence-toggle">查看依据</button>`
    : `<button class="btn primary" data-${type}-approve="${esc(id)}">接受</button><button class="btn secondary" data-${type}-reject="${esc(id)}">继续协调</button><button class="btn link d11v4-evidence-toggle">查看依据</button>`;
  return `<div class="d11v4-confirm-item" data-confirm-id="${esc(id)}">
    <div class="d11v4-confirm-header">
      <span class="d11v4-confirm-title">${esc(orderNo)}${customer?' · '+esc(customer):''}</span>
      <span class="tag warning">${type==='review'?'信息确认':type==='approval'?'主管审批':'系统建议'}</span>
    </div>
    <div class="d11v4-confirm-body">${esc(body)}</div>
    <div class="d11v4-confirm-question">${esc(question)}</div>
    <div class="d11v4-confirm-actions">${actions}</div>
    ${evidence?`<div class="d11v4-confirm-evidence"><strong>查看依据</strong><p>${evidence}</p></div>`:''}
  </div>`;
}
function d12ActionText(item){
  const labels={UPDATE_EXPECTED_DELIVERY_DATE:'修改客户正式交期',UPDATE_CUSTOMER_COMMITMENT:'修改客户正式承诺',ACCEPT_DELAY:'接受延期方案',HIGH_RISK_OVERRIDE:'高风险例外处理',RECORD_CONTACT:'记录已联系',SET_WAITING:'进入等待反馈',UPDATE_INTERNAL_PLAN:'更新内部跟进计划',RECORD_SUPPLIER_COMMITMENT:'记录供应商最新承诺',LINK_MESSAGE_ORDER:'确认消息与订单关联'};
  return labels[String(item.action_type||'').toUpperCase()]||'业务动作确认';
}
function d12ConfirmItem(item){
  const id=item.review_id||'';const isManager=currentUser()==='MANAGER-1';const needsManager=item.required_review==='MANAGER_APPROVAL';const canDecide=needsManager?isManager:(isManager||item.requested_by===currentUser());
  const payload=safeJson(item.payload_json,{});const state=safeJson(item.state_snapshot_json,{});const nextDate=payload.expected_delivery_date||payload.requested_delivery_date||payload.customer_delivery_date||'';
  const title=`${item.order_no||'订单'} · ${d12ActionText(item)}`;
  const body=item.reason|| (nextDate?`拟调整为 ${nextDate}`:'这项业务动作需要人工确认后才能继续。');
  const why=needsManager?'这会改变对客户的正式承诺，因此需要主管审批。':'这属于当前跟单员授权范围，可以由本人确认。';
  const question=needsManager?(isManager?'是否批准这次正式业务变更？':'已提交主管审批，批准前不会进入正式执行队列。'):'确认这项动作并记录到待执行队列？';
  const tag=needsManager?'需要主管审批':'我可以确认';
  const actions=canDecide?`<button class="btn primary" data-d12-approve="${esc(id)}">${needsManager?'批准':'确认'}</button><button class="btn secondary" data-d12-reject="${esc(id)}">${needsManager?'驳回':'取消'}</button><button class="btn link d11v4-evidence-toggle">查看依据</button>`:`<span class="tag muted">等待主管处理</span><button class="btn link d11v4-evidence-toggle">查看依据</button>`;
  const evidence=`${esc(why)}<br>${state.requested_delivery_date?`当前正式交期：${esc(state.requested_delivery_date)}<br>`:''}${nextDate?`拟变更为：${esc(nextDate)}<br>`:''}通过后只会记录业务动作并进入待执行队列，不代表ERP或外部系统已经完成。`;
  return `<div class="d11v4-confirm-item" data-confirm-id="${esc(id)}"><div class="d11v4-confirm-header"><span class="d11v4-confirm-title">${esc(title)}</span><span class="tag ${needsManager?'danger':'warning'}">${tag}</span></div><div class="d11v4-confirm-body">${esc(body)}</div><div class="d11v4-confirm-question">${esc(question)}</div><div class="d11v4-confirm-actions">${actions}</div><div class="d11v4-confirm-evidence"><strong>为什么需要这样确认</strong><p>${evidence}</p></div></div>`;
}
async function decideD12Review(id,decision){
  const approve=decision==='APPROVE';if(!confirm(approve?'确认继续这项业务动作吗？':'确定不继续这项业务动作吗？'))return;
  try{
    await api(`/api/d12/reviews/${encodeURIComponent(id)}/decision`,{method:'POST',body:JSON.stringify({decision})});
    if(approve){const result=await api(`/api/d12/reviews/${encodeURIComponent(id)}/submit`,{method:'POST',body:'{}'});toast(result.status==='ACCEPTED'?'已确认并记录，等待外部系统执行':'已确认','success')}
    else toast('已驳回，这次动作不会提交','success');
    renderRoute(false);
  }catch(e){toast(e.message,'error')}
}

function bindConfirmEvents(root){
  $$('.d11v4-evidence-toggle',root).forEach(btn=>{btn.onclick=()=>{const item=btn.closest('.d11v4-confirm-item');const ev=item?.querySelector('.d11v4-confirm-evidence');if(ev){ev.classList.toggle('show');btn.textContent=ev.classList.contains('show')?'收起依据':'查看依据'}}});
  $$('[data-review-open]',root).forEach(b=>b.onclick=()=>openReviewEditor(b.dataset.reviewOpen));$$('[data-review-confirm]',root).forEach(b=>b.onclick=()=>confirmReviewDirect(b.dataset.reviewConfirm));$$('[data-review-reject]',root).forEach(b=>b.onclick=()=>rejectReviewDirect(b.dataset.reviewReject));$$('[data-anomaly-approve]',root).forEach(b=>b.onclick=()=>decideAnomaly(b.dataset.anomalyApprove,'CONFIRM'));$$('[data-anomaly-reject]',root).forEach(b=>b.onclick=()=>decideAnomaly(b.dataset.anomalyReject,'REJECT'));$$('[data-approval-approve]',root).forEach(b=>b.onclick=()=>decideApproval(b.dataset.approvalApprove,'APPROVE'));$$('[data-approval-reject]',root).forEach(b=>b.onclick=()=>decideApproval(b.dataset.approvalReject,'REJECT'));$$('[data-d12-approve]',root).forEach(b=>b.onclick=()=>decideD12Review(b.dataset.d12Approve,'APPROVE'));$$('[data-d12-reject]',root).forEach(b=>b.onclick=()=>decideD12Review(b.dataset.d12Reject,'REJECT'));
}
async function pageConfirm(root){
  const role=currentUser()==='MANAGER-1'?'manager':'operator';const [reviews,agent,d12Reviews]=await Promise.all([api('/api/reviews'),api(`/api/agent/overview?current_user_id=${encodeURIComponent(currentUser())}&current_role=${role}`).catch(()=>({candidates:[],approvals:[],summary:{}})),api('/api/d12/reviews?status=PENDING').catch(()=>({items:[],count:0}))]);
  const reviewItems=(reviews.items||[]).filter(x=>x.status==='PENDING').filter(x=>!search||[x.order_no,x.customer_name,x.raw_content].join(' ').toLowerCase().includes(search.toLowerCase()));const anomalies=(agent.candidates||[]).filter(x=>['PENDING_CONFIRMATION','ANOMALY_CANDIDATE','PENDING'].includes(x.status||'PENDING'));const approvals=(agent.approvals||[]).filter(x=>String(x.status||'PENDING').includes('PENDING'));const d12Items=(d12Reviews.items||[]).filter(x=>!search||[x.order_no,x.customer_name,x.reason,x.action_type].join(' ').toLowerCase().includes(search.toLowerCase()));
  const allItems=[...d12Items.map(x=>({...x,_type:'d12'})),...reviewItems.map(x=>({...x,_type:'review'})),...anomalies.map(x=>({...x,_type:'anomaly'})),...approvals.map(x=>({...x,_type:'approval'}))];
  const total=allItems.length;updateBadges(null,total);
  const renderItem=x=>x._type==='d12'?d12ConfirmItem(x):d11v4ConfirmItem(x,x._type);
  root.innerHTML=`<div class="page-stack"><section class="d11v4-section"><div class="d11v4-section-head"><h3>待确认</h3><span class="tag danger">${total} 项</span><div class="d11v4-filter"><button class="btn link d11v4-filter-btn active" data-filter="ALL">全部</button><button class="btn link d11v4-filter-btn" data-filter="TODAY">今天新增</button><button class="btn link d11v4-filter-btn" data-filter="HIGH_RISK">需主管审批</button></div></div><div class="d11v4-section-body"><div class="d11v4-confirm-list">${allItems.map(renderItem).join('')||'<p class="demo-note">当前没有需要你确认的事项。</p>'}</div></div></section></div>`;
  const rerender=(filter)=>{let list=allItems;if(filter==='TODAY'){const today=new Date().toDateString();list=allItems.filter(x=>{const d=new Date(x.created_at||x.updated_at||Date.now());return d.toDateString()===today})}else if(filter==='HIGH_RISK'){list=allItems.filter(x=>x.required_review==='MANAGER_APPROVAL'||(()=>{const r=String(x.risk_level||x.severity||x.max_risk||'').toLowerCase();return r.includes('high')||r.includes('critical')||r.includes('高')||r.includes('严重')})())}$$('.d11v4-confirm-list',root).forEach(el=>{el.innerHTML=list.map(renderItem).join('')||'<p class="demo-note">当前没有需要你确认的事项。</p>'});bindConfirmEvents(root)};
  $$('.d11v4-filter-btn',root).forEach(btn=>{btn.onclick=()=>{$$('.d11v4-filter-btn',root).forEach(b=>b.classList.remove('active'));btn.classList.add('active');rerender(btn.dataset.filter)}});
  bindConfirmEvents(root);
  if(route.query.review)setTimeout(()=>openReviewEditor(route.query.review),0);
}

function d11v2OrderSituation(w,o){if(w){if(d11v2IsOverdue(w))return'等待已超时，需要重新处理';if(w.workspace_state==='ACTIONABLE')return d11v2IssueTitle(w);if((w.waiting_tasks||[]).length)return `等待${w.waiting_tasks[0].active_waiting?.reason||'外部回复'}`;}return o?.current_node?`${o.current_node}，暂无需要主动处理的异常`:'正常推进'}
function d11v2OrderNext(w){if(!w)return'按当前计划推进';return d11v2NextAction(w)}
function d11v2OrderRow(o,w){const caseId=w?.action_case?.action_case_id||'',due=w?d11v2WaitingDue(w):null;return `<div class="d11v2-order-row" ${caseId?`data-case-detail="${esc(caseId)}"`:`data-order-flow="${esc(o.order_id)}"`}><div class="d11v2-order-main"><strong>${esc(o.order_no||'未编号')} · ${esc(o.customer_name||'未知客户')}</strong><small>${esc(o.product_name||'')}</small></div><div class="d11v2-order-cell"><small>当前阶段</small><b>${esc(d11v2HumanStage(o,w))}</b></div><div class="d11v2-order-cell hide-md"><small>关键时间</small><b>${esc(due?fdt(due):fdate(o.requested_delivery_date||o.customer_delivery_date))}</b></div><div class="d11v2-order-cell"><small>当前情况</small><b>${esc(d11v2OrderSituation(w,o))}</b><small>${esc(d11v2OrderNext(w))}</small></div><button class="btn link">查看</button></div>`}

function d11v4OrderRow(o,w,group){
  const caseId = w?.action_case?.action_case_id || '';
  const currentStage = d11v2HumanStage(o,w);
  const due = w ? d11v2WaitingDue(w) : null;
  const situation = d11v2OrderSituation(w,o);
  const nextAction = d11v2OrderNext(w);
  const reason = w ? d11v2Reason(w) : '';
  const risk = group === 'attention';
  const btnAttr = caseId?`data-case-detail="${esc(caseId)}"`:`data-order-flow="${esc(o.order_id)}"`;
  return `<div class="d11v4-order-row" ${btnAttr}>
    <div class="d11v4-order-main">
      <strong>${esc(o.order_no||'未编号')} · ${esc(o.customer_name||'未知客户')}</strong>
      <small>${esc(o.product_name||'')}</small>
    </div>
    <div class="d11v4-order-cell">
      <small>当前阶段</small>
      <b>${esc(currentStage)}</b>
    </div>
    <div class="d11v4-order-cell">
      <small>关键时间</small>
      <b>${esc(due?fdt(due):fdate(o.requested_delivery_date||o.customer_delivery_date))}</b>
    </div>
    <div class="d11v4-order-cell">
      <small>当前情况</small>
      <b class="${risk?'risk':''}">${esc(situation)}</b>
    </div>
    <button class="btn link" ${btnAttr}>${esc(nextAction)}</button>
    ${reason?`<div class="d11v4-order-row-reason"><b>为什么：</b>${esc(reason)}</div>`:''}
  </div>`;
}

async function pageOrders(root){
  const [d,wd]=await Promise.all([ordersData(),workspaceData()]);
  const raw=(d.items||[]).filter(matchOrder);
  const wmap=new Map((wd.items||[]).map(w=>[w.order?.order_id,w]));
  const attention=[],waiting=[],normal=[];
  raw.forEach(o=>{
    const w=wmap.get(o.order_id);
    if(w&&(w.workspace_state==='ACTIONABLE'||d11v2IsOverdue(w))) attention.push([o,w]);
    else if(w&&(w.workspace_state==='WAITING_ONLY'||(w.waiting_tasks||[]).length)) waiting.push([o,w]);
    else normal.push([o,w]);
  });
  attention.sort((a,b)=>d11v2CaseScore(b[1])-d11v2CaseScore(a[1]));
  waiting.sort((a,b)=>String(d11v2WaitingDue(a[1])||'9999').localeCompare(String(d11v2WaitingDue(b[1])||'9999')));
  root.innerHTML=`<div class="page-stack">
    <div class="panel-head">
      <h3>订单</h3>
      <div class="row-actions">
        <span class="count">共 ${raw.length} 个订单</span>
        <input class="btn secondary" id="orderPageSearch" style="height:36px;width:170px;text-align:left" placeholder="搜索订单" value="${esc(search)}" />
        <button class="btn secondary" id="orderFilterBtn">筛选</button>
        <button class="btn secondary" id="orderRefreshBtn">更新数据</button>
      </div>
    </div>
    <details class="d11v4-group" open>
      <summary><span class="d11v4-group-label">需要关注</span><span class="d11v4-group-count">${attention.length} 个订单</span><span class="d11v4-group-chevron">${icon('chevron')}</span></summary>
      <div class="d11v4-group-body">${attention.map(([o,w])=>d11v4OrderRow(o,w,'attention')).join('')||'<p class="demo-note" style="padding:14px 18px">当前没有需要主动处理的订单。</p>'}</div>
    </details>
    <details class="d11v4-group" open>
      <summary><span class="d11v4-group-label">等待反馈</span><span class="d11v4-group-count">${waiting.length} 个订单</span><span class="d11v4-group-chevron">${icon('chevron')}</span></summary>
      <div class="d11v4-group-body">${waiting.map(([o,w])=>d11v4OrderRow(o,w,'waiting')).join('')||'<p class="demo-note" style="padding:14px 18px">当前没有等待反馈的订单。</p>'}</div>
    </details>
    <details class="d11v4-group">
      <summary><span class="d11v4-group-label">正常推进</span><span class="d11v4-group-count">${normal.length} 个订单</span><span class="d11v4-group-chevron">${icon('chevron')}</span></summary>
      <div class="d11v4-group-body">${normal.map(([o,w])=>d11v4OrderRow(o,w,'normal')).join('')||'<p class="demo-note" style="padding:14px 18px">暂无正常推进订单。</p>'}</div>
    </details>
  </div>`;
  $('#orderPageSearch',root).oninput=e=>{search=e.target.value;$('#globalSearch').value=search;clearTimeout(searchTimer);searchTimer=setTimeout(()=>renderRoute(false,true),160)};
  $('#orderRefreshBtn',root).onclick=()=>{cache={operators:cache.operators};renderRoute(false);toast('已更新订单数据','success')};
  $('#orderFilterBtn',root)?.addEventListener('click',()=>toast('筛选功能待实现','info'));
  bindD11CaseButtons(root);
  $$('[data-order-flow]',root).forEach(row=>row.onclick=e=>{e.stopPropagation();openOrderOnlyFlowDrawer(row.dataset.orderFlow)});
}

function d12TaskCanRequestDelivery(t){const text=`${t?.title||''} ${t?.recommended_action||''} ${t?.current_node||''}`;return /交期|交货|延期|delivery|delay/i.test(text)}
function d11v2TaskActionButtons(t,o){if(t.status==='TODO')return `<button class="btn secondary" data-d11-draft="${esc(t.task_id)}">生成沟通草稿</button><button class="btn primary" data-d11-start="${esc(t.task_id)}">开始处理</button>`;if(t.status==='IN_PROGRESS'){const delivery=d12TaskCanRequestDelivery(t)?`<button class="btn secondary" data-d12-delivery="${esc(t.task_id)}">申请修改客户交期</button>`:'';return `<button class="btn secondary" data-d11-draft="${esc(t.task_id)}">生成沟通草稿</button><button class="btn secondary" data-d11-wait="${esc(t.task_id)}">已联系，等待回复</button>${delivery}<button class="btn primary" data-d11-complete="${esc(t.task_id)}">这件事已完成</button>`}return''}
function d11v2TaskCard(t,o){return `<div class="d11v2-next"><small>${t.status==='IN_PROGRESS'?'正在处理':'可以继续推进'}</small><strong>${esc(t.title||t.recommended_action||'继续处理')}</strong><p>${esc(t.recommended_action||'')}</p><div class="review-actions" style="margin-top:10px">${d11v2TaskActionButtons(t,o)}</div></div>`}
function d11v2ProgressItems(w){const rows=[];(w.history_tasks||[]).slice(-4).reverse().forEach(t=>rows.push({time:fdt(t.updated_at),title:t.title,body:'已完成'}));(w.waiting_tasks||[]).forEach(t=>{const wt=t.active_waiting;if(wt)rows.push({time:fdt(wt.created_at),title:t.title,body:`开始等待：${wt.reason||wt.waiting_type||'外部回复'}`})});return rows.slice(0,6)}

function d11v4HistoryByStage(w,stageIdx){const nodes=[];(w.history_tasks||[]).forEach(t=>{const nt=String(t.current_node||'').toLowerCase();let matched=false;const stageMap={'接单':0,'接单确认':0,'开始生产':2,'生产中':2,'备货':1,'备货采购':1,'采购':1,'出货':3,'交付':4,'完成':4};for(const k in stageMap){if(nt.includes(String(k).toLowerCase())){nodes.push({stage:stageMap[k],time:fdt(t.updated_at),title:t.title,body:'已完成'});matched=true;break}}if(!matched&&t.status==='COMPLETED'){nodes.push({stage:stageIdx,time:fdt(t.updated_at),title:t.title,body:'已完成'})}});return nodes;}

async function openCaseDrawer(caseId){
  let w;try{w=(await api(`/api/action-workspace/${encodeURIComponent(caseId)}`)).item}catch(e){return toast(e.message,'error')}
  const c=w.action_case||{},o=w.order||{},actionable=w.actionable_tasks||[],waiting=w.waiting_tasks||[];
  const stageIdx=d11v2OrderStage(o,w);
  const stageLabels=['接单确认','备货采购','生产','出货','交付'];
  const issue=d11v2IssueTitle(w),reason=d11v2Reason(w);
  const historyByStage=d11v4HistoryByStage(w,stageIdx);
  const flowNodes=stageLabels.map((label,i)=>{
    const items=historyByStage.filter(h=>h.stage===i);
    if(i<stageIdx)return{label,state:'completed',items};
    if(i===stageIdx)return{label,state:'current',items};
    return{label,state:'upcoming',items};
  });
  const actionHtml=actionable.length===1?d11v2TaskCard(actionable[0],o):actionable.length>1?`<div class="boundary-note"><strong>现在有 ${actionable.length} 件事都可以推进</strong><br>它们属于同一个订单问题，但目前没有可靠依据替你强行排先后。</div>${actionable.map(t=>d11v2TaskCard(t,o)).join('')}`:'<p class="demo-note">当前没有需要你主动处理的事项。</p>';
  const waitHtml=waiting.map(t=>{const wt=t.active_waiting;return `<div class="d11v4-waiting-box"><strong>正在等待：${esc(wt?.reason||t.title||'外部回复')}</strong><span>最晚 ${esc(fdt(wt?.due_at))} · 已收到 ${Number(wt?.reply_count||0)} 条回复</span>${wt?`<button class="btn primary" data-d11-reply="${esc(wt.waiting_id)}">收到新回复</button>`:''}</div>`}).join('');
  const pendingExternal=[...actionable,...waiting].filter(t=>t.business_action&&String(t.business_action.status).toUpperCase()==='ACCEPTED');
  const nodesHtml=flowNodes.map((node,i)=>{
    const stateClass=`d11v4-flow-node ${node.state}${node.state==='current'?' expanded':''}`;
    const toggle=node.state==='completed'?'<button class="d11v4-flow-toggle">展开历史</button>':'';
    let body='';
    if(node.state==='current'){
      body=`<div class="d11v4-flow-content">
          <div class="d11v4-section-label">当前状态</div>
          <div class="d11v4-section-content"><p>${esc(d11v2HumanStage(o,w))} · ${esc(issue)}</p></div>
          <div class="d11v4-section-label">已发生事项</div>
          <div class="d11v4-history-list">${node.items.length?node.items.map(x=>`<div class="d11v4-history-item"><time>${esc(x.time)}</time><div><strong>${esc(x.title)}</strong><span>已完成</span></div></div>`).join(''):'<p class="demo-note">暂无记录</p>'}</div>
          <div class="d11v4-section-label">为什么需要关注</div>
          <div class="d11v4-section-content"><p>${esc(reason)}</p></div>
          ${waiting.length?`<div class="d11v4-section-label">等待状态</div>${waitHtml}`:''}
          <div class="d11v4-section-label">当前可执行动作</div>
          <div class="d11v4-action-row">${actionHtml}</div>
          ${pendingExternal.length?`<div class="boundary-note"><strong>${(()=>{const ex=pendingExternal.map(t=>t.outbox?.durable_execution).find(Boolean);if(ex?.state==='RESULT_UNCERTAIN')return'外部操作结果暂无法确认，系统已停止自动重试，请先核对。';if(ex?.state==='HUMAN_REQUIRED')return'外部操作需要人工处理，自动流程已暂停。';if(ex?.state==='RETRYABLE')return'外部服务暂时失败，已确认未产生副作用，可在有限预算内安全重试。';return'有一项业务修改已被系统记录，但还没有确认外部系统执行成功。'})()}</strong></div>`:''}
        </div>`;
    }else if(node.state==='completed'){
      body=`<div class="d11v4-flow-content"><div class="d11v4-section-label">当时做过什么</div><div class="d11v4-history-list">${node.items.length?node.items.map(x=>`<div class="d11v4-history-item"><time>${esc(x.time)}</time><div><strong>${esc(x.title)}</strong><span>已完成</span></div></div>`).join(''):'<p class="demo-note">该阶段暂无详细记录</p>'}</div></div>`;
    }else{
      body=`<div class="d11v4-flow-content"><p class="demo-note">该阶段尚未开始</p></div>`;
    }
    return `<div class="${stateClass}">
      <div class="d11v4-flow-dot">${node.state==='completed'?icon('check'):node.state==='current'?icon('factory'):icon('clock')}</div>
      <div class="d11v4-flow-body">
        <div class="d11v4-flow-header">
          <span class="d11v4-flow-title">${esc(node.label)}</span>
          <span class="d11v4-flow-meta">${node.state==='completed'?'已完成':node.state==='current'?'进行中':'未开始'}</span>
          ${toggle}
        </div>
        ${body}
      </div>
    </div>`;
  }).join('');
  const summary=`<div class="summary-row"><span class="summary-label">客户交期</span><span class="summary-value ${o.requested_delivery_date&&new Date(o.requested_delivery_date)<=new Date(Date.now()+7*86400000)?'risk':''}">${esc(o.requested_delivery_date?fdate(o.requested_delivery_date):'—')}</span></div>
    <div class="summary-row"><span class="summary-label">当前阶段</span><span class="summary-value">${esc(d11v2HumanStage(o,w))}</span></div>
    <div class="summary-row"><span class="summary-label">当前关注</span><span class="summary-value ${actionable.length?'risk':''}">${esc(issue)}</span></div>`;
  openDrawer(`<div class="drawer-head"><div><span>订单跟单进度</span><h2>${esc(o.order_no||'未编号')} · ${esc(o.customer_name||'未知客户')}</h2><p>${esc(o.product_name||'')}</p></div><button class="drawer-close" data-close-drawer>×</button></div><div class="drawer-body"><section class="d11v4-order-summary">${summary}</section><section class="d11v4-flow-section"><h4 class="section-title">流程时间轴</h4><div class="d11v4-flow">${nodesHtml}</div></section></div>`);
  bindD11DrawerActions(caseId,w,actionable,waiting);
}

function openD12DeliveryReviewModal(caseId,task,order){
  const current=(order?.requested_delivery_date||order?.customer_delivery_date||'').slice(0,10);
  const m=openModal({eyebrow:'HUMAN REVIEW',title:'申请修改客户正式交期',subtitle:'这是公司对客户的正式承诺变更，因此需要主管审批。供应商最新承诺属于事实记录，请在订单事实中单独更新。',body:`<div class="form-grid"><label class="field"><span>当前客户正式交期</span><input type="date" disabled value="${esc(current)}"></label><label class="field"><span>拟调整为</span><input id="d12DeliveryDate" type="date" min="${esc(current||'')}" required></label><label class="field" style="grid-column:1/-1"><span>调整原因</span><textarea id="d12DeliveryReason" rows="4" placeholder="例如：供应商确认无法满足原交期，已与客户沟通候选方案"></textarea></label></div><div class="boundary-note"><strong>审批通过 ≠ ERP 已修改</strong><br>通过后只会把这项业务动作记录到受控待执行队列，外部系统是否执行仍由后续受控写链处理。</div>`,actions:'<button class="btn" value="cancel">取消</button><button class="btn primary" type="button" id="submitD12Delivery">提交主管审批</button>'});
  $('#submitD12Delivery',m).onclick=async()=>{const next=$('#d12DeliveryDate',m)?.value||'';const reason=($('#d12DeliveryReason',m)?.value||'').trim();if(!next)return toast('请选择新的客户正式交期','error');if(current&&next===current)return toast('新交期与当前正式交期相同，无需发起审批','info');const key=`D12:DELIVERY:${task.task_id}:${next}`;try{await api('/api/d12/reviews',{method:'POST',body:JSON.stringify({task_id:task.task_id,action_type:'UPDATE_EXPECTED_DELIVERY_DATE',target_type:'ORDER',target_id:order.order_id,payload:{expected_delivery_date:next},idempotency_key:key,reason:reason||`申请将客户正式交期调整为 ${next}`})});closeModal();toast('已提交主管审批；批准前不会改变客户正式交期','success');renderRoute(false)}catch(e){toast(e.message,'error')}};
}

function bindD11DrawerActions(caseId,w,actionable,waiting){
  const drawer=document.getElementById('drawer');
  if(!drawer)return;
  $$('.d11v4-flow-toggle',drawer).forEach(el=>{
    el.addEventListener('click',()=>{
      const node=el.closest('.d11v4-flow-node');
      if(node) node.classList.toggle('expanded');
    });
  });
  $$('[data-d11-start]',drawer).forEach(b=>{
    b.addEventListener('click',()=>d11SimpleTaskAction(caseId,b.dataset.d11Start,'start'));
  });
  $$('[data-d11-complete]',drawer).forEach(b=>{
    b.addEventListener('click',()=>d11SimpleTaskAction(caseId,b.dataset.d11Complete,'complete'));
  });
  $$('[data-d11-wait]',drawer).forEach(b=>{
    b.addEventListener('click',()=>openD11WaitingModal(caseId,b.dataset.d11Wait));
  });
  $$('[data-d11-reply]',drawer).forEach(b=>{
    b.addEventListener('click',()=>openD11ReplyModal(caseId,b.dataset.d11Reply));
  });
  $$('[data-d11-draft]',drawer).forEach(b=>{
    b.addEventListener('click',()=>{
      const taskId=b.dataset.d11Draft;
      const task=actionable.find(x=>x.task_id===taskId);
      const order=w.order||{};
      openD11CommunicationDraft({task:task?{...task,order,target:'factory'}:{task_id:taskId,order,target:'factory'},order,draftType:'SUPPLIER_PROGRESS_FOLLOWUP'});
    });
  });
  $$('[data-d12-delivery]',drawer).forEach(b=>{
    b.addEventListener('click',()=>{
      const task=actionable.find(x=>x.task_id===b.dataset.d12Delivery);
      if(!task)return toast('未找到对应行动任务','error');
      openD12DeliveryReviewModal(caseId,task,w.order||{});
    });
  });
}



async function openOrderOnlyFlowDrawer(orderId){
  try{
    const d=await api(`/api/orders/${encodeURIComponent(orderId)}?current_user_id=${encodeURIComponent(currentUser())}`);
    const o=d.order;
    const stageIdx=d11v2OrderStage(o,null);
    const stageLabels=['接单确认','备货采购','生产','出货','交付'];
    const flowNodes=stageLabels.map((label,i)=>{
      if(i<stageIdx)return{label,state:'completed',items:[]};
      if(i===stageIdx)return{label,state:'current',items:[]};
      return{label,state:'upcoming',items:[]};
    });
    const nodesHtml=flowNodes.map((node)=>{
      const stateClass=`d11v4-flow-node ${node.state}${node.state==='current'?' expanded':''}`;
      const toggle=node.state==='completed'?'<button class="d11v4-flow-toggle">展开历史</button>':'';
      let body='';
      if(node.state==='current'){
        body=`<div class="d11v4-flow-content"><div class="d11v4-section-label">当前业务进展</div><div class="d11v4-section-content"><p>${esc(d11v2HumanStage(o,null))}</p></div><div class="d11v4-section-label">最近更新</div><div class="d11v4-history-list">${(d.events||[]).slice(0,4).map(e=>`<div class="d11v4-history-item"><time>${esc(fdt(e.created_at))}</time><div><strong>${esc(e.event_type||'订单更新')}</strong><span>${esc(eventSummary(e.payload_json))}</span></div></div>`).join('')||'<p class="demo-note">暂无更多进展。</p>'}</div></div>`;
      }else if(node.state==='completed'){
        body=`<div class="d11v4-flow-content"><div class="d11v4-section-label">完成时间</div><div class="d11v4-section-content"><p>该阶段已完成</p></div></div>`;
      }else{
        body=`<div class="d11v4-flow-content"><p class="demo-note">该阶段尚未开始</p></div>`;
      }
      return `<div class="${stateClass}"><div class="d11v4-flow-dot">${node.state==='completed'?icon('check'):node.state==='current'?icon('factory'):icon('clock')}</div><div class="d11v4-flow-body"><div class="d11v4-flow-header"><span class="d11v4-flow-title">${esc(node.label)}</span><span class="d11v4-flow-meta">${node.state==='completed'?'已完成':node.state==='current'?'进行中':'未开始'}</span>${toggle}</div>${body}</div></div>`;
    }).join('');
    const summary=`<div class="summary-row"><span class="summary-label">客户交期</span><span class="summary-value">${esc(o.requested_delivery_date?fdate(o.requested_delivery_date):'—')}</span></div><div class="summary-row"><span class="summary-label">当前阶段</span><span class="summary-value">${esc(d11v2HumanStage(o,null))}</span></div><div class="summary-row"><span class="summary-label">订单状态</span><span class="summary-value">正常推进中</span></div>`;
    openDrawer(`<div class="drawer-head"><div><span>订单跟单进度</span><h2>${esc(o.order_no)} · ${esc(o.customer_name||'未知客户')}</h2><p>${esc(o.product_name||'')}</p></div><button class="drawer-close" data-close-drawer>×</button></div><div class="drawer-body"><section class="d11v4-order-summary">${summary}</section><section class="d11v4-flow-section"><h4 class="section-title">流程时间轴</h4><div class="d11v4-flow">${nodesHtml}</div></section><div class="boundary-note" style="margin-top:14px"><strong>当前没有需要你主动处理的异常。</strong><br>保持正常推进即可；如果新消息或订单事实发生变化，这张订单会自动进入"需要关注"或"等待反馈"。</div></div>`);
    const drawer=document.getElementById('drawer');
    if(drawer){
      $$('.d11v4-flow-toggle',drawer).forEach(el=>{
        el.addEventListener('click',()=>{
          const node=el.closest('.d11v4-flow-node');
          if(node) node.classList.toggle('expanded');
        });
      });
    }
  }catch(e){toast(e.message,'error')}
}
function openD11WaitingModal(caseId,taskId){const m=openModal({eyebrow:'等待反馈',title:'记录这次已经联系过',subtitle:'进入等待后，这件事先不占用你的注意力；到期或收到有效回复时再回来。',body:`<div class="form-grid"><label class="field full"><span>现在在等什么 *</span><input name="reason" placeholder="例如：等待供应商确认最终交期"></label><label class="field"><span>在等谁</span><select name="waiting_type"><option value="SUPPLIER_REPLY">供应商</option><option value="CUSTOMER_CONFIRMATION">客户</option><option value="EXTERNAL_REPLY">其他外部反馈</option></select></label><label class="field"><span>最晚等到 *</span><input name="due_at" type="datetime-local"></label></div>`,actions:'<button class="btn" value="cancel">取消</button><button class="btn primary" type="button" id="d11ConfirmWait">开始等待</button>'});$('#d11ConfirmWait',m).onclick=async()=>{const reason=$('[name="reason"]',$('#modalBody')).value.trim(),raw=$('[name="due_at"]',$('#modalBody')).value,waiting_type=$('[name="waiting_type"]',$('#modalBody')).value;if(!raw)return toast('请填写最晚等待时间','error');try{await api(`/api/action-workspace/tasks/${encodeURIComponent(taskId)}/wait`,{method:'POST',body:JSON.stringify({waiting_type,reason,due_at:new Date(raw).toISOString()})});invalidateWorkspace();closeModal();toast('已记录等待，系统会替你盯住到期时间','success');await openCaseDrawer(caseId);renderRoute(false)}catch(e){toast(e.message,'error')}}}
function openD11ReplyModal(caseId,waitingId){const m=openModal({eyebrow:'收到回复',title:'这条回复够不够继续往下处理？',subtitle:'收到消息不一定代表问题已经解决。只有信息足够时，当前事项才重新进入处理。',body:`<label class="field"><span>回复内容</span><textarea name="reply_payload" rows="5" placeholder="例如：供应商确认最早 8 月 23 日交货"></textarea></label><label class="override-choice"><input name="satisfies_completion" type="checkbox"> 这条回复已经回答了我在等的问题，可以继续处理下一步</label>`,actions:'<button class="btn" value="cancel">取消</button><button class="btn primary" type="button" id="d11ConfirmReply">记录回复</button>'});$('#d11ConfirmReply',m).onclick=async()=>{const reply_payload=$('[name="reply_payload"]',$('#modalBody')).value.trim(),satisfies_completion=$('[name="satisfies_completion"]',$('#modalBody')).checked;try{await api(`/api/action-workspace/waitings/${encodeURIComponent(waitingId)}/reply`,{method:'POST',body:JSON.stringify({reply_id:`UI-${Date.now()}`,reply_payload:{summary:reply_payload},satisfies_completion})});invalidateWorkspace();closeModal();toast(satisfies_completion?'回复已记录，这件事重新需要处理':'回复已记录，继续等待','success');await openCaseDrawer(caseId);renderRoute(false)}catch(e){toast(e.message,'error')}}}
async function d11SimpleTaskAction(caseId,taskId,action){try{await api(`/api/action-workspace/tasks/${encodeURIComponent(taskId)}/${action}`,{method:'POST',body:'{}'});invalidateWorkspace();toast(action==='start'?'已开始处理':'已完成这件事','success');await openCaseDrawer(caseId);renderRoute(false)}catch(e){toast(e.message,'error')}}


function openD11CommunicationDraft(ctx){
  communicationContext=ctx;openDrawer(`<div class="drawer-head"><div><span>沟通草稿</span><h2>基于当前订单准备沟通</h2><p>系统会使用订单事实和当前要推进的事情生成草稿；你确认后再复制或发送。</p></div><button class="drawer-close" data-close-drawer>×</button></div><div class="drawer-body">${communicationContextHtml(ctx)}<div id="communicationPanel"></div></div>`);
  renderFT06Panel($('#communicationPanel',$('#drawer')));
}

function d11v2TomorrowPlanKey(){return `floworderTomorrowPlan:${currentUser()}:${todayISO()}`}
function d11v4RecapItem(title,body){return `<div class="d11v4-recap-item"><strong>${esc(title)}</strong><p>${esc(body)}</p></div>`}
async function pageRecap(root){
  const [wd,reviews]=await Promise.all([workspaceData(),api('/api/reviews').catch(()=>({items:[]}))]);const cases=(wd.items||[]).filter(matchWorkspace);const completed=[];cases.forEach(w=>(w.history_tasks||[]).filter(t=>String(t.updated_at||'').slice(0,10)===todayISO()).forEach(t=>completed.push({w,t})));const waiting=cases.filter(w=>(w.waiting_tasks||[]).length),unfinished=d11v2SortCases(cases.filter(w=>w.workspace_state==='ACTIONABLE'||d11v2IsOverdue(w)));const confirmedToday=(reviews.items||[]).filter(x=>x.status==='CONFIRMED'&&String(x.updated_at||x.created_at||'').slice(0,10)===todayISO());const suggested=unfinished.slice(0,5);let saved=safeJson(localGet(d11v2TomorrowPlanKey()),null);
  root.innerHTML=`<div class="page-stack"><header class="d11v4-header"><h2>复盘</h2><span class="d11v4-date">${esc(todayISO())}</span></header><div class="d11v4-recap-grid"><section class="d11v4-section"><div class="d11v4-section-head"><h3>今天完成</h3><span class="d11v4-recap-count">${completed.length}</span></div><div class="d11v4-section-body">${completed.slice(0,8).map(({w,t})=>d11v4RecapItem(`${w.order?.order_no||'未编号'} · ${t.title}`,'已完成，订单继续按当前计划推进。')).join('')||d11v4RecapItem('今天还没有记录完成的事项。','')}</div></section><section class="d11v4-section"><div class="d11v4-section-head"><h3>还没收口</h3><span class="d11v4-recap-count">${unfinished.length+waiting.length}</span></div><div class="d11v4-section-body">${unfinished.slice(0,5).map(w=>d11v4RecapItem(`${w.order?.order_no||'未编号'} · ${d11v2NextAction(w)}`,d11v2Reason(w))).join('')}${waiting.slice(0,5).map(w=>d11v4RecapItem(`${w.order?.order_no||'未编号'} · 正在等待反馈`,`${w.waiting_tasks?.[0]?.active_waiting?.reason||'等待外部回复'} · 最晚 ${fdt(d11v2WaitingDue(w))}`)).join('')||d11v4RecapItem('当前没有未收口事项。','')}</div></section><section class="d11v4-section"><div class="d11v4-section-head"><h3>今天的关键变化</h3><span class="d11v4-recap-count">${confirmedToday.length}</span></div><div class="d11v4-section-body">${confirmedToday.slice(0,8).map(x=>d11v4RecapItem(`${x.order_no||'订单'} · 信息已确认`,(x.raw_content||'').slice(0,160))).join('')||d11v4RecapItem('今天暂无新的已确认外部变化。','')}</div></section><section class="d11v4-section"><div class="d11v4-section-head"><h3>明天计划</h3></div><div class="d11v4-section-body"><div class="d11v4-tomorrow" id="d11v4TomorrowList" style="padding:12px 0">${(saved||suggested.map((w,i)=>({case_id:w.action_case?.action_case_id,order_no:w.order?.order_no,title:d11v2NextAction(w)}))).map((x,i)=>`<div class="d11v4-tomorrow-row"><b>${i+1}</b><span>${esc(x.order_no||'订单')} · ${esc(x.title||'继续推进')}</span></div>`).join('')||d11v4RecapItem('当前没有需要带到明天的事项。','')}</div><div class="review-actions" style="padding-bottom:12px"><button class="btn primary" id="d11v4SaveTomorrow">保存明日计划</button></div></div></section></div></div>`;
  $('#d11v4SaveTomorrow',root)?.addEventListener('click',()=>{const plan=suggested.map(w=>({case_id:w.action_case?.action_case_id,order_no:w.order?.order_no,title:d11v2NextAction(w)}));localSet(d11v2TomorrowPlanKey(),JSON.stringify(plan));toast('明日计划已保存；明早会以此为基础，再根据新信息调整','success')});
}

// D11 V0.4 bootstrap contract: the script is loaded at the end of <body>, so
// shell elements already exist. Keep one explicit initialization call here;
// without it the UI remains permanently in the loading state even though all
// backend APIs are healthy.
init();
