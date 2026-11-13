"""After-close Dhan-only F&O research watchlist. No order placement.

Uses verified Dhan security mappings, saved snapshots and market-wide FII/DII context.
Does not infer stock-level institutional holdings or verified option contracts.
"""
import datetime as dt
import json
import math
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import requests

IST = ZoneInfo('Asia/Kolkata')
OUT = Path(__file__).parent / 'snapshots' / 'next_day.json'
INDICES = {'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'NIFTYNXT50', 'SENSEX'}
HEAD = {'User-Agent': 'Mozilla/5.0 (compatible; ResearchWatchlist/1.0)'}


def headlines(query, limit=3):
    url = 'https://news.google.com/rss/search?q=' + quote(query) + '&hl=en-IN&gl=IN&ceid=IN:en'
    try:
        r = requests.get(url, headers=HEAD, timeout=12)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        result = []
        for item in root.findall('./channel/item')[:limit]:
            title = item.findtext('title', '').strip()
            link = item.findtext('link', '').strip()
            published = item.findtext('pubDate', '').strip()
            if title and link:
                result.append({'title': title, 'url': link, 'published': published,
                               'discovery': 'Google News RSS; article not independently verified'})
        return result
    except (requests.RequestException, ET.ParseError):
        return []


def institutional():
    """NSE aggregate cash market only; not stock-wise FII/DII positions."""
    try:
        with requests.Session() as session:
            session.headers.update({'User-Agent': 'Mozilla/5.0',
                                    'Referer': 'https://www.nseindia.com/',
                                    'Accept': 'application/json,text/plain,*/*'})
            session.get('https://www.nseindia.com/', timeout=10)
            response = session.get('https://www.nseindia.com/api/fiidiiTradeReact', timeout=12)
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, list) or not data:
            raise ValueError('No NSE aggregate flow data')
        return {'source': 'NSE aggregate cash market (not stock-specific)',
                'rows': data[:8], 'verified': True}
    except (requests.RequestException, ValueError) as exc:
        return {'source': 'NSE', 'rows': [], 'verified': False, 'reason': str(exc)[:120]}


def normalize_candles(raw):
    """Normalize case and common volume aliases, without fabricating missing candles."""
    if raw is None or not isinstance(raw, pd.DataFrame) or raw.empty:
        raise ValueError('empty Dhan candle response')
    df = raw.copy()
    columns = {str(c).strip().lower(): c for c in df.columns}
    if len(columns) != len(df.columns):
        raise ValueError('duplicate OHLCV columns differing only by case')
    df = df.rename(columns={original: lower for lower, original in columns.items()})
    if 'volume' not in df.columns and 'vol' in df.columns:
        df = df.rename(columns={'vol': 'volume'})
    required = ['open', 'high', 'low', 'close', 'volume']
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f'missing Dhan OHLCV columns: {missing}; present={list(df.columns)[:12]}')
    for col in required:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=required).sort_index()
    if len(df) < 70:
        raise ValueError(f'insufficient valid Dhan candles: {len(df)}')
    if df.index.has_duplicates:
        raise ValueError('duplicate candle timestamps')
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError('candle index is not a datetime index')
    if ((df['high'] < df['low']) | (df['high'] < df[['open', 'close']].max(axis=1)) |
            (df['low'] > df[['open', 'close']].min(axis=1)) |
            (df['close'] <= 0) | (df['volume'] < 0)).any():
        raise ValueError('invalid OHLCV values')
    return df


def finite_number(value, name):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'invalid {name}: {value!r}') from exc
    if not math.isfinite(result):
        raise ValueError(f'non-finite {name}')
    return result


def build():
    import dhan_feed
    import mtt_v2 as m

    now = dt.datetime.now(IST)
    client_id, token = dhan_feed.get_dhan_credentials()
    if not client_id or not token:
        raise RuntimeError('Dhan credentials missing; previous snapshot preserved')

    universe = dhan_feed.load_fo_universe()
    symbols = sorted(s for s in universe if s not in INDICES)
    if not symbols:
        raise RuntimeError('No verified Dhan F&O stock mappings; previous snapshot preserved')

    rows, errors, session_dates = [], [], []
    validated = 0
    for sym in symbols:
        try:
            raw = dhan_feed.fetch_dhan_candles(sym, interval='5', days=12)
            df = normalize_candles(raw)
            ts = pd.Timestamp(df.index[-1])
            ts = ts.tz_localize(IST) if ts.tzinfo is None else ts.tz_convert(IST)
            if ts.date() > now.date() or (now.date() - ts.date()).days > 5:
                raise ValueError('candle outside recent trading sessions')
            if ts.time() < dt.time(15, 10):
                raise ValueError('last candle before session close')
            if ts.date() == now.date() and now.time() < dt.time(15, 30):
                raise ValueError('market session still open')

            # The existing toolkit may require Title Case OHLCV names. Supply both
            # spellings to preserve compatibility without changing other modules.
            analysis_df = df.copy()  # mtt_v2.normalize_ohlcv expects lowercase OHLCV
            analysis = m.analyse(sym, '5m', 'next', m.Config(), df=analysis_df)
            if not isinstance(analysis, dict):
                raise ValueError('analysis did not return a dictionary')
            sc = analysis['score']
            maximum = finite_number(sc.get('max'), 'score maximum')
            bull_points = finite_number(sc['bull'], 'bull points')
            bear_points = finite_number(sc['bear'], 'bear points')
            if maximum <= 0 or not (0 <= bull_points <= maximum and 0 <= bear_points <= maximum):
                raise ValueError('invalid directional technical scores')
            features = analysis['features']
            if features is None or features.empty:
                raise ValueError('missing technical features')
            last = features.iloc[-1]
            vr = finite_number(last.get('volr'), 'volume ratio')
            price = finite_number(analysis['price'], 'underlying price')
            atr = finite_number(analysis['atr'], 'ATR')
            ema21 = finite_number(last.get('ema21'), 'EMA21')
            if price <= 0 or atr <= 0 or vr < 0:
                raise ValueError('invalid underlying price, ATR or volume ratio')

            validated += 1
            session_dates.append(ts.date())
            if abs(price - ema21) > 1.5 * atr:
                continue

            # Use the last completed session's full high/low, not a single 5m candle.
            session_mask = analysis_df.index
            if session_mask.tz is None:
                dates = session_mask.date
            else:
                dates = session_mask.tz_convert(IST).date
            day = df.loc[dates == ts.date()]
            if day.empty:
                raise ValueError('no candles for latest trading session')
            session_high = finite_number(day['high'].max(), 'session high')
            session_low = finite_number(day['low'].min(), 'session low')

            for side, points, opposite in [('Bull', bull_points, bear_points),
                                           ('Bear', bear_points, bull_points)]:
                if points < 5 or points <= opposite or vr < 0.8:
                    continue
                score = round(70 * points / maximum + 15 * min(vr / 1.5, 1)
                              + 15 * max(0, 1 - abs(price - ema21) / (1.5 * atr)), 1)
                if side == 'Bull':
                    entry = session_high + 0.10 * atr
                    stop = entry - 1.5 * atr
                    target1, target2 = entry + 1.5 * atr, entry + 3 * atr
                else:
                    entry = session_low - 0.10 * atr
                    stop = entry + 1.5 * atr
                    target1, target2 = entry - 1.5 * atr, entry - 3 * atr
                if min(entry, stop, target1, target2) <= 0:
                    continue
                rows.append({
                    'symbol': sym, 'side': side, 'score': score,
                    'technical_points': round(points, 2),
                    'opposite_points': round(opposite, 2),
                    'price': round(price, 2), 'volume_ratio': round(vr, 2),
                    'candle_ist': ts.isoformat(), 'source': 'Dhan historical 5m',
                    'entry_trigger_underlying': round(entry, 2),
                    'stop_underlying': round(stop, 2),
                    'target1_underlying': round(target1, 2),
                    'target2_underlying': round(target2, 2),
                    'level_basis': 'Previous completed session high/low and ATR; indicative underlying levels, not option premiums',
                    'advantages': [f'{points:g}/{maximum:g} directional technical points',
                                   f'Volume {vr:.2f}x baseline'],
                    'disadvantages': ['Next-session direction is uncertain',
                                      'Option expiry, strike, premium and liquidity not verified'],
                    'entry_rule': 'Next-session completed 5m close beyond entry trigger with volume and directional confirmation; otherwise WAIT',
                    'strike': 'Not verified',
                })
        except Exception as exc:
            errors.append({'symbol': sym, 'reason': f'{type(exc).__name__}: {exc}'[:180]})
        time.sleep(1.1)

    if validated == 0:
        raise RuntimeError('No valid Dhan analyses; previous snapshot preserved. ' + str(errors[:5]))

    newest = max(session_dates)
    rows = [r for r in rows if pd.Timestamp(r['candle_ist']).date() == newest]
    bull = sorted((r for r in rows if r['side'] == 'Bull'),
                  key=lambda r: (-r['score'], r['symbol']))[:10]
    bear = sorted((r for r in rows if r['side'] == 'Bear'),
                  key=lambda r: (-r['score'], r['symbol']))[:10]
    for row in bull + bear:
        sym = row['symbol']
        row['news'] = headlines(f'"{sym}" NSE stock India when:2d', 3)
        row['moneycontrol_news'] = headlines(f'"{sym}" site:moneycontrol.com when:3d', 2)
        row['news_risk'] = 'Headlines unverified; review articles and exchange filings'

    snapshot = {
        'generated_ist': now.isoformat(), 'session_date': newest.isoformat(),
        'status': 'research_only',
        'method': 'Dhan-only technical ranking; news context unscored',
        'fii_dii': institutional(),
        'market_news': headlines('NSE India stock market FII DII outlook when:2d', 5),
        'bull': bull, 'bear': bear, 'scanned': len(symbols), 'universe_size': len(universe),
        'validated': validated, 'eligible': len(set(r['symbol'] for r in rows)),
        'errors': errors,
        'warnings': [
            'No guarantee of next-day movement',
            'FII/DII data are market-wide, not stock-wise',
            'Google News and Moneycontrol headlines are discovery links, not verified sentiment',
            'Actual F&O membership, expiry, strikes and option premiums must be independently verified',
        ],
    }
    if not bull and not bear:
        snapshot['warnings'].append('No qualifying candidates after validated analyses; no trade')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUT.with_suffix('.tmp')
    temporary.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(OUT)
    print(f'Wrote {OUT}: {len(bull)} bull / {len(bear)} bear; '
          f'{validated} validated / {len(symbols)} scanned; {len(errors)} excluded')


if __name__ == '__main__':
    build()
