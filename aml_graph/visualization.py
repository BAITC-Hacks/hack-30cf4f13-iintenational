"""Самодостаточный Canvas-интерфейс без внешних CDN."""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pandas as pd


ROLE_COLORS = {
    "coordinator": "#e45756",
    "consolidator": "#f2a541",
    "distributor": "#7b61ff",
    "transit": "#25a18e",
    "terminal": "#3f88c5",
    "peripheral": "#8a94a6",
}


def _component_boxes(frame: pd.DataFrame) -> dict[int, tuple[float, float, float, float]]:
    component_sizes = frame.groupby("component_id").size().sort_values(ascending=False)
    boxes: dict[int, tuple[float, float, float, float]] = {}
    for index, component_id in enumerate(component_sizes.index):
        if index == 0:
            boxes[int(component_id)] = (100, 100, 2_200, 1_500)
        elif index == 1:
            boxes[int(component_id)] = (2_450, 100, 1_000, 1_000)
        else:
            offset = index - 2
            column = offset % 4
            row = offset // 4
            boxes[int(component_id)] = (2_450 + column * 570, 1_250 + row * 420, 500, 330)
    return boxes


def _positions(frame: pd.DataFrame) -> dict[int, tuple[float, float]]:
    boxes = _component_boxes(frame)
    positions: dict[int, tuple[float, float]] = {}
    for component_id, component in frame.groupby("component_id"):
        x0, y0, width, height = boxes[int(component_id)]
        for depth, group in component.sort_values(["cluster_id", "gid"]).groupby("depth", sort=True):
            ordered = group.sort_values(["cluster_id", "gid"])
            x = x0 + (int(depth) + 0.5) * width / 5
            for index, gid in enumerate(ordered["gid"]):
                y = y0 + (index + 1) * height / (len(ordered) + 1)
                positions[int(gid)] = (round(x, 2), round(y, 2))
    return positions


def write_graph_view(output_path: Path, graph: nx.DiGraph, frame: pd.DataFrame) -> None:
    positions = _positions(frame)
    records = frame.sort_values("gid").to_dict("records")
    nodes = []
    for row in records:
        gid = int(row["gid"])
        x, y = positions[gid]
        nodes.append(
            {
                "id": gid,
                "x": x,
                "y": y,
                "role": str(row["role"]),
                "roleScore": round(float(row["role_score"]), 4),
                "priority": round(float(row["priority_score"]), 4),
                "cluster": int(row["cluster_id"]),
                "component": int(row["component_id"]),
                "depth": int(row["depth"]),
                "inDegree": int(row["in_degree"]),
                "outDegree": int(row["out_degree"]),
                "sumIn": int(row["sum_in"]),
                "sumOut": int(row["sum_out"]),
                "evidence": str(row["evidence"]),
            }
        )
    edges = [
        {"s": int(source), "t": int(target), "v": int(data["sum_kzt"])}
        for source, target, data in graph.edges(data=True)
    ]
    payload = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, separators=(",", ":"))
    role_colors = json.dumps(ROLE_COLORS, ensure_ascii=False)

    document = r'''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src data:; font-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Граф потоков — объяснимый просмотр</title>
<style>
:root{color-scheme:dark;--bg:#0b1020;--panel:#121a2e;--line:#2a3654;--text:#edf2ff;--muted:#a8b3cc;--focus:#7dd3fc}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.4 system-ui,sans-serif;overflow:hidden}
header{height:72px;display:flex;align-items:center;gap:12px;padding:12px 18px;background:#10182b;border-bottom:1px solid var(--line)}
h1{font-size:18px;margin:0 16px 0 0;white-space:nowrap}.control{display:flex;align-items:center;gap:7px}input,select,button{background:#18223a;color:var(--text);border:1px solid #3b4a6c;border-radius:8px;padding:9px 11px}
input:focus,select:focus,button:focus{outline:2px solid var(--focus);outline-offset:2px}button{cursor:pointer}.hint{color:var(--muted);font-size:12px}
#wrap{display:grid;grid-template-columns:1fr 320px;height:calc(100vh - 72px)}#stage{position:relative;min-width:0}canvas{display:block;width:100%;height:100%;cursor:grab}canvas:active{cursor:grabbing}
aside{background:var(--panel);border-left:1px solid var(--line);padding:16px;overflow:auto}aside h2{font-size:16px;margin:0 0 12px}.card{padding:12px;border:1px solid var(--line);border-radius:10px;background:#0e1628}.row{display:flex;justify-content:space-between;gap:10px;margin:6px 0}.value{text-align:right}.muted{color:var(--muted)}
#legend{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:14px}.legend-item{display:flex;align-items:center;gap:7px}.dot{width:10px;height:10px;border-radius:50%}.notice{margin-top:16px;padding:10px;border-left:3px solid #f2a541;background:#1b2030;color:#dce5f7}
@media(max-width:800px){header{height:auto;flex-wrap:wrap}#wrap{grid-template-columns:1fr;height:calc(100vh - 130px)}aside{position:absolute;right:0;top:130px;width:290px;height:calc(100vh - 130px);background:rgba(18,26,46,.96)}}
</style>
</head>
<body>
<header>
  <h1>Граф денежных потоков</h1>
  <div class="control"><label for="gid">GID</label><input id="gid" inputmode="numeric" placeholder="Например, 82"><button id="find">Найти</button></div>
  <div class="control"><label for="mode">Цвет</label><select id="mode"><option value="role">Роль</option><option value="cluster">Кластер</option></select></div>
  <button id="reset">Весь граф</button>
  <span class="hint">Колесо — масштаб, перетаскивание — навигация, клик — карточка. Стрелки показывают направление.</span>
</header>
<div id="wrap">
  <main id="stage"><canvas id="graph" aria-label="Интерактивная схема направленного графа"></canvas></main>
  <aside aria-live="polite">
    <h2>Карточка узла</h2><div id="details" class="card muted">Введите gid или выберите узел на схеме.</div>
    <div id="legend"></div>
    <div class="notice">Роли и гипотезы — сигналы для аналитической проверки, а не утверждения о виновности. Узлы depth=4 находятся на границе выгрузки.</div>
  </aside>
</div>
<script>
const DATA=__PAYLOAD__;const ROLE_COLORS=__ROLE_COLORS__;
const canvas=document.getElementById('graph'),ctx=canvas.getContext('2d'),stage=document.getElementById('stage');
const nodeMap=new Map(DATA.nodes.map(n=>[n.id,n]));let selected=null,scale=.24,offsetX=20,offsetY=20,drag=null;
function clusterColor(id){return `hsl(${(id*137.508)%360} 68% 58%)`}
function color(n){return document.getElementById('mode').value==='role'?ROLE_COLORS[n.role]:clusterColor(n.cluster)}
function esc(value){return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function resize(){const dpr=window.devicePixelRatio||1;canvas.width=stage.clientWidth*dpr;canvas.height=stage.clientHeight*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);draw()}
function screen(n){return{x:n.x*scale+offsetX,y:n.y*scale+offsetY}}
function drawArrow(a,b,highlight){const p=screen(a),q=screen(b);ctx.strokeStyle=highlight?'rgba(125,211,252,.85)':'rgba(86,105,142,.22)';ctx.fillStyle=ctx.strokeStyle;ctx.lineWidth=highlight?1.8:.55;ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(q.x,q.y);ctx.stroke();const angle=Math.atan2(q.y-p.y,q.x-p.x),size=highlight?7:3;ctx.beginPath();ctx.moveTo(q.x,q.y);ctx.lineTo(q.x-size*Math.cos(angle-.45),q.y-size*Math.sin(angle-.45));ctx.lineTo(q.x-size*Math.cos(angle+.45),q.y-size*Math.sin(angle+.45));ctx.closePath();ctx.fill()}
function draw(){ctx.clearRect(0,0,stage.clientWidth,stage.clientHeight);const neighbors=new Set();if(selected){neighbors.add(selected.id);for(const e of DATA.edges){if(e.s===selected.id)neighbors.add(e.t);if(e.t===selected.id)neighbors.add(e.s)}}for(const e of DATA.edges){const a=nodeMap.get(e.s),b=nodeMap.get(e.t);drawArrow(a,b,selected&&(e.s===selected.id||e.t===selected.id))}for(const n of DATA.nodes){const p=screen(n),active=selected&&n.id===selected.id,near=neighbors.has(n.id);ctx.globalAlpha=selected&&!near?.22:1;ctx.fillStyle=color(n);ctx.beginPath();ctx.arc(p.x,p.y,active?7:Math.max(2.2,3.6*scale/.24),0,Math.PI*2);ctx.fill();if(active){ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.stroke();ctx.fillStyle='#fff';ctx.font='12px system-ui';ctx.fillText(String(n.id),p.x+10,p.y-10)}}ctx.globalAlpha=1}
function show(n){selected=n;const fmt=v=>new Intl.NumberFormat('ru-RU').format(v);document.getElementById('details').classList.remove('muted');document.getElementById('details').innerHTML=`<div class="row"><b>gid</b><span class="value">${n.id}</span></div><div class="row"><span>Роль</span><span class="value">${esc(n.role)} (${n.roleScore.toFixed(2)})</span></div><div class="row"><span>Приоритет</span><span class="value">${n.priority.toFixed(3)}</span></div><div class="row"><span>Кластер / компонента</span><span class="value">${n.cluster} / ${n.component}</span></div><div class="row"><span>Depth</span><span class="value">${n.depth}</span></div><div class="row"><span>In / out degree</span><span class="value">${n.inDegree} / ${n.outDegree}</span></div><div class="row"><span>Вход / выход</span><span class="value">${fmt(n.sumIn)} / ${fmt(n.sumOut)} KZT</span></div><p>${esc(n.evidence)}</p>`;draw()}
function findNode(){const id=Number(document.getElementById('gid').value),n=nodeMap.get(id);if(!n){document.getElementById('details').textContent='gid не найден в выгрузке.';return}scale=1.3;offsetX=stage.clientWidth/2-n.x*scale;offsetY=stage.clientHeight/2-n.y*scale;show(n)}
document.getElementById('find').onclick=findNode;document.getElementById('gid').addEventListener('keydown',e=>{if(e.key==='Enter')findNode()});document.getElementById('mode').onchange=draw;document.getElementById('reset').onclick=()=>{scale=.24;offsetX=20;offsetY=20;selected=null;draw()};
canvas.addEventListener('wheel',e=>{e.preventDefault();const rect=canvas.getBoundingClientRect(),mx=e.clientX-rect.left,my=e.clientY-rect.top,wx=(mx-offsetX)/scale,wy=(my-offsetY)/scale,f=e.deltaY<0?1.15:.87;scale=Math.max(.08,Math.min(5,scale*f));offsetX=mx-wx*scale;offsetY=my-wy*scale;draw()},{passive:false});
canvas.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY,ox:offsetX,oy:offsetY};canvas.setPointerCapture(e.pointerId)});canvas.addEventListener('pointermove',e=>{if(!drag)return;offsetX=drag.ox+e.clientX-drag.x;offsetY=drag.oy+e.clientY-drag.y;draw()});canvas.addEventListener('pointerup',e=>{if(!drag)return;const moved=Math.hypot(e.clientX-drag.x,e.clientY-drag.y);drag=null;if(moved<5){const rect=canvas.getBoundingClientRect(),x=(e.clientX-rect.left-offsetX)/scale,y=(e.clientY-rect.top-offsetY)/scale;let best=null,dist=12/scale;for(const n of DATA.nodes){const d=Math.hypot(n.x-x,n.y-y);if(d<dist){best=n;dist=d}}if(best)show(best)}});
const legend=document.getElementById('legend');legend.innerHTML=Object.entries(ROLE_COLORS).map(([r,c])=>`<div class="legend-item"><span class="dot" style="background:${c}"></span>${esc(r)}</div>`).join('');window.addEventListener('resize',resize);resize();
</script></body></html>'''
    document = document.replace("__PAYLOAD__", payload).replace("__ROLE_COLORS__", role_colors)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
