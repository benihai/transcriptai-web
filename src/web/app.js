/* ============================================================
   SYNTHIA · Command Center — application logic
   NOTE: $, $$, esc, nd, et, ic, svg, hydrateIcons, toast, nav,
   switchTab, setLiveStatus, setProcessing, cancelProc,
   afterProcessing, proc-timer helpers + ICONS are defined inline
   in index.html (loaded before this file).
   Preserves all window.pywebview.api.* calls + live-push hooks.
   ============================================================ */

/* ---------- shared state ---------- */
let _projects = ['כללי'];
let _meetings = [];
let _recordings = [];
let _selected = new Map();          // key -> {kind,id,path,project,title,date} for bulk actions
let _hubRowMap = {};                // key -> row meta for currently rendered hub rows
let _trash = [];                    // session-local trash (STUB — TODO(backend): storage.list_trash)

/* ============================================================
   PROJECTS
   ============================================================ */
async function loadProjects(){
  _projects = await window.pywebview.api.list_projects();
  const sel = $('#projSelect');
  if (sel) sel.innerHTML = _projects.map(p=>`<option value="${esc(p)}">${esc(p)}</option>`).join('');
}
function newProject(){ $('#newProjInput').style.display='inline-block'; $('#newProjOk').style.display='inline-flex'; $('#newProjInput').focus(); }
async function createProject(){
  const name = $('#newProjInput').value.trim(); if(!name) return;
  const r = await window.pywebview.api.create_project(name);
  if(!r.ok){ toast(r.error,'alert'); return; }
  await loadProjects(); $('#projSelect').value=r.name;
  $('#newProjInput').value=''; $('#newProjInput').style.display='none'; $('#newProjOk').style.display='none';
  toast('הפרויקט נוצר','check');
}
async function createProjectFromView(){
  const name = $('#projNewName').value.trim(); if(!name) return;
  const r = await window.pywebview.api.create_project(name);
  if(!r.ok){ toast(r.error,'alert'); return; }
  $('#projNewName').value=''; await loadProjects(); loadProjectsView();
  toast('הפרויקט נוצר','check');
}
function fillFilter(sel, items){
  const projs = [...new Set(items.map(i=>i.project))];
  const cur = sel.value || '__all__';
  sel.innerHTML = `<option value="__all__">כל הלקוחות</option>` + projs.map(p=>`<option value="${esc(p)}">${esc(p)}</option>`).join('');
  sel.value = [...sel.options].some(o=>o.value===cur) ? cur : '__all__';
}

async function loadProjectsView(){
  const list = $('#projects-list');
  list.innerHTML = '<div class="empty">טוען…</div>';
  const stats = await window.pywebview.api.project_stats();
  // approximate billable hours per project from recordings durations
  let recs = _recordings;
  if(!recs.length){ try{ recs = await window.pywebview.api.list_recordings(); _recordings=recs; }catch(e){} }
  const hrsByProj = {};
  recs.forEach(r=>{ hrsByProj[r.project]=(hrsByProj[r.project]||0)+durToSec(r.duration); });
  list.innerHTML = stats.map(p => {
    const hrs = hrsByProj[p.name] ? (hrsByProj[p.name]/3600).toFixed(1)+'h' : '—';
    return `<div class="card proj-card" style="cursor:pointer" onclick="openProject('${esc(p.name)}')">
      <div style="display:flex;align-items:center;gap:14px;min-width:0">
        ${p.logo ? `<img src="${p.logo}" class="proj-logo">` : `<div class="proj-logo placeholder">${esc((p.name||'?').trim().charAt(0))}</div>`}
        <div style="min-width:0">
          <div class="pc-name">${esc(p.name)}${p.is_default?' <span class="tag">ברירת מחדל</span>':''}</div>
          <div class="pc-meta"><span><b>${p.n_meetings}</b> סיכומים</span><span><b>${p.n_recordings}</b> הקלטות</span><span>זמן מצטבר ~<b>${hrs}</b></span></div>
        </div>
      </div>
      <div style="display:flex;gap:7px;align-items:center;flex-wrap:wrap" onclick="event.stopPropagation()">
        <button class="btn btn-ghost btn-sm" onclick="openProfile('${esc(p.name)}')"><span class="ic">${svg('settings')}</span> פרופיל</button>
        ${p.is_default?'':`<button class="btn btn-ghost btn-sm" onclick="renameProject('${esc(p.name)}')"><span class="ic">${svg('edit')}</span> שם</button>`}
        ${p.is_default?'':`<button class="icon-btn danger" title="מחק פרויקט" onclick="deleteProject('${esc(p.name)}',${p.n_meetings||0},${p.n_recordings||0})">${svg('trash',14)}</button>`}
        <button class="btn btn-ghost btn-sm" onclick="openProject('${esc(p.name)}')">צפה ←</button>
      </div>
    </div>`;
  }).join('');
}
function durToSec(d){ // "MM:SS" or "HH:MM:SS" -> seconds
  if(!d) return 0; const p=String(d).split(':').map(Number); if(p.some(isNaN)) return 0;
  return p.length===3 ? p[0]*3600+p[1]*60+p[2] : p.length===2 ? p[0]*60+p[1] : 0;
}
function openProject(name){
  nav('summaries');
  const sel = $('#sumFilter');
  setTimeout(()=>{ if([...sel.options].some(o=>o.value===name)){ sel.value=name; renderHub(); } }, 60);
}

/* ---------- Project profile ---------- */
let _profileProject=null;
async function openProfile(name){
  _profileProject=name; $('#profProjName').textContent=name;
  const p=await window.pywebview.api.get_project_profile(name);
  $('#profClient').value=p.client_name||''; $('#profContact').value=p.contact||'';
  $('#profPhone').value=p.phone||''; $('#profEmail').value=p.email||'';
  $('#profAddress').value=p.address||''; $('#profNotes').value=p.notes||'';
  $('#profDomain').value = (p.email||'').includes('@') ? p.email.split('@')[1].trim() : '';
  profLogoStatus(p.has_logo); $('#profileModal').style.display='flex';
}
async function fetchProfileLogo(){
  const d=$('#profDomain').value.trim(); if(!d){ toast('הזן דומיין (apple.com)','alert'); return; }
  toast('מאחזר לוגו…','refresh');
  const r=await window.pywebview.api.fetch_logo_from_domain(_profileProject, d);
  if(r.ok){ toast('הלוגו אוחזר','check'); profLogoStatus(true); } else toast(r.error||'לא נמצא לוגו','alert');
}
function profLogoStatus(has){ const e=$('#profLogoStatus'); e.textContent=has?'לוגו קיים':'אין לוגו'; }
async function saveProfile(){
  await window.pywebview.api.save_project_profile(_profileProject, {
    client_name:$('#profClient').value, contact:$('#profContact').value, phone:$('#profPhone').value,
    email:$('#profEmail').value, address:$('#profAddress').value, notes:$('#profNotes').value });
  $('#profileModal').style.display='none'; toast('הפרופיל נשמר','check');
}
async function uploadProfileLogo(){
  const r = window.IS_WEB ? await webUpload('/api/upload_logo', _profileProject, 'image/*')
                          : await window.pywebview.api.upload_client_logo(_profileProject);
  if(r.ok){ toast('הלוגו הועלה','check'); profLogoStatus(true); } else if(!r.canceled) toast(r.error||'נכשל','alert');
}
async function removeProfileLogo(){
  const r=await window.pywebview.api.remove_client_logo(_profileProject);
  if(r.removed) toast('הלוגו הוסר','x'); profLogoStatus(false);
}
async function renameProject(name){
  const v=await askPrompt('שם הפרויקט', name||''); if(v==null||!v||v===name) return;
  const r=await window.pywebview.api.rename_project(name, v);
  if(r.ok){ toast('שם הפרויקט עודכן','check'); await loadProjects(); loadProjectsView(); } else toast(r.error||'נכשל','alert');
}
async function deleteProject(name, nMeet, nRec){
  const has=(nMeet||0)+(nRec||0);
  const msg = has ? `למחוק את הפרויקט "${name}"? פעולה זו תמחק לצמיתות ${nMeet||0} סיכומים ו-${nRec||0} הקלטות. בלתי הפיך!`
                  : `למחוק את הפרויקט "${name}"?`;
  if(!await askConfirm(msg)) return;
  const r=await window.pywebview.api.delete_project(name);
  if(r&&r.ok){ toast('הפרויקט נמחק','trash'); await loadProjects(); loadProjectsView(); }
  else toast((r&&r.error)||'מחיקה נכשלה','alert');
}

/* ============================================================
   OVERVIEW DASHBOARD
   ============================================================ */
async function loadOverview(){
  try{ _meetings = await window.pywebview.api.list_meetings(); }catch(e){ _meetings=[]; }
  try{ _recordings = await window.pywebview.api.list_recordings(); }catch(e){ _recordings=[]; }
  updateNavCounts();
  const done=_meetings.filter(m=>m.summarized);
  const tasks=done.reduce((a,m)=>a+(m.n_tasks||0),0);
  const pending=_meetings.length-done.length;
  // hours this month (approx, from recordings durations)
  const now=new Date(), hrs=(_recordings.reduce((a,r)=>a+durToSec(r.duration),0)/3600);
  $('#ovStats').innerHTML = `
    <div class="stat"><div class="num">${done.length}</div><div class="lbl">${ic('check',13)} פגישות סוכמו</div></div>
    <div class="stat"><div class="num">${tasks}</div><div class="lbl">${ic('list',13)} משימות זוהו</div></div>
    <div class="stat"><div class="num">${pending}</div><div class="lbl">${ic('clock',13)} ממתינות לסיכום</div></div>
    <div class="stat"><div class="num">${hrs.toFixed(1)}<small>h</small></div><div class="lbl">${ic('activity',13)} זמן מתומלל ~</div></div>`;

  // next meeting + live status
  let cal={ok:false,events:[]};
  try{ cal=await window.pywebview.api.calendar_upcoming(); }catch(e){}
  const ev=(cal.events||[])[0];
  if(ev){
    const s=new Date(ev.start), t=s.toLocaleString('he-IL',{weekday:'short',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
    const lbl=ev.platform==='meet'?'Meet':ev.platform==='zoom'?'Zoom':ev.platform==='teams'?'Teams':'';
    $('#ovNext').innerHTML = `
      <div style="font-size:16px;font-weight:700;margin-bottom:6px">${et(ev.title)}</div>
      <div class="mono" style="font-size:12px;color:var(--muted);margin-bottom:12px">${esc(t)} ${lbl?`· <span class="plat plat-${ev.platform}">${lbl}</span>`:''}</div>
      <button class="btn btn-primary btn-sm" onclick="sendBotNow('${esc(ev.url||'')}','${esc(ev.title||'')}')">${svg('send',14)} שלח בוט עכשיו</button>`;
  } else {
    $('#ovNext').innerHTML = `<div class="empty" style="padding:26px">${ic('calendar',26)}<br>אין פגישה קרובה ביומן.</div>`;
  }
  const recActive=(typeof recState!=='undefined'&&recState==='recording');
  const botActive=!!cal.bot_active; // TODO(backend): no live-bot status API yet
  let live, color;
  if(recActive){ live='RECORDING'; color='var(--rec)'; setLiveStatus('rec'); }
  else if(botActive){ live='BOT ACTIVE'; color='var(--accent)'; setLiveStatus('bot'); }
  else { live='IDLE'; color='var(--muted)'; setLiveStatus('idle'); }
  $('#ovLive').innerHTML = `
    <div style="display:flex;align-items:center;gap:12px">
      <span style="width:14px;height:14px;border-radius:50%;background:${color};display:inline-block"></span>
      <span class="mono" style="font-size:20px;letter-spacing:.06em;color:${color}">${live}</span>
    </div>
    <div class="sub" style="margin-top:12px">${cal.enabled?'הצטרפות אוטומטית פעילה':'הצטרפות אוטומטית כבויה (ראה הגדרות)'} · בוט: ${esc(cal.bot_name||'—')}</div>`;

  // recent
  const recent=done.slice(0,4);
  $('#ovRecent').innerHTML = recent.length ? recent.map(m=>`
    <div class="cal-row" style="cursor:pointer" onclick="openMeeting('${m.id}','${esc(m.project)}')">
      <div class="c-when mono" style="width:120px;flex-shrink:0">${esc(m.date)}</div>
      <div class="cal-main"><div class="ttl">${et(m.title)}</div>
        <div class="c-sub">${esc(m.project)}${m.n_points?` · ${m.n_points} נקודות`:''}</div></div>
      ${m.n_tasks?`<span class="badge accent">${m.n_tasks} משימות</span>`:''}
    </div>`).join('') : `<div class="empty">${ic('list',26)}<br>אין פגישות עדיין.</div>`;
}
function updateNavCounts(){
  const done=_meetings.filter(m=>m.summarized).length;
  $('#navCountMeetings').textContent=_meetings.length||'';
  // tasks count filled by loadMyTasks; leave meetings here
}
// STUB — TODO(backend): expose bot_dispatch.dispatch via gui.py Api
async function sendBotNow(url, title){
  if(!url){ toast('אין קישור לפגישה זו','alert'); return; }
  if(!await askConfirm('לשלוח את הבוט להצטרף לפגישה "'+title+'" עכשיו?')) return;
  toast('הבקשה נרשמה (דמו) — חיבור שליחה ידנית טרם פעיל','send');
}

/* ============================================================
   MEETING HUB (unified table)
   ============================================================ */
async function loadSummaries(){
  $('#hub-list').innerHTML = '<div class="empty">טוען…</div>';
  _meetings = await window.pywebview.api.list_meetings();
  try{ _recordings = await window.pywebview.api.list_recordings(); }catch(e){ _recordings=[]; }
  fillFilter($('#sumFilter'), _meetings.concat(_recordings));
  updateNavCounts();
  _selected.clear(); renderHub();
}
function hubRows(){
  // meetings (summarized + pending transcripts)
  const rows = _meetings.map(m=>({
    kind:'meeting', key:'m:'+m.id, id:m.id, path:m.audio_path||'', project:m.project, date:m.date, title:nd(m.title),
    status: m.summarized ? 'processed' : (m.status==='failed'?'failed':m.status==='canceled'?'failed':'pending'),
    tasks:m.n_tasks||0, tr:m.tr_chars||0, preview:m.preview||'', _m:m
  }));
  // raw recordings not tied to a meeting
  const used = new Set(_meetings.map(m=>(m.audio_path||'').replace(/\\/g,'/').toLowerCase()).filter(Boolean));
  _recordings.forEach(r=>{
    const rk=(r.path||'').replace(/\\/g,'/').toLowerCase();
    if(used.has(rk)) return;
    rows.push({ kind:'rec', key:'r:'+r.path, id:null, path:r.path, project:r.project, date:r.date, title:nd(r.name),
      status:'raw', tasks:0, tr:0, preview:'', _r:r });
  });
  return rows;
}
const STATUS_BADGE={processed:'<span class="badge ok">PROCESSED</span>',pending:'<span class="badge pending">PENDING</span>',
  failed:'<span class="badge fail">FAILED</span>',raw:'<span class="badge raw">UNPROCESSED</span>'};
function renderHub(){
  const list=$('#hub-list');
  const f=$('#sumFilter').value, sf=$('#statusFilter').value;
  const q=($('#sumSearch').value||'').trim().toLowerCase();
  let items=hubRows();
  if(f && f!=='__all__') items=items.filter(r=>r.project===f);
  if(sf && sf!=='__all__'){ if(sf==='processed')items=items.filter(r=>r.status==='processed');
    else if(sf==='pending')items=items.filter(r=>r.status==='pending'||r.status==='failed');
    else if(sf==='raw')items=items.filter(r=>r.status==='raw'); }
  if(q) items=items.filter(r=>(r.title+' '+r.preview+' '+r.project).toLowerCase().includes(q));
  if(!items.length){ list.innerHTML=`<div class="empty">${ic('list',28)}<br>אין פגישות להצגה.<br>לחץ "פגישה חדשה" כדי להתחיל.</div>`;
    _hubRowMap={}; syncBulk(); return; }
  _hubRowMap={};
  items.forEach(r=>{ _hubRowMap[r.key]={kind:r.kind, id:r.id, path:r.path, project:r.project, title:r.title, date:r.date}; });
  const allOn = items.length && items.every(r=>_selected.has(r.key));
  list.innerHTML = `<table class="dtable"><thead><tr>
      <th class="checkcell"><input type="checkbox" ${allOn?'checked':''} onchange="toggleAllSel(this)"></th>
      <th>תאריך</th><th>כותרת</th><th>לקוח</th><th>סטטוס</th><th>משימות</th><th></th>
    </tr></thead><tbody>` + items.map(r=>{
      const onclick = r.kind==='meeting' ? `openMeeting('${r.id}','${esc(r.project)}')` :
                      `nav('recordings')`;
      return `<tr onclick="${onclick}">
        <td class="checkcell" onclick="event.stopPropagation()"><input type="checkbox" ${_selected.has(r.key)?'checked':''} onchange="toggleSelRow('${esc(r.key).replace(/\\/g,'\\\\')}',this)"></td>
        <td class="c-when">${esc(r.date)}</td>
        <td><div class="c-title">${esc(r.title)}</div>${r.tr?`<div class="c-sub">תמלול ${Number(r.tr).toLocaleString()} תווים</div>`:''}</td>
        <td><span class="tag proj">${esc(r.project)}</span></td>
        <td>${STATUS_BADGE[r.status]||''}</td>
        <td class="mono">${r.tasks||'—'}</td>
        <td onclick="event.stopPropagation()"><div class="row-actions">${rowActions(r)}</div></td>
      </tr>`;
    }).join('') + `</tbody></table>`;
  syncBulk();
}
function rowActions(r){
  if(r.kind==='rec'){
    const p=esc(r.path).replace(/\\/g,'\\\\');
    return `<button class="btn btn-accent btn-sm" onclick="processRec('${p}','${esc(r.project)}')">${svg('activity',13)} תמלל וסכם</button>
            <button class="icon-btn danger" title="מחק" onclick="deleteRecordingHub('${p}')">${svg('trash',14)}</button>`;
  }
  if(r.status==='pending'||r.status==='failed')
    return `<button class="btn btn-accent btn-sm" onclick="summarizeMeeting('${r.id}','${esc(r.project)}')">${svg('refresh',13)} הפק סיכום</button>
            <button class="icon-btn danger" title="מחק" onclick="deleteMeeting('${r.id}','${esc(r.project)}')">${svg('trash',14)}</button>`;
  return `<button class="icon-btn danger" title="מחק" onclick="deleteMeeting('${r.id}','${esc(r.project)}')">${svg('trash',14)}</button>`;
}
async function deleteRecordingHub(path){
  if(!await askConfirm('למחוק את ההקלטה לצמיתות?')) return;
  await window.pywebview.api.delete_recording(path); toast('ההקלטה נמחקה','x'); loadSummaries();
}
/* ---- bulk selection (meetings + raw recordings) ---- */
function toggleSelRow(key, cb){
  if(cb.checked) _selected.set(key, _hubRowMap[key]); else _selected.delete(key);
  // reflect header "select-all" without full re-render
  const head=document.querySelector('#hub-list thead input[type=checkbox]');
  if(head){ const total=Object.keys(_hubRowMap).length; head.checked = total>0 && _selected.size>=total; }
  syncBulk();
}
function toggleAllSel(cb){
  if(cb.checked) Object.keys(_hubRowMap).forEach(k=>_selected.set(k,_hubRowMap[k]));
  else Object.keys(_hubRowMap).forEach(k=>_selected.delete(k));
  document.querySelectorAll('#hub-list tbody input[type=checkbox]').forEach(x=>x.checked=cb.checked);
  syncBulk();
}
function clearSel(){ _selected.clear(); renderHub(); }
function syncBulk(){
  const bb=$('#bulkbar'); if(!bb) return;
  bb.classList.toggle('show', _selected.size>0);
  $('#bulkCnt').textContent=_selected.size+' נבחרו';
  $('#bulkMove').innerHTML=`<option value="">↦ העבר לפרויקט…</option>`+_projects.map(p=>`<option value="${esc(p)}">${esc(p)}</option>`).join('');
}
async function bulkMoveGo(){
  const to=$('#bulkMove').value; if(!to){ toast('בחר פרויקט יעד','alert'); return; }
  let n=0; for(const [k,it] of _selected){
    if(it.kind==='meeting'){ if(it.project===to) continue; const r=await window.pywebview.api.move_meeting(it.id, it.project, to); if(r&&r.ok) n++; }
    else { const r=await window.pywebview.api.move_recording(it.path, to); if(r&&r.ok) n++; }
  }
  toast(n+' פריטים הועברו ל'+to,'folder'); _selected.clear(); loadSummaries();
}
async function bulkDelete(){
  const n=_selected.size; if(!n) return;
  if(!await askConfirm('למחוק '+n+' פריטים נבחרים? (כולל הקלטות שלא סוכמו)')) return;
  for(const [k,it] of _selected){
    if(it.kind==='meeting'){
      _trash.push({id:it.id, project:it.project, title:it.title, date:it.date, ts:Date.now()});
      await window.pywebview.api.delete_meeting(it.id, it.project); // TODO(backend): soft-delete
    } else {
      await window.pywebview.api.delete_recording(it.path);
    }
  }
  toast(n+' פריטים נמחקו','trash'); _selected.clear(); loadSummaries();
}

/* ---------- global search ---------- */
function globalSearch(q, enter){
  if(currentView!=='summaries') nav('summaries');
  const box=$('#sumSearch'); if(box){ box.value=q; renderHub(); }
}

/* ============================================================
   MEETING DETAIL (5 tabs)
   ============================================================ */
async function openMeeting(id, project){
  const m = await window.pywebview.api.get_meeting(id, project);
  if(!m){ toast('הסיכום לא נמצא','alert'); return; }
  window._detailMeeting = m; _chatHistory = [];
  $('#detail-output').innerHTML = renderSummary(m, {withExport:true});
  nav('detail');
}
function renderSummary(m, opts={}){
  const s = m.summary || m;
  const nTasks=(s.action_items||[]).length;
  let head = `<div class="detail-head"><div>
      <div class="sum-title">${et(s.title)}</div>
      <div class="sum-date">${esc(m.date||'')}${m.project?` · ${esc(m.project)}`:''}</div></div>`;
  if(opts.withExport && m.id){
    head += `<div class="detail-actions">
      ${moveSelect(m.project, `moveMeeting('${m.id}','${esc(m.project)}',this)`)}
      <button class="btn btn-ghost btn-sm" onclick="renameMeeting('${m.id}','${esc(m.project)}')">${svg('edit',13)} שם</button>
      <button class="btn btn-ghost btn-sm" onclick="copyMeeting()">${svg('copy',13)} העתק</button>
      <button class="btn btn-ghost btn-sm" onclick="emailMeeting()">${svg('mail',13)} מייל</button>
      <button class="btn btn-ghost btn-sm" onclick="formatFromMeeting('${m.id}','${esc(m.project)}')">${svg('file',13)} מעוצב</button>
      <button class="btn btn-ghost btn-sm" onclick="exportWord('${m.id}')">${svg('download',13)} Word</button>
      <button class="icon-btn danger" title="מחק" onclick="deleteMeeting('${m.id}','${esc(m.project)}')">${svg('trash',14)}</button></div>`;
  }
  head += `</div>`;

  const editable = !!(opts.withExport && m.id);

  // Summary pane
  let sumPane='';
  if(editable) sumPane += `<div style="display:flex;justify-content:flex-end;margin-bottom:10px">
      <button class="btn btn-ghost btn-sm" id="editSumBtn" onclick="enterSummaryEdit()">${svg('edit',13)} ערוך ידנית</button></div>`;
  sumPane += `<div id="summaryContent">${summaryReadHTML(s)}</div>`;
  if(editable) sumPane += chatBlockHTML();

  // Transcript
  const trPane = m.transcript ? `<div class="transcript">${et(m.transcript)}</div>` : '<div class="empty">אין תמלול זמין.</div>';

  // Audio
  let audioPane;
  if(m.audio_path){
    const ap=esc(m.audio_path).replace(/\\/g,'\\\\');
    audioPane = `<div class="player">
        <button class="play" onclick="playRec('${ap}')">${svg('play',16)}</button>
        <div class="track"><div class="fill"></div></div>
        <span class="tt">לחץ להשמעה במנגן המערכת</span></div>
      <div class="sub">קובץ: <span class="mono">${esc((m.audio_path||'').split(/[\\/]/).pop())}</span></div>`;
  } else audioPane='<div class="empty">אין קובץ אודיו מקושר.</div>';

  // Tasks
  const tasksPane = `<div id="tasksContent">${tasksHTML(s)}</div>`;

  // Files (STUB)
  const filesPane = filesTabHTML();

  const tabs = `<div class="tabs-wrap">
    <div class="tabs">
      <button class="tab active" onclick="switchTab(this,0)">${svg('file',14)} סיכום</button>
      <button class="tab" onclick="switchTab(this,1)">${svg('list',14)} תמלול</button>
      <button class="tab" onclick="switchTab(this,2)">${svg('play',14)} אודיו</button>
      <button class="tab" onclick="switchTab(this,3)">${svg('check',14)} משימות <span class="cnt">${nTasks}</span></button>
      <button class="tab" onclick="switchTab(this,4)">${svg('paperclip',14)} קבצים</button>
    </div>
    <div class="tabpane active">${sumPane}</div>
    <div class="tabpane">${trPane}</div>
    <div class="tabpane">${audioPane}</div>
    <div class="tabpane">${tasksPane}</div>
    <div class="tabpane">${filesPane}</div>
  </div>`;
  return head + tabs;
}

function summaryReadHTML(s){
  let h = `<div class="tldr">${et(s.summary)||'—'}</div>`;
  if(s.topics && s.topics.length)
    h += `<div class="block"><h3>פירוט הנושאים</h3>` + s.topics.map(t=>
      `<div class="topic"><div class="topic-ttl">${et(t.title)}</div><ul>${(t.points||[]).map(p=>`<li>${et(p)}</li>`).join('')}</ul></div>`
    ).join('') + `</div>`;
  if(s.decisions && s.decisions.length)
    h += `<div class="block"><h3>החלטות שהתקבלו</h3><ul>${s.decisions.map(d=>`<li>${et(d)}</li>`).join('')}</ul></div>`;
  if(s.key_points && s.key_points.length)
    h += `<div class="block"><h3>נקודות מפתח</h3><ul>${s.key_points.map(p=>`<li>${et(p)}</li>`).join('')}</ul></div>`;
  return h;
}

/* ---------- Tasks tab: checkbox(done) + assignee + due, persisted via update_summary ---------- */
function taskItemHTML(a, i){
  const meta = `<div class="tmeta">
      <span class="ml">אחראי</span><input value="${esc(a.owner||'')}" placeholder="—" onchange="updTaskField(${i},'owner',this.value)">
      <span class="ml">מועד</span><input value="${esc(a.due||'')}" placeholder="—" onchange="updTaskField(${i},'due',this.value)">
    </div>`;
  return `<div class="task ${a.done?'done':''}">
    <input type="checkbox" ${a.done?'checked':''} onchange="toggleTaskDone(${i},this.checked)">
    <div style="flex:1">
      <div class="tk">${et(a.task)}</div>${meta}
    </div>
    <div class="task-tools">
      <button class="btn btn-ghost btn-sm" onclick="addTaskToMine(${i})">${svg('plus',13)} למשימות שלי</button>
      <button class="btn btn-ghost btn-sm" onclick="sendToTaskMgr(${i})" title="Trello / ClickUp">${svg('send',13)} שלח</button>
    </div>
  </div>`;
}
function tasksHTML(s){
  const items=s.action_items||[];
  if(!items.length) return '<div class="empty">לא זוהו משימות בפגישה זו.</div>';
  const open=[], done=[];
  items.forEach((a,i)=>{ (a.done?done:open).push({a,i}); });
  let h = open.length ? open.map(o=>taskItemHTML(o.a,o.i)).join('')
                      : '<div class="sub">כל המשימות הושלמו.</div>';
  if(done.length){
    h += `<div class="arch-wrap collapsed"><div class="arch-head" onclick="this.parentNode.classList.toggle('collapsed')">
        <span class="ic caret">${svg('chevron',14)}</span> ארכיון שהושלם (${done.length})</div>
      <div class="arch-body">${done.map(o=>taskItemHTML(o.a,o.i)).join('')}</div></div>`;
  }
  return h;
}
async function persistSummary(){
  const m=window._detailMeeting; if(!m||!m.id) return false;
  const r=await window.pywebview.api.update_summary(m.id, m.project, m.summary);
  if(r&&r.ok){ m.summary=r.summary||m.summary; return true; }
  toast((r&&r.error)||'שמירה נכשלה','alert'); return false;
}
async function toggleTaskDone(i, checked){
  const s=(window._detailMeeting||{}).summary; if(!s) return;
  (s.action_items||[])[i].done=checked;
  if(await persistSummary()){ reRenderSummaryParts(); toast(checked?'הושלם':'הוחזר לפעילות','check'); }
}
async function updTaskField(i, key, val){
  const s=(window._detailMeeting||{}).summary; if(!s) return;
  (s.action_items||[])[i][key]=val.trim();
  await persistSummary(); // silent
}
// STUB — TODO(backend): real Trello/ClickUp integration
function sendToTaskMgr(i){ toast('נשלח למנהל המשימות (דמו — Trello/ClickUp טרם מחובר)','send'); }

function filesTabHTML(){
  // STUB — TODO(backend): attach_file / list_attachments per meeting (not persisted)
  return `<div class="card" style="margin:0">
    <h2>${svg('paperclip',14)} קבצים מצורפים <span class="badge pending" style="margin-inline-start:6px">דמו</span></h2>
    <div class="sub" style="margin-bottom:14px">צרף מצגות (PDF/PPT) הקשורות לפגישה. <b style="color:var(--warn)">טרם נשמר בצד השרת</b> — דורש backend.</div>
    <div id="filesList"></div>
    <label class="add-btn" style="display:inline-flex;align-items:center;gap:7px">
      ${svg('upload',14)} בחר קובץ לצירוף
      <input type="file" multiple style="display:none" onchange="attachFilesStub(this.files)">
    </label>
  </div>`;
}
function attachFilesStub(files){
  const box=$('#filesList'); if(!box) return;
  [...files].forEach(f=>{
    const row=document.createElement('div'); row.className='rec-row';
    row.style.cssText='display:flex;align-items:center;gap:12px;border:1px solid var(--line);padding:11px 14px;margin-bottom:8px';
    row.innerHTML=`<span class="ic">${svg('file')}</span><div style="flex:1"><div style="font-size:13px;font-weight:600">${esc(f.name)}</div>
      <div class="mono" style="font-size:11px;color:var(--muted)">${(f.size/1024).toFixed(0)} KB · מקומי בלבד</div></div>
      <button class="icon-btn danger" onclick="this.parentNode.remove()">${svg('x',14)}</button>`;
    box.appendChild(row);
  });
  toast('צורף מקומית (דמו)','paperclip');
}

/* ---------- output actions ---------- */
async function copyMeeting(){
  const m=window._detailMeeting; if(!m) return;
  const s=m.summary||m;
  const html=$('#summaryContent') ? $('#summaryContent').innerHTML : summaryReadHTML(s);
  const plain=`${nd(s.title||'')}\n\n${nd(s.summary||'')}`;
  try{
    if(navigator.clipboard && window.ClipboardItem){
      await navigator.clipboard.write([new ClipboardItem({
        'text/html': new Blob([`<h2>${esc(nd(s.title||''))}</h2>`+html],{type:'text/html'}),
        'text/plain': new Blob([plain],{type:'text/plain'}) })]);
    } else await navigator.clipboard.writeText(plain);
    toast('הסיכום הועתק (טקסט עשיר)','copy');
  }catch(e){ try{ await navigator.clipboard.writeText(plain); toast('הועתק','copy'); }
    catch(_){ toast('ההעתקה נכשלה','alert'); } }
}
// STUB — TODO(backend): SMTP send to calendar invitees
function emailMeeting(){
  const m=window._detailMeeting||{}; const s=m.summary||m;
  $('#emailRecips').value=''; $('#emailModal').style.display='flex';
}
function sendEmailStub(){ $('#emailModal').style.display='none'; toast('נשלח (דמו — חיבור SMTP טרם פעיל)','mail'); }

async function exportWord(id){
  toast('מכין Word…','download');
  const r=await window.pywebview.api.export_word(id);
  if(r.ok) toast('נשמר ונפתח ב-Word','check'); else if(!r.canceled) toast(r.error||'ייצוא נכשל','alert');
}

/* ---------- manual summary edit + chat ---------- */
function chatBlockHTML(){
  return `<div class="block" style="margin-top:26px"><h3>צ׳אט על הפגישה</h3>
    <div class="chat" id="chatBox">
      <div class="chat-msgs" id="chatMsgs"><div class="chat-hint">שאל שאלה, חפש משהו בתמלול, או בקש לשנות/להוסיף/לתקן בסיכום — השינוי יוטמע בסיכום המקורי.</div></div>
      <div class="chat-input">
        <textarea id="chatInput" rows="1" placeholder="כתוב הודעה… (Enter לשליחה, Shift+Enter לשורה חדשה)" onkeydown="chatKey(event)"></textarea>
        <button class="btn btn-primary" onclick="sendChat()">${svg('send',14)}</button>
      </div>
    </div></div>`;
}
function reRenderSummaryParts(){
  const s=(window._detailMeeting||{}).summary||{};
  const sc=$('#summaryContent'); if(sc) sc.innerHTML=summaryReadHTML(s);
  const tc=$('#tasksContent'); if(tc) tc.innerHTML=tasksHTML(s);
  const cnt=document.querySelector('.view.active .tab .cnt'); if(cnt) cnt.textContent=(s.action_items||[]).length;
  const eb=$('#editSumBtn'); if(eb) eb.style.display='';
}
function enterSummaryEdit(){
  const s=(window._detailMeeting||{}).summary||{};
  const sc=$('#summaryContent'); if(!sc) return;
  const topicsText=(s.topics||[]).map(t=>`### ${t.title}\n`+(t.points||[]).join('\n')).join('\n\n');
  const tasksText=(s.action_items||[]).map(a=>[a.task,a.owner,a.due].filter(Boolean).join(' | ')).join('\n');
  sc.innerHTML = `
    <div class="fld"><label>תקציר</label><textarea id="edSummary" style="min-height:120px">${esc(s.summary||'')}</textarea></div>
    <div class="fld"><label>נושאים — שורה שמתחילה ב-### היא כותרת, וכל שורה אחריה נקודה</label><textarea id="edTopics" style="min-height:170px">${esc(topicsText)}</textarea></div>
    <div class="fld"><label>החלטות — שורה לכל החלטה</label><textarea id="edDecisions" style="min-height:80px">${esc((s.decisions||[]).join('\n'))}</textarea></div>
    <div class="fld"><label>נקודות מפתח — שורה לכל נקודה</label><textarea id="edKeyPoints" style="min-height:80px">${esc((s.key_points||[]).join('\n'))}</textarea></div>
    <div class="fld"><label>משימות — שורה לכל משימה (אפשר: משימה | אחראי | מועד)</label><textarea id="edTasks" style="min-height:100px">${esc(tasksText)}</textarea></div>
    <div style="display:flex;gap:8px">
      <button class="btn btn-primary" onclick="saveSummaryEdit()">שמור סיכום</button>
      <button class="btn btn-ghost" onclick="reRenderSummaryParts()">ביטול</button></div>`;
  const eb=$('#editSumBtn'); if(eb) eb.style.display='none';
}
async function saveSummaryEdit(){
  const m=window._detailMeeting; if(!m||!m.id) return;
  const lines = el => ($(el).value||'').split('\n').map(x=>x.trim()).filter(Boolean);
  const topics=[]; let cur=null;
  ($('#edTopics').value||'').split('\n').forEach(line=>{ const t=line.trim();
    if(t.startsWith('###')){ cur={title:t.replace(/^#+/,'').trim(),points:[]}; topics.push(cur); }
    else if(t){ if(!cur){ cur={title:'',points:[]}; topics.push(cur);} cur.points.push(t); } });
  const s = Object.assign({}, m.summary||{}, {
    summary:$('#edSummary').value.trim(), topics:topics,
    decisions:lines('#edDecisions'), key_points:lines('#edKeyPoints'),
    action_items: lines('#edTasks').map(line=>{ const p=line.split('|').map(y=>y.trim()); return {task:p[0]||'',owner:p[1]||'',due:p[2]||''}; }),
  });
  const r=await window.pywebview.api.update_summary(m.id, m.project, s);
  if(r.ok){ m.summary=r.summary||s; reRenderSummaryParts(); toast('הסיכום נשמר','check'); }
  else toast(r.error||'נכשל','alert');
}
function chatKey(e){ if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); sendChat(); } }
let _chatHistory=[];
function appendChat(role, text){
  const box=$('#chatMsgs'); if(!box) return;
  const hint=box.querySelector('.chat-hint'); if(hint) hint.remove();
  const d=document.createElement('div'); d.className='cmsg '+(role==='user'?'user':'bot');
  d.textContent=text; box.appendChild(d); box.scrollTop=box.scrollHeight; return d;
}
async function sendChat(){
  const inp=$('#chatInput'); if(!inp) return;
  const msg=inp.value.trim(); if(!msg) return;
  const m=window._detailMeeting; if(!m||!m.id){ toast('צ׳אט זמין לפגישות שמורות','alert'); return; }
  inp.value=''; appendChat('user', msg); _chatHistory.push({role:'user', text:msg});
  const typing=appendChat('bot','כותב…'); typing.classList.add('typing');
  let r;
  try{ r=await window.pywebview.api.meeting_chat(m.id, m.project, msg, _chatHistory.slice(0,-1)); }
  catch(e){ r={ok:false, error:String(e)}; }
  typing.remove();
  if(r&&r.ok){ appendChat('bot', r.reply); _chatHistory.push({role:'assistant', text:r.reply});
    if(r.edited && r.summary){ window._detailMeeting.summary=r.summary; reRenderSummaryParts(); toast('הסיכום עודכן לפי בקשתך','check'); } }
  else appendChat('bot', '⚠ '+((r&&r.error)||'שגיאה'));
}

/* ---------- move / rename ---------- */
function moveSelect(currentProject, onchangeCall){
  const opts = _projects.filter(p=>p!==currentProject).map(p=>`<option value="${esc(p)}">${esc(p)}</option>`).join('');
  if(!opts) return '';
  return `<select class="move-select" onchange="${onchangeCall}"><option value="">↦ העבר…</option>${opts}</select>`;
}
async function moveMeeting(id, from, sel){
  const to=sel.value; if(!to) return;
  const r=await window.pywebview.api.move_meeting(id, from, to);
  if(r.ok){ toast('הועבר ל'+to,'folder'); nav('summaries'); } else toast(r.error||'נכשל','alert');
}
async function moveRecording(path, sel){
  const to=sel.value; if(!to) return;
  const r=await window.pywebview.api.move_recording(path, to);
  if(r.ok){ toast('ההקלטה הועברה ל'+to,'folder'); loadRecordings(); } else toast(r.error||'נכשל','alert');
}
async function renameMeeting(id, project){
  const v=await askPrompt('שם הפגישה', (window._detailMeeting&&(window._detailMeeting.summary||{}).title)||'');
  if(v==null||!v) return;
  const r=await window.pywebview.api.rename_meeting(id, project, v);
  if(r.ok){ toast('השם עודכן','check'); openMeeting(id, project); } else toast(r.error||'נכשל','alert');
}
async function renameRecording(path, curName){
  const v=await askPrompt('שם ההקלטה', curName||''); if(v==null||!v) return;
  const r=await window.pywebview.api.rename_recording(path, v);
  if(r.ok){ toast('שם ההקלטה עודכן','check'); loadRecordings(); } else toast(r.error||'נכשל','alert');
}

/* ============================================================
   TRASH (STUB — session-local; TODO(backend): storage soft-delete)
   ============================================================ */
function loadTrash(){
  const el=$('#trash-list');
  if(!_trash.length){ el.innerHTML=`<div class="empty">${ic('trash',28)}<br>סל המיחזור ריק.</div>`; return; }
  el.innerHTML = `<div class="card" style="background:var(--warn-soft);border-color:var(--warn)">
      <div class="sub" style="color:var(--warn)">${svg('alert',14)} שחזור מלא דורש backend (soft-delete) — כרגע הרשימה מציגה מחיקות מהפעלה זו בלבד.</div></div>` +
    _trash.map((t,i)=>`<div class="cal-row">
      <div class="c-when mono" style="width:120px">${esc(t.date||'')}</div>
      <div class="cal-main"><div class="ttl">${et(t.title)}</div><div class="c-sub">${esc(t.project)} · נמחק זה עתה</div></div>
      <button class="btn btn-ghost btn-sm" onclick="restoreTrash(${i})">${svg('restore',13)} שחזר</button>
      <button class="btn btn-danger btn-sm" onclick="purgeTrash(${i})">${svg('x',13)} מחק לצמיתות</button>
    </div>`).join('');
}
function restoreTrash(i){ _trash.splice(i,1); toast('שחזור דורש backend (דמו) — הוסר מהרשימה','restore'); loadTrash(); }
function purgeTrash(i){ _trash.splice(i,1); toast('נמחק לצמיתות','x'); loadTrash(); }

/* ============================================================
   CALENDAR
   ============================================================ */
async function loadCalendar(){
  const el=$('#calendar-list'), sub=$('#calSub');
  el.innerHTML=`<div class="empty"><span class="spinner"></span> טוען מהיומן…</div>`;
  let r; try{ r=await window.pywebview.api.calendar_upcoming(); }catch(e){ r={ok:false,error:String(e)}; }
  if(!r||!r.ok){ el.innerHTML=`<div class="empty">${ic('calendar',28)}<br>`+esc((r&&r.error)||'טעינה נכשלה')+'<br>הגדר אימייל וסיסמה בלשונית "הגדרות".</div>'; return; }
  if(sub) sub.textContent=(r.enabled?'הצטרפות אוטומטית פעילה':'הצטרפות אוטומטית כבויה (ראה הגדרות)')+' · בוט: '+(r.bot_name||'');
  const evs=r.events||[];
  if(!evs.length){ el.innerHTML=`<div class="empty">${ic('calendar',28)}<br>אין פגישות קרובות ביומן.<br>הזמן את כתובת המייל של המערכת לפגישה.</div>`; return; }
  const groups={};
  evs.forEach(e=>{ const k=new Date(e.start).toLocaleDateString('he-IL',{weekday:'long',day:'2-digit',month:'2-digit'});
                   (groups[k]=groups[k]||[]).push(e); });
  const tt=p=>p.toLocaleTimeString('he-IL',{hour:'2-digit',minute:'2-digit'});
  el.innerHTML=Object.entries(groups).map(([day,list])=>
    `<div class="cal-day-h">${esc(day)}</div>`+ list.map(e=>{
      const s=new Date(e.start), en=new Date(e.end), plat=e.platform;
      const lbl=plat==='meet'?'Meet':plat==='zoom'?'Zoom':plat==='teams'?'Teams':'';
      const join=(r.use_docker_bot!==false && plat)?'<span class="badge accent">הבוט יצטרף</span>'
               :(e.url?'<span class="tag">קישור</span>':'<span class="badge pending">ללא קישור</span>');
      return `<div class="cal-row">
        <div class="cal-time"><b>${tt(s)}</b><span>${tt(en)}</span></div>
        <div class="cal-main"><div class="ttl">${et(e.title)}</div>
          ${lbl?`<span class="plat plat-${plat}">${lbl}</span>`:'<span class="c-sub">פגישה ללא קישור מזוהה</span>'}</div>
        ${join}
      </div>`;
    }).join('')
  ).join('');
}

/* ============================================================
   MY TASKS
   ============================================================ */
function myTaskRowHTML(t){
  const meta=[]; if(t.owner)meta.push(`<span class="ml">אחראי</span> ${esc(t.owner)}`); if(t.due)meta.push(`<span class="ml">מועד</span> ${esc(t.due)}`);
  if(t.source_title)meta.push(`מתוך: ${esc(t.source_title)}`);
  return `<div class="task ${t.done?'done':''}">
    <input type="checkbox" ${t.done?'checked':''} onchange="toggleMyTask('${t.id}')">
    <div style="flex:1"><div class="tk">${esc(t.task)}</div>${meta.length?`<div class="tmeta">${meta.join(' · ')}</div>`:''}</div>
    <button class="icon-btn danger" title="מחק" onclick="deleteMyTask('${t.id}')">${svg('trash',14)}</button>
  </div>`;
}
async function loadMyTasks(){
  const el=$('#mytasks-list'); el.innerHTML='<div class="empty">טוען…</div>';
  const tasks=await window.pywebview.api.list_my_tasks();
  if(!_projects||!_projects.length){ try{ _projects=await window.pywebview.api.list_projects(); }catch(e){} }
  const open=tasks.filter(t=>!t.done).length;
  $('#navCountTasks').textContent = open || '';
  if(!tasks.length){ el.innerHTML=`<div class="empty">${ic('check',28)}<br>אין עדיין משימות.<br>פתח פגישה, עבור ללשונית "משימות" ולחץ "+ למשימות שלי".</div>`; return; }
  const groups={}; tasks.forEach(t=>{ const k=t.project||'כללי'; (groups[k]=groups[k]||[]).push(t); });
  const order=[...new Set([...(_projects||[]), ...Object.keys(groups)])].filter(p=>groups[p]&&groups[p].length);
  el.innerHTML = `<div class="sub" style="margin-bottom:14px">${open} פתוחות · ${tasks.length} סה"כ · ${order.length} פרויקטים</div>` +
    order.map(proj=>{ const list=groups[proj]; const openN=list.filter(t=>!t.done).length;
      return `<div class="arch-wrap"><div class="arch-head" style="border-top:none;margin-top:0" onclick="this.parentNode.classList.toggle('collapsed')">
          <span class="ic caret">${svg('chevron',14)}</span> ${svg('folder',14)} ${esc(proj)} <span class="badge accent" style="margin-inline-start:auto">${openN}/${list.length}</span></div>
        <div class="arch-body">${list.map(myTaskRowHTML).join('')}</div></div>`;
    }).join('');
}
async function toggleMyTask(id){ await window.pywebview.api.toggle_my_task(id); loadMyTasks(); }
async function deleteMyTask(id){ await window.pywebview.api.delete_my_task(id); toast('המשימה הוסרה','x'); loadMyTasks(); }
async function addTaskToMine(idx){
  const m=window._detailMeeting; if(!m) return;
  const s=m.summary||m; const a=(s.action_items||[])[idx]; if(!a) return;
  const r=await window.pywebview.api.add_my_task({ task:a.task, owner:a.owner||'', due:a.due||'',
    source_id:m.id||'', source_title:(s.title||''), project:m.project||'' });
  if(r.ok) toast(r.duplicate ? 'כבר ב"המשימות שלי"' : 'נוסף ל"המשימות שלי"','check');
  else toast(r.error||'נכשל','alert');
}

/* ============================================================
   RECORDINGS (raw media)
   ============================================================ */
let _recSel = new Map();   // path -> {path,project} for recordings bulk actions
async function loadRecordings(){
  $('#recordings-list').innerHTML='<div class="empty">טוען…</div>';
  _recordings=await window.pywebview.api.list_recordings();
  _recSel.clear(); const sa=$('#recSelectAll'); if(sa) sa.checked=false;
  fillFilter($('#recFilter'), _recordings); renderRecordings();
}
function recVisible(){ const f=$('#recFilter').value;
  return (f && f!=='__all__') ? _recordings.filter(r=>r.project===f) : _recordings; }
function renderRecordings(){
  const list=$('#recordings-list');
  const items=recVisible();
  if(!items.length){ list.innerHTML=`<div class="empty">${ic('audio',28)}<br>אין הקלטות להצגה.</div>`; recSyncBulk(); return; }
  list.innerHTML = items.map(r=>{
    const p=esc(r.path).replace(/\\/g,'\\\\');
    return `<div class="cal-row">
      <input type="checkbox" ${_recSel.has(r.path)?'checked':''} onclick="event.stopPropagation()" onchange="recToggleSel('${p}','${esc(r.project)}',this)">
      <span class="ic">${svg(r.is_video?'monitor':'audio')}</span>
      <div class="cal-main"><div class="ttl">${esc(r.name)}</div>
        <div class="c-sub mono"><span class="tag proj">${esc(r.project)}</span>${r.is_video?' <span class="tag">וידאו</span>':''} ${esc(r.date)} · ${r.duration} · ${r.size_mb}MB</div></div>
      <button class="btn btn-ghost btn-sm" onclick="playRec('${p}')">${svg('play',13)} נגן</button>
      <button class="btn btn-accent btn-sm" onclick="processRec('${p}','${esc(r.project)}')">${svg('activity',13)} תמלל וסכם</button>
      ${moveSelect(r.project, `moveRecording('${p}',this)`)}
      <button class="icon-btn" title="שנה שם" onclick="renameRecording('${p}','${esc(r.name)}')">${svg('edit',14)}</button>
      <button class="icon-btn danger" title="מחק" onclick="deleteRecording('${p}')">${svg('trash',14)}</button>
    </div>`;
  }).join('');
  recSyncBulk();
}
function recToggleSel(path, project, cb){
  if(cb.checked) _recSel.set(path,{path,project}); else _recSel.delete(path);
  const sa=$('#recSelectAll'); if(sa){ const t=recVisible().length; sa.checked=t>0 && _recSel.size>=t; }
  recSyncBulk();
}
function recToggleAll(cb){
  if(cb.checked) recVisible().forEach(r=>_recSel.set(r.path,{path:r.path,project:r.project}));
  else _recSel.clear();
  renderRecordings();
}
function recClearSel(){ _recSel.clear(); const sa=$('#recSelectAll'); if(sa) sa.checked=false; renderRecordings(); }
function recSyncBulk(){
  const bb=$('#recBulkbar'); if(!bb) return;
  bb.classList.toggle('show', _recSel.size>0);
  $('#recBulkCnt').textContent=_recSel.size+' נבחרו';
  $('#recBulkMove').innerHTML=`<option value="">↦ העבר לפרויקט…</option>`+_projects.map(p=>`<option value="${esc(p)}">${esc(p)}</option>`).join('');
}
async function recBulkMoveGo(){
  const to=$('#recBulkMove').value; if(!to){ toast('בחר פרויקט יעד','alert'); return; }
  let n=0; for(const [path,it] of _recSel){ if(it.project===to) continue;
    const r=await window.pywebview.api.move_recording(path, to); if(r&&r.ok) n++; }
  toast(n+' הקלטות הועברו ל'+to,'folder'); _recSel.clear(); loadRecordings();
}
async function recBulkDelete(){
  const n=_recSel.size; if(!n) return;
  if(!await askConfirm('למחוק '+n+' הקלטות נבחרות לצמיתות?')) return;
  for(const [path] of _recSel){ await window.pywebview.api.delete_recording(path); }
  toast(n+' הקלטות נמחקו','trash'); _recSel.clear(); loadRecordings();
}
async function playRec(path){ await window.pywebview.api.play_recording(path); }
async function processRec(path, project){
  nav('processing'); setProcessing(true); startProcTimer();
  $('#proc-status').innerHTML=`<span class="spinner"></span> מעבד…`;
  $('#processing-output').innerHTML=procCardHTML();
  const res=await window.pywebview.api.process_recording(path, project);
  showProcessed(res); afterProcessing(res);
}
async function deleteRecording(path){
  if(!await askConfirm('למחוק את ההקלטה לצמיתות?')) return;
  await window.pywebview.api.delete_recording(path); toast('ההקלטה נמחקה','x'); loadRecordings();
}
async function deleteMeeting(id, project){
  if(!await askConfirm('להעביר את הסיכום לסל המיחזור?')) return;
  const m=_meetings.find(x=>x.id===id); if(m) _trash.push({id, project, title:m.title, date:m.date, ts:Date.now()});
  await window.pywebview.api.delete_meeting(id, project); // TODO(backend): soft-delete
  toast('הועבר לסל המיחזור','trash'); nav('summaries');
}

/* ---------- upload media ---------- */
// Web mode: browser file picker + multipart POST (desktop uses native dialog API).
// window.IS_WEB is set by the web shim (build_web.py); undefined on desktop.
function webUpload(endpoint, project, accept){
  return new Promise(resolve=>{
    const inp=document.createElement('input'); inp.type='file'; if(accept) inp.accept=accept;
    inp.style.display='none'; document.body.appendChild(inp);
    inp.onchange=async()=>{
      const f=inp.files&&inp.files[0]; inp.remove();
      if(!f){ resolve({ok:false,canceled:true}); return; }
      toast('מעלה קובץ…','upload');
      const fd=new FormData(); fd.append('file', f, f.name); fd.append('project', project||'כללי');
      try{ const resp=await fetch(endpoint,{method:'POST',body:fd}); resolve(await resp.json()); }
      catch(e){ resolve({ok:false,error:String(e)}); }
    };
    inp.click();
  });
}
async function uploadMedia(){
  const proj=($('#projSelect') && $('#projSelect').value) || 'כללי';
  if(window.IS_WEB){
    const r=await webUpload('/api/upload_file', proj, 'audio/*,video/*,.webm,.m4a,.mp4,.wav,.mp3');
    if(!r.ok){ if(!r.canceled) toast(r.error||'ההעלאה נכשלה','alert'); return; }
    showNameModal({path:r.path, project:r.project}); return;
  }
  toast('בחר קובץ להעלאה…','upload');
  const r=await window.pywebview.api.upload_media(proj);
  if(!r.ok){ if(!r.canceled) toast(r.error||'ההעלאה נכשלה','alert'); return; }
  showNameModal({path:r.path, project:r.project});
}

/* ============================================================
   PROCESSING (live transcript + progress) — push hooks preserved
   ============================================================ */
function procCardHTML(summaryOnly){
  return `<div class="card">
    <div class="progress-wrap">
      <div class="progress-label" id="progLabel">${summaryOnly?'מסכם עם Gemini…':'מתמללים את הפגישה…'}</div>
      <div class="progress-bar"><div class="progress-fill${summaryOnly?' indeterminate':''}" id="progFill"></div></div>
    </div>
    ${summaryOnly?'':'<h2>'+svg('list',13)+' תמלול חי</h2><div class="transcript live" id="transcript"></div>'}</div>`;
}
// ----- live-push hooks (called from Python via evaluate_js) -----
window.addSegment = function(seg){
  markProgress();
  const tEl=$('#transcript'); if(!tEl) return;
  const d=document.createElement('div'); d.className='seg';
  d.innerHTML=`<span class="t">${seg.start.toFixed(0)}s</span>${esc(seg.text)}`;
  tEl.appendChild(d); tEl.scrollTop=tEl.scrollHeight;
};
window.transcribeProgress = function(p){
  markProgress();
  const f=$('#progFill'), l=$('#progLabel');
  if(f){ f.classList.remove('indeterminate'); f.style.width=p.percent+'%'; }
  if(l) l.textContent='מתמללים ומזהים דוברים… '+p.percent+'%';
};
window.setProcessingStatus = function(txt){
  markProgress();
  const l=$('#progLabel'); if(l) l.textContent=txt;
  const f=$('#progFill'); if(f){ f.classList.remove('indeterminate'); f.style.width='0%'; }
  const ps=$('#proc-status'); if(ps) ps.innerHTML='<span class="spinner"></span> '+txt;
  const rs=$('#recStatus'); if(rs) rs.innerHTML=txt;
};
window.summarizeProgress = function(p){
  markProgress();
  const f=$('#progFill'), l=$('#progLabel');
  if(f){ f.classList.remove('indeterminate'); f.style.width=p.percent+'%'; }
  if(l) l.textContent='מזקקים תובנות… '+p.percent+'%';
};
function showProcessed(res){
  const failedSummary=!!(res.summary_error || res.status==='failed');
  if(!res.ok){
    $('#proc-status').innerHTML = res.canceled ? ic('x')+' העיבוד בוטל' : ic('alert')+' '+esc(res.error||'שגיאה');
    let html=res.id ? retryBar(res) : '';
    if(res.transcript) html+=`<div class="card"><h2>${svg('list',13)} תמלול (נשמר)</h2><div class="transcript">${esc(res.transcript)}</div></div>`;
    if(html) $('#processing-output').innerHTML=html;
    return;
  }
  window._detailMeeting=res; _chatHistory=[];
  $('#proc-status').innerHTML = failedSummary ? ic('alert')+' הסיכום נכשל' : ic('check')+' מוכן';
  $('#processing-output').innerHTML=(failedSummary && res.id ? retryBar(res):'') + renderSummary(res, {withExport:true});
}
function retryBar(res){
  return `<div class="card"><div class="tldr">${res.canceled?'הסיכום בוטל — ':'הסיכום לא הושלם — '}התמלול נשמר. אפשר להפיק את הסיכום שוב.</div>
    <div style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn btn-primary" onclick="summarizeMeeting('${res.id}','${esc(res.project||'')}')">${svg('refresh',14)} נסה לסכם שוב</button>
      <button class="btn btn-ghost" onclick="nav('summaries')">למאגר הפגישות</button></div></div>`;
}
async function summarizeMeeting(id, project){
  nav('processing'); setProcessing(true); startProcTimer();
  $('#proc-status').innerHTML=`<span class="spinner"></span> מסכם…`;
  $('#processing-output').innerHTML=procCardHTML(true);
  const res=await window.pywebview.api.summarize_meeting(id, project);
  showProcessed(res); afterProcessing(res);
}

/* ============================================================
   LIVE RECORDING + VU meter + screen preview + floating widget
   ============================================================ */
let recState='idle', recStart=0, recTimer=null, paused=false, pausedAt=0;
function fmt(s){ const m=Math.floor(s/60),x=Math.floor(s%60); return String(m).padStart(2,'0')+':'+String(x).padStart(2,'0'); }
function setRecBtnLabel(txt, icon){ const b=$('#recordBtn'); if(b) b.innerHTML=`<span class="ic">${svg(icon||'mic',22)}</span><span>${txt}</span>`; }
function showFloatRec(on){ const f=$('#floatRec'); if(f) f.classList.toggle('show', on); }

async function togglePause(){
  if(recState!=='recording') return;
  const pb=$('#pauseBtn'), fp=$('#frPause');
  if(!paused){
    await window.pywebview.api.pause_recording();
    paused=true; pausedAt=Date.now(); clearInterval(recTimer);
    if(pb) pb.innerHTML=`${svg('play',16)} המשך`; if(fp) fp.innerHTML=svg('play',14);
    $('#recStatus').textContent='מושהה'; $('#recordBtn').classList.remove('recording');
  } else {
    await window.pywebview.api.resume_recording();
    paused=false; recStart += (Date.now()-pausedAt);
    recTimer=setInterval(tickRec,200);
    if(pb) pb.innerHTML=`${svg('pause',16)} השהה`; if(fp) fp.innerHTML=svg('pause',14);
    $('#recStatus').textContent='מקליט…'; $('#recordBtn').classList.add('recording');
  }
}
function tickRec(){ const t=fmt((Date.now()-recStart)/1000); $('#timer').textContent=t; const fr=$('#frTime'); if(fr) fr.textContent=t; }
async function toggleRecord(){
  const btn=$('#recordBtn');
  if(recState==='idle'){
    if(!window.pywebview||!window.pywebview.api){ $('#recStatus').textContent='הממשק נטען, נסה שוב'; return; }
    const proj=$('#projSelect').value||'כללי', mic=$('#micSelect').value||null,
          mute=$('#muteMic').checked, video=$('#capVideo').checked;
    btn.classList.add('recording'); $('#recStatus').textContent='מתחיל…';
    try{ const r=await window.pywebview.api.start_recording(proj, mic, mute, video); if(!r||!r.ok) throw new Error((r&&r.error)||'נכשל'); }
    catch(e){ btn.classList.remove('recording'); $('#recStatus').textContent=(e.message||e); return; }
    recState='recording'; setRecBtnLabel('עצור','stop');
    $('#recStatus').textContent = video ? 'מקליט מסך + קול…' : 'מקליט…';
    paused=false; $('#pauseBtn').style.display = video ? 'none' : 'inline-flex';
    if($('#pauseBtn')) $('#pauseBtn').innerHTML=`${svg('pause',16)} השהה`;
    recStart=Date.now(); recTimer=setInterval(tickRec,200);
    showFloatRec(true); setLiveStatus('rec'); startVU();
  } else if(recState==='recording'){
    clearInterval(recTimer); recState='idle';
    btn.classList.remove('recording'); btn.disabled=false; setRecBtnLabel('הקלט','mic');
    $('#pauseBtn').style.display='none'; paused=false;
    $('#timer').textContent='00:00'; $('#recStatus').textContent='מוכן להקלטה';
    showFloatRec(false); setLiveStatus('idle'); stopVU();
    const st=await window.pywebview.api.stop_recording();
    if(!st.ok){ toast(st.error||'שגיאת הקלטה','alert'); return; }
    showNameModal({path:st.path, project:st.project});
  }
}

/* ---------- VU meter (frontend-only viz stream) ---------- */
let _vuCtx=null, _vuStream=null, _vuRAF=null, _vuTesting=false;
function buildVuBars(){ const vu=$('#vuMeter'); if(vu && !vu.children.length){ for(let i=0;i<24;i++){ const b=document.createElement('div'); b.className='bar'; vu.appendChild(b);} } }
async function startVU(){
  buildVuBars(); const vu=$('#vuMeter'); if(!vu) return;
  try{
    _vuStream=await navigator.mediaDevices.getUserMedia({audio:true});
    _vuCtx=new (window.AudioContext||window.webkitAudioContext)();
    const src=_vuCtx.createMediaStreamSource(_vuStream);
    const an=_vuCtx.createAnalyser(); an.fftSize=64; src.connect(an);
    const data=new Uint8Array(an.frequencyBinCount); const bars=[...vu.children];
    vu.classList.add('on');
    const loop=()=>{ an.getByteFrequencyData(data);
      bars.forEach((b,i)=>{ const v=data[i*2]||0; b.style.height=Math.max(8,(v/255)*100)+'%'; });
      _vuRAF=requestAnimationFrame(loop); };
    loop();
  }catch(e){ vu.classList.remove('on'); /* permission/no-mic: caveat under WebView2 */ }
}
function stopVU(){
  if(_vuTesting) return; // test-audio keeps it alive
  cancelAnimationFrame(_vuRAF); _vuRAF=null;
  if(_vuStream){ _vuStream.getTracks().forEach(t=>t.stop()); _vuStream=null; }
  if(_vuCtx){ _vuCtx.close().catch(()=>{}); _vuCtx=null; }
  const vu=$('#vuMeter'); if(vu){ vu.classList.remove('on'); [...vu.children].forEach(b=>b.style.height='8%'); }
}
async function toggleTestAudio(){
  const btn=$('#testAudioBtn');
  if(!_vuTesting){ _vuTesting=true; await startVU(); btn.innerHTML=`${svg('stop',13)} עצור בדיקה`;
    if(!_vuStream){ _vuTesting=false; btn.innerHTML=`${svg('activity',13)} בדוק אודיו`; toast('אין גישה למיקרופון','alert'); } }
  else { _vuTesting=false; btn.innerHTML=`${svg('activity',13)} בדוק אודיו`; if(recState!=='recording') stopVU(); }
}
/* ---------- screen preview ---------- */
let _scrStream=null;
async function onCapVideoToggle(){
  const on=$('#capVideo').checked, wrap=$('#prevWrap'), vid=$('#screenPrev');
  if(on){
    try{ _scrStream=await navigator.mediaDevices.getDisplayMedia({video:true});
      vid.srcObject=_scrStream; vid.style.display='block'; wrap.classList.add('show');
      _scrStream.getVideoTracks()[0].addEventListener('ended',()=>{ wrap.classList.remove('show'); $('#capVideo').checked=false; });
    }catch(e){ /* user cancelled picker — keep checkbox, Python still captures */ wrap.classList.add('show');
      vid.style.display='none'; }
  } else { if(_scrStream){ _scrStream.getTracks().forEach(t=>t.stop()); _scrStream=null; } wrap.classList.remove('show'); }
}

/* ---------- microphones ---------- */
async function loadMics(){
  const sel=$('#micSelect'); let mics=[];
  try{ mics=await window.pywebview.api.list_microphones(); }catch(e){}
  sel.innerHTML=`<option value="">ברירת מחדל של המערכת</option>`+mics.map(m=>`<option value="${esc(m)}">${esc(m)}</option>`).join('');
}

/* ============================================================
   MODALS (confirm / prompt / name)
   ============================================================ */
let _confirmResolve=null;
function askConfirm(text){ $('#confirmText').textContent=text; $('#confirmModal').style.display='flex';
  return new Promise(r=>_confirmResolve=r); }
function closeConfirm(v){ $('#confirmModal').style.display='none'; if(_confirmResolve){_confirmResolve(v);_confirmResolve=null;} }
let _promptResolve=null;
function askPrompt(title, value){ $('#promptTitle').textContent=title; $('#promptInput').value=value||'';
  $('#promptModal').style.display='flex'; setTimeout(()=>$('#promptInput').focus(),50);
  return new Promise(r=>_promptResolve=r); }
function closePrompt(ok){ const v=$('#promptInput').value.trim(); $('#promptModal').style.display='none';
  if(_promptResolve){_promptResolve(ok?v:null);_promptResolve=null;} }
let _pendingRec=null;
function showNameModal(rec){ _pendingRec=rec; $('#meetingNameInput').value=''; $('#nameModal').style.display='flex';
  setTimeout(()=>$('#meetingNameInput').focus(),50); }
async function confirmName(skip){
  const title=skip?'':$('#meetingNameInput').value.trim();
  $('#nameModal').style.display='none';
  const rec=_pendingRec; _pendingRec=null; if(!rec) return;
  nav('processing'); setProcessing(true); startProcTimer();
  $('#proc-status').innerHTML=`<span class="spinner"></span> מעבד…`;
  $('#processing-output').innerHTML=procCardHTML();
  const res=await window.pywebview.api.process_recording(rec.path, rec.project, title);
  showProcessed(res); afterProcessing(res);
}

/* ============================================================
   FORMATTED SUMMARY (DIT)
   ============================================================ */
let formState={project_name:'',topic:'',date:'',location:'',participants:[],findings:[],project:''};
function renderFormatForm(){
  $('#fProject').value=formState.project_name||''; $('#fTopic').value=formState.topic||'';
  $('#fDate').value=formState.date||''; $('#fLocation').value=formState.location||'';
  const sel=$('#fLogoProject'); const projs=(_projects&&_projects.length)?_projects:['כללי'];
  sel.innerHTML=projs.map(p=>`<option value="${esc(p)}">${esc(p)}</option>`).join('');
  sel.value=(formState.project && projs.includes(formState.project))?formState.project:projs[0];
  refreshLogoStatus(); renderParticipants(); renderFindings();
}
async function refreshLogoStatus(){
  const proj=$('#fLogoProject').value||'כללי'; let s={exists:false};
  try{ s=await window.pywebview.api.client_logo_status(proj); }catch(e){}
  $('#logoStatus').textContent=s.exists ? 'לוגו קיים' : 'אין לוגו';
}
async function uploadClientLogo(){
  const proj=$('#fLogoProject').value||'כללי';
  const r = window.IS_WEB ? await webUpload('/api/upload_logo', proj, 'image/*')
                          : await window.pywebview.api.upload_client_logo(proj);
  if(r.ok){ toast('הלוגו הועלה','check'); refreshLogoStatus(); } else if(!r.canceled) toast(r.error||'נכשל','alert');
}
async function removeClientLogo(){
  const proj=$('#fLogoProject').value||'כללי';
  const r=await window.pywebview.api.remove_client_logo(proj);
  if(r.removed) toast('הלוגו הוסר','x'); else toast('אין לוגו להסרה','alert'); refreshLogoStatus();
}
function renderParticipants(){
  $('#participantsList').innerHTML=(formState.participants||[]).map((p,i)=>`
    <div class="fld-row" style="grid-template-columns:1fr 1fr 1fr auto">
      <input placeholder="שם" value="${esc(p.name||'')}" oninput="updP(${i},'name',this.value)">
      <input placeholder="תפקיד" value="${esc(p.role||'')}" oninput="updP(${i},'role',this.value)">
      <input placeholder="חברה" value="${esc(p.company||'')}" oninput="updP(${i},'company',this.value)">
      <button class="row-del" onclick="delP(${i})">${svg('x',14)}</button>
    </div>`).join('');
}
function renderFindings(){
  $('#findingsList').innerHTML=(formState.findings||[]).map((f,i)=>`
    <div class="find-box">
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
        <b class="mono" style="color:var(--accent)">${String(i+1).padStart(2,'0')}</b>
        <button class="row-del" style="margin-inline-start:auto" onclick="delF(${i})">${svg('x',14)}</button>
      </div>
      <textarea placeholder="תיאור הממצא" oninput="updF(${i},'description',this.value)">${esc(f.description||'')}</textarea>
      <div class="sub2">
        <input placeholder="אחראי" value="${esc(f.responsible||'')}" oninput="updF(${i},'responsible',this.value)">
        <input placeholder="מועד" value="${esc(f.due||'')}" oninput="updF(${i},'due',this.value)">
      </div>
      <input style="margin-top:8px" placeholder="הערה (אופציונלי)" value="${esc(f.note||'')}" oninput="updF(${i},'note',this.value)">
    </div>`).join('');
}
function updP(i,k,v){ formState.participants[i][k]=v; }
function delP(i){ formState.participants.splice(i,1); renderParticipants(); }
function addParticipant(){ formState.participants.push({name:'',role:'',company:''}); renderParticipants(); }
function updF(i,k,v){ formState.findings[i][k]=v; }
function delF(i){ formState.findings.splice(i,1); renderFindings(); }
function addFinding(){ formState.findings.push({description:'',responsible:'',due:'',note:''}); renderFindings(); }
function collectFormat(){ return { project_name:$('#fProject').value, topic:$('#fTopic').value,
  date:$('#fDate').value, location:$('#fLocation').value, participants:formState.participants, findings:formState.findings }; }
async function exportFormatPdf(){
  const proj=($('#fLogoProject') && $('#fLogoProject').value)||''; window._previewProj=proj;
  toast('מכין תצוגה מקדימה…','file');
  const r=await window.pywebview.api.preview_format_html(collectFormat(), proj);
  if(!r.ok){ toast(r.error||'נכשל','alert'); return; }
  $('#previewFrame').srcdoc=r.html; $('#previewModal').style.display='flex';
}
async function confirmDownloadPdf(){
  $('#previewModal').style.display='none'; toast('מכין PDF…','download');
  const r=await window.pywebview.api.export_format_pdf(collectFormat(), window._previewProj||'');
  if(r.ok) toast('ה-PDF נשמר ונפתח','check'); else if(!r.canceled) toast(r.error||'נכשל','alert');
}
function showFormatProgress(pct){
  const wrap=$('#formatProgressWrap'); if(!wrap) return;
  wrap.innerHTML=`<div class="card"><div class="progress-label"><span class="spinner"></span> בונה את הפורמט מהסיכום… <b id="fmtPct">${pct||0}%</b></div>
    <div class="progress-bar" style="margin-top:8px"><div class="progress-fill" id="fmtFill" style="width:${pct||0}%"></div></div></div>`;
}
window.formatProgress = function(p){
  const fill=$('#fmtFill'), pc=$('#fmtPct');
  if(fill) fill.style.width=p.percent+'%'; if(pc) pc.textContent=p.percent+'%';
};
async function formatFromMeeting(id, project){
  formState.project=project||''; nav('format'); showFormatProgress(0);
  let r; try{ r=await window.pywebview.api.extract_format(id); }catch(e){ r={ok:false,error:String(e)}; }
  $('#formatProgressWrap').innerHTML='';
  if(!r||!r.ok){ toast((r&&r.error)||'מילוי הפורמט נכשל - אפשר למלא ידנית','alert'); return; }
  formState=Object.assign({project_name:'',topic:'',date:'',location:'',participants:[],findings:[],project:project||''}, r.fields);
  formState.project=project||''; renderFormatForm(); toast('הפורמט מולא מהסיכום','check');
}

/* ============================================================
   SETTINGS + WIZARD
   ============================================================ */
async function loadSettings(){
  if(!_projects||!_projects.length){ try{ _projects=await window.pywebview.api.list_projects(); }catch(e){} }
  $('#calProject').innerHTML=(_projects||['כללי']).map(p=>`<option value="${esc(p)}">${esc(p)}</option>`).join('');
  let c={}; try{ c=await window.pywebview.api.get_calendar_config(); }catch(e){}
  $('#calEnabled').checked=!!c.enabled; $('#calEmail').value=c.email||''; $('#calPass').value=c.app_password||'';
  $('#calImap').value=c.imap_host||'imap.gmail.com';
  if(c.project && (_projects||[]).includes(c.project)) $('#calProject').value=c.project;
  $('#calLead').value=c.lead_seconds||120;
  $('#calBot').checked=c.use_browser_bot!==false; $('#calDocker').checked=c.use_docker_bot!==false;
  $('#calBotName').value=c.bot_name||'Synthia Notetaker'; $('#calResult').textContent='';
}
function collectCalendar(){
  return { enabled:$('#calEnabled').checked, email:$('#calEmail').value.trim(),
    app_password:$('#calPass').value.trim(), imap_host:$('#calImap').value.trim()||'imap.gmail.com',
    project:$('#calProject').value, lead_seconds:parseInt($('#calLead').value)||120,
    use_browser_bot:$('#calBot').checked, use_docker_bot:$('#calDocker').checked,
    bot_name:$('#calBotName').value.trim()||'Synthia Notetaker' };
}
async function saveCalendar(){
  const r=await window.pywebview.api.save_calendar_config(collectCalendar());
  if(r.ok) toast('ההגדרות נשמרו','check'); else toast('נכשל','alert');
}
// human-readable error mapping
function humanizeCalError(err){
  const e=(err||'').toLowerCase();
  if(e.includes('auth')||e.includes('login')||e.includes('credential')||e.includes('password'))
    return 'ההתחברות נכשלה. ודא ש-App Password באורך 16 תווים (לא סיסמת החשבון) ושאימות דו-שלבי מופעל.';
  if(e.includes('imap')||e.includes('connect')||e.includes('timed out')||e.includes('resolve'))
    return 'החיבור לשרת נכשל. ודא ש-IMAP מופעל ב-Gmail (Settings → Forwarding and POP/IMAP) וששרת ה-IMAP הוא imap.gmail.com.';
  return err||'נכשל';
}
async function testCalendar(){
  const el=$('#calResult'); el.style.color='var(--muted)'; el.innerHTML='<span class="spinner"></span> בודק חיבור…';
  const r=await window.pywebview.api.test_calendar(collectCalendar());
  if(!r.ok){ el.style.color='var(--rec)'; el.innerHTML=svg('alert',13)+' '+esc(humanizeCalError(r.error)); return; }
  el.style.color='var(--text)';
  if(!r.events.length){ el.innerHTML=svg('check',13)+' החיבור תקין. לא נמצאו פגישות קרובות (הזמן את הכתובת לפגישה ביומן).'; return; }
  el.innerHTML=svg('check',13)+' החיבור תקין. פגישות קרובות:<br>'+r.events.map(e=>{
    const d=new Date(e.start); const t=d.toLocaleString('he-IL',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
    return `<div style="margin-top:6px">• <b>${esc(e.title)}</b> — ${t}${e.url?'':' (ללא קישור)'}</div>`;
  }).join('');
}

/* ---------- 3-step setup wizard ---------- */
let _wizStep=0;
function openWizard(){ _wizStep=0; renderWizard(); $('#wizardModal').style.display='flex'; }
function setWizStep(n){ _wizStep=n; $$('#wizardModal .wiz-step').forEach((s,i)=>s.classList.toggle('on', i<=n)); renderWizard(); }
function renderWizard(){
  $$('#wizardModal .wiz-step').forEach((s,i)=>s.classList.toggle('on', i<=_wizStep));
  const body=$('#wizBody');
  if(_wizStep===0){
    body.innerHTML=`<div class="wiz-num">שלב 1 / 3</div><h3>חיבור Gmail</h3>
      <p>הזן את כתובת ה-Gmail הייעודית של המערכת ו-App Password (16 תווים). ודא שאימות דו-שלבי ו-IMAP מופעלים.</p>
      <div class="fld"><label>כתובת Gmail</label><input id="wzEmail" value="${esc($('#calEmail').value)}" placeholder="bot@gmail.com"></div>
      <div class="fld"><label>App Password</label><input id="wzPass" type="password" value="${esc($('#calPass').value)}" placeholder="16 תווים"></div>
      <div class="modal-actions">
        <button class="btn btn-primary" onclick="wizNext()">המשך ←</button>
        <button class="btn btn-ghost" onclick="$('#wizardModal').style.display='none'">ביטול</button></div>`;
  } else if(_wizStep===1){
    body.innerHTML=`<div class="wiz-num">שלב 2 / 3</div><h3>זהות הבוט</h3>
      <p>בחר את השם שיוצג כשהבוט מצטרף לפגישות, ואת אופן ההצטרפות.</p>
      <div class="fld"><label>שם הבוט</label><input id="wzName" value="${esc($('#calBotName').value)}" placeholder="Synthia Notetaker"></div>
      <label class="opt-row chk" style="margin-bottom:14px;color:var(--text)"><input type="checkbox" id="wzDocker" ${$('#calDocker').checked?'checked':''}> בוט Docker (כל הפלטפורמות, כולל Teams)</label>
      <div class="modal-actions">
        <button class="btn btn-primary" onclick="wizNext()">המשך ←</button>
        <button class="btn btn-ghost" onclick="setWizStep(0)">→ חזרה</button></div>`;
  } else {
    body.innerHTML=`<div class="wiz-num">שלב 3 / 3</div><h3>${svg('check',18)} הכל מוכן</h3>
      <p>ההגדרות יישמרו וההצטרפות האוטומטית תופעל. הזמן את כתובת הבוט כמשתתף לכל פגישה ביומן.</p>
      <div class="modal-actions">
        <button class="btn btn-accent" onclick="wizFinish()">${svg('check',14)} סיים והפעל</button>
        <button class="btn btn-ghost" onclick="setWizStep(1)">→ חזרה</button></div>`;
  }
}
function wizNext(){
  if(_wizStep===0){ $('#calEmail').value=$('#wzEmail').value.trim(); $('#calPass').value=$('#wzPass').value.trim(); }
  if(_wizStep===1){ $('#calBotName').value=$('#wzName').value.trim(); $('#calDocker').checked=$('#wzDocker').checked; }
  setWizStep(_wizStep+1);
}
async function wizFinish(){
  $('#calEnabled').checked=true;
  const r=await window.pywebview.api.save_calendar_config(collectCalendar());
  $('#wizardModal').style.display='none';
  if(r.ok) toast('ההתקנה הושלמה — הצטרפות אוטומטית פעילה','check'); else toast('שמירה נכשלה','alert');
  loadSettings();
}

/* ============================================================
   INIT
   ============================================================ */
async function init(){
  hydrateIcons(document);
  $('#brandMark').innerHTML=svg('activity',18);
  $('#searchIc').innerHTML=svg('search',16);
  try{ await loadProjects(); }catch(e){}
  let st={mode:'home'};
  try{ st=await window.pywebview.api.get_startup(); }catch(e){}
  if(st.mode==='process'){
    nav('processing'); setProcessing(true); startProcTimer();
    $('#proc-status').innerHTML=`<span class="spinner"></span> מעבד…`;
    $('#processing-output').innerHTML=procCardHTML();
    const res=await window.pywebview.api.process_file();
    showProcessed(res); afterProcessing(res);
  } else { nav('overview'); }
  // first-run wizard if calendar not configured
  try{ const c=await window.pywebview.api.get_calendar_config();
    if(!c || !c.email){ /* offer wizard quietly via toast */ } }catch(e){}
}
window.addEventListener('pywebviewready', init);
// hydrate icons immediately so static chrome shows even before bridge ready
document.addEventListener('DOMContentLoaded', ()=>{ try{ hydrateIcons(document);
  const bm=$('#brandMark'); if(bm) bm.innerHTML=svg('activity',18);
  const si=$('#searchIc'); if(si) si.innerHTML=svg('search',16); }catch(e){} });
