"""Dhan v2 market data connector. No claims of unlimited API rates or guaranteed latency."""
from __future__ import annotations
import io, os, re, time, threading, logging
from pathlib import Path
import requests
import pandas as pd

LOG = logging.getLogger(__name__)
BASE = 'https://api.dhan.co/v2'
IST = 'Asia/Kolkata'
MASTER_URLS = [
 'https://images.dhan.co/api-data/api-scrip-master-detailed.csv',
 'https://images.dhan.co/api-data/api-scrip-master.csv',
]
NSE_LOTS_URL = 'https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv'
_LOCK = threading.Lock()
_LAST = 0.0
_MIN_INTERVAL = float(os.getenv('DHAN_REQUEST_GAP', '0.55'))
_MASTER_CACHE = None
_UNIVERSE_CACHE = None
DHAN_SECURITY_MAP = {}  # populated dynamically by load_fo_universe(); no fixed 47-stock ceiling


def get_dhan_credentials():
    cid = os.getenv('DHAN_CLIENT_ID', '').strip()
    token = os.getenv('DHAN_ACCESS_TOKEN', '').strip()
    if cid and token:
        return cid, token
    try:
        import streamlit as st
        cfg = st.secrets
        group = cfg.get('dhan', {})
        return cid or str(cfg.get('DHAN_CLIENT_ID', group.get('client_id', ''))).strip(), token or str(cfg.get('DHAN_ACCESS_TOKEN', group.get('access_token', ''))).strip()
    except Exception:
        return cid, token


def _headers():
    cid, token = get_dhan_credentials()
    if not cid or not token:
        raise RuntimeError('Dhan credentials missing in environment or Streamlit secrets')
    return {'access-token': token, 'client-id': cid, 'Content-Type': 'application/json', 'Accept': 'application/json'}


def _request(path, payload, timeout=15):
    global _LAST
    with _LOCK:
        delay = _MIN_INTERVAL - (time.monotonic() - _LAST)
        if delay > 0:
            time.sleep(delay)
        _LAST = time.monotonic()
        for attempt in range(3):
            try:
                response = requests.post(BASE + path, headers=_headers(), json=payload, timeout=timeout)
            except requests.RequestException as exc:
                if attempt == 2:
                    raise RuntimeError(f'Dhan network error: {exc}') from exc
                time.sleep(2 ** attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    try:
                        retry = float(response.headers.get('Retry-After', 0))
                    except ValueError:
                        retry = 0
                    time.sleep(min(max(retry, 2 ** (attempt + 1)), 30))
                    continue
            if not response.ok:
                raise RuntimeError(f'Dhan HTTP {response.status_code}: {response.text[:200]}')
            try:
                return response.json()
            except ValueError as exc:
                raise RuntimeError('Dhan returned non-JSON data') from exc
    raise RuntimeError('Dhan request retries exhausted')


def _col(df, *names):
    columns = {re.sub('[^a-z0-9]', '', str(c).lower()): c for c in df.columns}
    for name in names:
        match = columns.get(re.sub('[^a-z0-9]', '', name.lower()))
        if match is not None:
            return match
    return None


def _master():
    global _MASTER_CACHE
    if _MASTER_CACHE is not None:
        return _MASTER_CACHE
    errors = []
    for url in MASTER_URLS:
        try:
            r = requests.get(url, timeout=45, headers={'User-Agent': 'Mozilla/5.0'})
            r.raise_for_status()
            df = pd.read_csv(io.BytesIO(r.content), low_memory=False, dtype=str)
            if len(df) < 1000:
                raise ValueError('Unexpectedly small Dhan instrument master')
            _MASTER_CACHE = df
            return df
        except (requests.RequestException, ValueError, pd.errors.ParserError) as exc:
            errors.append(str(exc)[:150])
    raise RuntimeError('Cannot load Dhan instrument master: ' + '; '.join(errors))


def _nse_fo_symbols():
    """Try NSE's current F&O market-lot publication; return None if unavailable."""
    try:
        session = requests.Session()
        session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 'Referer': 'https://www.nseindia.com/'})
        r = session.get(NSE_LOTS_URL, timeout=20)
        r.raise_for_status()
        lines = r.content.decode('utf-8-sig', errors='replace').splitlines()
        import csv
        symbols = set()
        for line in lines:
            parts = next(csv.reader([line]))
            if not parts:
                continue
            symbol = parts[0].strip().upper().replace('"', '')
            if re.fullmatch(r'[A-Z0-9&_.-]{2,30}', symbol) and symbol not in {'SYMBOL', 'UNDERLYING', 'INDEX', 'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX', 'NIFTYNXT50'}:
                # Only rows with at least one numeric lot-size field are accepted.
                if any(re.fullmatch(r'\d+', p.strip()) for p in parts[1:]):
                    symbols.add(symbol)
        return symbols if len(symbols) >= 50 else None
    except (requests.RequestException, ValueError):
        return None


def load_fo_universe(force=False):
    """Discover F&O membership from NSE market lots and match Dhan cash IDs.

    Supports Dhan compact/detailed master variants. Fail with diagnostic samples,
    not an unexplained zero count. Never fabricate a security ID.
    """
    global _UNIVERSE_CACHE
    if _UNIVERSE_CACHE is not None and not force:
        return dict(_UNIVERSE_CACHE)
    master = _master()
    sec_id = _col(master, 'SEM_SMST_SECURITY_ID', 'SECURITY_ID', 'SECURITYID', 'SECURITY_ID')
    trading = _col(master, 'SEM_TRADING_SYMBOL', 'TRADING_SYMBOL', 'SYMBOL', 'SM_SYMBOL_NAME')
    segment = _col(master, 'SEM_SEGMENT', 'SEGMENT', 'EXCHANGE_SEGMENT')
    exchange = _col(master, 'SEM_EXM_EXCH_ID', 'EXCH_ID', 'EXCHANGE')
    instrument = _col(master, 'SEM_INSTRUMENT_NAME', 'INSTRUMENT', 'INSTRUMENT_TYPE')
    if not sec_id or not trading:
        raise RuntimeError(f'Dhan master missing security/trading columns: {list(master.columns)[:30]}')
    symbols = _nse_fo_symbols()
    if not symbols:
        raise RuntimeError('NSE F&O market-lot list unavailable; cannot verify current F&O membership. No fabricated universe.')
    sym_series = master[trading].fillna('').astype(str).str.strip().str.upper()
    mask = sym_series.isin(symbols)
    if segment:
        seg = master[segment].fillna('').astype(str).str.upper().str.strip()
        # Dhan NSE cash equities are segment E; reject derivatives, currency and BSE.
        mask &= seg.isin(['E','NSE_EQ','EQ','EQUITY']) | seg.str.contains('NSE_EQ',regex=False)
    if exchange:
        exch = master[exchange].fillna('').astype(str).str.upper().str.strip()
        mask &= exch.isin(['NSE','NSE_EQ'])
    if instrument:
        inst = master[instrument].fillna('').astype(str).str.upper().str.strip()
        mask &= inst.isin(['EQUITY','EQ','E'])
    eq = master.loc[mask]
    result = {}
    duplicates = set()
    for _,row in eq.iterrows():
        symbol = str(row[trading]).strip().upper()
        raw_id = str(row[sec_id]).strip()
        if not re.fullmatch(r'\d+(?:\.0+)?',raw_id):
            continue
        sid = str(int(float(raw_id)))
        if symbol in result and result[symbol][1] != sid:
            duplicates.add(symbol)
        else:
            result[symbol] = ('NSE_EQ',sid,'EQUITY')
    for symbol in duplicates:
        result.pop(symbol,None)
    if len(result)<50:
        # Provide useful diagnostics without silently accepting bad mappings.
        example = master[[trading,sec_id]+[c for c in (segment,exchange,instrument) if c]].head(4).to_dict('records')
        raise RuntimeError(f'Only {len(result)} NSE F&O cash IDs matched; NSE list={len(symbols)}; master columns={list(master.columns)[:20]}; sample={example}')
    DHAN_SECURITY_MAP.clear()
    DHAN_SECURITY_MAP.update(result)
    _UNIVERSE_CACHE=dict(result)
    LOG.info('Verified NSE F&O stock cash mappings: %d / %d',len(result),len(symbols))
    return dict(result)


def fetch_dhan_candles(symbol, interval='5', days=12):
    mapping = load_fo_universe()
    key = symbol.upper().removesuffix('.NS')
    if key not in mapping:
        raise KeyError(f'{key} is not in verified current Dhan F&O universe')
    seg, sid, inst = mapping[key]
    now = pd.Timestamp.now(tz=IST)
    start = now - pd.Timedelta(days=days)
    payload = {'securityId': sid, 'exchangeSegment': seg, 'instrument': inst,
               'interval': str(interval), 'oi': False,
               'fromDate': start.strftime('%Y-%m-%d'),
               'toDate': now.strftime('%Y-%m-%d')}
    obj = _request('/charts/intraday', payload)
    if not isinstance(obj, dict):
        raise ValueError('Dhan candle response is not an object')
    ts = obj.get('timestamp', obj.get('start_Time'))
    fields = ['open', 'high', 'low', 'close', 'volume']
    if ts is None or any(obj.get(k) is None for k in fields):
        raise ValueError(f'Dhan OHLCV response missing keys; keys={list(obj)[:15]}')
    lengths = [len(obj[k]) for k in fields] + [len(ts)]
    if len(set(lengths)) != 1 or not lengths[0]:
        raise ValueError(f'Dhan OHLCV length mismatch: {lengths}')
    times = pd.to_datetime(ts, unit='s', utc=True).tz_convert(IST)
    out = pd.DataFrame({k: pd.to_numeric(obj[k], errors='coerce') for k in fields}, index=times)
    out = out.dropna(subset=fields).loc[lambda d: ~d.index.duplicated(keep='last')].sort_index()
    if out.empty:
        raise ValueError(f'Dhan returned no valid candles for {key}')
    return out


def fetch_dhan_ltp_batch(symbols):
    mapping = load_fo_universe()
    ids = {s: int(mapping[s][1]) for s in symbols if s in mapping}
    if not ids:
        return {}
    out = {}
    # Dhan marketfeed accepts batches; chunk to avoid request-size restrictions.
    for start in range(0, len(ids), 100):
        part = dict(list(ids.items())[start:start + 100])
        obj = _request('/marketfeed/ltp', {'NSE_EQ': list(part.values())})
        data = obj.get('data', {}).get('NSE_EQ', {})
        for sym, sid in part.items():
            item = data.get(str(sid), {})
            if item.get('last_price') is not None:
                out[sym] = float(item['last_price'])
    return out
