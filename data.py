"""MTT V2 data provider: Dhan-first, explicit Yahoo fallback, safe diagnostics."""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

LOG = logging.getLogger(__name__)
IST = ZoneInfo('Asia/Kolkata')
INDEX = {
    'NIFTY 50': '^NSEI', 'BANK NIFTY': '^NSEBANK', 'SENSEX': '^BSESN',
    'FINNIFTY': 'NIFTY_FIN_SERVICE.NS', 'MIDCAP NIFTY': 'NIFTY_MID_SELECT.NS',
    'INDIA VIX': '^INDIAVIX', 'GIFT NIFTY': None,
}
ALIASES = {
    'NIFTY 50': ('NIFTY 50', 'NIFTY'),
    'BANK NIFTY': ('NIFTY BANK', 'BANKNIFTY'),
    'SENSEX': ('SENSEX',),
    'FINNIFTY': ('NIFTY FIN SERVICE', 'NIFTY FINANCIAL SERVICES', 'FINNIFTY'),
    'MIDCAP NIFTY': ('NIFTY MID SELECT', 'MIDCPNIFTY'),
    'INDIA VIX': ('INDIA VIX',),
    'GIFT NIFTY': ('GIFT NIFTY',),
}
_LOCK = threading.RLock()
_NEXT_REQUEST = 0.0
_BLOCKED_UNTIL = 0.0
_BLOCK_REASON = ''
_SESSION = requests.Session()
_MASTER = None
_MASTER_AT = 0.0


def _norm(value):
    return re.sub(r'[^A-Z0-9]', '', str(value).upper())


def _column(df, *names):
    cols = {_norm(c): c for c in df.columns}
    return next((cols[_norm(n)] for n in names if _norm(n) in cols), None)


def _failure_reason(response):
    try:
        obj = response.json()
        if isinstance(obj, dict):
            data = obj.get('data')
            if isinstance(data, dict) and data.get('805'):
                return str(data['805'])
            return str(obj.get('remarks') or obj.get('message') or obj.get('errorMessage') or obj)[:260]
    except (ValueError, TypeError):
        pass
    return response.text[:260]


class DataProvider:
    def __init__(self, client_id='', token=''):
        self.client_id = str(client_id or os.getenv('DHAN_CLIENT_ID', '')).strip()
        self.token = str(token or os.getenv('DHAN_ACCESS_TOKEN', '')).strip()
        self.blocked_until = 0.0
        self.error = ''
        self.master = None
        self.last_dhan_error = ''

    def request(self, path, payload=None, method='POST', timeout=8):
        global _NEXT_REQUEST, _BLOCKED_UNTIL, _BLOCK_REASON
        if not self.client_id or not self.token:
            raise RuntimeError('Dhan credentials missing')
        method = method.upper()
        with _LOCK:
            now = time.monotonic()
            if now < _BLOCKED_UNTIL:
                raise RuntimeError(f'Dhan cooldown ({int(_BLOCKED_UNTIL-now)}s): {_BLOCK_REASON}')
            wait = max(0.0, _NEXT_REQUEST-now)
            # Serialize calls across all providers in this process.
            if wait:
                time.sleep(wait)
            _NEXT_REQUEST = time.monotonic() + max(1.0, float(os.getenv('DHAN_MIN_INTERVAL_SECONDS', '1.5')))
            try:
                response = _SESSION.request(
                    method, 'https://api.dhan.co/v2/' + path.lstrip('/'),
                    json=payload if method != 'GET' else None,
                    params=payload if method == 'GET' else None,
                    headers={'access-token': self.token, 'client-id': self.client_id,
                             'Content-Type': 'application/json', 'Accept': 'application/json'},
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                self.last_dhan_error = f'Network error: {exc}'
                raise RuntimeError(self.last_dhan_error) from exc
            if response.status_code in (401, 403, 429):
                reason = _failure_reason(response)
                self.last_dhan_error = f'HTTP {response.status_code}: {reason}'
                if response.status_code == 429:
                    try:
                        retry = int(response.headers.get('Retry-After', '0'))
                    except ValueError:
                        retry = 0
                    _BLOCKED_UNTIL = time.monotonic() + max(600, retry)
                    _BLOCK_REASON = self.last_dhan_error
                # Auth and entitlement errors need credential/plan correction, not repeated requests.
                elif response.status_code in (401, 403):
                    _BLOCKED_UNTIL = time.monotonic() + 300
                    _BLOCK_REASON = self.last_dhan_error
                raise RuntimeError(self.last_dhan_error)
            try:
                response.raise_for_status()
                obj = response.json()
            except (requests.RequestException, ValueError) as exc:
                self.last_dhan_error = f'Dhan HTTP {response.status_code}: {_failure_reason(response)}'
                raise RuntimeError(self.last_dhan_error) from exc
            if isinstance(obj, dict) and str(obj.get('status', '')).lower() == 'failed':
                self.last_dhan_error = f'Dhan API rejected request: {obj}'[:300]
                raise RuntimeError(self.last_dhan_error)
            self.last_dhan_error = ''
            return obj

    def instrument_master(self):
        global _MASTER, _MASTER_AT
        if self.master is not None:
            return self.master
        with _LOCK:
            if _MASTER is not None and time.monotonic()-_MASTER_AT < 86400:
                self.master = _MASTER
                return _MASTER
            try:
                df = pd.read_csv('https://images.dhan.co/api-data/api-scrip-master.csv', low_memory=False)
                if df.empty:
                    raise ValueError('empty CSV')
            except Exception as exc:
                raise RuntimeError(f'Instrument master unavailable: {exc}') from exc
            _MASTER, _MASTER_AT = df, time.monotonic()
            self.master = df
            return df

    def resolve_index(self, symbol):
        df = self.instrument_master()
        idcol = _column(df, 'SEM_SMST_SECURITY_ID', 'SECURITY_ID', 'SECURITYID')
        namecol = _column(df, 'SEM_CUSTOM_SYMBOL', 'DISPLAY_NAME', 'SYMBOL_NAME', 'SEM_TRADING_SYMBOL', 'TRADING_SYMBOL')
        segcol = _column(df, 'SEM_EXM_EXCH_ID', 'EXCH_ID', 'EXCHANGE')
        typecol = _column(df, 'SEM_INSTRUMENT_NAME', 'INSTRUMENT', 'INSTRUMENT_TYPE')
        if not idcol or not namecol or not segcol:
            raise RuntimeError('Dhan instrument master missing ID/name/exchange columns')
        wanted_exchange = 'BSE' if symbol == 'SENSEX' else 'NSE'
        wanted_segment = 'BSE_IDX' if symbol == 'SENSEX' else 'NSE_IDX'
        aliases = {_norm(a) for a in ALIASES.get(symbol, (symbol,))}
        matched = df.loc[df[namecol].map(_norm).isin(aliases) & df[segcol].astype(str).str.upper().str.strip().eq(wanted_exchange)]
        if typecol:
            # Index master rows generally have instrument type INDEX. Reject derivatives.
            matched = matched.loc[matched[typecol].astype(str).str.upper().str.contains('INDEX|IDX', regex=True, na=False)]
        candidates = []
        for _, row in matched.iterrows():
            try:
                candidates.append((int(row[idcol]), wanted_segment, str(row[typecol]) if typecol else 'INDEX'))
            except (TypeError, ValueError):
                continue
        if len(candidates) != 1:
            raise LookupError(f'{symbol}: expected one verified index mapping, found {len(candidates)}; inspect master columns/rows')
        return candidates[0]

    def dhan_quote(self, security_id, segment='NSE_EQ'):
        sid = str(int(security_id))
        obj = self.request('marketfeed/ltp', {segment: [int(sid)]})
        if not isinstance(obj, dict):
            raise ValueError('Dhan quote response is not a JSON object')
        data = obj.get('data')
        if not isinstance(data, dict):
            raise ValueError(f'Dhan quote has no data object: {str(obj)[:240]}')
        group = data.get(segment)
        if not isinstance(group, dict):
            raise ValueError(f'Dhan quote missing segment {segment}; available={list(data)}')
        item = group.get(sid) or group.get(int(sid))
        if not isinstance(item, dict):
            raise ValueError(f'Dhan quote missing security {sid}; available={list(group)[:8]}')
        price = item.get('last_price')
        if price is None:
            raise ValueError(f'Dhan quote missing last_price; fields={list(item)}')
        ltp = float(price)
        if ltp <= 0:
            raise ValueError(f'Invalid Dhan LTP {ltp}')
        return {'ltp': ltp, 'change': None, 'source': 'Dhan', 'reason': ''}

    def index_quote(self, symbol):
        reason = 'Dhan credentials missing'
        if self.client_id and self.token and symbol != 'GIFT NIFTY':
            try:
                sid, segment, _ = self.resolve_index(symbol)
                return self.dhan_quote(sid, segment)
            except Exception as exc:
                reason = str(exc)
                self.last_dhan_error = reason
                LOG.warning('Dhan index %s: %s', symbol, reason)
        elif symbol == 'GIFT NIFTY':
            reason = 'No verified GIFT NIFTY data source configured'
        result = self.quote(symbol)
        result['reason'] = f'Dhan unavailable: {reason}; {result.get("reason", "")}'
        return result

    def candles(self, symbol, interval='5m', period='5d', security_id=None,
                segment='NSE_EQ', instrument='EQUITY'):
        if security_id is not None:
            try:
                now = datetime.now(IST)
                intraday = interval != '1d'
                days = 5 if intraday else 365
                path = 'charts/intraday' if intraday else 'charts/historical'
                payload = {
                    'securityId': str(security_id), 'exchangeSegment': segment,
                    'instrument': instrument,
                    'fromDate': (now-timedelta(days=days)).strftime('%Y-%m-%d'),
                    'toDate': now.strftime('%Y-%m-%d'), 'oi': False,
                }
                if intraday:
                    if not interval.endswith('m'):
                        raise ValueError(f'Unsupported Dhan intraday interval {interval}')
                    payload['interval'] = int(interval[:-1])
                obj = self.request(path, payload)
                if not isinstance(obj, dict) or not obj.get('close'):
                    raise ValueError(f'Empty Dhan candles: {str(obj)[:160]}')
                timestamps = obj.get('start_Time') or obj.get('startTime')
                if timestamps is None:
                    raise ValueError('Dhan candle timestamps missing')
                df = pd.DataFrame({
                    'Open': obj['open'], 'High': obj['high'], 'Low': obj['low'],
                    'Close': obj['close'], 'Volume': obj['volume'],
                }, index=pd.to_datetime(timestamps, unit='s', utc=True).tz_convert(IST))
                return df.sort_index().dropna(), 'Dhan'
            except Exception as exc:
                self.last_dhan_error = str(exc)
                LOG.warning('Dhan candles %s: %s', symbol, exc)
        ticker = INDEX.get(symbol, symbol+'.NS' if not symbol.startswith('^') and not symbol.endswith('.NS') else symbol)
        if not ticker:
            return pd.DataFrame(), 'N/A'
        try:
            df = yf.download(ticker, period=period, interval=interval,
                             progress=False, auto_adjust=False, threads=False, timeout=7)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if df.empty:
                return pd.DataFrame(), 'N/A'
            return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna(), 'Yahoo (delayed/unverified)'
        except Exception as exc:
            LOG.warning('Yahoo candles %s: %s', symbol, exc)
            return pd.DataFrame(), 'N/A'

    def quote(self, symbol, security_id=None, segment='NSE_EQ'):
        if security_id is not None:
            try:
                return self.dhan_quote(security_id, segment)
            except Exception as exc:
                self.last_dhan_error = str(exc)
                LOG.warning('Dhan quote %s: %s', symbol, exc)
        df, source = self.candles(symbol, '1d', '5d')
        if df.empty:
            return {'ltp': None, 'change': None, 'source': 'N/A',
                    'reason': self.last_dhan_error or 'No Dhan/Yahoo price'}
        last = float(df['Close'].iloc[-1])
        prev = float(df['Close'].iloc[-2]) if len(df) > 1 else None
        return {'ltp': last, 'change': (last/prev-1)*100 if prev and prev > 0 else None,
                'source': source, 'reason': 'Yahoo quote may be delayed'}

    def option_chain(self, underlying_id, segment='NSE_EQ', expiry=None):
        if not expiry:
            raise ValueError('Expiry required from verified instrument master/expiry list')
        return self.request('optionchain', {'UnderlyingScrip': int(underlying_id),
                                            'UnderlyingSeg': segment, 'Expiry': expiry})
