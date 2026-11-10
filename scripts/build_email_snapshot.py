#!/usr/bin/env python3
"""Build a private, interactive HTML snapshot from aggregate tracker JSON."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent.parent


def build(source: Path, output: Path) -> None:
    payload = json.loads(source.read_text(encoding="utf-8"))
    style = re.search(r"<style>(.*?)</style>",
                      (ROOT / "template/dashboard_template.html").read_text(encoding="utf-8"),
                      re.S).group(1)
    widget = (ROOT / "template/email_tracker_widget.js").read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Commercial and trade email · CVR and engagement snapshot</title><style>{style}</style></head>
<body><header><div class="top"><div><h1>Commercial &amp; Trade Email</h1>
<div class="sub">CVR and engagement · Zendesk + Shopify · as of {payload['asof']}</div></div>
<div class="row"><button class="btn" id="download" type="button">Download HTML</button>
<button class="btn" id="theme" type="button">Dark / light</button></div></div></header>
<main id="main"></main><script>
const DATA={data};
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
const money=(v,d=0)=>'$'+Number(v).toLocaleString('en-US',{{minimumFractionDigits:d,maximumFractionDigits:d}});
const pct=(v,d=1)=>(v*100).toFixed(d)+'%',num=v=>Number(v).toLocaleString('en-US');
const state={{emailBox:'both',emailView:'comparison'}};
{widget}
function render(){{document.getElementById('main').innerHTML=emailTrackerHtml(DATA,state);}}
document.getElementById('main').addEventListener('click',e=>{{
 const b=e.target.closest('[data-email-box]');if(b){{state.emailBox=b.dataset.emailBox;render();}}
 const v=e.target.closest('[data-email-view]');if(v){{state.emailView=v.dataset.emailView;render();}}
}});
document.getElementById('theme').onclick=()=>{{const r=document.documentElement;
 r.dataset.theme=r.dataset.theme==='dark'?'light':'dark';}};
document.getElementById('download').onclick=()=>{{const blob=new Blob(['<!doctype html>'+document.documentElement.outerHTML],{{type:'text/html;charset=utf-8'}});
 const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='Commercial_Trade_Email_CVR_Engagement_2026-11-10.html';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}};
render();</script></body></html>'''
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: build_email_snapshot.py aggregate.json output.html")
    build(Path(sys.argv[1]), Path(sys.argv[2]))
