#!/usr/bin/env python3
"""Render a generic PLATEAU match-review GeoPackage with GSI background tiles."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import geopandas as gpd

TOOL_VERSION = "0.1.0"


def run(gpkg: Path, output: Path | None = None) -> Path:
    gpkg = gpkg.expanduser().resolve()
    if not gpkg.is_file():
        raise FileNotFoundError(gpkg)
    output = output.expanduser().resolve() if output else gpkg.with_name(gpkg.stem + ".html")
    buildings = json.loads(gpd.read_file(gpkg, layer="review_neighborhood_buildings").to_json(drop_id=True))
    points = json.loads(gpd.read_file(gpkg, layer="review_location_points").to_json(drop_id=True))
    with sqlite3.connect(gpkg) as connection:
        connection.row_factory = sqlite3.Row
        audit = [dict(row) for row in connection.execute(
            "SELECT * FROM review_audit ORDER BY machine_status DESC, entity_name"
        )]
    template = r'''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PLATEAU spatial match review</title><style>
html,body{height:100%;margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#17202a}#app{display:grid;grid-template-columns:400px 1fr;height:100%}#side{padding:14px;overflow:auto;background:#fafafa;border-right:1px solid #bbb}#mapbox{position:relative;height:100%;background:#eef2f3}#map{width:100%;height:100%}select,button,textarea{width:100%;box-sizing:border-box;margin:5px 0;padding:8px}.status{padding:7px;background:#e8f4ea;border-radius:5px}.candidate{background:#fff;border:1px solid #bbb;border-radius:6px;padding:7px;margin:7px 0;cursor:pointer}.candidate.selected{border:3px solid #d73027}.meta{font-size:12px;color:#555;line-height:1.45}image.tile{opacity:.82}path.neighborhood{fill:#bdbdbd;fill-opacity:.18;stroke:#4d4d4d;stroke-width:1.2;vector-effect:non-scaling-stroke;cursor:pointer}path.machine{fill:#66bd63;fill-opacity:.42;stroke:#1a9850;stroke-width:3}path.selected{fill:#d73027;fill-opacity:.5;stroke:#a50026;stroke-width:4}circle.anchor{fill:#d73027;stroke:#fff;stroke-width:3;vector-effect:non-scaling-stroke}.attribution{position:absolute;right:8px;bottom:5px;background:#ffffffe8;padding:3px 6px;font-size:11px}@media(max-width:700px){#app{grid-template-columns:1fr;grid-template-rows:52% 48%}}
</style></head><body><div id="app"><div id="side"><h2>PLATEAU照合レビュー</h2><div id="health" class="status"></div><label><input id="background" type="checkbox" checked> 地理院地図</label><select id="entity"></select><div id="summary"></div><div id="cards"></div><textarea id="notes" placeholder="判断根拠・注記"></textarea><button data-action="auto_match_human_confirmed">自動照合でOK</button><button data-action="location_guided_human_confirmed">選択Buildingを確定</button><button data-action="multiple_buildings_human_selected">選択した複数棟を確定</button><button data-action="auto_candidates_rejected_location_guided_selected">自動候補を却下して選択を採用</button><button data-action="plateau_footprint_absent">PLATEAU footprintなし</button><button data-action="location_requires_review">位置情報を要確認</button><button data-action="deferred">保留</button><button id="download">Audit CSVをダウンロード</button></div><div id="mapbox"><svg id="map" viewBox="0 0 1000 800"></svg><div class="attribution"><a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank">出典：国土地理院ウェブサイト（地理院タイル）</a></div></div></div><script>
'use strict';const buildings=__BUILDINGS__,points=__POINTS__,audit=__AUDIT__,decisions={},chosen=new Set(),NS='http://www.w3.org/2000/svg',svg=document.getElementById('map'),selector=document.getElementById('entity');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));document.getElementById('health').textContent=`全対象 ${audit.length}件／周辺Building ${buildings.features.length}件`;
audit.forEach(a=>{const o=document.createElement('option');o.value=a.entity_id;o.textContent=`${a.machine_status==='confirmed'?'自動確定':'未確定'}｜${a.entity_name}`;selector.appendChild(o)});function rings(g){if(!g)return[];if(g.type==='Polygon')return g.coordinates;if(g.type==='MultiPolygon')return g.coordinates.flat();return[]}function world(c,z){const n=256*2**z,s=Math.sin(Math.max(-85.0511,Math.min(85.0511,c[1]))*Math.PI/180);return[(c[0]+180)/360*n,(.5-Math.log((1+s)/(1-s))/(4*Math.PI))*n]}
function render(id){chosen.clear();svg.replaceChildren();document.getElementById('cards').replaceChildren();const a=audit.find(x=>x.entity_id===id),fs=buildings.features.filter(f=>f.properties.entity_id===id),pf=points.features.find(f=>f.properties.entity_id===id),coords=fs.flatMap(f=>rings(f.geometry).flat());if(pf)coords.push(pf.geometry.coordinates);document.getElementById('summary').innerHTML=`<b>${esc(a.entity_name)}</b><div class="meta">${esc(a.address)}<br>machine=${esc(a.machine_status)}<br>${esc(a.machine_match_methods)}<br>周辺Building=${a.neighborhood_building_count}</div>`;if(!coords.length)return;let z=18,p=coords.map(c=>world(c,z));while(z>5&&(Math.max(...p.map(c=>c[0]))-Math.min(...p.map(c=>c[0]))>820||Math.max(...p.map(c=>c[1]))-Math.min(...p.map(c=>c[1]))>620)){z--;p=coords.map(c=>world(c,z))}const cx=(Math.min(...p.map(c=>c[0]))+Math.max(...p.map(c=>c[0])))/2,cy=(Math.min(...p.map(c=>c[1]))+Math.max(...p.map(c=>c[1])))/2,ox=cx-500,oy=cy-400,xy=c=>{const q=world(c,z);return[q[0]-ox,q[1]-oy]};if(document.getElementById('background').checked){for(let x=Math.floor(ox/256);x<=Math.floor((ox+1000)/256);x++)for(let y=Math.floor(oy/256);y<=Math.floor((oy+800)/256);y++){const im=document.createElementNS(NS,'image');im.setAttribute('href',`https://cyberjapandata.gsi.go.jp/xyz/std/${z}/${x}/${y}.png`);im.setAttribute('x',x*256-ox);im.setAttribute('y',y*256-oy);im.setAttribute('width',256);im.setAttribute('height',256);im.setAttribute('class','tile');svg.appendChild(im)}}fs.forEach(f=>{const v=f.properties,path=document.createElementNS(NS,'path');let d='';rings(f.geometry).forEach(r=>{r.forEach((c,i)=>{const q=xy(c);d+=`${i?'L':'M'}${q[0]},${q[1]}`});d+='Z'});path.setAttribute('d',d);path.dataset.id=v.building_gml_id;path.setAttribute('class',`neighborhood ${v.machine_selected?'machine':''}`);path.onclick=()=>toggle(v.building_gml_id);svg.appendChild(path);const card=document.createElement('div');card.className='candidate';card.dataset.id=v.building_gml_id;card.innerHTML=`<b>${esc(v.building_name||'(名称なし)')}</b><div class="meta">${esc(v.building_gml_id)}<br>${esc(v.building_address)}<br>${v.distance_m}m ${v.machine_selected?'／自動選択':''}</div>`;card.onclick=()=>toggle(v.building_gml_id);document.getElementById('cards').appendChild(card)});if(pf){const q=xy(pf.geometry.coordinates),c=document.createElementNS(NS,'circle');c.setAttribute('cx',q[0]);c.setAttribute('cy',q[1]);c.setAttribute('r',8);c.setAttribute('class','anchor');svg.appendChild(c)}}
function toggle(id){chosen.has(id)?chosen.delete(id):chosen.add(id);document.querySelectorAll('[data-id]').forEach(x=>x.classList.toggle('selected',chosen.has(x.dataset.id)))}function decide(type){const a=audit.find(x=>x.entity_id===selector.value),selected=[...chosen],machine=(a.machine_building_ids||'').split(';').filter(Boolean);if(type==='auto_match_human_confirmed'&&!machine.length)return alert('自動確定Buildingがありません');if(type==='multiple_buildings_human_selected'&&selected.length<2)return alert('複数のBuildingを選択してください');if(['location_guided_human_confirmed','auto_candidates_rejected_location_guided_selected'].includes(type)&&!selected.length)return alert('Buildingを選択してください');decisions[a.entity_id]={entity_id:a.entity_id,entity_name:a.entity_name,machine_status:a.machine_status,machine_building_ids:a.machine_building_ids,machine_match_methods:a.machine_match_methods,human_status:type==='deferred'?'deferred':type.includes('requires_review')?'needs_review':type.includes('absent')?'no_plateau_footprint':'confirmed',human_decision_type:type,human_selected_building_ids:type==='auto_match_human_confirmed'?a.machine_building_ids:selected.join(';'),human_rejected_building_ids:type.startsWith('auto_candidates_rejected')?a.machine_building_ids:'',reviewer:'',reviewed_at:new Date().toISOString(),notes:document.getElementById('notes').value};alert('Audit判断を保存しました')}
document.querySelectorAll('button[data-action]').forEach(b=>b.onclick=()=>decide(b.dataset.action));document.getElementById('download').onclick=()=>{const cols=['entity_id','entity_name','machine_status','machine_building_ids','machine_match_methods','human_status','human_decision_type','human_selected_building_ids','human_rejected_building_ids','reviewer','reviewed_at','notes'],q=v=>'"'+String(v??'').replaceAll('"','""')+'"',csv=[cols.join(','),...Object.values(decisions).map(r=>cols.map(c=>q(r[c])).join(','))].join('\n'),a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['\ufeff'+csv],{type:'text/csv'}));a.download='plateau_human_review_audit.csv';a.click()};selector.onchange=()=>render(selector.value);document.getElementById('background').onchange=()=>render(selector.value);if(audit.length)render(audit[0].entity_id);
</script></body></html>'''
    html = template.replace("__BUILDINGS__", json.dumps(buildings, ensure_ascii=False)).replace(
        "__POINTS__", json.dumps(points, ensure_ascii=False)
    ).replace("__AUDIT__", json.dumps(audit, ensure_ascii=False))
    output.write_text(html, encoding="utf-8")
    return output


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Render a PLATEAU human-review package")
    result.add_argument("gpkg", type=Path)
    result.add_argument("--output", type=Path)
    result.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION}")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        print(run(args.gpkg, args.output))
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
