#!/usr/bin/env python3
"""Regenerate the Lumina SVG, native, React, sprite and catalog exports.

Usage: python3 source/build.py
Requires only Python 3.9+ standard library. Geometry lives in icons.json.
Raster previews are generated separately by render.py.
"""
from pathlib import Path
import json
import re
import html
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).resolve().parent / "icons.json"
DATA = json.loads(SOURCE.read_text())
ICONS = DATA["icons"]

def pascal(identifier):
    return "".join(p[:1].upper() + p[1:] for p in identifier.split("-"))

def camel(identifier):
    p = pascal(identifier)
    return p[0].lower() + p[1:]

def svg(body, label, native=False):
    attrs = "" if native else f' role="img" aria-label="{html.escape(label, quote=True)}"'
    color = "#000000" if native else "currentColor"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
            f'viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.75" '
            f'stroke-linecap="round" stroke-linejoin="round"{attrs}>\n  '
            f'{body.replace("currentColor", color)}\n</svg>\n')

def save(path, text):
    path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)

def jsx(body):
    for attribute in ["fill-rule", "clip-rule", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "stroke-dashoffset", "fill-opacity", "stroke-opacity"]:
        body = body.replace(attribute + "=", camel(attribute) + "=")
    return body

def validate():
    assert len({i["id"] for i in ICONS}) == len(ICONS), "Duplicate icon IDs"
    allowed = {"svg", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "g"}
    for item in ICONS:
        assert re.fullmatch(r"[a-z][a-z0-9-]+", item["id"])
        for variant in ["outline", "solid"]:
            if variant not in item:
                continue
            body = item[variant]
            tree = ET.fromstring(svg(body, item["label"]))
            for node in tree.iter():
                assert node.tag.rsplit("}", 1)[-1] in allowed, (item["id"], node.tag)
                assert all(not key.startswith("on") for key in node.attrib), item["id"]
                assert all("url(" not in str(value) for value in node.attrib.values()), item["id"]
            assert "href=" not in body, item["id"]

validate()
manifest_icons = []
symbols = []
artwork = []
native_cases = []
solid_native = []
catalog_items = []
for item in ICONS:
    slug = item["id"]
    native = item.get("nativeName", "Lumina" + pascal(slug))
    has_solid = "solid" in item
    swift_identifier = f'`{camel(slug)}`' if slug == "repeat" else camel(slug)
    native_cases.append(f'    case {swift_identifier} = "{native}"')
    variants = {}
    for variant in ["outline", "solid"]:
        if variant not in item:
            continue
        body = item[variant]
        if variant == "outline":
            master = f"svg/{slug}.svg"
            asset = native
        else:
            master = f'svg/{"selected" if item.get("legacySelected") else "solid"}/{slug}.svg'
            asset = native + ("Selected" if item.get("legacySelected") else "Solid")
            solid_native.append(f'        case .{camel(slug)}: return "{asset}"')
        raw = svg(body, item["label"])
        save(master, raw)
        image_set = f"native/LuminaMediaIcons.xcassets/{asset}.imageset"
        save(f"{image_set}/{asset}.svg", svg(body, item["label"], native=True))
        save(f"{image_set}/Contents.json", json.dumps({
            "images": [{"filename": asset + ".svg", "idiom": "universal"}],
            "info": {"author": "xcode", "version": 1},
            "properties": {"preserves-vector-representation": True, "template-rendering-intent": "template"}
        }, indent=2) + "\n")
        variants[variant] = {"svg": master, "native": asset}
        symbol_id = f'lumina-{slug}{"-solid" if variant == "solid" else ""}'
        symbol = f'<symbol id="{symbol_id}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">\n{body}\n</symbol>'
        symbols.append(symbol)
        if variant == "solid" and item.get("legacySelected"):
            symbols.append(symbol.replace(f'id="{symbol_id}"', f'id="lumina-{slug}-selected"'))
    entry = {"id": slug, "label": item["label"], "category": item["category"], "meaning": item["meaning"], "hasSolid": has_solid, "variants": variants}
    manifest_icons.append(entry)
    artwork.append(f'  "{slug}": {{\n    outline: (<>{jsx(item["outline"])}</>),\n    solid: ' + (f'(<>{jsx(item["solid"])}</>)' if has_solid else "null") + "\n  }")
    catalog_items.append({**entry, "outline": svg(item["outline"], item["label"]), "solid": svg(item["solid"], item["label"]) if has_solid else None})

save("native/LuminaMediaIcons.xcassets/Contents.json", json.dumps({"info": {"author": "xcode", "version": 1}}, indent=2) + "\n")
save("manifest.json", json.dumps({
    "name": "Lumina Media Icons", "version": "1.0.0", "designCount": len(ICONS),
    "solidCount": sum("solid" in i for i in ICONS), "viewBox": "0 0 24 24",
    "strokeWidth": 1.75, "categories": DATA["categories"], "icons": manifest_icons
}, indent=2) + "\n")
save("web/lumina-media-sprite.svg", '<svg xmlns="http://www.w3.org/2000/svg">\n' + "\n".join(symbols) + "\n</svg>\n")

tsx = '''// Generated from source/icons.json. Edit the geometry there and run source/build.py.
import * as React from "react";
import type { SVGProps } from "react";

const artwork = {
''' + ",\n".join(artwork) + '''
} as const;

export type LuminaIconName = keyof typeof artwork;
export type LuminaIconVariant = "outline" | "solid";
export type LuminaIconProps = Omit<SVGProps<SVGSVGElement>,
  "children" | "name" | "width" | "height" | "aria-label" | "aria-hidden" | "role"
> & {
  name: LuminaIconName;
  variant?: LuminaIconVariant;
  size?: number;
  /** Omit for decorative artwork inside an already labeled control. */
  label?: string;
};

export const LuminaIcon = React.forwardRef<SVGSVGElement, LuminaIconProps>(function LuminaIcon(
  {name, variant = "outline", size = 24, label, ...props}, ref
) {
  const icon = artwork[name];
  const body = variant === "solid" ? (icon.solid ?? icon.outline) : icon.outline;
  return (
    <svg {...props} ref={ref} xmlns="http://www.w3.org/2000/svg" width={size} height={size}
      viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}
      strokeLinecap="round" strokeLinejoin="round" focusable="false"
      role={label ? "img" : undefined} aria-label={label}
      aria-hidden={label ? undefined : true}>
      {body}
    </svg>
  );
});
LuminaIcon.displayName = "LuminaIcon";

export function luminaIconHasSolid(name: LuminaIconName): boolean {
  return artwork[name].solid !== null;
}
'''
save("web/LuminaIcon.tsx", tsx)

swift = '''// Generated from source/icons.json. Edit the geometry there and run source/build.py.
import SwiftUI

enum LuminaIconVariant {
    case outline, solid
}

enum LuminaGlyph: String, CaseIterable {
''' + "\n".join(native_cases) + '''

    var solidAssetName: String? {
        switch self {
''' + "\n".join(solid_native) + '''
        default: return nil
        }
    }

    func assetName(for variant: LuminaIconVariant) -> String {
        switch variant {
        case .outline: return rawValue
        case .solid: return solidAssetName ?? rawValue
        }
    }
}

/// Decorative artwork: supply the action or destination label on its containing control.
struct LuminaIcon: View {
    let glyph: LuminaGlyph
    var variant: LuminaIconVariant = .outline
    var size: CGFloat = 24

    var body: some View {
        Image(glyph.assetName(for: variant))
            .renderingMode(.template)
            .resizable()
            .scaledToFit()
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}
'''
save("native/LuminaIcon.swift", swift)

# One local file, no framework, network request, external typeface or build step.
catalog = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lumina — media icons</title>
<style>
:root{color-scheme:light;--paper:#f4f0e8;--card:#fcfaf6;--ink:#15130f;--muted:#706b64;--rule:#ded7cb;--accent:#dbb688;--selected:#e9ddca}
body.dark{color-scheme:dark;--paper:#151517;--card:#1e1e21;--ink:#efe9dd;--muted:#aca69e;--rule:#363438;--selected:#40352a}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 system-ui,sans-serif;transition:background .15s,color .15s}
header,main,footer{max-width:1440px;margin:auto;padding:32px 40px}header{padding-bottom:12px}h1{font:normal 64px/1 Georgia,serif;margin:0 0 16px;letter-spacing:-3px}h1 span{font:12px/1.5 system-ui,sans-serif;letter-spacing:2px;text-transform:uppercase;display:inline-block;margin-left:28px;vertical-align:middle}.intro{max-width:760px;color:var(--muted);margin:0 0 24px}.controls{display:flex;flex-wrap:wrap;align-items:end;gap:12px;border-block:1px solid var(--rule);padding:18px 0}.field{display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--muted)}input,select,button{font:inherit;color:var(--ink);border:1px solid var(--rule);background:var(--card);border-radius:9px;min-height:42px;padding:9px 12px}input{min-width:260px}button{cursor:pointer}button:hover{border-color:var(--muted)}button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid var(--accent);outline-offset:3px}#summary{color:var(--muted);font-size:13px}.category{margin:14px 0 30px}.category h2{font-size:13px;text-transform:uppercase;letter-spacing:2px;font-weight:500;margin:0 0 14px}.category h2 span{color:var(--muted);letter-spacing:0;margin-left:10px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(142px,1fr));gap:10px}.tile{position:relative;min-height:142px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;padding:18px 8px 14px;background:var(--card);text-align:center}.tile svg{display:block;width:var(--icon-size,32px);height:var(--icon-size,32px)}.tile span{font-size:11px;color:var(--muted);line-height:1.2;overflow-wrap:anywhere}.tile .dot{position:absolute;top:11px;right:11px;width:4px;height:4px;border-radius:50%;background:var(--accent)}.tile[aria-pressed=true]{background:var(--selected);border-color:var(--accent)}.inspector{position:sticky;bottom:18px;display:none;gap:18px;align-items:center;background:var(--card);border:1px solid var(--rule);border-radius:16px;padding:20px;box-shadow:0 10px 35px #0002;margin-top:26px}.inspector.open{display:flex}.sample{flex:none;padding:8px}.sample svg{width:48px;height:48px;display:block}.details{flex:1;min-width:0}.details b{display:block;font-size:15px}.details p{font-size:12px;color:var(--muted);margin:4px 0}.actions{display:flex;gap:8px;flex-wrap:wrap}#status{min-height:22px;color:var(--muted);font-size:12px}footer{border-top:1px solid var(--rule);color:var(--muted);font-size:12px;padding-top:20px}dialog{width:min(740px,95vw);background:var(--card);color:var(--ink);border:1px solid var(--rule);border-radius:14px;padding:24px}dialog::backdrop{background:#0008}dialog textarea{width:100%;height:260px;background:var(--paper);color:var(--ink);border:1px solid var(--rule);padding:12px;font:12px/1.5 monospace}dialog h2{margin-top:0;font-size:18px}.empty{color:var(--muted)}@media(max-width:600px){header,main,footer{padding-left:18px;padding-right:18px}h1{font-size:52px}h1 span{display:block;margin:16px 0;letter-spacing:1.5px}.field{flex:1}input,select{width:100%;min-width:0}.grid{grid-template-columns:repeat(3,minmax(0,1fr))}.tile{min-height:125px}.inspector{flex-wrap:wrap;padding:14px}.actions{width:100%}}
@media(prefers-reduced-motion:reduce){body{transition:none}}
</style></head><body>
<header><h1>lumina<span>Media icons / 01</span></h1><p class="intro">104 original icons for the whole media experience. Rounded geometry, clear silhouettes, and 40 solid alternates. Select an icon to copy its name or actual SVG source.</p>
<div class="controls"><label class="field">Search<input id="search" type="search" placeholder="Play, popcorn, backup…"></label><label class="field">Family<select id="category"><option value="">All families</option></select></label><label class="field">Style<select id="variant"><option value="outline">Outline</option><option value="solid">Solid where available</option></select></label><label class="field">Size<select id="size"><option>20</option><option>24</option><option>28</option><option selected>32</option><option>48</option></select></label><button id="theme" type="button" aria-pressed="false">Dark appearance</button></div><p id="summary" aria-live="polite"></p></header>
<main><div id="catalog"></div><aside class="inspector" id="inspector" aria-label="Selected icon"><div class="sample" id="sample"></div><div class="details"><b id="icon-name"></b><p id="meaning"></p><p id="file-path"></p></div><div class="actions"><button id="copy-name" type="button">Copy name</button><button id="copy-svg" type="button">Copy SVG</button><button id="dismiss" type="button" aria-label="Close icon details">×</button></div></aside><p id="status" role="status" aria-live="polite"></p></main>
<footer>24 × 24 grid · 1.75-unit structural strokes · True transparent vectors · Warm accent #DBB688<br>Solid is an artwork style. The containing control owns selected, playing, pressed and disabled states. A small warm dot indicates a solid alternate exists. Assets without one retain their outline.</footer>
<dialog id="fallback"><h2 id="fallback-title">Copy source</h2><textarea id="fallback-source" readonly aria-label="Source to copy"></textarea><p>Use your system copy shortcut on the selected text.</p><button id="close-dialog" type="button">Done</button></dialog>
<script>
const icons=__ICONS__, families=__FAMILIES__;
const $=id=>document.getElementById(id);
let chosen=null;
for(const name of families){const option=document.createElement('option');option.value=name;option.textContent=name;$('category').append(option)}
function mode(item){return $('variant').value==='solid'&&item.solid?'solid':'outline'}
function markup(item){return item[mode(item)]}
function select(item){chosen=item;showDetails();document.querySelectorAll('.tile').forEach(tile=>tile.setAttribute('aria-pressed',String(tile.dataset.id===item.id)))}
function showDetails(){if(!chosen)return;$('sample').innerHTML=markup(chosen);$('sample').querySelector('svg').setAttribute('aria-hidden','true');$('icon-name').textContent=chosen.id;$('meaning').textContent=chosen.meaning;$('file-path').textContent=chosen.variants[mode(chosen)].svg+($('variant').value==='solid'&&!chosen.solid?' · outline fallback':'');$('inspector').classList.add('open')}
function draw(){const query=$('search').value.trim().toLowerCase();const category=$('category').value;const visible=icons.filter(i=>(!category||i.category===category)&&`${i.id} ${i.label} ${i.meaning}`.toLowerCase().includes(query));$('catalog').replaceChildren();document.documentElement.style.setProperty('--icon-size',$('size').value+'px');
for(const family of families){const members=visible.filter(i=>i.category===family);if(!members.length)continue;const section=document.createElement('section');section.className='category';const title=document.createElement('h2');title.textContent=family+' ';const count=document.createElement('span');count.textContent=members.length;title.append(count);section.append(title);const grid=document.createElement('div');grid.className='grid';
for(const item of members){const tile=document.createElement('button');tile.type='button';tile.className='tile';tile.dataset.id=item.id;tile.setAttribute('aria-label',item.label+(item.solid?', solid alternate available':''));tile.setAttribute('aria-pressed',String(chosen?.id===item.id));tile.innerHTML=markup(item);const shape=tile.querySelector('svg');shape.removeAttribute('aria-label');shape.removeAttribute('role');shape.setAttribute('aria-hidden','true');const label=document.createElement('span');label.textContent=item.id;tile.append(label);if(item.solid){const dot=document.createElement('i');dot.className='dot';dot.setAttribute('aria-hidden','true');tile.append(dot)}tile.addEventListener('click',()=>select(item));grid.append(tile)}section.append(grid);$('catalog').append(section)}
if(!visible.length){const empty=document.createElement('p');empty.className='empty';empty.textContent='No matching icons. Try a different name or family.';$('catalog').append(empty)}$('summary').textContent=visible.length+' of '+icons.length+' designs · '+icons.filter(i=>i.solid).length+' solid alternates · '+$('size').value+' px';showDetails()}
for(const id of ['search','category','variant','size'])$(id).addEventListener(id==='search'?'input':'change',draw);
$('theme').addEventListener('click',()=>{const dark=document.body.classList.toggle('dark');$('theme').textContent=dark?'Light appearance':'Dark appearance';$('theme').setAttribute('aria-pressed',String(dark))});
async function copy(value,title){try{if(!navigator.clipboard)throw new Error('Clipboard unavailable');await navigator.clipboard.writeText(value);$('status').textContent=title+' copied.'}catch{$('fallback-title').textContent=title;$('fallback-source').value=value;$('fallback').showModal();$('fallback-source').focus();$('fallback-source').select()}}
$('copy-name').addEventListener('click',()=>chosen&&copy(chosen.id,'Icon name'));
$('copy-svg').addEventListener('click',()=>chosen&&copy(markup(chosen),'SVG source'));
$('dismiss').addEventListener('click',()=>{const tile=chosen&&Array.from(document.querySelectorAll('.tile')).find(t=>t.dataset.id===chosen.id);chosen=null;$('inspector').classList.remove('open');document.querySelectorAll('.tile').forEach(t=>t.setAttribute('aria-pressed','false'));if(tile)tile.focus()});
$('close-dialog').addEventListener('click',()=>$('fallback').close());draw();
</script></body></html>
'''.replace("__ICONS__", json.dumps(catalog_items).replace("</", "<\\/" )).replace("__FAMILIES__", json.dumps(DATA["categories"]))
save("catalog.html", catalog)
print(json.dumps({"designs":len(ICONS), "solid":sum("solid" in i for i in ICONS), "masters":sum(1+("solid" in i) for i in ICONS), "root":str(ROOT)}))
