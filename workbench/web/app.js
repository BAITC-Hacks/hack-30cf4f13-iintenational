"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
  const fmt = new Intl.NumberFormat("ru-RU");
  const kinds = {transactions:"Переводы",nodes:"Узлы",edges:"Агрегированные связи"};
  const statuses = {queued:"В очереди",running:"Выполняется",completed:"Готово",failed:"Ошибка",interrupted:"Прервано"};
  const capabilityNames = {structural_roles:"Структурные роли",flow_roles:"Транзит и удержание",clustering:"Кластеры",ranking:"Приоритеты",seed_context:"Контекст seed",temporal_analysis:"Временные паттерны"};
  const capabilityStatuses = {available:"Доступно",completed:"Выполнено",limited:"Ограничено",unavailable:"Недоступно"};
  const fields = {nodes:["gid","depth","is_seed"],edges:["src","dst","amount","n_tx","depth","currency"],transactions:["src","dst","amount","date","currency"]};
  const fieldNames = {gid:"Идентификатор узла *",src:"Отправитель *",dst:"Получатель *",amount:"Сумма *",n_tx:"Количество переводов",depth:"Глубина обхода",is_seed:"Стартовый узел (seed)",date:"Дата перевода",currency:"Валюта в строке"};
  const aliases = {gid:["gid","id","node_id"],src:["src","src_gid","source","from","sender"],dst:["dst","dst_gid","target","to","recipient"],amount:["amount","sum_kzt","amount_kzt","sum","value"],n_tx:["n_tx","count","transaction_count"],depth:["depth"],is_seed:["is_seed","seed"],date:["date","tx_date","timestamp","transaction_date"],currency:["currency"]};
  const state = {session:null,sources:[],activeId:null,busy:false,poll:null,frameUrl:null,validated:null,runs:[]};
  const selected = (a,b) => a===b?" selected":"";
  const option = (value,label,current) => `<option value="${esc(value)}"${selected(value,current)}>${esc(label)}</option>`;

  function clearError() {$("error").hidden=true;$("error").textContent="";}
  function showError(error) {
    const issues = Array.isArray(error.issues)?error.issues:[];
    $("error").innerHTML=`<p><strong>${esc(error.message || "Не удалось выполнить действие.")}</strong></p>${issues.length?`<ul>${issues.slice(0,12).map(issue=>`<li>${esc([issue.table,issue.row!=null?`строка ${issue.row}`:null,issue.field].filter(Boolean).join(" · "))}: ${esc(issue.message || issue.code || "Проверьте значение")}</li>`).join("")}</ul>`:""}`;
    $("error").hidden=false;$("error").focus();
  }
  function busy(value,message="") {
    state.busy=value;$("activity").textContent=message;
    ["file-input","to-mapping","validate","start-run","new-import","back-files","back-mapping","refresh-history"].forEach(id=>$(id).disabled=value || !state.session);
    $("to-mapping").disabled=value || !state.sources.length;
    document.querySelectorAll("[data-remove], [data-kind], [data-reinspect], [data-history]").forEach(el=>el.disabled=value);
    $("config-form").inert=value;$("source-settings").inert=value;
  }
  async function operation(message,action) {
    if(state.busy)return;
    clearError();busy(true,message);
    try {await action();} catch(error) {showError(error);} finally {busy(false);}
  }
  async function api(path,{method="GET",body,headers={},raw=false}={}) {
    const response = await fetch(path,{method,headers:{...(state.session?{"X-Session-Token":state.session.token}:{}),...(body!==undefined&&!raw?{"Content-Type":"application/json"}:{}),...headers},body:body===undefined?undefined:raw?body:JSON.stringify(body),cache:"no-store",credentials:"omit"});
    if(!response.ok){let value;try{value=await response.json();}catch{value={error:`Ошибка HTTP ${response.status}`};}const error=new Error(response.status===403?"Сессия недоступна. Обновите страницу; сохранённые расчёты останутся в истории.":value.error);error.issues=value.issues;throw error;}
    return response;
  }
  const json = async (path,options) => (await api(path,options)).json();
  function step(name) {
    for(const id of ["files","mapping","review","result"]){$("step-"+id).hidden=id!==name;const item=document.querySelector(`[data-step="${id}"]`);if(id===name)item.setAttribute("aria-current","step");else item.removeAttribute("aria-current");}
    $("main").scrollIntoView({block:"start"});
  }
  function invalidate() {state.validated=null;}
  function renderFiles() {
    $("file-list").innerHTML=state.sources.map((s,i)=>`<div class="file-row"><div><strong>${esc(s.filename)}</strong><small>${fmt.format(s.size)} байт · ${s.info?`${fmt.format(s.info.rows)} строк · ${s.info.columns.length} колонок`:"Настройте чтение на следующем шаге"}</small></div><label><span class="sr-only">Тип таблицы</span><select data-kind="${i}" aria-label="Тип таблицы ${esc(s.filename)}">${Object.entries(kinds).map(([v,l])=>option(v,l,s.kind)).join("")}</select></label><button data-remove="${i}" aria-label="Убрать ${esc(s.filename)} из набора">Убрать</button></div>`).join("");
    $("files-summary").textContent=state.sources.length?`Файлов в наборе: ${state.sources.length} из 3`:"Файлы ещё не выбраны";
    $("to-mapping").disabled=state.busy || !state.sources.length;
  }
  function suggestMapping(source) {
    const columns=source.info?.columns || [];
    source.mapping=Object.fromEntries(fields[source.kind].map(field=>[field,columns.find(c=>aliases[field].includes(c.toLowerCase())) || ""]));
  }
  async function inspect(source) {
    source.info=await json("/api/inspect",{method:"POST",body:{upload_id:source.id,options:source.options}});source.inspectError=null;
    const columns=source.info.columns;
    if(!Object.keys(source.mapping).length)suggestMapping(source);
    for(const key of Object.keys(source.mapping)){if(!columns.includes(source.mapping[key]))source.mapping[key]="";}
  }
  function renderSources() {
    $("source-settings").innerHTML=state.sources.map((s,i)=>{
      const xlsx=/\.xlsx$/i.test(s.filename),text=/\.(csv|tsv)$/i.test(s.filename);
      const preview=s.info?`<div class="preview-table"><table><caption>Первые ${s.info.preview.length} строк из ${fmt.format(s.info.rows)} · исходные значения</caption><thead><tr>${s.info.columns.map(c=>`<th scope="col">${esc(c)}</th>`).join("")}</tr></thead><tbody>${s.info.preview.map(row=>`<tr>${s.info.columns.map(c=>`<td title="${esc(row[c])}">${esc(row[c])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`:"";
      return `<article class="panel" data-source="${i}"><div class="source-heading"><div><h3>${esc(s.filename)}</h3><p class="muted">${kinds[s.kind]}${s.info?` · ${fmt.format(s.info.rows)} строк`:""}</p></div></div>
      <details class="format-settings"${!s.info?" open":""}><summary>Настройки чтения${s.inspectError?" · нужна проверка":""}</summary><div class="form-grid">
      ${text?`<label>Разделитель столбцов<select data-option="delimiter">${[[",","Запятая"],[";","Точка с запятой"],["\t","Табуляция"],["|","Вертикальная черта"]].map(([v,l])=>option(v,l,s.options.delimiter)).join("")}</select></label><label>Кодировка<select data-option="encoding">${["utf-8-sig","utf-8","cp1251"].map(v=>option(v,v,s.options.encoding)).join("")}</select></label>`:""}
      ${xlsx?`<label>Лист Excel<input data-option="sheet" list="sheets-${i}" value="${esc(s.options.sheet || s.info?.sheets?.[0] || "")}" placeholder="Первый лист по умолчанию"><datalist id="sheets-${i}">${(s.info?.sheets || []).map(v=>option(v,v,"")).join("")}</datalist></label>`:""}
      <label>Десятичный разделитель<select data-option="decimal">${option(".","Точка · 12.50",s.options.decimal)}${option(",","Запятая · 12,50",s.options.decimal)}</select></label>
      <label>Формат даты<input data-option="date_format" value="${esc(s.options.date_format)}" placeholder="iso или %d.%m.%Y"></label></div><button data-reinspect="${i}" class="secondary">Применить и перечитать</button><p class="muted">ISO: 2026-07-01. Для 01.07.2026 укажите %d.%m.%Y. Часовой пояс не угадывается. Изменение настроек чтения требует повторного предпросмотра.</p></details>
      ${s.inspectError?`<p class="error-box">${esc(s.inspectError)}</p>`:""}
      <div class="mapping-grid">${s.info?fields[s.kind].map(field=>`<label>${fieldNames[field]}<select data-field="${field}" aria-label="${esc(fieldNames[field])} в ${esc(s.filename)}">${option("","Не задано",s.mapping[field])}${s.info.columns.map(c=>option(c,c,s.mapping[field])).join("")}</select></label>`).join(""):""}</div>${preview}</article>`;
    }).join("");
  }
  function config() {
    if(["currency","decimals","top-n"].some(id=>!$(id).value.trim()))throw new Error("Укажите валюту, число знаков после запятой и размер приоритетного списка.");
    return {profile:$("profile").value,currency:$("currency").value.trim().toUpperCase(),decimals:Number($("decimals").value),top_n:Number($("top-n").value),coverage:$("coverage").value,max_depth:$("coverage").value==="outward"&&$("max-depth").value!==""?Number($("max-depth").value):null};
  }
  function updateProfile(changed=false) {
    const strict=$("profile").value==="hackalem";
    if(changed&&strict){$("currency").value="KZT";$("decimals").value="0";$("top-n").value="30";$("coverage").value="outward";$("max-depth").value="4";}
    if(changed&&!strict){$("coverage").value="unknown";$("max-depth").value="";}
    for(const id of ["currency","decimals","top-n","coverage"])$(id).disabled=strict;
    $("max-depth").disabled=strict||$("coverage").value!=="outward";
    $("profile-note").textContent=strict?"HackAlem: требуются три таблицы и все контрольные значения ТЗ, включая 2248 узлов, 3119 связей, 16 компонент и 444 узла границы. Несовпадение останавливает расчёт; данные не подгоняются.":"Если полнота наблюдений неизвестна, система не делает выводов об удержании или транзите денег. Seed и глубина не восстанавливаются по догадке.";
  }
  function requestBody() {
    if(state.sources.some(s=>!s.info||s.dirty))throw new Error("Примените настройки чтения каждого файла и проверьте предпросмотр.");
    if(new Set(state.sources.map(s=>s.kind)).size!==state.sources.length)throw new Error("В наборе может быть одна таблица каждого типа. Выберите правильные типы на шаге «Файлы».");
    return {sources:state.sources.map(s=>({upload_id:s.id,kind:s.kind,mapping:Object.fromEntries(Object.entries(s.mapping).filter(([,v])=>v)),options:s.options})),config:config()};
  }
  function summary(report,collapsed=false) {
    const names={nodes:"Узлов",edges:"Связей",transactions:"Переводов",components:"Компонент",seeds:"Seed"};
    const counts=report.counts||{};
    return `<p class="muted">${report.profile==="hackalem"?"HackAlem · строгий профиль":"Пользовательский профиль"} · ${esc(report.currency)} · точность: ${esc(report.decimals)} знаков${report.amount_total!=null?` · оборот: ${esc(report.amount_total)} ${esc(report.currency)}`:""}</p><dl class="counts">${Object.entries(names).map(([key,name])=>`<div><dt>${name}</dt><dd>${counts[key]==null?"—":fmt.format(counts[key])}</dd></div>`).join("")}</dl><p class="muted">«—» означает отсутствие сведений, а не ноль.</p>
    ${report.warnings?.length?`<ul class="warning-list">${report.warnings.map(w=>`<li>${esc(typeof w==="string"?w:w.message || w.reason || w.code)}</li>`).join("")}</ul>`:""}
    ${collapsed?'<details class="report-details"><summary>Методика и доступность анализа</summary>':'<h3>Что можно заключить из этого набора</h3>'}${(report.capabilities || []).map(c=>`<div class="capability"><strong>${esc(capabilityNames[c.id] || c.id)}</strong><span class="tag">${esc(capabilityStatuses[c.status] || c.status)}</span><p>${esc(c.reason)}</p></div>`).join("")}${collapsed?"</details>":""}
    ${report.total_seconds!=null?`<p class="muted">Расчёт: ${esc(report.total_seconds)} с · кластеров: ${esc(counts.clusters)} · в приоритетном списке: ${esc(counts.top_nodes)}. Версия правил: ${esc(report.rules_version)}. Параметры и контрольные суммы источников — в report.json.</p>`:""}`;
  }
  function runTitle(run) {return (run.sources || run.report?.sources)?.map(s=>s.filename).join(" + ") || `Расчёт ${run.id.slice(0,8)}`;}
  function renderHistory() {
    $("run-history").innerHTML=state.runs.map(run=>`<button class="history-item" data-history="${esc(run.id)}" aria-current="${state.activeId===run.id}"><strong>${esc(runTitle(run))}</strong><small>${esc(statuses[run.status] || run.status)} · ${esc(new Date(run.created_at).toLocaleString("ru-RU",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}))}</small></button>`).join("") || '<p class="muted">Здесь появятся ваши расчёты. Они сохраняются после закрытия страницы.</p>';
  }
  async function refreshHistory() {state.runs=(await json("/api/runs")).runs;renderHistory();}
  function stopPoll() {clearTimeout(state.poll);state.poll=null;}
  function clearFrame() {$("result-frame").removeAttribute("src");$("result-frame").style.removeProperty("height");if(state.frameUrl)URL.revokeObjectURL(state.frameUrl);state.frameUrl=null;$("graph-container").hidden=true;}
  async function showRun(id) {
    stopPoll();state.activeId=id;step("result");clearFrame();$("result-summary").textContent="";$("downloads").textContent="";$("run-status").textContent="Загрузка результата…";renderHistory();await pollRun(id);
  }
  async function pollRun(id) {
    try {
      const run=await json(`/api/runs/${id}`);if(state.activeId!==id)return;
      $("result-title").textContent=runTitle(run);$("run-status").textContent=statuses[run.status] || run.status;
      if(["queued","running"].includes(run.status)) {$("run-status").textContent+=". Можно оставить страницу открытой или вернуться к расчёту в истории.";state.poll=setTimeout(()=>pollRun(id),1000);return;}
      await refreshHistory();if(state.activeId!==id)return;
      if(run.status!=="completed") {$("result-summary").textContent=typeof run.error==="string"?run.error:"Расчёт не завершён. Исправьте данные или повторите импорт. Предыдущие результаты сохранены.";if(run.issues?.length){const error=new Error(run.error);error.issues=run.issues;showError(error);}return;}
      $("result-summary").innerHTML=summary(run.report,true);
      $("downloads").innerHTML=(run.artifacts||[]).map(name=>`<button data-download="${esc(name)}">↓ ${esc(name)}</button>`).join("");
      const html=await (await api(`/api/runs/${id}/artifacts/graph_view.html`)).text();if(state.activeId!==id)return;
      // Только наш сгенерированный артефакт; произвольный пользовательский HTML не принимается.
      // Отдельный CSP nonce разрешает его скрипт внутри opaque-origin sandbox, не inline-код мастера.
      const parsed=new DOMParser().parseFromString(html,"text/html");parsed.querySelectorAll("script").forEach(script=>script.setAttribute("nonce",state.session.csp_nonce));
      state.frameUrl=URL.createObjectURL(new Blob(["<!doctype html>\n"+parsed.documentElement.outerHTML],{type:"text/html;charset=utf-8"}));
      $("result-frame").src=state.frameUrl;$("graph-container").hidden=false;
    } catch(error) {if(state.activeId===id){$("run-status").textContent="Не удалось загрузить результат. Откройте расчёт из истории повторно.";showError(error);}}
  }
  $("file-input").addEventListener("change",()=>{
    const files=[...$("file-input").files];$("file-input").value="";
    operation("Загружаю и проверяю структуру файлов…",async()=>{
      if(files.length+state.sources.length>3)throw new Error("Выберите не более трёх файлов: по одной таблице каждого типа.");
      for(const file of files){
        if(file.size>state.session.limits.max_file_bytes)throw new Error(`Файл ${file.name} превышает лимит 20 МиБ.`);
        const upload=await json("/api/uploads",{method:"POST",body:file,raw:true,headers:{"Content-Type":"application/octet-stream","X-Filename":encodeURIComponent(file.name)}});
        const kind=/nodes/i.test(file.name)?"nodes":/edges/i.test(file.name)?"edges":"transactions";
        const source={...upload,kind,options:{delimiter:/\.tsv$/i.test(file.name)?"\t":",",encoding:"utf-8-sig",decimal:".",date_format:"iso"},mapping:{},info:null,dirty:false};
        state.sources.push(source);invalidate();
        try{await inspect(source);}catch(error){source.inspectError=error.message;}
        renderFiles();
      }
    });
  });
  $("file-list").addEventListener("change",event=>{const el=event.target.closest("[data-kind]");if(!el)return;const s=state.sources[Number(el.dataset.kind)];s.kind=el.value;suggestMapping(s);invalidate();});
  $("file-list").addEventListener("click",event=>{const el=event.target.closest("[data-remove]");if(!el||state.busy)return;state.sources.splice(Number(el.dataset.remove),1);invalidate();renderFiles();$("activity").textContent="Файл убран из текущего набора. Локальная копия остаётся в хранилище.";});
  $("source-settings").addEventListener("change",event=>{
    const el=event.target,card=el.closest("[data-source]");if(!card)return;const source=state.sources[Number(card.dataset.source)];
    if(el.dataset.field)source.mapping[el.dataset.field]=el.value;
    if(el.dataset.option){if(el.dataset.option==="sheet"&&!el.value)delete source.options.sheet;else source.options[el.dataset.option]=el.value;source.dirty=true;}
    invalidate();
  });
  $("source-settings").addEventListener("click",event=>{const el=event.target.closest("[data-reinspect]");if(!el)return;operation("Перечитываю таблицу…",async()=>{const source=state.sources[Number(el.dataset.reinspect)];try{await inspect(source);source.dirty=false;}catch(error){source.info=null;source.inspectError=error.message;throw error;}finally{renderSources();renderFiles();}});});
  $("config-form").addEventListener("submit",event=>event.preventDefault());
  $("config-form").addEventListener("change",event=>{invalidate();updateProfile(event.target.id==="profile");});
  $("to-mapping").addEventListener("click",()=>{clearError();renderSources();step("mapping");});
  $("back-files").addEventListener("click",()=>{clearError();renderFiles();step("files");});
  $("back-mapping").addEventListener("click",()=>{clearError();step("mapping");});
  $("validate").addEventListener("click",()=>operation("Проверяю таблицы, суммы и условия наблюдений…",async()=>{const body=requestBody();const report=await json("/api/preview",{method:"POST",body});state.validated=body;$("review-summary").innerHTML=summary(report);step("review");}));
  $("start-run").addEventListener("click",()=>operation("Запускаю расчёт…",async()=>{if(!state.validated)throw new Error("Сначала проверьте текущие настройки.");const run=await json("/api/runs",{method:"POST",body:state.validated});await refreshHistory();await showRun(run.id);}));
  $("run-history").addEventListener("click",event=>{const el=event.target.closest("[data-history]");if(el)operation("Открываю расчёт…",()=>showRun(el.dataset.history));});
  $("refresh-history").addEventListener("click",()=>operation("Обновляю историю…",refreshHistory));
  $("downloads").addEventListener("click",event=>{
    const el=event.target.closest("[data-download]");if(!el)return;
    operation("Подготавливаю файл…",async()=>{const blob=await (await api(`/api/runs/${state.activeId}/artifacts/${el.dataset.download}`)).blob();const url=URL.createObjectURL(blob);const anchor=document.createElement("a");anchor.href=url;anchor.download=el.dataset.download;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);});
  });
  $("new-import").addEventListener("click",()=>{stopPoll();clearFrame();clearError();state.sources=[];state.activeId=null;invalidate();$("config-form").reset();updateProfile();renderFiles();renderHistory();step("files");});
  window.addEventListener("message",event=>{if(event.source!==$("result-frame").contentWindow || event.data?.type!=="potoki:height" || !Number.isSafeInteger(event.data.height))return;$("result-frame").style.height=`${Math.max(320,Math.min(20000,event.data.height+4))}px`;});
  window.addEventListener("beforeunload",()=>{stopPoll();if(state.frameUrl)URL.revokeObjectURL(state.frameUrl);});
  busy(true,"Подключаюсь к локальному приложению…");
  (async()=>{try{state.session=await json("/api/session");const limits=state.session.limits;$("upload-limits").textContent=`До ${limits.max_file_bytes/1024/1024} МиБ на файл · ${fmt.format(limits.max_rows)} строк · ${fmt.format(limits.max_nodes)} узлов · ${fmt.format(limits.max_edges)} связей`;await refreshHistory();const active=state.runs.find(r=>["queued","running"].includes(r.status));if(active)await showRun(active.id);}catch(error){showError(error);}finally{busy(false);}})();
})();
