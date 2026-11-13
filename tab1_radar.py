"""Tab 1 Live F&O Radar. Uses Dhan candles and the existing MTT scoring engine."""

import re

import numpy as np
import pandas as pd
import streamlit as st

import dhan_feed
import mtt_v2 as m


IST = "Asia/Kolkata"
BAR = pd.Timedelta(minutes=5)
OHLCV = ("open", "high", "low", "close", "volume")
INDEX_NAMES = {
    "NIFTY", "NIFTY50", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
    "NIFTYNXT50", "SENSEX", "BANKEX", "INDIAVIX",
}
ALL_STOCKS = "All mapped F&O stocks"
SAMPLE = "50-stock diagnostic sample (alphabetical)"


def _column(frame, *names):
    def key(value):
        return re.sub(r"[^a-z0-9]", "", str(value).lower())

    columns = {key(column): column for column in frame.columns}
    return next((columns[key(name)] for name in names if key(name) in columns), None)


def _text(series):
    return series.fillna("").astype(str).str.strip().str.upper()


def _symbol(value):
    return str(value).strip().upper().removesuffix(".NS").removesuffix("-EQ")


def _security_id(value):
    value = str(value).strip()
    if not re.fullmatch(r"\d+(?:\.0+)?", value):
        return None
    number = int(value.split(".", 1)[0])
    return str(number) if number > 0 else None


def _master_maps(frame, today):
    """Extract NSE cash IDs and stock-futures membership from the Dhan master."""
    id_col = _column(frame, "SEM_SMST_SECURITY_ID", "SECURITY_ID")
    trading_col = _column(
        frame, "SEM_TRADING_SYMBOL", "TRADING_SYMBOL", "SYMBOL_NAME", "SYMBOL"
    )
    exchange_col = _column(frame, "SEM_EXM_EXCH_ID", "EXCH_ID", "EXCHANGE")
    segment_col = _column(frame, "SEM_SEGMENT", "SEGMENT", "EXCHANGE_SEGMENT")
    type_col = _column(frame, "SEM_INSTRUMENT_NAME", "INSTRUMENT", "INSTRUMENT_TYPE")
    series_col = _column(frame, "SEM_SERIES", "SERIES")
    expiry_col = _column(frame, "SEM_EXPIRY_DATE", "SM_EXPIRY_DATE", "EXPIRY_DATE")
    underlying_col = _column(
        frame, "UNDERLYING_SYMBOL", "SEM_UNDERLYING_SYMBOL", "SM_SYMBOL_NAME"
    )
    underlying_id_col = _column(
        frame, "UNDERLYING_SECURITY_ID", "SEM_UNDERLYING_SECURITY_ID"
    )

    if any(col is None for col in (id_col, trading_col, exchange_col, type_col)):
        raise ValueError(f"Unrecognized Dhan master columns: {list(frame.columns)[:20]}")

    exchange = _text(frame[exchange_col])
    kind = _text(frame[type_col])
    cash_mask = exchange.isin(["NSE", "NSE_EQ"]) & kind.isin(["EQUITY", "EQ"])
    futures_mask = exchange.isin(["NSE", "NSE_FNO"]) & kind.eq("FUTSTK")

    if segment_col is not None:
        segment = _text(frame[segment_col])
        cash_mask &= segment.isin(["E", "EQ", "EQUITY", "NSE_EQ"])
        futures_mask &= segment.isin(["D", "FNO", "NSE_FNO", "DERIVATIVE", "DERIVATIVES"])

    if series_col is not None:
        cash_mask &= _text(frame[series_col]).isin(["", "EQ"])

    cash_candidates = {}
    for name, raw_id in frame.loc[cash_mask, [trading_col, id_col]].itertuples(
        index=False, name=None
    ):
        name, sid = _symbol(name), _security_id(raw_id)
        if sid and name not in INDEX_NAMES and re.fullmatch(r"[A-Z0-9][A-Z0-9&_.-]*", name):
            cash_candidates.setdefault(name, set()).add(sid)

    # Never select an arbitrary ID if several different IDs match the same symbol.
    cash = {name: next(iter(ids)) for name, ids in cash_candidates.items() if len(ids) == 1}
    names_by_id = {}
    for name, sid in cash.items():
        names_by_id.setdefault(sid, set()).add(name)

    futures = frame.loc[futures_mask]
    if expiry_col is not None:
        expiry = pd.to_datetime(
            futures[expiry_col], format="mixed", errors="coerce"
        )
        futures = futures.loc[expiry.notna() & (expiry.dt.date >= today)]

    stock_names = set()
    for _, row in futures.iterrows():
        if underlying_id_col is not None:
            sid = _security_id(row[underlying_id_col])
            if sid:
                matches = names_by_id.get(sid, set())
                if len(matches) == 1:
                    stock_names.update(matches)
                continue

        if underlying_col is not None:
            name = _symbol(row[underlying_col])
            if name in cash:
                stock_names.add(name)
                continue

        contract = str(row[trading_col]).strip().upper()
        # Preserve hyphenated stock names such as BAJAJ-AUTO.
        name = re.split(r"-(?=\d{1,2}[A-Z]{3})|\s", contract, maxsplit=1)[0]
        if name in cash:
            stock_names.add(name)

    return cash, stock_names


@st.cache_data(ttl=3600, show_spinner=False)
def discover():
    import io
    import requests

    url = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
    fields = {
        "EXCH_ID", "SEGMENT", "INSTRUMENT", "SECURITY_ID",
        "SYMBOL_NAME", "SERIES", "UNDERLYING_SECURITY_ID",
        "UNDERLYING_SYMBOL", "SM_EXPIRY_DATE",
    }

    response = requests.get(url, timeout=(10, 60))
    response.raise_for_status()
    master = pd.read_csv(
        io.BytesIO(response.content),
        encoding="utf-8-sig",
        dtype=str,
        usecols=lambda name: name in fields,
        low_memory=False,
    )

    missing = fields - set(master.columns)
    if missing:
        raise ValueError(f"Dhan detailed master missing fields: {sorted(missing)}")

    exchange = _text(master["EXCH_ID"])
    segment = _text(master["SEGMENT"])
    kind = _text(master["INSTRUMENT"])
    series = _text(master["SERIES"])

    cash_rows = master.loc[
        exchange.eq("NSE")
        & segment.eq("E")
        & kind.isin(["EQUITY", "EQ"])
        & series.isin(["", "EQ"])
    ]
    cash_ids = {
        sid
        for name, raw_id in cash_rows[["SYMBOL_NAME", "SECURITY_ID"]].itertuples(
            index=False, name=None
        )
        if "NSETEST" not in _symbol(name)
        if (sid := _security_id(raw_id)) is not None
    }

    futures = master.loc[
        exchange.eq("NSE") & segment.eq("D") & kind.eq("FUTSTK")
    ]
    expiry = pd.to_datetime(
        futures["SM_EXPIRY_DATE"], format="mixed", errors="coerce"
    )
    today = pd.Timestamp.now(tz=IST).date()
    active = futures.loc[expiry.notna() & (expiry.dt.date >= today)]

    candidates = {}
    test_symbols = set()
    for raw_name, raw_id in active[
        ["UNDERLYING_SYMBOL", "UNDERLYING_SECURITY_ID"]
    ].itertuples(index=False, name=None):
        name = _symbol(raw_name)
        if "NSETEST" in name:
            test_symbols.add(name)
            continue
        if pd.isna(raw_name) or name in INDEX_NAMES or not re.fullmatch(
            r"[A-Z0-9][A-Z0-9&_.-]{1,29}", name
        ):
            continue

        ids = candidates.setdefault(name, set())
        sid = _security_id(raw_id)
        if sid is not None:
            ids.add(sid)

    mapping = {}
    for name, ids in sorted(candidates.items()):
        if len(ids) == 1:
            sid = next(iter(ids))
            if sid in cash_ids:
                mapping[name] = sid

    unmatched = len(candidates) - len(mapping)
    if not mapping:
        raise RuntimeError(
            f"No stock futures mapped to NSE cash IDs. "
            f"Active futures rows: {len(active)}. "
            f"Stock symbols found: {len(candidates)}. "
            f"NSETEST symbols excluded: {len(test_symbols)}."
        )

    source = (
        f"Dhan detailed master, joined by underlying ID · "
        f"Stock symbols found: {len(candidates)} · Unmatched: {unmatched} · "
        f"NSETEST excluded: {len(test_symbols)}"
    )
    return mapping, source, None

def normalize_candles(payload):
    if not isinstance(payload, dict):
        raise ValueError("Dhan candle response is not a JSON object")

    timestamps = payload.get("timestamp")
    if timestamps is None:
        timestamps = payload.get("start_Time")

    if not isinstance(timestamps, list) or any(
        not isinstance(payload.get(col), list) for col in OHLCV
    ):
        raise ValueError("Dhan response is missing timestamp or OHLCV arrays")

    sizes = {len(timestamps), *(len(payload[col]) for col in OHLCV)}
    if len(sizes) != 1 or len(timestamps) < 70:
        raise ValueError("Dhan returned insufficient candles or mismatched array lengths")

    index = pd.to_datetime(timestamps, unit="s", utc=True, errors="coerce").tz_convert(IST)
    frame = pd.DataFrame(
        {
            col: pd.to_numeric(pd.Series(payload[col]), errors="coerce").to_numpy()
            for col in OHLCV
        },
        index=index,
    )
    frame = frame.loc[~frame.index.isna()]
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna()
    frame = frame.loc[~frame.index.duplicated(keep="last")].sort_index()
    frame = frame.loc[
        (frame["low"] > 0)
        & (frame["high"] >= frame[["open", "close"]].max(axis=1))
        & (frame["low"] <= frame[["open", "close"]].min(axis=1))
        & (frame["volume"] >= 0)
    ]
    return frame


@st.cache_data(ttl=300, show_spinner=False)
def candles(symbol, sid):
    now = pd.Timestamp.now(tz=IST)
    payload = dhan_feed._request(
        "/charts/intraday",
        {
            "securityId": sid,
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "interval": "5",
            "oi": False,
            "fromDate": (now - pd.Timedelta(days=12)).strftime("%Y-%m-%d %H:%M:%S"),
            "toDate": now.strftime("%Y-%m-%d %H:%M:%S"),
        },
        timeout=15,
    )
    frame = normalize_candles(payload)
    frame = frame.loc[frame.index + BAR <= pd.Timestamp.now(tz=IST)]
    if len(frame) < 70:
        raise ValueError(f"{symbol}: fewer than 70 valid completed candles")
    return frame


def rank_one(symbol, frame):
    result = m.analyse(symbol, "5m", "live", m.Config(), df=frame, vix_val=0.14)
    scores = result["score"]
    last = result["features"].iloc[-1]
    maximum = float(scores["max"])
    bull, bear = int(scores["bull"]), int(scores["bear"])
    volume = float(last["volr"])
    atr, price, ema21 = float(result["atr"]), float(result["price"]), float(last["ema21"])

    if (
        not all(np.isfinite(x) for x in (maximum, volume, atr, price, ema21))
        or maximum <= 0
        or atr <= 0
        or price <= 0
        or not (0 <= bull <= maximum and 0 <= bear <= maximum)
    ):
        raise ValueError("Invalid technical inputs")

    distance = abs(price - ema21)
    points = max(bull, bear)
    if distance > 1.5 * atr or volume < 0.8 or points < 5 or bull == bear:
        return None

    score = (
        70 * points / maximum
        + 15 * min(volume / 1.5, 1)
        + 15 * max(0, 1 - distance / (1.5 * atr))
    )
    direction = "CE" if bull > bear else "PE"
    row = {
        "Symbol": symbol,
        "Direction": direction,
        "Close ₹": round(price, 2),
        "Score": round(score, 1),
        "Bull": bull,
        "Bear": bear,
        "Max": int(maximum),
        "Volume ×": round(volume, 2),
        "Candle IST": pd.Timestamp(result["time"]).strftime("%Y-%m-%d %H:%M"),
        "Source": "Dhan 5m",
    }
    for name, _, bull_points, bear_points, available, _ in scores.get("comps", []):
        points_for_side = bull_points if direction == "CE" else bear_points
        row[name] = "➖" if not available else ("✅" if points_for_side > 0 else "❌")
    return row


def _error_message(exc):
    message = str(exc)
    client_id, token = dhan_feed.get_dhan_credentials()
    for secret in (client_id, token):
        if secret:
            message = message.replace(secret, "[redacted]")
    return message[:240]


@st.cache_data(ttl=300, show_spinner=False)
def scan(mapping, limit):
    rows, errors = [], []
    valid = 0
    selected = list(mapping.items())[:limit]
    for symbol, sid in selected:
        try:
            frame = candles(symbol, sid)
            row = rank_one(symbol, frame)
            valid += 1
            if row is not None:
                rows.append(row)
        except Exception as exc:
            message = _error_message(exc)
            errors.append({"Symbol": symbol, "Reason": message})
            if re.search(r"\b(?:401|403|429|805)\b|DH-90[124]", message, re.I):
                break

    if rows:
        latest = max(pd.Timestamp(row["Candle IST"]) for row in rows)
        rows = [
            row for row in rows
            if pd.Timestamp(row["Candle IST"]).date() == latest.date()
            and latest - pd.Timestamp(row["Candle IST"]) <= pd.Timedelta(minutes=10)
        ]
    return rows, errors, valid


@st.fragment(run_every="60s")
def render():
    st.subheader("🎯 Live F&O Radar")
    st.caption(
        "Tab 1 · Dhan universe fallback enabled · Completed 5-minute candles only. "
        "Prices are candle closes, not live option quotes."
    )
    selection = st.selectbox("Universe", [ALL_STOCKS, SAMPLE], key="tab1_universe")

    if st.button("Refresh Tab 1", key="tab1_refresh"):
        discover.clear()
        candles.clear()
        scan.clear()

    client_id, token = dhan_feed.get_dhan_credentials()
    if not client_id or not token:
        st.warning("Dhan client ID or access token is missing from Streamlit Secrets.")
        return

    try:
        mapping, source, nse_count = discover()
    except Exception as exc:
        st.error("Universe discovery failed: " + _error_message(exc))
        return

      limit = len(mapping) if selection == ALL_STOCKS else min(50, len(mapping))
    nse_note = f" · NSE list: {nse_count}" if nse_count is not None else ""
    st.info(
        f"F&O source: {source} · Matched Dhan cash IDs: {len(mapping)}"
        f"{nse_note} · Selected for scan: {limit}"
    )
    if nse_count is None:
        st.caption("Membership is derived from Dhan and not independently confirmed by NSE.")

    rows, errors, valid = scan(mapping, limit)
    attempted = valid + len(errors)
    st.caption(
        f"Analyzed: {valid}/{limit} · Errors: {len(errors)} · "
        f"Not attempted: {limit - attempted} · Qualified: {len(rows)}"
    )

    for side, title in (
        ("CE", "🟢 Top 10 Bullish (CE watchlist)"),
        ("PE", "🔴 Top 10 Bearish (PE watchlist)"),
    ):
        st.markdown("### " + title)
        ranked = sorted(
            (row for row in rows if row["Direction"] == side),
            key=lambda row: (-row["Score"], -row["Volume ×"], row["Symbol"]),
        )[:10]
        if ranked:
            st.dataframe(pd.DataFrame(ranked), hide_index=True, use_container_width=True)
        elif valid:
            st.info("No candidates on this side passed the filters in the available data.")
        else:
            st.info("No valid Dhan analyses yet. See Data diagnostics.")

    if errors:
        with st.expander("Data diagnostics"):
            st.dataframe(pd.DataFrame(errors), hide_index=True, use_container_width=True)

    st.caption("Watchlist only. Option strike, expiry, premium, and liquidity are not verified.")
