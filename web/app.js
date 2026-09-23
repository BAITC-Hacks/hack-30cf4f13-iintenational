/* Автономный интерфейс: без внешних библиотек, запросов и персональных данных. */
"use strict";
(() => {
  const data = JSON.parse(document.getElementById("graph-data").textContent);
  const $ = id => document.getElementById(id);
  const themeStyle = getComputedStyle(document.documentElement);
  const theme = Object.fromEntries(["ink","muted","accent","soft","paper","line","edge"].map(key => [key, themeStyle.getPropertyValue("--"+key).trim()]));
  const fmt = new Intl.NumberFormat("ru-RU");
  const decimal = new Intl.NumberFormat("ru-RU", {maximumFractionDigits: 1});
  const exactScore = new Intl.NumberFormat("ru-RU", {minimumFractionDigits: 6, maximumFractionDigits: 6});
  const percentile = new Intl.NumberFormat("ru-RU", {maximumFractionDigits: 4});
  const generic = data.meta.profile === "generic", currency = data.meta.currency || "KZT";
  const scale = data.meta.moneyScale || 0, divisor = 10n ** BigInt(scale);
  // Денежные значения generic передаются строками целых minor units, без потери int64.
  const money = value => {const n=BigInt(value);return fmt.format(n/divisor)+(scale?","+(n%divisor).toString().padStart(scale,"0"):"");};
  const count = value => value == null ? "неизвестно" : fmt.format(typeof value === "string" ? BigInt(value) : value);
  const boundary = n => generic ? n.boundary === true : n.depth === 4;
  const amountDescending = (a,b) => BigInt(a.v)>BigInt(b.v)?-1:BigInt(a.v)<BigInt(b.v)?1:0;
  const shortId = id => id.length>18?id.slice(0,15)+"…":id;
  const esc = value => String(value).replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
  const roles = {
    coordinator: {label:"Координатор", color:"#705483", text:"Гипотеза о структурно центральном узле сети."},
    consolidator: {label:"Консолидатор", color:"#ac7140", text:"Гипотеза о сборе потока от нескольких отправителей."},
    distributor: {label:"Распределитель", color:"#3e7797", text:"Гипотеза о распределении потока между получателями."},
    transit: {label:"Транзит", color:"#3d8068", text:"Гипотеза о сопоставимом входящем и исходящем обороте."},
    terminal: {label:"Терминальный", color:"#a15a5e", text:"Гипотеза об удержании части потока внутри наблюдаемой сети, не на границе обхода."},
    peripheral: {label:"Периферийный", color:"#7c8780", text:"Не выполнены правила остальных ролей; не означает отсутствие значимости."}
  };
  const role = n => roles[n.role] || {label:n.role,color:"#7c8780",text:"Роль в выгрузке."};
  const byId = new Map(data.nodes.map(n => [n.id,n]));
  const clusterById = new Map(data.clusters.map(c => [c.id,c]));
  const incoming = new Map(data.nodes.map(n => [n.id,[]]));
  const outgoing = new Map(data.nodes.map(n => [n.id,[]]));
  for (const edge of data.edges) {incoming.get(edge.t)?.push(edge);outgoing.get(edge.s)?.push(edge);}
  // GID остаётся строкой: int64 может быть больше Number.MAX_SAFE_INTEGER.
  const ranked = [...data.nodes].sort((a,b) => b.priority-a.priority || b.roleScore-a.roleScore || (BigInt(a.sortKey ?? a.id)<BigInt(b.sortKey ?? b.id)?-1:BigInt(a.sortKey ?? a.id)>BigInt(b.sortKey ?? b.id)?1:0));
  const state = {selected:ranked[0]?.id || null, page:0, edgePage:0, scope:"node", view:"diagram", color:"role"};
  const PAGE = 8, EDGE_PAGE = 8;
  let filtered = ranked;
  let camera = {scale:1,x:0,y:0,fitScale:1};
  let visibleEdges = [], componentBoxes = [], canvasWidth = 1, canvasHeight = 1;
  const canvas = $("network"), ctx = canvas.getContext("2d");
  const compactMoney = value => {const v=Number(value)/Number(divisor);return v>=1e6?`${decimal.format(v/1e6)} млн`:v>=1e3?`${decimal.format(v/1e3)} тыс.`:money(value);};
  const clusterColor = id => `hsl(${(id*137.508)%360}, 37%, 43%)`;
  const color = n => state.color === "cluster" ? clusterColor(n.cluster) : role(n).color;
  const dot = c => `<i class="role-dot" style="--role-color:${c}" aria-hidden="true"></i>`;
  const setMessage = (message, error=false) => {$("search-message").textContent=message;$("search-message").classList.toggle("error",error);$("gid").setAttribute("aria-invalid",String(error));};
  const pressed = (id,value) => $(id).setAttribute("aria-pressed",String(value));
  const option = (select,value,label) => {const el=document.createElement("option");el.value=value;el.textContent=label;select.append(el);};
  const filterIds = ["role-filter","component-filter","cluster-filter","depth-filter","seed-filter"];
  const matches = n => ($("role-filter").value==="all" || n.role===$("role-filter").value)
    && ($("component-filter").value==="all" || String(n.component)===$("component-filter").value)
    && ($("cluster-filter").value==="all" || String(n.cluster)===$("cluster-filter").value)
    && ($("depth-filter").value==="all" || ($("depth-filter").value==="unknown" ? n.depth == null : String(n.depth)===$("depth-filter").value))
    && ($("seed-filter").value==="all" || ($("seed-filter").value==="unknown" ? n.seed == null : $("seed-filter").value==="seed" ? n.seed === true : n.seed === false));

  function renderQueue() {
    const pages=Math.max(1,Math.ceil(filtered.length/PAGE));
    state.page=Math.min(state.page,pages-1);
    $("queue-count").textContent=`${fmt.format(filtered.length)} из ${fmt.format(data.nodes.length)} узлов`;
    $("node-list").innerHTML=filtered.slice(state.page*PAGE,(state.page+1)*PAGE).map(n => `<button class="node-row" data-node-id="${esc(n.id)}" aria-current="${n.id===state.selected}" aria-label="GID ${esc(n.id)}, ${esc(role(n).label)}, приоритет ${decimal.format(n.priority*100)} из 100"><span><strong>${esc(n.id)}${n.seed?'<span class="seed-tag">seed</span>':""}</strong><small>${dot(role(n).color)}${esc(role(n).label)}</small></span><span class="score">${decimal.format(n.priority*100)}<span class="score-track" aria-hidden="true"><i style="width:${Math.max(0,Math.min(100,n.priority*100))}%"></i></span></span></button>`).join("") || '<p class="empty-state">Нет узлов по этим фильтрам.</p>';
    $("nodes-page").textContent=filtered.length?`${state.page+1} / ${pages}`:"0 / 0";
    $("nodes-prev").disabled=state.page===0;$("nodes-next").disabled=state.page>=pages-1;
  }

  function renderDetails() {
    const n=byId.get(state.selected);
    $("details-heading").textContent=n?`GID ${n.id}`:"Узел не выбран";
    $("depth-badge").textContent=n?(n.depth==null?"Глубина неизвестна":`${n.depth}-е колено`):"";
    if(!n){$("details-content").innerHTML='<p class="empty-state">Измените фильтры, чтобы выбрать узел.</p>';return;}
    const c=clusterById.get(n.cluster);
    $("details-content").innerHTML=`
      <section class="detail-section"><span class="role-badge">${dot(role(n).color)}${esc(role(n).label)}</span>${n.seed?'<span class="seed-tag">seed</span>':""}<p>${esc(role(n).text)}</p>
        <div class="score-grid"><div><span class="detail-kicker">Приоритет проверки</span><strong class="detail-score">${decimal.format(n.priority*100)}<small> / 100</small></strong></div><div><span class="detail-kicker">Скор правила роли</span><strong class="detail-score">${decimal.format(n.roleScore*100)}<small> / 100</small></strong></div></div>
        <p>Скоры — расчётные показатели, не вероятность нарушения.</p>${boundary(n)?`<p class="boundary-note"><strong>Граница выгрузки</strong><br>Исходящие связи ${esc(n.depth)}-го колена не собраны. Нулевой out_degree не означает, что деньги остались здесь.</p>`:generic&&data.meta.coverage==="unknown"?'<p class="boundary-note">Полнота наблюдения неизвестна. Отсутствие связей не означает отсутствие переводов.</p>':""}
      </section>
      <section class="detail-section"><h3>Наблюдаемый поток</h3><div class="metric-grid"><div><span class="detail-kicker">Входящий, ${esc(currency)}</span><strong>${money(n.sumIn)}</strong></div><div><span class="detail-kicker">Исходящий, ${esc(currency)}</span><strong>${money(n.sumOut)}</strong></div><div><span class="detail-kicker">Плательщиков</span><strong>${fmt.format(n.inDegree)}</strong></div><div><span class="detail-kicker">Получателей</span><strong>${fmt.format(n.outDegree)}</strong></div></div>
        <details><summary>Почему присвоена роль</summary><p>${esc(n.evidence)}</p></details><details><summary>Как рассчитан приоритет</summary><p>${esc(n.why || data.meta.priorityDescription || "Приоритет: 45% базового веса роли, 25% скора правила роли, 20% максимального процентиля центральности в компоненте и 10% процентиля оборота. Для подтверждённой границы обхода итог умножается на 0,85.")}</p><p>Точный priority_score: ${exactScore.format(n.priority)} (шкала 0–1). Центральность в компоненте: ${percentile.format(n.centrality*100)}-й процентиль. Оборот в сети: ${percentile.format(n.turnoverPercentile*100)}-й процентиль.</p><p>Процентиль — положение в ранжированном наборе по шкале 0–100; при равных значениях используется средний ранг. Это не вероятность нарушения и не доля строго уступающих узлов.</p></details>
      </section>
      <section class="detail-section"><h3>Контекст сети</h3><p>Компонента ${n.component} · Кластер ${n.cluster}<br>${count(c?.size)} узлов · seed: ${count(c?.seeds)}</p><p>${esc(c?.hypothesis || "Гипотеза о связанном сообществе узлов.")}</p><button class="cluster-link" id="show-cluster">Показать кластер ${n.cluster} ↗</button></section>`;
    $("show-cluster").addEventListener("click",() => {filterIds.forEach(id => $(id).value="all");$("cluster-filter").value=String(n.cluster);applyFilters();setScope("all");});
  }

  function nodeConnections() {
    if(!state.selected)return [];
    const ins=(incoming.get(state.selected)||[]).map(e => ({...e,neighbor:e.s,direction:"Входящий"}));
    const outs=(outgoing.get(state.selected)||[]).map(e => ({...e,neighbor:e.t,direction:"Исходящий"}));
    return [...ins,...outs].sort((a,b) => amountDescending(a,b) || a.neighbor.localeCompare(b.neighbor));
  }

  function renderTable() {
    const edges=nodeConnections(), pages=Math.max(1,Math.ceil(edges.length/EDGE_PAGE));
    state.edgePage=Math.min(state.edgePage,pages-1);
    $("connection-rows").innerHTML=edges.slice(state.edgePage*EDGE_PAGE,(state.edgePage+1)*EDGE_PAGE).map(e => `<tr><td><button data-node-id="${esc(e.neighbor)}" aria-label="Открыть GID ${esc(e.neighbor)}">${esc(e.neighbor)}</button></td><td class="${e.direction==="Входящий"?"direction-in":"direction-out"}">${e.direction==="Входящий"?"↘":"↗"} ${e.direction}</td><td>${money(e.v)}</td><td>${count(e.count)}</td></tr>`).join("");
    $("connections-empty").hidden=edges.length>0;
    $("edges-page").textContent=edges.length?`${state.edgePage+1} / ${pages} · ${fmt.format(edges.length)} связей`:"0 связей";
    $("edges-prev").disabled=state.edgePage===0;$("edges-next").disabled=state.edgePage>=pages-1;
  }

  function svgNode(n,x,y,center=false,amount=null) {
    const w=center?154:142,h=center?66:amount!==null?44:26,subtitle=center?role(n).label:amount!==null?`${compactMoney(amount)} ${currency}`:null;
    return `<g class="svg-node" data-node-id="${esc(n.id)}" tabindex="0" role="button" aria-label="Открыть GID ${esc(n.id)}, ${esc(role(n).label)}"><title>GID ${esc(n.id)} · ${esc(role(n).label)}</title><rect x="${x-w/2}" y="${y-h/2}" width="${w}" height="${h}" rx="${center?8:5}" fill="${center?theme.soft:theme.paper}" stroke="${center?theme.accent:theme.line}"/><circle cx="${x-w/2+12}" cy="${subtitle?y-8:y}" r="3.5" fill="${color(n)}"/><text x="${x-w/2+22}" y="${subtitle?y-4:y+4}" fill="${theme.ink}" font-size="${n.id.length>13?9:12}" font-weight="600">${esc(shortId(n.id))}</text>${subtitle?`<text x="${x}" y="${y+15}" text-anchor="middle" fill="${theme.muted}" font-size="11">${esc(subtitle)}</text>`:""}</g>`;
  }

  const compactDiagram = () => $("stage").getBoundingClientRect().width < 520;
  const neighborLimit = () => compactDiagram()?4:12;
  const arrowDefinition = `<defs><marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 8 4 L 0 8 z" fill="${theme.edge}"/></marker></defs>`;

  function renderCompactNeighborhood(n,ins,outs) {
    let paths="",nodes="";
    for(const [edges,isIn] of [[ins,true],[outs,false]]) {
      edges.forEach((e,i)=>{
        const x=edges.length===1?210:i%2?315:105,y=(isIn?87:455)+Math.floor(i/2)*62;
        const sx=isIn?x:210,sy=isIn?y+22:318,tx=isIn?210:x,ty=isIn?252:y-22;
        paths+=`<path d="M ${sx} ${sy} C ${sx} ${(sy+ty)/2}, ${tx} ${(sy+ty)/2}, ${tx} ${ty}" stroke="${theme.edge}" stroke-width="2" opacity=".7" fill="none" marker-end="url(#arrow)"/>`;
        nodes+=svgNode(byId.get(isIn?e.s:e.t),x,y,false,e.v);
      });
    }
    return `${arrowDefinition}<text x="210" y="35" text-anchor="middle" fill="${theme.muted}" font-size="13">Входящие · ${n.inDegree}</text><text x="210" y="397" text-anchor="middle" fill="${theme.muted}" font-size="13">Исходящие · ${n.outDegree}</text>${paths}${nodes}${svgNode(n,210,285,true)}${!ins.length?`<text x="210" y="116" text-anchor="middle" fill="${theme.muted}" font-size="12">Нет наблюдаемых связей</text>`:""}${!outs.length?`<text x="210" y="469" text-anchor="middle" fill="${theme.muted}" font-size="12">${boundary(n)?'Граница выгрузки':'Нет наблюдаемых связей'}</text>`:""}`;
  }

  function renderNeighborhood() {
    const n=byId.get(state.selected), svg=$("neighborhood");
    if(!n){svg.innerHTML="";return;}
    const compact=compactDiagram(),limit=neighborLimit();
    const ins=[...(incoming.get(n.id)||[])].sort(amountDescending).slice(0,limit);
    const outs=[...(outgoing.get(n.id)||[])].sort(amountDescending).slice(0,limit);
    svg.setAttribute("viewBox",compact?"0 0 420 570":"0 0 800 480");
    svg.setAttribute("aria-label",`GID ${n.id}: ${n.inDegree} входящих и ${n.outDegree} исходящих связей. Полный список — в таблице связей.`);
    if(compact){svg.innerHTML=renderCompactNeighborhood(n,ins,outs);return;}
    let paths="",nodes="";
    const yFor=(i,total)=>total===1?248:78+i*(340/Math.max(1,total-1));
    for(const [edges,isIn] of [[ins,true],[outs,false]]) {
      edges.forEach((e,i) => {
        const y=yFor(i,edges.length),neighbor=byId.get(isIn?e.s:e.t);
        const start=isIn?211:477, end=isIn?323:589, sy=isIn?y:248,ey=isIn?248:y;
        paths+=`<path d="M ${start} ${sy} C ${isIn?269:535} ${sy}, ${isIn?269:535} ${ey}, ${end} ${ey}" stroke="${theme.edge}" stroke-width="${1+Math.min(3,Math.log10(Number(e.v)+1)/3)}" opacity=".7" fill="none" marker-end="url(#arrow)"/><text x="${isIn?227:573}" y="${y-5}" text-anchor="${isIn?'start':'end'}" fill="${theme.muted}" font-size="9">${compactMoney(e.v)}</text>`;
        if(neighbor)nodes+=svgNode(neighbor,isIn?140:660,y);
      });
    }
    svg.innerHTML=`${arrowDefinition}<text x="140" y="35" text-anchor="middle" fill="${theme.muted}" font-size="11">Входящие · ${fmt.format(n.inDegree)}</text><text x="660" y="35" text-anchor="middle" fill="${theme.muted}" font-size="11">Исходящие · ${fmt.format(n.outDegree)}</text>${paths}${nodes}${svgNode(n,400,248,true)}${!ins.length?`<text x="140" y="252" text-anchor="middle" fill="${theme.muted}" font-size="11">Нет наблюдаемых связей</text>`:""}${!outs.length?`<text x="660" y="252" text-anchor="middle" fill="${theme.muted}" font-size="11">${boundary(n)?'Граница выгрузки':'Нет наблюдаемых связей'}</text>`:""}`;
  }

  function renderLegend() {
    if(state.color==="role") {
      $("legend").innerHTML=Object.values(roles).map(r=>`<span>${dot(r.color)}${r.label}</span>`).join("");
    } else {
      const context=state.scope==="all"?filtered:[byId.get(state.selected),...nodeConnections().map(e=>byId.get(e.neighbor))].filter(Boolean);
      const ids=[...new Set(context.map(n=>n.cluster))].sort((a,b)=>a-b);
      $("legend").innerHTML=ids.slice(0,10).map(id=>`<span>${dot(clusterColor(id))}Кластер ${id}</span>`).join("")+(ids.length>10?`<span>Ещё ${ids.length-10} · номер в карточке узла</span>`:"");
    }
  }

  function updateGraph(fit=false) {
    const n=byId.get(state.selected), all=state.scope==="all",table=state.view==="table";
    pressed("scope-node",!all);pressed("scope-all",all);pressed("view-diagram",!table);pressed("view-table",table);
    $("graph-heading").textContent=all?"Обзор сети":n?`Связи GID ${n.id}`:"Связи узла";
    $("graph-subtitle").textContent=all?"Отдельная раскладка для каждой компоненты":compactDiagram()?"Входящие сверху, исходящие снизу":"Входящие слева, исходящие справа";
    $("stage").hidden=table;$("connections-view").hidden=!table;
    // SVGElement не имеет HTML-свойства hidden: меняем именно атрибут.
    $("neighborhood").toggleAttribute("hidden",all);canvas.hidden=!all;$("zoom-controls").hidden=!all || !filtered.length;
    $("graph-empty").hidden=filtered.length>0;
    if(table)renderTable(); else if(all){resizeCanvas();if(fit)fitNetwork();else drawNetwork();}else renderNeighborhood();
    $("graph-caption").textContent=table?"Все наблюдаемые связи выбранного узла, без фильтрации соседей.":all?`${fmt.format(filtered.length)} узлов · ${fmt.format(visibleEdges.length)} связей между ними. Размер узла — приоритет.`:n?`${Math.min(neighborLimit(),n.inDegree)+Math.min(neighborLimit(),n.outDegree)} из ${n.inDegree+n.outDegree} связей · крупнейшие по сумме, без фильтрации соседей.`:"Выберите узел в очереди проверки.";
    renderLegend();
  }

  function selectNode(id,announce=true) {
    if(!byId.has(id))return;
    const active=document.activeElement,fromNode=active?.hasAttribute("data-node-id"),container=active?.closest("#node-list, #neighborhood, #connection-rows")?.id;
    let reset=false;
    if(!matches(byId.get(id))){filterIds.forEach(key=>$(key).value="all");filtered=ranked;reset=true;}
    state.selected=id;state.edgePage=0;
    state.page=Math.max(0,Math.floor(filtered.findIndex(n=>n.id===id)/PAGE));
    rebuildVisibleEdges();renderQueue();renderDetails();updateGraph(reset);
    if(fromNode){
      const replacement=container&&[...$(container).querySelectorAll("[data-node-id]")].find(el=>el.dataset.nodeId===id);
      const target=replacement || $("details-heading");target.setAttribute("tabindex",replacement?"0":"-1");target.focus({preventScroll:true});
    }
    if(announce)setMessage(`Выбран GID ${id}.${reset?" Фильтры сброшены: узел был вне выбранной области.":""}`);
  }

  function rebuildVisibleEdges() {
    const ids=new Set(filtered.map(n=>n.id));visibleEdges=data.edges.filter(e=>ids.has(e.s)&&ids.has(e.t));
    const boxes=new Map();
    for(const n of filtered){
      if(!boxes.has(n.component))boxes.set(n.component,{id:n.component,minX:n.x,maxX:n.x,minY:n.y,maxY:n.y});
      const b=boxes.get(n.component);b.minX=Math.min(b.minX,n.x);b.maxX=Math.max(b.maxX,n.x);b.minY=Math.min(b.minY,n.y);b.maxY=Math.max(b.maxY,n.y);
    }
    componentBoxes=[...boxes.values()];
  }
  function applyFilters() {
    filtered=ranked.filter(matches);state.page=0;state.edgePage=0;
    if(!filtered.some(n=>n.id===state.selected))state.selected=filtered[0]?.id || null;
    rebuildVisibleEdges();renderQueue();renderDetails();updateGraph(true);
    setMessage(filtered.length?`Найдено ${fmt.format(filtered.length)} узлов. Схема связей показывает всех соседей выбранного узла.`:"По выбранным фильтрам ничего не найдено. Измените фильтр или нажмите «Сбросить».");
  }
  function setScope(scope) {state.scope=scope;state.view="diagram";updateGraph(true);}
  function resizeCanvas() {
    const rect=$("stage").getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);
    canvasWidth=rect.width;canvasHeight=rect.height;
    canvas.width=Math.max(1,Math.round(rect.width*dpr));canvas.height=Math.max(1,Math.round(rect.height*dpr));ctx.setTransform(dpr,0,0,dpr,0,0);
  }
  function fitNetwork() {
    if(!filtered.length){drawNetwork();return;}
    const xs=filtered.map(n=>n.x),ys=filtered.map(n=>n.y);
    const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
    const scale=Math.min((canvasWidth-60)/(maxX-minX+40),(canvasHeight-60)/(maxY-minY+40),4);
    camera={scale,fitScale:scale,x:canvasWidth/2-(minX+maxX)/2*scale,y:canvasHeight/2-(minY+maxY)/2*scale};drawNetwork();
  }
  function drawArrow(a,b,selected) {
    const sx=a.x*camera.scale+camera.x,sy=a.y*camera.scale+camera.y;
    const tx=b.x*camera.scale+camera.x,ty=b.y*camera.scale+camera.y;
    if(Math.max(sx,tx)<0||Math.min(sx,tx)>canvasWidth||Math.max(sy,ty)<0||Math.min(sy,ty)>canvasHeight)return;
    ctx.strokeStyle=selected?theme.accent:theme.edge;ctx.globalAlpha=selected?.8:.25;ctx.lineWidth=selected?1.3:.65;
    ctx.beginPath();ctx.moveTo(sx,sy);ctx.lineTo(tx,ty);ctx.stroke();
    const dx=tx-sx,dy=ty-sy,len=Math.hypot(dx,dy);
    if(len<6)return;
    const ux=dx/len,uy=dy/len,offset=3+Math.min(4,b.priority*5),ex=tx-ux*offset,ey=ty-uy*offset,sz=selected?4:2.5;
    ctx.fillStyle=ctx.strokeStyle;ctx.beginPath();ctx.moveTo(ex,ey);ctx.lineTo(ex-ux*sz-uy*sz*.5,ey-uy*sz+ux*sz*.5);ctx.lineTo(ex-ux*sz+uy*sz*.5,ey-uy*sz-ux*sz*.5);ctx.closePath();ctx.fill();
  }
  function drawNetwork() {
    ctx.clearRect(0,0,canvasWidth,canvasHeight);
    ctx.globalAlpha=1;ctx.setLineDash([3,4]);ctx.lineWidth=1;ctx.font="10px Segoe UI, Arial";
    for(const b of componentBoxes){
      const x=(b.minX-20)*camera.scale+camera.x,y=(b.minY-20)*camera.scale+camera.y,w=(b.maxX-b.minX+40)*camera.scale,h=(b.maxY-b.minY+40)*camera.scale;
      ctx.strokeStyle=theme.line;ctx.strokeRect(x,y,w,h);ctx.fillStyle=theme.muted;
      ctx.fillText(w>100?`Компонента ${b.id}`:`№ ${b.id}`,x,y-7);
    }
    ctx.setLineDash([]);
    for(const e of visibleEdges)drawArrow(byId.get(e.s),byId.get(e.t),e.s===state.selected||e.t===state.selected);
    ctx.globalAlpha=1;
    for(const n of filtered){
      const x=n.x*camera.scale+camera.x,y=n.y*camera.scale+camera.y,r=Math.max(.8,Math.min(6,(3+n.priority*5)*Math.sqrt(camera.scale)));
      if(x<-10||x>canvasWidth+10||y<-10||y>canvasHeight+10)continue;
      ctx.fillStyle=color(n);ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.fill();
      if(n.id===state.selected){ctx.strokeStyle=theme.accent;ctx.lineWidth=1.5;ctx.beginPath();ctx.arc(x,y,r+3,0,Math.PI*2);ctx.stroke();}
      if(camera.scale>2||n.id===state.selected){ctx.font="10px Segoe UI, Arial";ctx.fillStyle=theme.ink;ctx.fillText(shortId(n.id),x+r+5,y+3);}
    }
    $("zoom-value").textContent=`${Math.round(camera.scale/camera.fitScale*100)}%`;
  }
  function zoom(factor,x=canvasWidth/2,y=canvasHeight/2) {
    const scale=Math.max(camera.fitScale*.4,Math.min(camera.fitScale*80,camera.scale*factor));
    const ratio=scale/camera.scale;camera.x=x-(x-camera.x)*ratio;camera.y=y-(y-camera.y)*ratio;camera.scale=scale;drawNetwork();
  }

  // Инициализация всех подписей и селектов — из фактической выгрузки.
  $("period").textContent=data.meta.period;
  $("source-notice").querySelector("strong").textContent=data.meta.demo?"Демонстрационные данные":generic?"Пользовательский набор":"Проверьте источник";
  $("source-text").textContent=data.meta.notice || (data.meta.demo?"Синтетический набор для проверки системы. Результаты не относятся к реальным людям и не подтверждают качество на исходной выгрузке.":"Результаты рассчитаны по локальной выгрузке. Происхождение и полноту данных необходимо проверить отдельно.");
  $("total-nodes").textContent=fmt.format(data.nodes.length);$("total-edges").textContent=fmt.format(data.edges.length);
  $("total-components").textContent=fmt.format(data.meta.components);$("total-amount").textContent=money(data.meta.total);
  $("seed-count").textContent=data.meta.seeds==null?"Seed не указаны":`${fmt.format(data.meta.seeds)} стартовых узлов · seed`;
  $("currency-caption").textContent=`${currency} за период выгрузки`;
  $("amount-column").textContent=`Сумма, ${currency}`;
  if(generic){$("gid").inputMode="text";$("boundary-help").textContent="Учитывайте полноту наблюдения. Граница обхода определяется только по явно заданным глубине и способу выгрузки. Неизвестные seed и глубина не считаются нулём.";}
  $("cluster-count").textContent=`${fmt.format(data.clusters.length)} кластеров внутри компонент`;
  Object.entries(roles).forEach(([value,r])=>option($("role-filter"),value,r.label));
  [...new Set(data.nodes.map(n=>n.component))].sort((a,b)=>a-b).forEach(value=>option($("component-filter"),value,`№ ${value} · ${fmt.format(data.nodes.filter(n=>n.component===value).length)} узлов`));
  data.clusters.forEach(c=>option($("cluster-filter"),c.id,`№ ${c.id} · ${fmt.format(c.size)} узлов`));
  [...new Set(data.nodes.map(n=>n.depth).filter(v=>v!=null))].sort((a,b)=>a-b).forEach(value=>option($("depth-filter"),value,`${value}-е колено`));
  if(data.nodes.some(n=>n.depth==null)) option($("depth-filter"),"unknown","Глубина неизвестна");
  $("role-glossary").innerHTML=Object.values(roles).map(r=>`<p>${dot(r.color)} <strong>${r.label}.</strong> ${r.text}</p>`).join("");
  $("search-form").addEventListener("submit",e=>{
    e.preventDefault();const input=$("gid").value.trim();
    if(!input || (!generic&&!/^-?\d+$/.test(input))){setMessage(generic?"Введите идентификатор из выгрузки.":"Введите целочисленный GID из выгрузки, например из очереди проверки.",true);return;}
    const id=generic?input:BigInt(input).toString();
    if(!byId.has(id)){setMessage(`GID ${input} не найден в этой выгрузке. Текущий узел сохранён.`,true);return;}
    selectNode(id);$("gid").value=id;
  });
  filterIds.forEach(id=>$(id).addEventListener("change",applyFilters));
  $("reset-filters").addEventListener("click",()=>{filterIds.forEach(id=>$(id).value="all");$("gid").value="";applyFilters();});
  document.addEventListener("click",e=>{const button=e.target.closest("[data-node-id]");if(button)selectNode(button.dataset.nodeId);});
  $("neighborhood").addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){const target=e.target.closest("[data-node-id]");if(target){e.preventDefault();selectNode(target.dataset.nodeId);}}});
  $("nodes-prev").addEventListener("click",()=>{state.page--;renderQueue();});$("nodes-next").addEventListener("click",()=>{state.page++;renderQueue();});
  $("edges-prev").addEventListener("click",()=>{state.edgePage--;renderTable();});$("edges-next").addEventListener("click",()=>{state.edgePage++;renderTable();});
  $("scope-node").addEventListener("click",()=>setScope("node"));$("scope-all").addEventListener("click",()=>setScope("all"));
  $("view-diagram").addEventListener("click",()=>{state.view="diagram";updateGraph();});
  $("view-table").addEventListener("click",()=>{state.scope="node";state.view="table";updateGraph();});
  $("mode").addEventListener("change",()=>{state.color=$("mode").value;updateGraph();});
  $("fit").addEventListener("click",fitNetwork);$("zoom-in").addEventListener("click",()=>zoom(1.4));$("zoom-out").addEventListener("click",()=>zoom(1/1.4));
  canvas.addEventListener("wheel",e=>{e.preventDefault();const r=canvas.getBoundingClientRect();zoom(e.deltaY<0?1.15:1/1.15,e.clientX-r.left,e.clientY-r.top);},{passive:false});
  canvas.addEventListener("keydown",e=>{
    if(["+","=","-","ArrowLeft","ArrowRight","ArrowUp","ArrowDown","Home"].includes(e.key))e.preventDefault();
    if(e.key==="+"||e.key==="=")zoom(1.4);else if(e.key==="-")zoom(1/1.4);else if(e.key==="Home")fitNetwork();
    else if(e.key.startsWith("Arrow")){camera.x+=e.key==="ArrowLeft"?35:e.key==="ArrowRight"?-35:0;camera.y+=e.key==="ArrowUp"?35:e.key==="ArrowDown"?-35:0;drawNetwork();}
  });
  let drag=null;
  canvas.addEventListener("pointerdown",e=>{drag={startX:e.clientX,startY:e.clientY,x:e.clientX,y:e.clientY};canvas.setPointerCapture(e.pointerId);});
  canvas.addEventListener("pointermove",e=>{if(drag){camera.x+=e.clientX-drag.x;camera.y+=e.clientY-drag.y;drag.x=e.clientX;drag.y=e.clientY;drawNetwork();}});
  canvas.addEventListener("pointerup",e=>{
    if(drag&&Math.hypot(e.clientX-drag.startX,e.clientY-drag.startY)<5){const rect=canvas.getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top;let best=null,distance=12;for(const n of filtered){const d=Math.hypot(n.x*camera.scale+camera.x-x,n.y*camera.scale+camera.y-y);if(d<distance){best=n;distance=d;}}if(best)selectNode(best.id);}
    drag=null;
  });
  canvas.addEventListener("pointercancel",()=>{drag=null;});
  new ResizeObserver(()=>{if(state.view==="diagram")updateGraph(state.scope==="all");}).observe($("stage"));
  $("help-open").addEventListener("click",()=>$("help-dialog").showModal());
  $("help-close").addEventListener("click",()=>$("help-dialog").close());
  // В справке одна кнопка: удерживаем Tab внутри окна, не теряя видимый фокус.
  $("help-dialog").addEventListener("keydown",e=>{if(e.key==="Tab"){e.preventDefault();$("help-close").focus();}});
  $("download").addEventListener("click",()=>{
    const name=$("export-file").value,csv=data.downloads[name];if(typeof csv!=="string")return;
    const url=URL.createObjectURL(new Blob(["\ufeff",csv],{type:"text/csv;charset=utf-8"}));
    const anchor=document.createElement("a");anchor.href=url;anchor.download=name;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);
    setMessage(`Подготовлен ${name}: полная выгрузка, фильтры экрана не применяются.`);
  });
  rebuildVisibleEdges();renderQueue();renderDetails();updateGraph();
  // В локальном мастере iframe имеет opaque origin: передаём только высоту,
  // без GID, сумм и других данных. Автономный просмотр остаётся независимым.
  if(window.parent!==window)new ResizeObserver(()=>{
    window.parent.postMessage({type:"potoki:height",height:Math.ceil(document.body.getBoundingClientRect().height)},"*");
  }).observe(document.body);
})();
