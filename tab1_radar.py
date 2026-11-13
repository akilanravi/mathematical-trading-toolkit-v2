""Tab 1 only. Dhan cash candles, shared MTT scoring, background scan."""
import io
import re
import threading
import time
from datetime import time as clock

import numpy as np
import pandas as pd
import requests
import streamlit as st

import dhan_feed
import mtt_v2 as m

IST = "Asia/Kolkata"
MASTER = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
API = "https://api.dhan.co/v2/charts/intraday"
FULL, SAMPLE = "All F&O stocks", "50-stock diagnostic sample (alphabetical)"
BAR = pd.Timedelta(minutes=5)
COLS = ["open", "high", "low", "close", "volume"]
GATE = threading.Lock()
NEXT_REQUEST = 0.0
COOLDOWN = 0.0
MASTER_LOCK = threading.Lock()
MASTER_CACHE = None


def discover():
    global MASTER_CACHE
    with MASTER_LOCK:
        if MASTER_CACHE is None or time.monotonic() - MASTER_CACHE[0] > 21600:
            MASTER_CACHE = (time.monotonic(), _load_master())
        return MASTER_CACHE[1]


def _load_master():
    r = requests.get(MASTER, timeout=(5, 25))
    r.raise_for_status()
    df = pd.read_csv(io.BytesIO(r.content), dtype=str, keep_default_na=False)
    df.columns = df.columns.str.strip().str.upper().str.lstrip("\ufeff")
    needed = {"EXCH_ID", "SEGMENT", "INSTRUMENT", "SECURITY_ID",
              "UNDERLYING_SECURITY_ID", "UNDERLYING_SYMBOL", "SM_EXPIRY_DATE"}
    missing = needed.difference(df.columns)
    if missing:
        raise ValueError("Missing master columns: " + ", ".join(sorted(missing)))
    nse = df.loc[df.EXCH_ID.str.strip().str.upper().eq("NSE")]
    segment = nse.SEGMENT.str.strip().str.upper()
    instrument = nse.INSTRUMENT.str.strip().str.upper()
    cash = nse.loc[segment.eq("E") & instrument.eq("EQUITY"), "SECURITY_ID"]
    futures = nse.loc[segment.eq("D") & instrument.eq("FUTSTK")].copy()
    expiry = pd.to_datetime(futures.SM_EXPIRY_DATE.str.strip().str[:10], errors="coerce")
    futures = futures.loc[expiry.ge(pd.Timestamp.now(tz=IST).tz_localize(None).normalize())]
    ids = set(cash.str.strip())
    pairs = {}
    for symbol, sid in futures[["UNDERLYING_SYMBOL", "UNDERLYING_SECURITY_ID"]].itertuples(index=False, name=None):
        symbol, sid = symbol.strip().upper(), sid.strip()
        if symbol and "NSETEST" not in symbol:
            pairs.setdefault(symbol, set()).add(sid)
    mapping = {sym: next(iter(sids)) for sym, sids in pairs.items()
               if len(sids) == 1 and sids <= ids and next(iter(sids)).isdigit()}
    if not mapping:
        raise ValueError("No unexpired stock futures matched NSE cash security IDs.")
    return mapping, len(pairs) - len(mapping)


class HaltScan(Exception):
    """Account-wide or rate-limit failure. Do not repeat it for each stock."""


def fetch_candles(session, headers, sid, cutoff):
    global NEXT_REQUEST, COOLDOWN
    payload = {
        "securityId": sid, "exchangeSegment": "NSE_EQ", "instrument": "EQUITY",
        "interval": "5", "oi": False,
        "fromDate": (cutoff - pd.Timedelta(days=12)).strftime("%Y-%m-%d 09:15:00"),
        "toDate": cutoff.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with GATE:
        now = time.monotonic()
        if now < COOLDOWN:
            raise HaltScan(f"Dhan rate-limit cooldown, {int(COOLDOWN-now)+1}s remaining.")
        time.sleep(max(0, NEXT_REQUEST - now))
        NEXT_REQUEST = time.monotonic() + 0.5
        r = session.post(API, headers=headers, json=payload, timeout=(4, 7))
        if r.status_code == 429:
            retry = r.headers.get("Retry-After", "")
            COOLDOWN = time.monotonic() + max(60, int(retry) if retry.isdigit() else 0)
            raise HaltScan("Dhan HTTP 429. Scan paused for the rate-limit cooldown.")
    try:
        obj = r.json()
    except ValueError:
        obj = {}
    if not isinstance(obj, dict):
        obj = {}
    code = str(obj.get("errorCode") or obj.get("error_code") or obj.get("status") or "").strip()
    safe_code = code if re.fullmatch(r"(?:DH[-_]?)?\d{3}", code, re.I) else ""
    detail = f"HTTP {r.status_code}" + (f" / {safe_code}" if safe_code else "")
    if code.upper().replace("_", "-") == "DH-902":
        raise HaltScan("Dhan DH-902. The account/token was not granted access to this Data API.")
    if r.status_code in (401, 403):
        raise HaltScan(f"Dhan {detail}. Check access token and Data API permission.")
    if not r.ok:
        raise ValueError(f"Dhan {detail}")
    arrays = [obj.get("timestamp"), *(obj.get(c) for c in COLS)]
    sizes = [len(a) if isinstance(a, list) else 0 for a in arrays]
    if len(set(sizes)) != 1 or sizes[0] == 0:
        raise ValueError(f"Dhan candle arrays empty or mismatched (timestamp, OHLCV) {sizes}")
    index = pd.to_datetime(pd.to_numeric(arrays[0], errors="coerce"), unit="s",
                           utc=True, errors="coerce").tz_convert(IST)
    df = pd.DataFrame({c: pd.to_numeric(a, errors="coerce")
                       for c, a in zip(COLS, arrays[1:])}, index=index)
    df = df.loc[~df.index.isna() & ~df.index.duplicated(keep="last")].sort_index()
    df = df.loc[(df.index + BAR <= cutoff) & (df.index.dayofweek < 5)]
    df = df.between_time("09:15", "15:25")
    df = df.loc[np.isfinite(df).all(axis=1) & (df.low > 0) & (df.volume >= 0)
                & (df.high >= df[["open", "close"]].max(axis=1))
                & (df.low <= df[["open", "close"]].min(axis=1))]
    if len(df) < 70:
        raise ValueError(f"Only {len(df)} valid completed candles")
    return df


def analyse(symbol, df):
    # Same MTT indicators and weighted points, without option-price models.
    cfg = m.Config()
    feat = m.build_features(df, "intraday", cfg)
    sessions = m.period_ohlc(df, "intraday")
    prior = sessions.loc[sessions.index < pd.Timestamp(df.index[-1].date())]
    if len(prior) < 2:
        raise ValueError("Two previous trading sessions required for CPR")
    row = feat.iloc[-1]
    price, atr, ema = float(row.close), float(row.atr), float(row.ema21)
    volume = float(row.volr)
    if not np.isfinite([price, atr, ema, volume]).all() or atr <= 0:
        raise ValueError("Invalid ATR, EMA or volume multiple")
    x = {k: row[k] for k in ("ema9", "ema21", "ema50", "vwap", "rsi", "st", "volr")}
    x.update(m.levels_from(prior.iloc[-1], prior.iloc[-2], cfg))
    x.update(price=price, candle=int(np.sign(row.close - row.open)))
    score = m.score_setup(x, cfg, "intraday")
    bull, bear, maximum = score["bull"], score["bear"], score["max"]
    gap = abs(price - ema) / atr
    if maximum <= 0 or bull == bear or max(bull, bear) < 5 or volume < 0.8 or gap > 1.5:
        return None
    side = "CE" if bull > bear else "PE"
    rank = 70 * max(bull, bear) / maximum + 15 * min(volume / 1.5, 1) + 15 * (1 - gap / 1.5)
    result = {"Symbol": symbol, "Direction": side, "Close ₹": round(price, 2),
              "Score": round(rank, 1), "Bull": bull, "Bear": bear, "Max": maximum,
              "Volume ×": round(volume, 2), "Candle IST": df.index[-1].strftime("%Y-%m-%d %H:%M"),
              "Source": "Dhan 5m"}
    for label, _, b, p, available, _ in score["comps"]:
        result[label] = ("✅" if (b if side == "CE" else p) else "❌") if available else "—"
    return result


class Scan:
    def __init__(self, selection, cutoff):
        self.selection, self.cutoff = selection, cutoff
        self.lock = threading.Lock()
        self.total = self.unmatched = self.matched = 0
        self.done = False
        self.problem = ""
        self.rows, self.errors, self.times = [], [], []
        self.thread = None

    def snapshot(self):
        with self.lock:
            return (self.total, self.matched, self.unmatched, self.done, self.problem,
                    self.rows.copy(), self.errors.copy(), self.times.copy())

    def run(self, cid, token):
        try:
            if not cid or not token:
                raise HaltScan("Dhan client ID or access token is missing from Streamlit Secrets.")
            mapping, unmatched = discover()
            symbols = sorted(mapping)
            if self.selection == SAMPLE:
                symbols = symbols[:50]
            with self.lock:
                self.total, self.matched, self.unmatched = len(symbols), len(mapping), unmatched
            headers = {"access-token": token, "client-id": cid, "Accept": "application/json"}
            with requests.Session() as session:
                for symbol in symbols:
                    try:
                        df = fetch_candles(session, headers, mapping[symbol], self.cutoff)
                        row = analyse(symbol, df)
                        with self.lock:
                            self.times.append(df.index[-1])
                            if row is not None:
                                self.rows.append((df.index[-1], row))
                    except HaltScan:
                        raise
                    except Exception as exc:
                        reason = ("Dhan request timed out" if isinstance(exc, requests.Timeout) else
                                  "Dhan network request failed" if isinstance(exc, requests.RequestException)
                                  else str(exc)[:200] if isinstance(exc, (ValueError, RuntimeError))
                                  else f"Analysis error ({type(exc).__name__})")
                        with self.lock:
                            self.errors.append({"Symbol": symbol, "Reason": reason})
        except HaltScan as exc:
            with self.lock:
                self.problem = str(exc)
        except Exception as exc:
            with self.lock:
                self.problem = (str(exc)[:200] if isinstance(exc, ValueError) else
                                f"Scan stopped during master loading or analysis ({type(exc).__name__}).")
        finally:
            with self.lock:
                self.done = True


def _launch(selection, cutoff):
    job = Scan(selection, cutoff)
    cid, token = dhan_feed.get_dhan_credentials()
    job.thread = threading.Thread(target=job.run, args=(cid, token), daemon=True, name="mtt-tab1-scan")
    job.thread.start()
    return job


@st.fragment(run_every="2s")
def render():
    st.subheader("🎯 Live F&O Radar")
    st.caption("Dhan only · Completed 5-minute stock candles · Not live option quotes")
    selection = st.selectbox("Universe", (FULL, SAMPLE), key="tab1_background_universe")
    now = pd.Timestamp.now(tz=IST)
    cutoff = now.floor("5min")
    market_window = now.dayofweek < 5 and clock(9, 20) <= now.time() < clock(15, 35)
    state = st.session_state.setdefault("tab1_background_v1", {"job": None, "last": {}})
    job = state["job"]
    active = job is not None and not job.snapshot()[3]
    refresh = st.button("Refresh Tab 1", key="tab1_background_refresh", disabled=active)
    if not active:
        if job is not None:
            saved = job.snapshot()
            if saved[7] and not saved[4]:
                state["last"][job.selection] = (job.cutoff, saved)
        new_bar = job is not None and cutoff > job.cutoff and market_window
        if job is None or job.selection != selection or refresh or new_bar:
            job = state["job"] = _launch(selection, cutoff)

    total, matched, unmatched, done, problem, rows, errors, times = job.snapshot()
    checked = len(times) + len(errors)
    if matched:
        st.info(f"Dhan F&O stock IDs matched {matched} · Unmatched {unmatched} · Selected {total}")
    if job.selection != selection:
        st.caption("The selected universe will start when the current scan finishes.")
    if not done:
        st.progress(checked / total if total else 0,
                    text=f"Scanning Dhan — {checked}/{total} checked" if total else "Loading Dhan instrument master…")
    if problem:
        st.error(problem)
    st.caption(f"Analyzed {len(times)} · Errors {len(errors)} · Not checked {max(0, total-checked)}"
               f" · Scan cutoff {job.cutoff:%d %b %H:%M} IST")

    old = state["last"].get(selection)
    if old and ((not done) or problem or job.selection != selection):
        st.caption(f"Showing the last completed scan ({old[0]:%d %b %H:%M} IST) while current results are unavailable.")
        _, _, _, _, _, rows, _, times = old[1]
    elif not done and times:
        st.caption("Partial rankings. Results can change as more stocks are checked.")
    newest = max(times, default=None)
    if newest is not None:
        st.caption(f"Newest displayed candle {newest:%d %b %H:%M} IST. Prior sessions or bars over 10 minutes older are excluded.")
        expected = cutoff - BAR if market_window else now.normalize() + pd.Timedelta(hours=15, minutes=25)
        if not market_window or newest < expected - 2 * BAR:
            st.warning("Last available candles shown. This view is not confirmed current.")
    ranked = [row for ts, row in rows if ts.date() == newest.date() and newest-ts <= 2*BAR] if newest is not None else []
    for side, title in (("CE", "🟢 Top 10 Bullish"), ("PE", "🔴 Top 10 Bearish")):
        st.markdown("### " + title)
        picks = sorted((r for r in ranked if r["Direction"] == side),
                       key=lambda r: (-r["Score"], -r["Volume ×"], r["Symbol"]))[:10]
        if picks:
            st.dataframe(pd.DataFrame(picks), hide_index=True, use_container_width=True)
        elif not done:
            st.caption("Waiting for qualifying results.")
        elif times:
            st.info("No qualifying candidates in the available candles.")
        else:
            st.info("No usable Dhan results. Check Data diagnostics.")
    with st.expander("Data diagnostics"):
        if errors:
            report = pd.DataFrame(errors)
            st.dataframe(report, hide_index=True, use_container_width=True)
            st.download_button("Export diagnostics CSV", report.to_csv(index=False),
                               "tab1_diagnostics.csv", "text/csv", key="tab1_background_export")
        else:
            st.caption("No per-stock errors recorded.")
    st.caption("Watchlist only. The MTT engine uses eight named checks worth up to ten points. "
               "Strikes, expiry and option premiums are not verified here.")
