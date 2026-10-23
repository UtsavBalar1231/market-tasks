"""Download NSE daily reports into data/nse (run by the GitHub Action after the close).

Keeps the last 45 sessions; eod2_data covers older history. Writes data/status.json either way, so the
scheduled tasks can see whether today's files arrived.
"""
import datetime as dt, json, os, time, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
      "Accept": "*/*", "Referer": "https://www.nseindia.com/"}
ARC = "https://nsearchives.nseindia.com"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
KEEP = 45


def get(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 404, b""
            err = f"HTTP {e.code}"
        except Exception as e:
            err = type(e).__name__
        time.sleep(3 * (attempt + 1))
    return err, b""


def session_date(body):
    for line in body.decode("utf-8", "replace").splitlines()[1:3]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) > 2:
            return dt.datetime.strptime(parts[2], "%d-%b-%Y").date()


def main():
    today = dt.datetime.now(IST).date()
    log, last = [], None
    for back in range(7, -1, -1):
        d = today - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        tag = f"{d:%d%m%Y}"
        eq_path = f"data/nse/eq/sec_bhavdata_full_{tag}.csv"
        if os.path.exists(eq_path):
            last = d
            continue
        code, body = get(f"{ARC}/products/content/sec_bhavdata_full_{tag}.csv")
        if code != 200 or not body:
            log.append(f"{d}: bhavcopy {code}")
            continue
        if session_date(body) != d:  # NSE serves the previous session's file on holidays
            log.append(f"{d}: holiday")
            continue
        os.makedirs("data/nse/eq", exist_ok=True); open(eq_path, "wb").write(body)
        for sub, url, path in (("idx", f"{ARC}/content/indices/ind_close_all_{tag}.csv", f"data/nse/idx/ind_close_all_{tag}.csv"),):
            c, b = get(url)
            if c == 200 and b:
                os.makedirs(f"data/nse/{sub}", exist_ok=True); open(path, "wb").write(b)
            else:
                log.append(f"{d}: {sub} {c}")
        if d == today:
            for name in ("bulk", "block"):
                c, b = get(f"{ARC}/content/equities/{name}.csv")
                if c == 200 and b:
                    os.makedirs("data/nse/deals", exist_ok=True); open(f"data/nse/deals/{name}_{tag}.csv", "wb").write(b)
                else:
                    log.append(f"{d}: {name} deals {c}")
        last = d
        log.append(f"{d}: ok")
    for sub in ("eq", "idx", "deals"):
        folder = f"data/nse/{sub}"
        if os.path.isdir(folder):
            files = sorted(os.listdir(folder), key=lambda f: dt.datetime.strptime(f.rsplit("_", 1)[1][:8], "%d%m%Y"))
            for f in files[:-KEEP]:
                os.remove(os.path.join(folder, f))
    os.makedirs("data", exist_ok=True)
    json.dump({"checked_at": dt.datetime.now(IST).isoformat(timespec="minutes"), "last_session": str(last) if last else None,
               "note": "; ".join(log[-8:]) or "nothing new"}, open("data/status.json", "w"), indent=1)
    print(json.load(open("data/status.json")))


if __name__ == "__main__":
    main()
