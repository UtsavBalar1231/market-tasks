"""Pull read-only data from the Groww Trade API into data/groww (run by .github/workflows/groww.yml).

The repo is public, so this script never prints or saves credentials, tokens or anything from the account
(holdings, positions, orders, margin): it only reads public market data. No order, modify or cancel calls
exist anywhere in this file.

Modes:  test  - sign in and check each data call, write data/groww/status.json
"""
import datetime as dt, json, os, re, sys, time, traceback

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
OUT = "data/groww"
SECRETS = [os.environ.get("GROWW_API_KEY", ""), os.environ.get("GROWW_TOTP_SECRET", "")]


def clean(msg):
    """Error text with anything secret-looking removed."""
    msg = str(msg)
    for s in SECRETS:
        if s:
            msg = msg.replace(s, "[secret]")
    msg = re.sub(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", "[token]", msg)
    return msg[:300]


def now():
    return dt.datetime.now(IST)


def key_info(key):
    """Non-secret facts about the key and secret, to tell which kind of Groww key they are."""
    import base64
    info = {}
    try:
        p = key.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
        sub = json.loads(claims.get("sub", "{}"))
        info = {"key_role": sub.get("role"), "key_issued": dt.datetime.fromtimestamp(claims["iat"], IST).isoformat(timespec="minutes"),
                "key_expires": dt.datetime.fromtimestamp(claims["exp"], IST).date().isoformat()}
    except Exception as e:
        info = {"key_parse": type(e).__name__}
    s = os.environ.get("GROWW_TOTP_SECRET", "").strip()
    info["secret_shape"] = {"length": len(s), "upper": any(c.isupper() for c in s), "lower": any(c.islower() for c in s),
                            "digits_0189": any(c in "0189" for c in s), "symbols": any(not c.isalnum() for c in s)}
    return info


def login(report=None):
    """Sign in with the TOTP flow; if the secret is not a TOTP secret, try Groww's key-and-secret flow."""
    import pyotp
    from growwapi import GrowwAPI
    key = os.environ.get("GROWW_API_KEY", "").strip()
    raw = os.environ.get("GROWW_TOTP_SECRET", "").strip()
    seed = raw.replace(" ", "").upper()
    if not key or not raw:
        raise RuntimeError("GROWW_API_KEY or GROWW_TOTP_SECRET is not set in the repo's Actions secrets")
    if report is not None:
        report["diagnostics"] = key_info(key)
    if re.fullmatch(r"[A-Z2-7]+=*", seed):
        flow, token = "totp", GrowwAPI.get_access_token(api_key=key, totp=pyotp.TOTP(seed).now())
    else:
        flow = "key_and_secret"
        try:
            token = GrowwAPI.get_access_token(api_key=key, secret=raw)
        except Exception as e:
            raise RuntimeError(f"secret is not a TOTP secret (A-Z, 2-7 only); key-and-secret sign-in also failed: {clean(e)}")
    if report is not None:
        report["diagnostics"]["flow"] = flow
    print(f"::add-mask::{token}")
    SECRETS.append(token)
    return GrowwAPI(token)


def step(report, name, fn):
    try:
        report[name] = {"ok": True, **(fn() or {})}
    except Exception as e:
        report[name] = {"ok": False, "error": f"{type(e).__name__} {getattr(e, 'code', '')}: {clean(e)}"}
    print(name, "ok" if report[name]["ok"] else "FAILED")


def sign_in_probe():
    """Try each way of getting a session and record Groww's reply (never the token) plus one data call with it."""
    import base64, hashlib, requests, pyotp
    key = os.environ.get("GROWW_API_KEY", "").strip()
    raw = os.environ.get("GROWW_TOTP_SECRET", "").strip()
    url = "https://api.groww.in/v1/token/api/access"
    head = {"Authorization": "Bearer " + key, "Content-Type": "application/json", "x-client-id": "growwapi",
            "x-client-platform": "growwapi-python-client", "x-client-platform-version": "1.5.0", "x-api-version": "1.0"}
    ts = str(int(time.time()))
    bodies = {"approval_checksum": {"key_type": "approval", "checksum": hashlib.sha256((raw + ts).encode()).hexdigest(), "timestamp": int(ts)},
              }
    out = {}
    for name, body in bodies.items():
        try:
            r = requests.post(url, headers=head, json=body, timeout=20)
            try:
                j = r.json()
            except Exception:
                j = {}
            tok = j.get("token")
            res = {"http": r.status_code, "fields": sorted(j.keys()),
                   "active": j.get("active"), "token_ref_tail": str(j.get("tokenRefId", ""))[-6:], "expiry": j.get("expiry"), "sessionName": j.get("sessionName"),
                   "error": clean(json.dumps(j.get("error")))[:200] if j.get("error") else None}
            if tok:
                print(f"::add-mask::{tok}")
                SECRETS.append(tok)
                d = requests.get("https://api.groww.in/v1/live-data/ltp", timeout=20,
                                 params={"segment": "CASH", "exchange_symbols": "NSE_NIFTY"},
                                 headers={**head, "Authorization": "Bearer " + tok})
                try:
                    dj = d.json()
                except Exception:
                    dj = {"body": d.text[:200]}
                res["data_call"] = {"http": d.status_code, "reply": clean(json.dumps(dj))[:300]}
            out[name] = res
        except Exception as e:
            out[name] = {"error": f"{type(e).__name__}: {clean(e)}"}
        time.sleep(1)
    return out


def cmd_test():
    report = {"mode": "test", "at": now().isoformat(timespec="minutes")}
    try:
        report["sign_in_probe"] = sign_in_probe()
    except Exception as e:
        report["sign_in_probe"] = {"error": f"{type(e).__name__}: {clean(e)}"}
    g = None
    try:
        g = login(report)
        report["login"] = {"ok": True}
    except Exception as e:
        report["login"] = {"ok": False, "error": f"{type(e).__name__}: {clean(e)}"}
    if g:
        uni = [l.split(",")[0] for l in open("data/universe.csv").read().splitlines()[1:] if l]

        def instruments():
            df = g.get_all_instruments()
            cash = df[df["segment"] == "CASH"]
            idx = cash[cash["trading_symbol"].astype(str).str.contains("NIFTY|SENSEX|VIX", case=False, regex=True)
                       & ~cash["instrument_type"].astype(str).isin(["EQ"])]
            os.makedirs(f"{OUT}/meta", exist_ok=True)
            idx[["exchange", "trading_symbol", "groww_symbol", "name", "instrument_type", "exchange_token"]].to_csv(
                f"{OUT}/meta/index_symbols.csv", index=False)
            eq = set(cash[(cash["exchange"] == "NSE")]["trading_symbol"].astype(str))
            fno = df[(df["segment"] == "FNO") & (df["exchange"] == "NSE")]
            under = set(fno["underlying_symbol"].astype(str))
            return {"columns": list(df.columns), "rows": len(df),
                    "cash_types": cash["instrument_type"].astype(str).value_counts().head(12).to_dict(),
                    "fno_types": fno["instrument_type"].astype(str).value_counts().head(6).to_dict(),
                    "index_rows": len(idx), "universe_found": sum(s in eq for s in uni),
                    "universe_missing": [s for s in uni if s not in eq][:20],
                    "universe_fno": sum(s in under for s in uni)}

        def quote():
            q = g.get_quote(trading_symbol="NIFTY", exchange=g.EXCHANGE_NSE, segment=g.SEGMENT_CASH)
            t = q.get("last_trade_time")
            return {"keys": sorted(q.keys()), "last_price": q.get("last_price"), "ohlc": q.get("ohlc"),
                    "volume": q.get("volume"),
                    "last_trade": dt.datetime.fromtimestamp(t / 1000, IST).isoformat() if t else None}

        def stock_quote():
            q = g.get_quote(trading_symbol="KEC", exchange=g.EXCHANGE_NSE, segment=g.SEGMENT_CASH)
            return {k: q.get(k) for k in ("last_price", "ohlc", "volume", "day_change", "week_52_high",
                                           "week_52_low", "upper_circuit_limit", "market_cap")}

        def ltp():
            r = g.get_ltp(exchange_trading_symbols=("NSE_KEC", "NSE_PERSISTENT", "BSE_SENSEX"), segment=g.SEGMENT_CASH)
            return {"result": r}

        def ohlc():
            r = g.get_ohlc(exchange_trading_symbols=("NSE_KEC", "NSE_NIFTY"), segment=g.SEGMENT_CASH)
            return {"result": r}

        def candles():
            end = now().replace(tzinfo=None)
            r = g.get_historical_candles(exchange=g.EXCHANGE_NSE, segment=g.SEGMENT_CASH, groww_symbol="NSE-KEC",
                                         start_time=(end - dt.timedelta(days=12)).strftime("%Y-%m-%d %H:%M:%S"),
                                         end_time=end.strftime("%Y-%m-%d %H:%M:%S"),
                                         candle_interval=g.CANDLE_INTERVAL_DAY)
            c = r.get("candles") or []
            return {"n": len(c), "first": c[0] if c else None, "last": c[-1] if c else None}

        def intraday():
            end = now().replace(tzinfo=None)
            r = g.get_historical_candles(exchange=g.EXCHANGE_NSE, segment=g.SEGMENT_CASH, groww_symbol="NSE-KEC",
                                         start_time=(end - dt.timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S"),
                                         end_time=end.strftime("%Y-%m-%d %H:%M:%S"),
                                         candle_interval=g.CANDLE_INTERVAL_MIN_15)
            c = r.get("candles") or []
            return {"n": len(c), "last": c[-1] if c else None}

        def options():
            ex = g.get_expiries(exchange=g.EXCHANGE_NSE, underlying_symbol="NIFTY")
            exps = sorted(e for e in ex.get("expiries", []) if e >= now().date().isoformat())
            ch = g.get_option_chain(exchange=g.EXCHANGE_NSE, underlying="NIFTY", expiry_date=exps[0])
            strikes = ch.get("strikes", {})
            ce = sum((v.get("CE") or {}).get("open_interest") or 0 for v in strikes.values())
            pe = sum((v.get("PE") or {}).get("open_interest") or 0 for v in strikes.values())
            return {"next_expiries": exps[:4], "underlying_ltp": ch.get("underlying_ltp"),
                    "strikes": len(strikes), "pcr": round(pe / ce, 3) if ce else None}

        def stock_options():
            ex = g.get_expiries(exchange=g.EXCHANGE_NSE, underlying_symbol="KEC")
            exps = sorted(e for e in ex.get("expiries", []) if e >= now().date().isoformat())
            if not exps:
                return {"note": "KEC has no F&O expiries"}
            ch = g.get_option_chain(exchange=g.EXCHANGE_NSE, underlying="KEC", expiry_date=exps[0])
            return {"expiry": exps[0], "strikes": len(ch.get("strikes", {}))}

        for name, fn in [("instruments", instruments), ("quote_index", quote), ("quote_stock", stock_quote),
                         ("ltp", ltp), ("ohlc", ohlc), ("candles_day", candles), ("candles_15m", intraday),
                         ("option_chain_nifty", options), ("option_chain_stock", stock_options)]:
            step(report, name, fn)
            time.sleep(0.5)
    os.makedirs(OUT, exist_ok=True)
    json.dump(report, open(f"{OUT}/status.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "test"
    {"test": cmd_test}[mode]()
