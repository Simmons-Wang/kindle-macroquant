#!/usr/bin/env python3
"""Render data/dashboard.json into a 600x800 16-level grayscale PNG for a Kindle (8th gen, 167 ppi).

Usage: python3 render/render.py [--data data/dashboard.json] [--out out]
Writes out/dashboard.png, out/dashboard.html and out/meta.txt.
Auto-shrinks the type scale until everything fits on one screen; exits non-zero if it cannot.
"""
import argparse
import datetime as dt
import html
import json
import pathlib
import sys
from zoneinfo import ZoneInfo

from PIL import Image
from playwright.sync_api import sync_playwright

W, H = 600, 800
FOOTER_H = 14            # bottom strip left for the Kindle's own battery / fetch-time overlay
LDN = ZoneInfo("Europe/London")

# Publishing schedule (London time) — keep in sync with the scheduled tasks.
RUNS = {  # weekday (Mon=0) -> list of (hh, mm)
    0: [(6, 6), (20, 36)], 1: [(6, 6), (20, 36)], 2: [(6, 6), (20, 36)],
    3: [(6, 6), (20, 36)], 4: [(6, 6)], 5: [], 6: [(20, 36)],
}
FETCH_LAG_MIN = 40       # Kindle wakes this long after a scheduled run starts


def esc(s):
    return html.escape(str(s), quote=False)


BADGE = {"CB": "CB", "ECO": "ECO", "EARN": "EARN", "RISK": "RISK"}


def badge(cat, imp=""):
    cat = cat.upper()
    mark = "" if not imp else f'<i class="imp">{esc(imp)}</i>'
    return f'<span class="b b-{cat.lower()}">{BADGE.get(cat, esc(cat))}</span>{mark}'


CSS = """
:root{--s:1}
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:600px;height:800px;background:#fff;color:#000;overflow:hidden}
body{font-family:'DejaVu Sans Condensed','DejaVu Sans',sans-serif;font-size:calc(10.4px*var(--s));
  line-height:1.22;-webkit-font-smoothing:antialiased}
#root{padding:0 7px}
.hdr{background:#000;color:#fff;margin:0 -7px;padding:4px 7px 3px;display:flex;justify-content:space-between;align-items:baseline}
.hdr .t{font-family:'DejaVu Sans',sans-serif;font-weight:700;font-size:calc(15px*var(--s));letter-spacing:.6px}
.hdr .r{font-weight:700;font-size:calc(11px*var(--s))}
.tz{font-size:calc(9px*var(--s));padding:2px 0 1px;border-bottom:2px solid #000;display:flex;justify-content:space-between}
.sec{margin-top:calc(4px*var(--s))}
.sh{display:flex;align-items:center;gap:5px;font-weight:700;font-size:calc(9.6px*var(--s));letter-spacing:.5px;
  border-bottom:1px solid #000;padding-bottom:1px;margin-bottom:2px}
.sh .n{background:#000;color:#fff;padding:0 4px}
.regime{font-size:calc(10.4px*var(--s))}
.alert{margin-top:2px;border:2px solid #000;padding:2px 4px;font-weight:700;font-size:calc(10.4px*var(--s))}
.b{display:inline-block;font-family:'DejaVu Sans',sans-serif;font-weight:700;font-size:calc(7.6px*var(--s));line-height:1;
  padding:1.5px 2px 1px;min-width:calc(27px*var(--s));text-align:center;letter-spacing:.2px;vertical-align:1px}
.b-cb{background:#000;color:#fff;border:1px solid #000}
.b-eco{border:1px solid #000}
.b-earn{border:1px dashed #000}
.b-risk{background:#000;color:#fff;border:1px solid #000;outline:1px solid #000;outline-offset:1px}
.imp{font-style:normal;font-weight:700;font-size:calc(7.6px*var(--s));margin-left:1px;vertical-align:1px}
table{border-collapse:collapse;width:100%}
.ag td{padding:0.6px 2px;vertical-align:top}
.ag .day td{font-weight:700;font-size:calc(9px*var(--s));padding-top:2px;border-bottom:1px dotted #000;letter-spacing:.3px}
.ag .tm{width:calc(33px*var(--s));font-variant-numeric:tabular-nums;white-space:nowrap}
.ag .tm2{width:calc(38px*var(--s));white-space:nowrap}
.ag .bd{width:calc(40px*var(--s));white-space:nowrap}
.ag .v{text-align:right;white-space:nowrap;font-weight:700}
.ag .h td.tx{font-weight:700}
.colh td{font-size:calc(8px*var(--s));font-weight:700;padding:0 2px}
.two{display:flex;gap:7px}
.two>div{flex:1}
.sub{font-weight:700;font-size:calc(8.6px*var(--s));letter-spacing:.4px;margin-bottom:1px}
ul{list-style:none}
li{padding-left:8px;text-indent:-8px;margin-bottom:1px}
li:before{content:'▸ '}
.hz td{padding:0.6px 2px;vertical-align:top;border-bottom:1px dotted #999}
.hz .d{width:calc(42px*var(--s));font-weight:700;white-space:nowrap}
.hz .bd{width:calc(36px*var(--s))}
.hz .nt{display:block;font-style:italic;font-size:calc(9.4px*var(--s))}
.q td{padding:0.8px 2px;vertical-align:top}
.q .k{width:calc(38px*var(--s));font-weight:700;font-size:calc(8px*var(--s));letter-spacing:.3px}
.q .k span{display:block;border:1px solid #000;text-align:center;padding:1px 0}
.foot{position:absolute;left:7px;bottom:0;height:14px;font-size:8px;line-height:14px;width:410px;white-space:nowrap;overflow:hidden}
"""


def build_html(d):
    s = d["summary"]
    gen = dt.datetime.fromisoformat(d["generated_utc"].replace("Z", "+00:00")).astimezone(LDN)
    gen_s = gen.strftime("%a %d %b %H:%M ") + gen.tzname()

    ag_rows = ['<tr class="colh"><td class="tm">LDN</td><td class="tm2">NY</td><td></td><td></td><td class="v">CONS | PRIOR</td></tr>']
    for day in d["agenda"]:
        ag_rows.append(f'<tr class="day"><td colspan="5">{esc(day["day"])}</td></tr>')
        for it in day["items"]:
            cls = ' class="h"' if it.get("imp") == "H" else ""
            ag_rows.append(
                f'<tr{cls}><td class="tm">{esc(it["ldn"])}</td><td class="tm2">{esc(it["ny"])}</td>'
                f'<td class="bd">{badge(it["cat"], it.get("imp", ""))}</td>'
                f'<td class="tx">{esc(it["text"])}</td><td class="v">{esc(it.get("val", ""))}</td></tr>')

    def ul(items):
        return "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in items) + "</ul>"

    hz_rows = []
    for r in d["horizon"]:
        note = f'<span class="nt">⤷ {esc(r["note"])}</span>' if r.get("note") else ""
        hz_rows.append(f'<tr><td class="d">{esc(r["day"])}</td><td class="bd">{badge(r["cat"])}</td>'
                       f'<td>{esc(r["text"])}{note}</td></tr>')

    q_rows = [f'<tr><td class="k"><span>{esc(q["k"])}</span></td><td>{esc(q["v"])}</td></tr>' for q in d["quant"]]

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<div id="root">
 <div class="hdr"><span class="t">MACROQUANT INTELLIGENCE</span><span class="r">{esc(d['session'])} · {esc(d.get('edition',''))}</span></div>
 <div class="tz"><span>{esc(d['tz'])}</span><span>built {esc(gen_s)}</span></div>

 <div class="sec"><div class="sh"><span class="n">1</span>EXECUTIVE SUMMARY · CATALYST ALERT</div>
  <div class="regime">{esc(s['regime'])}</div>
  <div class="alert">▲ {esc(s['alert'])}</div></div>

 <div class="sec"><div class="sh"><span class="n">2</span>CALENDAR · NEXT SESSIONS
   <span style="margin-left:auto;font-weight:400">{badge('CB')} {badge('ECO')} {badge('EARN')} {badge('RISK')} H/M=impact</span></div>
  <table class="ag">{''.join(ag_rows)}</table></div>

 <div class="sec"><div class="sh"><span class="n">3</span>ROADMAP · {esc(d['session'])}</div>
  <div class="two"><div><div class="sub">MACRO &amp; CENTRAL BANKS</div>{ul(d['roadmap']['macro'])}</div>
  <div><div class="sub">MEGA-CAP EARNINGS</div>{ul(d['roadmap']['earnings'])}</div></div></div>

 <div class="sec"><div class="sh"><span class="n">4</span>WEEK-AHEAD HORIZON · 10 SESSIONS · LDN/NY</div>
  <table class="hz">{''.join(hz_rows)}</table></div>

 <div class="sec"><div class="sh"><span class="n">5</span>QUANT &amp; MARKET STRUCTURE</div>
  <table class="q">{''.join(q_rows)}</table></div>
</div>
<div class="foot">SRC: {esc(d.get('sources',''))} · n/v = not verified · not advice</div>
</body></html>"""


def next_fetch_epoch(now_utc):
    """Next scheduled run start (London) + FETCH_LAG_MIN, as a UTC epoch."""
    now_l = now_utc.astimezone(LDN)
    for add in range(0, 8):
        day = (now_l + dt.timedelta(days=add)).date()
        for hh, mm in RUNS[day.weekday()]:
            t = dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=LDN) + dt.timedelta(minutes=FETCH_LAG_MIN)
            if t > now_l + dt.timedelta(minutes=5):
                return int(t.timestamp())
    return int((now_utc + dt.timedelta(hours=12)).timestamp())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/dashboard.json")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    d = json.loads(pathlib.Path(a.data).read_text(encoding="utf-8"))
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    doc = build_html(d)
    (out / "dashboard.html").write_text(doc, encoding="utf-8")

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        pg.set_content(doc, wait_until="load")
        s, fitted = 1.0, False
        while s >= 0.80:
            pg.evaluate(f"document.documentElement.style.setProperty('--s','{s:.2f}')")
            h = pg.evaluate("document.getElementById('root').scrollHeight")
            if h <= H - FOOTER_H:
                fitted = True
                break
            s -= 0.02
        png_tmp = out / "_raw.png"
        pg.screenshot(path=str(png_tmp), clip={"x": 0, "y": 0, "width": W, "height": H})
        b.close()

    img = Image.open(png_tmp).convert("L")
    img = img.point(lambda v: min(255, round(v / 17) * 17))  # 16 grey levels, no dithering
    img.save(out / "dashboard.png", optimize=True)
    png_tmp.unlink()

    now = dt.datetime.now(dt.timezone.utc)
    (out / "meta.txt").write_text(
        f"GENERATED_EPOCH={int(now.timestamp())}\nNEXT_FETCH_EPOCH={next_fetch_epoch(now)}\n"
        f"LDN_OFFSET_S={int(now.astimezone(LDN).utcoffset().total_seconds())}\n"
        f"SESSION={d['session']}\nEDITION={d.get('edition','')}\nSCALE={s:.2f}\n", encoding="ascii", errors="replace")
    print(f"scale={s:.2f} content_h={h} fitted={fitted}")
    if not fitted:
        print("ERROR: content does not fit on one screen even at 0.80 scale — shorten text.", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
