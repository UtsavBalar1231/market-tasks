"""Market scanner for the scheduled market tasks.

History comes from eod2_data (NSE end-of-day mirror on GitHub, split/bonus adjusted, synced about weekly).
Sessions after eod2's last sync come from this repo's daily NSE files (data/nse, written by the GitHub
Action), then from --extra CSV files (candles the run fetched itself). Commands:

  python3 mscan.py day                    trading-day check, data freshness, sessions still missing
  python3 mscan.py scan [--extra f.csv]   regime, breadth, sectors, top 20 per bucket, charts.html
  python3 mscan.py show SYM ... [--extra] last 60 sessions per symbol (index names in quotes work too)
  python3 mscan.py week [--extra]         weekly scoreboard and charts.html

--extra CSV columns: symbol,date(YYYY-MM-DD),open,high,low,close,volume[,deliv_qty]
"""
import csv, io, os, sys, json, datetime as dt, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

EOD2 = "https://raw.githubusercontent.com/BennyThadikaran/eod2_data/main/"
REPO = "https://raw.githubusercontent.com/UtsavBalar1231/market-tasks/main/"
INDICES = ["nifty 50", "nifty midcap 150", "nifty smallcap 250", "india vix"]
BUCKET_INDEX = {"Midcap": "nifty midcap 150", "Smallcap": "nifty smallcap 250"}
MIN_ADTV = {"Midcap": 25, "Smallcap": 5}
SECTORS = ["auto", "bank", "financial services", "fmcg", "it", "media", "metal", "pharma", "psu bank", "private bank",
           "realty", "healthcare index", "consumer durables", "oil & gas", "chemicals", "capital markets", "india defence",
           "energy", "infrastructure", "cpse", "pse", "india consumption", "commodities", "capital goods"]
CACHE = "mscan_cache"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
GREEN, RED, AMBER, INK, MUTED, LINE, RULE, FAINT, HEAD = "#22C55E", "#F87171", "#F59E0B", "#E2E8F0", "#94A3B8", "#1E293B", "#334155", "#64748B", "#F8FAFC"
SANS = "'Fira Sans',-apple-system,'Segoe UI',Roboto,Arial,sans-serif"
MONO = "'Fira Code',Menlo,Consolas,monospace"
SPARK = "▁▂▃▄▅▆▇█"
EXTRA, HTML, NOTES = {}, [], []


def fetch(url):
    os.makedirs(CACHE, exist_ok=True)
    local = os.path.join(CACHE, urllib.parse.quote(url, safe=""))
    if os.path.exists(local):
        return open(local).read() or None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "mscan"})
        body = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    except Exception:
        body = ""
    open(local, "w").write(body)
    return body or None


def eod2(path):
    return fetch(EOD2 + urllib.parse.quote(path))


def num(x):
    try:
        v = float(str(x).replace(",", ""))
        return None if v != v else v
    except (TypeError, ValueError):
        return None


def universe():
    body = open("data/universe.csv").read() if os.path.exists("data/universe.csv") else fetch(REPO + "data/universe.csv")
    if not body:
        sys.exit("FAILED: cannot load data/universe.csv from the repo")
    return list(csv.DictReader(io.StringIO(body)))


def meta():
    m = json.loads(eod2("meta.json") or "{}")
    return m, {dt.datetime.strptime(k, "%d-%b-%Y").date() for k in m.get("holidays", {})}


def sessions_after(a, b, hol):
    out, d = [], a + dt.timedelta(days=1)
    while d <= b:
        if d.weekday() < 5 and d not in hol:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


_NSE = {}


def nse_files(d):
    """This repo's NSE daily files for date d, as ({SYMBOL: row}, {index name lower: row})."""
    if d not in _NSE:
        tag = f"{d:%d%m%Y}"
        eq, ix = {}, {}
        for r in csv.DictReader(io.StringIO(fetch(f"{REPO}data/nse/eq/sec_bhavdata_full_{tag}.csv") or "")):
            r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
            if r.get("SERIES") in ("EQ", "BE") and r.get("DATE1") and dt.datetime.strptime(r["DATE1"], "%d-%b-%Y").date() == d:
                eq[r["SYMBOL"]] = r
        for r in csv.DictReader(io.StringIO(fetch(f"{REPO}data/nse/idx/ind_close_all_{tag}.csv") or "")):
            r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
            if r.get("Index Name"):
                ix[r["Index Name"].lower()] = r
        _NSE[d] = (eq, ix)
    return _NSE[d]


def load_extra(paths):
    for p in paths:
        for r in csv.reader(open(p)):
            if len(r) < 7 or r[0].strip().lower() == "symbol":
                continue
            d = dt.date.fromisoformat(r[1].strip())
            EXTRA.setdefault(r[0].strip().lower(), {})[d] = {
                "d": d, "o": num(r[2]), "h": num(r[3]), "l": num(r[4]), "c": num(r[5]), "v": num(r[6]) or 0,
                "dlv": num(r[7]) if len(r) > 7 else None, "pe": None, "series": "EQ", "src": "extra"}


_SER = {}


def series(name):
    key = name.lower()
    if key in _SER:
        return _SER[key]
    rows = []
    for r in csv.DictReader(io.StringIO(eod2(f"daily/{key}.csv") or "")):
        c = num(r.get("Close"))
        if c is not None:
            rows.append({"d": dt.date.fromisoformat(r["Date"]), "o": num(r["Open"]), "h": num(r["High"]), "l": num(r["Low"]),
                         "c": c, "v": num(r.get("Volume")) or 0, "dlv": num(r.get("DLV_QTY")), "pe": num(r.get("P/E")),
                         "series": r.get("Series") or "EQ", "src": "eod2"})
    last = rows[-1]["d"] if rows else dt.date.today() - dt.timedelta(days=30)
    _, hol = meta()
    for d in sessions_after(last, dt.datetime.now(IST).date(), hol):
        eq, ix = nse_files(d)
        r = ix.get(key) if key in [i.lower() for i in INDICES] or key.startswith("nifty") or key == "india vix" else None
        if r:
            rows.append({"d": d, "o": num(r["Open Index Value"]), "h": num(r["High Index Value"]), "l": num(r["Low Index Value"]),
                         "c": num(r["Closing Index Value"]), "v": num(r.get("Volume")) or 0, "dlv": None, "pe": num(r.get("P/E")),
                         "series": "IDX", "src": "nse"})
            continue
        r = eq.get(name.upper())
        if r:
            if rows and num(r["OPEN_PRICE"]) and num(r["OPEN_PRICE"]) / rows[-1]["c"] < 0.7:
                NOTES.append(f"{name}: {d} opened {num(r['OPEN_PRICE']) / rows[-1]['c'] - 1:+.0%} vs prior close; check for a split or bonus")
            rows.append({"d": d, "o": num(r["OPEN_PRICE"]), "h": num(r["HIGH_PRICE"]), "l": num(r["LOW_PRICE"]),
                         "c": num(r["CLOSE_PRICE"]), "v": num(r["TTL_TRD_QNTY"]) or 0, "dlv": num(r.get("DELIV_QTY")), "pe": None,
                         "series": r["SERIES"], "src": "nse"})
    last = rows[-1]["d"] if rows else dt.date.min
    rows += sorted((x for x in EXTRA.get(key, {}).values() if x["d"] > last), key=lambda x: x["d"])
    _SER[key] = rows
    return rows


def sma(xs, n, end=None):
    xs = xs[:end] if end else xs
    return sum(xs[-n:]) / n if len(xs) >= n else None


def rsi(c, n=14):
    # Cutler RSI (simple 14-day means): the same values Groww reports
    if len(c) <= n:
        return None
    d = [c[i] - c[i - 1] for i in range(len(c) - n, len(c))]
    g, l = sum(max(x, 0) for x in d), sum(max(-x, 0) for x in d)
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def ret(xs, n):
    return round((xs[-1] / xs[-1 - n] - 1) * 100, 1) if len(xs) > n and xs[-1 - n] else None


NICE = {"it": "IT", "psu": "PSU", "fmcg": "FMCG", "cpse": "CPSE", "pse": "PSE", "vix": "VIX", "&": "&"}


def nice(name):
    return " ".join(NICE.get(w, w.capitalize()) for w in name.split())


def spark(xs):
    lo, hi = min(xs), max(xs)
    return "".join(SPARK[int((x - lo) / (hi - lo) * 7) if hi > lo else 3] for x in xs)


def stock_metrics(sym, bucket, ic):
    rows = series(sym)
    if len(rows) < 60:
        return None
    c = [r["c"] for r in rows]; h = [r["h"] or r["c"] for r in rows]; l = [r["l"] or r["c"] for r in rows]; v = [r["v"] for r in rows]
    tr = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    atr = sum(tr[-14:]) / 14
    s20, s50, s150, s200 = (sma(c, n) for n in (20, 50, 150, 200))
    s200p = sma(c, 200, end=-20)
    hi, lo = max(h[-252:]), min(l[-252:])
    rs = [r["c"] / ic[r["d"]] for r in rows if r["d"] in ic]
    dl = [r["dlv"] / r["v"] * 100 for r in rows[-21:-1] if r["dlv"] and r["v"]]
    last, avgv = rows[-1], sum(v[-21:-1]) / 20
    up = sum(v[i] for i in range(-50, 0) if c[i] > c[i - 1]); dn = sum(v[i] for i in range(-50, 0) if c[i] < c[i - 1])
    m = {"symbol": sym, "bucket": bucket, "asof": str(last["d"]), "src": last["src"], "series": last["series"],
         "close": round(c[-1], 2), "chg_pct": round((c[-1] / c[-2] - 1) * 100, 2), "sma20": round(s20, 2), "sma50": round(s50, 2),
         "sma150": s150 and round(s150, 2), "sma200": s200 and round(s200, 2), "sma200_20d_ago": s200p and round(s200p, 2),
         "hi52": round(hi, 2), "pct_from_hi52": round((c[-1] / hi - 1) * 100, 2), "pct_above_lo52": round((c[-1] / lo - 1) * 100, 2),
         "atr14": round(atr, 2), "atr_pct": round(atr / c[-1] * 100, 2), "rsi14": round(rsi(c), 1),
         "vol_x20": round(v[-1] / avgv, 2) if avgv else None,
         "deliv_pct": round(last["dlv"] / last["v"] * 100, 1) if last["dlv"] and last["v"] else None,
         "deliv_avg20": round(sum(dl) / len(dl), 1) if dl else None,
         "adtv_cr": round(sum(r["c"] * r["v"] for r in rows[-20:]) / 20 / 1e7, 1),
         "ret_1m": ret(c, 21), "ret_3m": ret(c, 63), "rs_3m": ret(rs, 63), "rs_at_high": len(rs) > 200 and rs[-1] >= 0.98 * max(rs[-252:]),
         "ext_atr_sma50": round((c[-1] - s50) / atr, 1), "range15_pct": round((max(h[-15:]) - min(l[-15:])) / c[-1] * 100, 1),
         "updown_vol_50d": round(up / dn, 2) if dn else None, "spark30": spark(c[-30:])}
    f = []
    if s200 and s200p and c[-1] > s50 > s150 > s200 and s200 > s200p and m["pct_above_lo52"] >= 25 and m["pct_from_hi52"] >= -25:
        f.append("TREND")
    if m["pct_from_hi52"] >= -5: f.append("NEAR_HIGH")
    if c[-1] >= max(h[-252:-1]): f.append("NEW_HIGH")
    if m["vol_x20"] and m["vol_x20"] >= 1.5: f.append("VOLUME")
    if m["deliv_pct"] and m["deliv_avg20"] and m["deliv_pct"] > m["deliv_avg20"]: f.append("DELIVERY")
    if m["range15_pct"] <= 2.5 * m["atr_pct"]: f.append("TIGHT")
    if (abs(c[-1] - s20) <= atr or abs(c[-1] - s50) <= atr) and c[-1] > s50 and m["vol_x20"] and m["vol_x20"] < 1: f.append("PULLBACK")
    if m["rs_at_high"]: f.append("RS_HIGH")
    if m["ext_atr_sma50"] > 4 or m["rsi14"] > 80: f.append("EXTENDED")
    if m["series"] == "BE": f.append("T2T")
    if not s200: f.append("SHORT_HISTORY")
    m["flags"] = " ".join(f)
    m["score"] = sum(x in f for x in ("TREND", "NEAR_HIGH", "VOLUME", "DELIVERY", "TIGHT", "PULLBACK", "RS_HIGH")) - 2 * ("EXTENDED" in f) - 5 * ("T2T" in f)
    return m


def idx_text(name):
    s = series(name)
    c = [r["c"] for r in s]
    pe = next((r["pe"] for r in reversed(s) if r["pe"]), None)
    return (f"{nice(name)}: {c[-1]:.2f} as of {s[-1]['d']} [{s[-1]['src']}] ({ret(c, 1):+}% day, {ret(c, 5):+}% 1w, {ret(c, 21):+}% 1m, "
            f"{ret(c, 63):+}% 3m) vs50DMA {(c[-1] / sma(c, 50) - 1) * 100:+.1f}% vs200DMA {(c[-1] / sma(c, 200) - 1) * 100:+.1f}%" + (f" P/E {pe:g}" if pe else ""))


def tracker():
    t = list(csv.DictReader(io.StringIO(eod2("market_tracker.csv") or "")))
    if t:
        a, b = t[-1], t[-6]
        print(f"All-NSE breadth {a['Date']} [eod2]: {float(a['PCT_50']):.0f}% above 50 DMA, {float(a['PCT_200']):.0f}% above 200 DMA, "
              f"net new highs {float(a['NET_NEW_HIGHS']):.0f}, McClellan {float(a['MCCLELLAN_OSC'] or 0):.1f} (5 sessions earlier {float(b['PCT_50']):.0f}% / {float(b['PCT_200']):.0f}%)")
    return t


def sectors():
    n = [r["c"] for r in series("nifty 50")]
    out = []
    for s in SECTORS:
        c = [r["c"] for r in series("nifty " + s)]
        if len(c) > 63:
            out.append((ret(c, 21) - ret(n, 21), ret(c, 5) - ret(n, 5), ret(c, 63) - ret(n, 63), "Nifty " + nice(s)))
    out.sort(key=lambda t: -(t[0] + t[1]))
    print("Sectors vs Nifty (1m / 1w / 3m, pp), strongest first: " + "; ".join(f"{x[3]} {x[0]:+.1f}/{x[1]:+.1f}/{x[2]:+.1f}" for x in out))
    return out


# ---- HTML chart blocks: dark Swiss style matching guide/email.html (inline styles only, Gmail-safe) ----
def tbl(rows, head=""):
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;'
            f'font-family:{SANS};font-size:14px;color:{INK}">{head}{"".join(rows)}</table>')


def arrow(v):
    return "&#9650;" if v > 0 else "&#9660;" if v < 0 else "&#9679;"


def pct_td(v, pad="6px"):
    col = FAINT if v is None else GREEN if v > 0 else RED if v < 0 else MUTED
    txt = "n/a" if v is None else f"{v:+.1f}"
    return f'<td style="padding:{pad};text-align:right;color:{col};white-space:nowrap;font-family:{MONO};font-size:13px">{txt}</td>'


def lbl(t, align="left", pad="0 6px 6px"):
    return f'<td style="padding:{pad};text-align:{align};border-bottom:1px solid {RULE};font-family:{MONO};font-size:10px;letter-spacing:1px;color:{FAINT}">{t}</td>'


def html_indices(names):
    rows = []
    for name in names:
        s = series(name); c = [r["c"] for r in s]
        pe = next((r["pe"] for r in reversed(s) if r["pe"]), None)
        d50, d200 = (c[-1] / sma(c, 50) - 1) * 100, (c[-1] / sma(c, 200) - 1) * 100
        col = GREEN if d50 > 0 and d200 > 0 else RED if d50 < 0 and d200 < 0 else AMBER
        state = "ABOVE BOTH" if d50 > 0 and d200 > 0 else "BELOW BOTH" if d50 < 0 and d200 < 0 else "MIXED"
        rows.append(f'<tr><td style="padding:9px 6px 0 0;color:{HEAD};font-weight:600">{nice(name)}</td>'
                    f'<td style="padding:9px 6px 0;text-align:right;font-family:{MONO};font-size:14px;color:{HEAD}">{c[-1]:,.2f}</td>'
                    + pct_td(ret(c, 1), "9px 6px 0") + pct_td(ret(c, 5), "9px 6px 0") + pct_td(ret(c, 21), "9px 0 0 6px") + '</tr>'
                    f'<tr><td style="padding:2px 6px 9px 0;border-bottom:1px solid {LINE};font-family:{MONO};font-size:10px;color:{FAINT}">{s[-1]["d"]:%d %b}{f" &middot; P/E {pe:g}" if pe else ""}</td>'
                    f'<td colspan="4" style="padding:2px 0 9px;border-bottom:1px solid {LINE};text-align:right;font-family:{MONO};font-size:10px;color:{col}">'
                    f'<span style="font-size:12px;letter-spacing:-1px;white-space:nowrap">{spark(c[-20:])}</span><br>{state} &middot; 50D {d50:+.1f} &middot; 200D {d200:+.1f}</td></tr>')
    head = "<tr>" + lbl("INDEX", pad="0 6px 6px 0") + lbl("CLOSE", "right") + lbl("1D %", "right") + lbl("1W %", "right") + lbl("1M %", "right", "0 0 6px 6px") + "</tr>"
    HTML.append(("INDICES &middot; 20-SESSION SPARKLINE, % FROM 50 AND 200 DMA", tbl(rows, head)))


def html_meters(title, items):
    rows = []
    for label, v in items:
        col = GREEN if v >= 60 else AMBER if v >= 40 else RED
        word = "HEALTHY" if v >= 60 else "MIXED" if v >= 40 else "WEAK"
        rows.append(f'<tr><td style="padding:5px 10px 5px 0;white-space:nowrap;font-size:13px">{label}</td>'
                    f'<td style="width:100%;padding:5px 0"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
                    f'<td style="width:{max(int(v), 1)}%;height:6px;background:{col};font-size:0;line-height:0">&nbsp;</td>'
                    f'<td style="height:6px;background:{LINE};font-size:0;line-height:0">&nbsp;</td></tr></table></td>'
                    f'<td style="padding:5px 0 5px 10px;text-align:right;white-space:nowrap;font-family:{MONO};font-size:13px;color:{col}">{v:.0f}%</td>'
                    f'<td style="padding:5px 0 5px 8px;white-space:nowrap;font-family:{MONO};font-size:10px;color:{col}">{word}</td></tr>')
    HTML.append((title, tbl(rows)))


def html_sectors(data):
    data = sorted(data, key=lambda t: -t[0])
    data = data if len(data) <= 12 else data[:6] + data[-6:]
    scale = max(abs(x[0]) for x in data) or 1
    rows = []
    for i, (m, w, _, nm) in enumerate(data):
        px, col = max(min(int(abs(m) / scale * 44), 44), 1), GREEN if m >= 0 else RED
        sep = f"border-top:1px dashed {RULE};" if i == 6 and len(data) == 12 else ""
        cell = f'<td style="width:{px}px;height:8px;background:{col};font-size:0;line-height:0">&nbsp;</td>'
        left = (f'<table role="presentation" cellpadding="0" cellspacing="0" align="right"><tr>{cell}</tr></table>') if m < 0 else ""
        right = (f'<table role="presentation" cellpadding="0" cellspacing="0"><tr>{cell}</tr></table>') if m >= 0 else ""
        rows.append(f'<tr><td style="{sep}padding:4px 8px 4px 0;white-space:nowrap;font-size:13px">{nm[6:]}</td>'
                    f'<td style="{sep}width:44px;padding:4px 0;border-right:1px solid {RULE}">{left}</td><td style="{sep}width:44px;padding:4px 0">{right}</td>'
                    f'<td style="{sep}padding:4px 0 4px 8px;text-align:right;white-space:nowrap;font-family:{MONO};font-size:13px;color:{col}">{arrow(m)} {m:+.1f}</td>'
                    f'<td style="{sep}padding:4px 0 4px 8px;text-align:right;white-space:nowrap;font-family:{MONO};font-size:11px;color:{FAINT}">{w:+.1f}</td></tr>')
    head = "<tr>" + lbl("SECTOR", pad="0 8px 6px 0") + lbl("&#9664;", "right", "0 4px 6px 0") + lbl("&#9654;", "left", "0 0 6px 4px") + lbl("1M", "right", "0 0 6px 8px") + lbl("1W", "right", "0 0 6px 8px") + "</tr>"
    HTML.append(("SECTORS VS NIFTY 50 &middot; 1-MONTH, PERCENTAGE POINTS &middot; TOP 6 AND BOTTOM 6", tbl(rows, head)))


def write_html():
    with open("charts.html", "w") as fh:
        for i, (title, block) in enumerate(HTML):
            fh.write(f'<!-- CHART: {title} -->\n<div style="font-family:{MONO};font-size:10px;letter-spacing:1px;color:{MUTED};margin:{6 if i == 0 else 22}px 0 8px">{title}</div>\n{block}\n\n')
    print("Chart blocks for the email: charts.html (paste each block unchanged)")


# ---- commands ----
def cmd_day():
    now = dt.datetime.now(IST); today = now.date()
    m, hol = meta()
    trading = today.weekday() < 5 and today not in hol
    closed = now.time() >= dt.time(15, 40)
    last = max(r["d"] for r in series("nifty 50") if r["src"] == "eod2")
    upto = today if (trading and closed) else today - dt.timedelta(days=1)
    missing = sessions_after(last, upto, hol)
    covered = [d for d in missing if nse_files(d)[0]]
    status = json.loads(fetch(REPO + "data/status.json") or "{}")
    print(f"NOW {now:%Y-%m-%d %a %H:%M} IST | TRADING_DAY {'yes' if trading else 'no'} | MARKET {'closed' if closed or not trading else 'open'}")
    print(f"EOD2_LAST {last} | REPO_NSE_LAST {status.get('last_session', 'none')} ({status.get('note', 'no status file')})")
    print("SESSIONS_AFTER_EOD2 " + (",".join(map(str, missing)) or "none"))
    print("STILL_MISSING " + (",".join(str(d) for d in missing if d not in covered) or "none") + "  <- fill these with --extra candles")


def cmd_scan():
    uni = universe()
    with ThreadPoolExecutor(16) as ex:
        list(ex.map(lambda n: eod2(f"daily/{n.lower()}.csv"), [u["symbol"] for u in uni] + INDICES + ["nifty " + s for s in SECTORS]))
    for name in INDICES:
        print(idx_text(name))
    t = tracker()
    out = []
    for u in uni:
        ic = {r["d"]: r["c"] for r in series(BUCKET_INDEX[u["bucket"]])}
        m = stock_metrics(u["symbol"], u["bucket"], ic)
        if m:
            m["groww_slug"] = u.get("groww_slug", "")
            out.append(m)
    newest = max(m["asof"] for m in out)
    stale = [m["symbol"] for m in out if m["asof"] != newest]
    print(f"Stocks: {len(out)}; newest session {newest}; {len(stale)} lack it" + (f" (e.g. {', '.join(stale[:10])})" if stale else ""))
    meters = []
    for b in BUCKET_INDEX:
        bb = [m for m in out if m["bucket"] == b]
        a50 = sum(m["close"] > m["sma50"] for m in bb) * 100 / len(bb); a200 = sum(bool(m["sma200"]) and m["close"] > m["sma200"] for m in bb) * 100 / len(bb)
        meters += [(f"{b} above 50 DMA", a50), (f"{b} above 200 DMA", a200)]
        print(f"{b} breadth: {a50:.0f}% above 50 DMA, {a200:.0f}% above 200 DMA, {sum(m['pct_from_hi52'] >= 0 for m in bb)} at 52w high, "
              f"{sum(m['chg_pct'] > 0 for m in bb)}/{len(bb)} up on their last session")
    if t:
        meters += [("All NSE above 50 DMA", float(t[-1]["PCT_50"])), ("All NSE above 200 DMA", float(t[-1]["PCT_200"]))]
    sec = sectors()
    for n in NOTES:
        print("NOTE " + n)
    html_indices(INDICES); html_meters("BREADTH: SHARE OF STOCKS ABOVE THEIR MOVING AVERAGES", meters); html_sectors(sec)
    fname = f"scan_{newest.replace('-', '')}.csv"
    with open(fname, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys())); w.writeheader(); w.writerows(out)
    cols = ["symbol", "asof", "close", "chg_pct", "score", "flags", "pct_from_hi52", "vol_x20", "deliv_pct", "deliv_avg20", "rsi14", "atr_pct", "rs_3m", "adtv_cr", "groww_slug"]
    for b in BUCKET_INDEX:
        ok = [m for m in out if m["bucket"] == b and m["close"] >= 50 and m["adtv_cr"] >= MIN_ADTV[b] and "T2T" not in m["flags"]]
        top = sorted(ok, key=lambda m: (-m["score"], -(m["rs_3m"] or 0)))[:20]
        print(f"\nTop 20 {b} (price >= 50, 20d avg turnover >= {MIN_ADTV[b]} cr, not T2T); full table {fname}\n" + ",".join(cols))
        for m in top:
            print(",".join(str(m[k]) for k in cols))
    write_html()


def cmd_show(names):
    for s in names:
        rows = series(s)
        if not rows:
            print(f"\n{s}: no data"); continue
        print(f"\n{s} last 60 sessions {spark([r['c'] for r in rows[-60:]])}: date,open,high,low,close,volume,deliv_pct,source")
        for r in rows[-60:]:
            dp = round(r["dlv"] / r["v"] * 100, 1) if r["dlv"] and r["v"] else ""
            print(f"{r['d']},{r['o']},{r['h']},{r['l']},{r['c']},{r['v']:.0f},{dp},{r['src']}")


def cmd_week():
    for name in INDICES + ["nifty bank"]:
        wk = {}
        for r in series(name):
            wk[r["d"].isocalendar()[:2]] = r["c"]
        w = list(wk.values())
        print(idx_text(name) + f" | weekly close vs 20w avg {(w[-1] / sma(w, 20) - 1) * 100:+.1f}%, vs 50w avg {(w[-1] / sma(w, 50) - 1) * 100:+.1f}%")
    t = tracker()
    sec = sectors()
    html_indices(INDICES + ["nifty bank"])
    if t:
        html_meters("ALL-NSE BREADTH: THIS WEEK VS LAST WEEK", [("Above 50 DMA now", float(t[-1]["PCT_50"])), ("Above 50 DMA a week ago", float(t[-6]["PCT_50"])),
                                                              ("Above 200 DMA now", float(t[-1]["PCT_200"])), ("Above 200 DMA a week ago", float(t[-6]["PCT_200"]))])
    html_sectors(sec)
    write_html()


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--extra" in args:
        i = args.index("--extra"); load_extra(args[i + 1:]); args = args[:i]
    cmd = args[0] if args else "scan"
    {"day": cmd_day, "scan": cmd_scan, "week": cmd_week}.get(cmd, lambda: cmd_show(args[1:]))()
