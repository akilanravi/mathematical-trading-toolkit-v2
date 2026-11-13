"""
DhanHQ Fast Data Feed Connector
================================
Fetches live market candles and quotes directly from DhanHQ API v2.
Zero rate limits, official exchange feeds (<250ms).
"""
import os
import requests
import pandas as pd
import datetime

DHAN_BASE_URL = "https://api.dhan.co/v2"
IST = "Asia/Kolkata"

# Major F&O Dhan Security IDs
DHAN_SECURITY_MAP = {
    "NIFTY": ("IDX_I", "13", "INDEX"),
    "BANKNIFTY": ("IDX_I", "25", "INDEX"),
    "FINNIFTY": ("IDX_I", "27", "INDEX"),
    "MIDCPNIFTY": ("IDX_I", "44", "INDEX"),
    "RELIANCE": ("NSE_EQ", "2885", "EQUITY"),
    "HDFCBANK": ("NSE_EQ", "1333", "EQUITY"),
    "ICICIBANK": ("NSE_EQ", "4963", "EQUITY"),
    "SBIN": ("NSE_EQ", "3045", "EQUITY"),
    "TCS": ("NSE_EQ", "11536", "EQUITY"),
    "INFY": ("NSE_EQ", "1594", "EQUITY"),
    "BHARTIARTL": ("NSE_EQ", "10604", "EQUITY"),
    "ITC": ("NSE_EQ", "1660", "EQUITY"),
    "LT": ("NSE_EQ", "11483", "EQUITY"),
    "KOTAKBANK": ("NSE_EQ", "1922", "EQUITY"),
    "AXISBANK": ("NSE_EQ", "5900", "EQUITY"),
    "HINDUNILVR": ("NSE_EQ", "1394", "EQUITY"),
    "TATAMOTORS": ("NSE_EQ", "3456", "EQUITY"),
    "MARUTI": ("NSE_EQ", "10999", "EQUITY"),
    "SUNPHARMA": ("NSE_EQ", "3351", "EQUITY"),
    "BAJFINANCE": ("NSE_EQ", "317", "EQUITY"),
    "TITAN": ("NSE_EQ", "3506", "EQUITY"),
    "TATASTEEL": ("NSE_EQ", "3499", "EQUITY"),
    "COALINDIA": ("NSE_EQ", "20374", "EQUITY"),
    "HINDALCO": ("NSE_EQ", "1363", "EQUITY"),
    "JSWSTEEL": ("NSE_EQ", "11723", "EQUITY"),
    "BAJAJ-AUTO": ("NSE_EQ", "16669", "EQUITY"),
    "HEROMOTOCO": ("NSE_EQ", "1348", "EQUITY"),
    "DIVISLAB": ("NSE_EQ", "10940", "EQUITY"),
    "CIPLA": ("NSE_EQ", "694", "EQUITY"),
    "APOLLOHOSP": ("NSE_EQ", "157", "EQUITY"),
    "CHOLAFIN": ("NSE_EQ", "685", "EQUITY"),
    "VEDL": ("NSE_EQ", "3063", "EQUITY"),
    "EICHERMOT": ("NSE_EQ", "910", "EQUITY"),
    "GRASIM": ("NSE_EQ", "1232", "EQUITY"),
    "BEL": ("NSE_EQ", "383", "EQUITY"),
    "HAL": ("NSE_EQ", "2303", "EQUITY"),
    "NTPC": ("NSE_EQ", "11630", "EQUITY"),
    "POWERGRID": ("NSE_EQ", "14977", "EQUITY"),
    "ONGC": ("NSE_EQ", "2475", "EQUITY"),
    "BPCL": ("NSE_EQ", "526", "EQUITY"),
    "DLF": ("NSE_EQ", "14732", "EQUITY"),
    "INDUSINDBK": ("NSE_EQ", "5258", "EQUITY"),
    "CANBK": ("NSE_EQ", "10794", "EQUITY"),
    "SHRIRAMFIN": ("NSE_EQ", "4306", "EQUITY"),
    "TRENT": ("NSE_EQ", "1964", "EQUITY"),
    "POLYCAB": ("NSE_EQ", "9590", "EQUITY"),
    "DIXON": ("NSE_EQ", "21690", "EQUITY"),
    "PERSISTENT": ("NSE_EQ", "18365", "EQUITY"),
    "COFORGE": ("NSE_EQ", "11543", "EQUITY"),
    "LTIM": ("NSE_EQ", "17818", "EQUITY"),
    "VBL": ("NSE_EQ", "17963", "EQUITY"),
}

def get_dhan_credentials():
    """Reads credentials safely from Streamlit secrets or environment variables."""
    client_id, token = None, None
    try:
        import streamlit as st
        if "dhan" in st.secrets:
            client_id = str(st.secrets["dhan"].get("client_id", "")).strip()
            token = str(st.secrets["dhan"].get("access_token", "")).strip()
    except Exception:
        pass
    if not client_id:
        client_id = os.environ.get("DHAN_CLIENT_ID", "").strip()
    if not token:
        token = os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
    return client_id, token

def fetch_dhan_candles(symbol: str, interval: str = "15", days: int = 10) -> pd.DataFrame:
    """
    Fetches completed OHLCV candles from Dhan API v2.
    interval: '1', '5', '15', '25', '60'
    """
    client_id, token = get_dhan_credentials()
    if not client_id or not token:
        return pd.DataFrame()

    sym_clean = symbol.upper().replace(".NS", "").replace("^NSEI", "NIFTY").replace("^NSEBANK", "BANKNIFTY")
    if sym_clean not in DHAN_SECURITY_MAP:
        return pd.DataFrame()

    segment, sec_id, inst_type = DHAN_SECURITY_MAP[sym_clean]
    
    # Format dates
    now = pd.Timestamp.now(tz=IST)
    from_date = (now - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    to_date = now.strftime("%Y-%m-%d")

    url = f"{DHAN_BASE_URL}/charts/intraday"
    headers = {
        "access-token": token,
        "client-id": client_id,
        "Content-Type": "application/json"
    }
    payload = {
        "securityId": sec_id,
        "exchangeSegment": segment,
        "instrument": inst_type,
        "interval": str(interval),
        "oi": False,
        "fromDate": from_date,
        "toDate": to_date
    }

    try:
        res = requests.post(url, headers=headers, json=payload, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if "open" in data and len(data["open"]) > 0:
                timestamps = pd.to_datetime(data["timestamp"], unit="s", utc=True).tz_convert(IST)
                df = pd.DataFrame({
                    "open": data["open"],
                    "high": data["high"],
                    "low": data["low"],
                    "close": data["close"],
                    "volume": data["volume"]
                }, index=timestamps)
                return df[~df.index.duplicated()].sort_index()
    except Exception:
        pass
    return pd.DataFrame()

def fetch_dhan_ltp_batch(symbols: list[str]) -> dict[str, float]:
    """Fetches real-time LTP quotes for a batch of instruments in one call."""
    client_id, token = get_dhan_credentials()
    if not client_id or not token:
        return {}

    url = f"{DHAN_BASE_URL}/marketfeed/ltp"
    headers = {
        "access-token": token,
        "client-id": client_id,
        "Content-Type": "application/json"
    }
    
    payload_dict = {}
    reverse_map = {}
    for s in symbols:
        sym_clean = s.upper().replace(".NS", "").replace("^NSEI", "NIFTY").replace("^NSEBANK", "BANKNIFTY")
        if sym_clean in DHAN_SECURITY_MAP:
            seg, sec_id, _ = DHAN_SECURITY_MAP[sym_clean]
            if seg not in payload_dict:
                payload_dict[seg] = []
            payload_dict[seg].append(int(sec_id))
            reverse_map[(seg, int(sec_id))] = s

    if not payload_dict:
        return {}

    try:
        res = requests.post(url, headers=headers, json=payload_dict, timeout=5)
        if res.status_code == 200:
            data = res.json().get("data", {})
            out = {}
            for seg, scrips in data.items():
                for sec_id_str, quote_data in scrips.items():
                    key = (seg, int(sec_id_str))
                    if key in reverse_map:
                        out[reverse_map[key]] = float(quote_data.get("last_price", 0.0))
            return out
    except Exception:
        pass
    return {}
