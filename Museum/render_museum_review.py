#!/usr/bin/env python3
"""Render Museum review layers as a dependency-free selectable SVG map."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import geopandas as gpd

TOOL_VERSION = "0.3.0"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Render Museum PLATEAU review candidates")
    result.add_argument("gpkg", type=Path)
    result.add_argument("--output", type=Path, default=None)
    result.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION}")
    return result


def run(gpkg: Path, output: Path | None = None) -> Path:
    gpkg = gpkg.expanduser().resolve()
    if not gpkg.is_file():
        raise FileNotFoundError(f"Museum GeoPackage not found: {gpkg}")
    output = output.expanduser().resolve() if output else gpkg.with_name(gpkg.stem + "_review.html")
    candidates = gpd.read_file(gpkg, layer="museum_manual_review_buildings")
    with sqlite3.connect(gpkg) as connection:
        connection.row_factory = sqlite3.Row
        queue = [dict(row) for row in connection.execute(
            "SELECT * FROM museum_manual_review_queue ORDER BY review_priority, museum_name"
        )]
    data_json = json.dumps(json.loads(candidates.to_json(drop_id=True)), ensure_ascii=False)
    queue_json = json.dumps(queue, ensure_ascii=False)
    template = r'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Museum PLATEAU building review</title>
<style>
html,body{height:100%;margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#17202a}
#app{display:grid;grid-template-columns:380px 1fr;height:100%}#side{padding:14px;overflow:auto;background:#fafafa;border-right:1px solid #ccc}
#mapbox{height:100%;background:#eef2f3;display:flex;align-items:center;justify-content:center}#map{width:100%;height:100%}
select,button,textarea{width:100%;box-sizing:border-box;margin:5px 0;padding:8px}.candidate{background:white;border:1px solid #bbb;border-radius:6px;padding:8px;margin:8px 0;cursor:pointer}.candidate:hover{background:#eef6ff}.selected{border:3px solid #d73027}.meta{font-size:12px;color:#555;line-height:1.45}.status{padding:7px;background:#e8f4ea;border-radius:5px;margin-bottom:8px}
image.gsi-tile{opacity:.82}path.building{fill:#67a9cf;fill-opacity:.35;stroke:#004c99;stroke-width:2;vector-effect:non-scaling-stroke;cursor:pointer}path.building:hover{fill:#fdae61}path.chosen{fill:#d73027;fill-opacity:.5;stroke:#a50026;stroke-width:4}circle.osm{fill:#d73027;stroke:white;stroke-width:3;vector-effect:non-scaling-stroke}.attribution{position:absolute;right:8px;bottom:5px;background:rgba(255,255,255,.88);padding:3px 6px;font-size:11px}#mapbox{position:relative}#empty{padding:30px;font-size:18px;color:#555}
@media(max-width:700px){#app{grid-template-columns:1fr;grid-template-rows:50% 50%}#side{border-right:0;border-bottom:1px solid #ccc}}
</style></head><body><div id="app"><div id="side"><h2>博物館建物レビュー</h2><div id="health" class="status"></div><label><input id="background" type="checkbox" checked> 地理院地図を背景表示</label><select id="facility"></select><div id="summary"></div><div id="cards"></div><textarea id="notes" placeholder="判断根拠・注記"></textarea><button id="confirm">選択建物を確定</button><button id="reject">この施設を保留</button><button id="download">判断CSVをダウンロード</button></div><div id="mapbox"><svg id="map" viewBox="0 0 1000 800" preserveAspectRatio="xMidYMid meet"></svg><div id="empty" hidden></div><div class="attribution"><a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">出典：国土地理院ウェブサイト（地理院タイル）</a></div></div></div>
<script>
'use strict';
const data=__DATA__, queue=__QUEUE__, decisions={}; let selectedBuilding=null;
const facility=document.getElementById('facility'), svg=document.getElementById('map'), NS='http://www.w3.org/2000/svg';
const escapeHtml=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
document.getElementById('health').textContent=`施設 ${queue.length}件／候補ポリゴン ${data.features.length}件（オフライン表示）`;
queue.forEach(q=>{const o=document.createElement('option');o.value=q.museum_id;o.textContent=`${q.review_priority}｜${q.museum_name}｜${q.review_category}`;facility.appendChild(o)});
function rings(g){if(!g)return[];if(g.type==='Polygon')return g.coordinates;if(g.type==='MultiPolygon')return g.coordinates.flat();return[]}
function world(c,z){const n=256*Math.pow(2,z),lon=c[0],lat=Math.max(-85.05112878,Math.min(85.05112878,c[1])),s=Math.sin(lat*Math.PI/180);return[(lon+180)/360*n,(.5-Math.log((1+s)/(1-s))/(4*Math.PI))*n]}
function show(id){
 selectedBuilding=null;svg.replaceChildren();document.getElementById('cards').replaceChildren();const q=queue.find(x=>x.museum_id===id);if(!q)return;
 const fs=data.features.filter(f=>f.properties.museum_id===id),coords=fs.flatMap(f=>rings(f.geometry).flat()),lon=Number(q.osm_longitude),lat=Number(q.osm_latitude);
 if(Number.isFinite(lon)&&Number.isFinite(lat))coords.push([lon,lat]);
 document.getElementById('summary').innerHTML=`<b>${escapeHtml(q.museum_name)}</b><div class="meta">${escapeHtml(q.review_category)} / candidates=${q.candidate_building_count}<br>${q.osm_url?`<a href="${escapeHtml(q.osm_url)}" target="_blank" rel="noopener">OSM objectを開く</a>`:''}</div>`;
 if(!coords.length){document.getElementById('empty').hidden=false;document.getElementById('empty').textContent='この施設には表示可能な候補ポリゴンがありません';return}document.getElementById('empty').hidden=true;
 let zoom=18,projected=coords.map(c=>world(c,zoom));while(zoom>5&&(Math.max(...projected.map(c=>c[0]))-Math.min(...projected.map(c=>c[0]))>820||Math.max(...projected.map(c=>c[1]))-Math.min(...projected.map(c=>c[1]))>620)){zoom--;projected=coords.map(c=>world(c,zoom))}const xs=projected.map(c=>c[0]),ys=projected.map(c=>c[1]),cx=(Math.min(...xs)+Math.max(...xs))/2,cy=(Math.min(...ys)+Math.max(...ys))/2,ox=cx-500,oy=cy-400,xy=c=>{const p=world(c,zoom);return[p[0]-ox,p[1]-oy]};
 if(document.getElementById('background').checked){const firstX=Math.floor(ox/256),lastX=Math.floor((ox+1000)/256),firstY=Math.floor(oy/256),lastY=Math.floor((oy+800)/256),limit=Math.pow(2,zoom);for(let tx=firstX;tx<=lastX;tx++)for(let ty=firstY;ty<=lastY;ty++){if(tx<0||ty<0||tx>=limit||ty>=limit)continue;const image=document.createElementNS(NS,'image');image.setAttribute('href',`https://cyberjapandata.gsi.go.jp/xyz/std/${zoom}/${tx}/${ty}.png`);image.setAttribute('x',tx*256-ox);image.setAttribute('y',ty*256-oy);image.setAttribute('width',256);image.setAttribute('height',256);image.setAttribute('class','gsi-tile');svg.appendChild(image)}}
 fs.forEach(f=>{const p=f.properties,path=document.createElementNS(NS,'path');let d='';rings(f.geometry).forEach(r=>{r.forEach((c,i)=>{const [x,y]=xy(c);d+=`${i?'L':'M'}${x.toFixed(2)},${y.toFixed(2)}`});d+='Z'});path.setAttribute('d',d);path.setAttribute('class','building');path.dataset.id=p.building_gml_id;path.onclick=()=>select(p.building_gml_id);const title=document.createElementNS(NS,'title');title.textContent=`${p.candidate_rank}. ${p.building_name||'(名称なし)'} / ${p.building_gml_id}`;path.appendChild(title);svg.appendChild(path);
  const card=document.createElement('div');card.className='candidate';card.dataset.id=p.building_gml_id;card.innerHTML=`<b>${p.candidate_rank}. ${escapeHtml(p.building_name||'(名称なし)')}</b><div class="meta">${escapeHtml(p.building_gml_id)}<br>${escapeHtml(p.building_address||'')}<br>距離 ${p.distance_m} m / 用途 ${escapeHtml(p.detailed_usage_label||p.detailed_usage_code||'')}</div>`;card.onclick=()=>select(p.building_gml_id);document.getElementById('cards').appendChild(card)});
 if(Number.isFinite(lon)&&Number.isFinite(lat)){const [x,y]=xy([lon,lat]),c=document.createElementNS(NS,'circle');c.setAttribute('cx',x);c.setAttribute('cy',y);c.setAttribute('r',8);c.setAttribute('class','osm');const t=document.createElementNS(NS,'title');t.textContent='OSM位置';c.appendChild(t);svg.appendChild(c)}
}
function select(id){selectedBuilding=id;document.querySelectorAll('.candidate').forEach(x=>x.classList.toggle('selected',x.dataset.id===id));svg.querySelectorAll('path.building').forEach(x=>x.classList.toggle('chosen',x.dataset.id===id))}
document.getElementById('confirm').onclick=()=>{if(!selectedBuilding)return alert('建物を選択してください');decisions[facility.value]={museum_id:facility.value,building_gml_id:selectedBuilding,action:'confirm',building_role:'primary',notes:document.getElementById('notes').value,reviewer:'',reviewed_at:new Date().toISOString()};alert('判断をブラウザ内に保存しました')};
document.getElementById('reject').onclick=()=>{decisions[facility.value]={museum_id:facility.value,building_gml_id:'',action:'defer',building_role:'',notes:document.getElementById('notes').value,reviewer:'',reviewed_at:new Date().toISOString()};alert('保留をブラウザ内に保存しました')};
document.getElementById('download').onclick=()=>{const cols=['museum_id','building_gml_id','action','building_role','notes','reviewer','reviewed_at'],quote=v=>'"'+String(v??'').replaceAll('"','""')+'"',csv=[cols.join(','),...Object.values(decisions).map(r=>cols.map(c=>quote(r[c])).join(','))].join('\n'),a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['\ufeff'+csv],{type:'text/csv'}));a.download='museum_building_overrides.csv';a.click()};
facility.onchange=()=>show(facility.value);if(queue.length)show(queue[0].museum_id);else{document.getElementById('empty').hidden=false;document.getElementById('empty').textContent='レビューキューが空です。GPKG内のmuseum_manual_review_queueを確認してください'}
document.getElementById('background').onchange=()=>show(facility.value);
</script></body></html>'''
    html = template.replace("__DATA__", data_json).replace("__QUEUE__", queue_json)
    output.write_text(html, encoding="utf-8")
    return output


def main() -> int:
    args = parser().parse_args()
    try:
        print(run(args.gpkg, args.output))
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
