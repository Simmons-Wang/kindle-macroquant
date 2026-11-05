#!/usr/bin/env python3
"""Render data/dashboard.json into a 600x800 16-level grayscale PNG for a Kindle (8th gen, 167 ppi).

Layout: header · regime/alert banner · date-grouped event table
        (Date | Time GMT/EST | Category | Event / Catalyst | Consensus / Notes) · levels strip · footer.

Usage: python3 render/render.py [--data data/dashboard.json] [--out out]
Writes out/dashboard.png, out/dashboard.html and out/meta.txt.
Auto-shrinks the type scale until everything fits on one screen; exits 2 if it cannot.
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
NY = ZoneInfo("America/New_York")

# Publishing schedule (London time) — keep in sync with the scheduled tasks.
RUNS = {  # weekday (Mon=0) -> list of (hh, mm)
    0: [(6, 6), (20, 36)], 1: [(6, 6), (20, 36)], 2: [(6, 6), (20, 36)],
    3: [(6, 6), (20, 36)], 4: [(6, 6)], 5: [], 6: [(20, 36)],
}
FETCH_LAG_MIN = 40       # Kindle wakes this long after a scheduled run starts

# Monochrome stand-ins for the colour dots: shape + label
CATS = {
    "CB":   ("■", "Central Bank"),
    "ECO":  ("○", "Economic"),
    "EARN": ("◐", "Earnings"),
    "RISK": ("▲", "Systemic Risk"),
}


def esc(s):
    return html.escape(str(s), quote=False)


CSS = """
:root{--s:1}
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:600px;height:800px;background:#fff;color:#000;overflow:hidden}
body{font-family:'DejaVu Sans Condensed','DejaVu Sans',sans-serif;font-size:calc(10.6px*var(--s));
  line-height:1.2;-webkit-font-smoothing:antialiased}
#root{padding:0 6px}
.hdr{background:#000;color:#fff;margin:0 -6px;padding:4px 6px 3px;display:flex;justify-content:space-between;align-items:baseline}
.hdr .t{font-family:'DejaVu Sans',sans-serif;font-weight:700;font-size:14.5px;letter-spacing:.6px;white-space:nowrap}
.hdr .r{font-weight:700;font-size:10.6px;white-space:nowrap}
.ban{padding:calc(3px*var(--s)) 0 calc(3px*var(--s));border-bottom:2px solid #000}
.ban .rg{font-size:calc(10.2px*var(--s))}
.ban .al{margin-top:2px;font-weight:700;border:2px solid #000;padding:1px 4px}
table.bd{border-collapse:collapse;width:100%;table-layout:fixed;margin-top:1px}
.bd th{text-align:left;font-weight:400;font-size:calc(9.2px*var(--s));color:#000;padding:calc(3px*var(--s)) 3px 2px;
  border-bottom:1.5px solid #000;white-space:nowrap}
.bd td{padding:calc(2.6px*var(--s)) 3px;vertical-align:top;border-bottom:1px solid #8a8a8a}
.bd tr.g0 td{border-top:1.5px solid #000}
.bd td.dt{font-weight:700;border-bottom:1.5px solid #000}
.bd td.dt .tg{display:block;font-weight:400;font-size:calc(9.2px*var(--s))}
.bd td.tm{white-space:nowrap;font-variant-numeric:tabular-nums}
.bd td.tm b{display:block}
.bd td.tm span{font-size:calc(9.2px*var(--s))}
.bd td.ct i{font-style:normal;font-family:'DejaVu Sans',sans-serif;margin-right:2px}
.bd td.ev{font-weight:700}
.bd tr.h td.ev{text-decoration:underline;text-decoration-thickness:1.5px;text-underline-offset:2px}
.bd tr.h td.ev:after{content:' ★';font-family:'DejaVu Sans',sans-serif}
.bd td.nt{font-size:calc(10.2px*var(--s))}
.lv{margin-top:calc(4px*var(--s));border-top:2px solid #000;padding-top:2px}
.lv div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:calc(10px*var(--s))}
.lv b{display:inline-block;width:calc(40px*var(--s))}
.foot{position:absolute;left:6px;bottom:0;height:14px;font-size:8px;line-height:14px;width:400px;white-space:nowrap;overflow:hidden}
"""


def tz_labels(session_date):
    """('GMT','EST') or ('BST','EDT') etc. for the session date."""
    noon = dt.datetime(session_date.year, session_date.month, session_date.day, 12, tzinfo=dt.timezone.utc)
    return noon.astimezone(LDN).tzname(), noon.astimezone(NY).tzname()


def parse_session(s):
    try:
        return dt.datetime.strptime(s.title(), "%a %d %b %Y").date()
    except ValueError:
        return dt.date.today()


def build_html(d):
    s = d["summary"]
    gen = dt.datetime.fromisoformat(d["generated_utc"].replace("Z", "+00:00")).astimezone(LDN)
    gen_s = gen.strftime("%a %H:%M ") + gen.tzname()
    lz, nz = tz_labels(parse_session(d["session"]))

    rows = [f'<colgroup><col style="width:12.5%"><col style="width:11%"><col style="width:14%">'
            f'<col style="width:28.5%"><col></colgroup>',
            f'<tr><th>Date</th><th>{lz} / {nz}</th><th>Category</th><th>Event / Catalyst</th>'
            f'<th>Consensus / Notes</th></tr>']
    for g in d["board"]:
        n = len(g["rows"])
        tag = f'<span class="tg">({esc(g["tag"])})</span>' if g.get("tag") else ""
        for i, r in enumerate(g["rows"]):
            cls = ["g0"] if i == 0 else []
            if r.get("imp") == "H":
                cls.append("h")
            sym, label = CATS.get(r["cat"].upper(), ("·", r["cat"]))
            date_s = esc(g["date"]).replace(", ", ",<br>").replace(" ", "&nbsp;")
            date_td = f'<td class="dt" rowspan="{n}">{date_s}{tag}</td>' if i == 0 else ""
            ny = f'<span>{esc(r["ny"])}</span>' if r.get("ny") else ""
            rows.append(
                f'<tr class="{" ".join(cls)}">{date_td}'
                f'<td class="tm"><b>{esc(r["ldn"])}</b>{ny}</td>'
                f'<td class="ct"><i>{sym}</i>{esc(label)}</td>'
                f'<td class="ev">{esc(r["event"])}</td>'
                f'<td class="nt">{esc(r.get("notes", ""))}</td></tr>')

    lv = "".join(f'<div><b>{esc(x["k"])}</b>{esc(x["v"])}</div>' for x in d.get("levels", []))

    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<div id="root">
 <div class="hdr"><span class="t">MACROQUANT INTELLIGENCE</span>
  <span class="r">{esc(d['session'])} · {esc(d.get('edition',''))} · {esc(gen_s)}</span></div>
 <div class="ban"><div class="rg">{esc(s['regime'])}</div><div class="al">▲ {esc(s['alert'])}</div></div>
 <table class="bd">{''.join(rows)}</table>
 <div class="lv">{lv}</div>
</div>
<div class="foot">■ CB ○ Econ ◐ Earn ▲ Risk · ★ high impact · n/v = not verified · sources in repo</div>
</body></html>"""


def next_fetch_epoch(now_utc):
    """Start of the next scheduled run after now (London) + FETCH_LAG_MIN, as a UTC epoch."""
    now_l = now_utc.astimezone(LDN)
    for add in range(0, 8):
        day = (now_l + dt.timedelta(days=add)).date()
        for hh, mm in RUNS[day.weekday()]:
            start = dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=LDN)
            if start > now_l:  # the NEXT run, never the run that is publishing right now
                return int((start + dt.timedelta(minutes=FETCH_LAG_MIN)).timestamp())
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
        s, fitted = 1.32, False  # grow type when there is room, shrink until it fits
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
