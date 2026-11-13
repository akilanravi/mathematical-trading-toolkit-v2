"""Isolated Tab 1 radar. Does not call or modify other tab engines."""
import io
import re
import time
import requests
import numpy as np
import pandas as pd
import streamlit as st
import mtt_v2 as m

IST='Asia/Kolkata'
MASTER='https://images.dhan.co/api-data/api-scrip-master-detailed.csv'
INDEX_NAMES={'NIFTY','BANKNIFTY','FINNIFTY','MIDCPNIFTY','NIFTYNXT50','SENSEX'}


def _column(frame, *names):
    lookup={re.sub('[^a-z0-9]','',str(k).lower()):k for k in frame.columns}
    return next((lookup[re.sub('[^a-z0-9]','',n.lower())] for n in names if re.sub('[^a-z0-9]','',n.lower()) in lookup),None)


def parse_master(frame, allowed):
    """Match current F&O symbols against NSE cash-equity IDs, not derivative IDs."""
    sid=_column(frame,'SEM_SMST_SECURITY_ID','SECURITY_ID','SECURITYID')
    symbol=_column(frame,'SEM_TRADING_SYMBOL','TRADING_SYMBOL','SYMBOL')
    segment=_column(frame,'SEM_SEGMENT','SEGMENT')
    exchange=_column(frame,'SEM_EXM_EXCH_ID','EXCHANGE','EXCH_ID')
    inst=_column(frame,'SEM_INSTRUMENT_NAME','INSTRUMENT','INSTRUMENT_TYPE')
    if not sid or not symbol:
        raise ValueError('Master lacks security ID or trading symbol; columns: '+str(list(frame.columns)[:20]))
    out={}
    for _,r in frame.iterrows():
        s=str(r[symbol]).strip().upper()
        # Some Dhan masters contain RELIANCE-EQ as trading symbol.
        if s.endswith('-EQ') and s[:-3] in allowed:s=s[:-3]
        if s not in allowed:continue
        seg=str(r[segment]).strip().upper() if segment else ''
        ex=str(r[exchange]).strip().upper() if exchange else ''
        typ=str(r[inst]).strip().upper() if inst else ''
        if segment and seg not in ('E','EQ','EQUITY','NSE_EQ'):continue
        if exchange and ex not in ('NSE','NSE_EQ'):continue
        if inst and typ not in ('EQUITY','EQ','E'):continue
        raw=str(r[sid]).strip()
        if not re.fullmatch(r'\d+(?:\.0+)?',raw):continue
        identifier=str(int(float(raw)))
        if s in out and out[s][1]!=identifier:
            out.pop(s,None)
            continue
        out[s]=('NSE_EQ',identifier,'EQUITY')
    return out


@st.cache_data(ttl=3600,show_spinner=False)
def discover(allowed_symbols):
    import dhan_feed
    allowed=set(allowed_symbols)-INDEX_NAMES
    try:
        nse=dhan_feed._nse_fo_symbols()
    except Exception as exc:
        raise RuntimeError(f'NSE current F&O list unavailable: {exc}') from exc
    if not nse:
        raise RuntimeError('NSE current F&O market-lot list unavailable. Refusing to label static names as verified.')
    allowed=set(nse) # Discover full exchange universe, not just old UI list
    try:
        master=dhan_feed._master()
    except Exception as exc:
        raise RuntimeError(f'Dhan instrument master download failed: {exc}') from exc
    mapping=parse_master(master,allowed)
    if not mapping:
        raise RuntimeError('No matching NSE cash IDs in Dhan master. Fields: '+str(list(master.columns)[:15]))
    return mapping, len(allowed)


def normalize_candles(obj):
    if not isinstance(obj,dict):raise ValueError('Non-object Dhan candle response')
    cols=['open','high','low','close','volume']
    stamp=obj.get('timestamp',obj.get('start_Time'))
    if stamp is None or any(obj.get(c) is None for c in cols):
        raise ValueError('Missing OHLCV/timestamp fields: '+str(list(obj)[:15]))
    sizes=[len(stamp)]+[len(obj[c]) for c in cols]
    if len(set(sizes))!=1 or sizes[0]<70:raise ValueError(f'Insufficient/mismatched candle arrays: {sizes}')
    index=pd.to_datetime(stamp,unit='s',utc=True).tz_convert(IST)
    # Use positional arrays: Series + timezone index would align by labels to NaN.
    df=pd.DataFrame({c:pd.to_numeric(pd.Series(obj[c]),errors='coerce').to_numpy() for c in cols},index=index)
    df=df.dropna().loc[lambda d:~d.index.duplicated(keep='last')].sort_index()
    df=df[(df.high>=df.low)&(df.low>0)&(df.close>0)&(df.open>0)&(df.volume>=0)]
    if len(df)<70:raise ValueError('Insufficient valid OHLCV candles')
    return df


@st.cache_data(ttl=300,show_spinner=False)
def candles(symbol,sid):
    import dhan_feed
    now=pd.Timestamp.now(tz=IST)
    obj=dhan_feed._request('/charts/intraday',{'securityId':sid,'exchangeSegment':'NSE_EQ','instrument':'EQUITY','interval':'5','oi':False,'fromDate':(now-pd.Timedelta(days=12)).strftime('%Y-%m-%d'),'toDate':now.strftime('%Y-%m-%d')},timeout=20)
    return normalize_candles(obj)


def rank_one(sym,df):
    # mtt_v2 uses lowercase OHLCV and requires a tz-aware datetime index.
    result=m.analyse(sym,'5m','next',m.Config(),df=df,vix_val=0.14)
    sc=result['score']; mx=float(sc['max'])
    if mx<=0:raise ValueError('Invalid technical denominator')
    row=result['features'].iloc[-1]
    vol=float(row['volr']) if pd.notna(row['volr']) else 0
    atr=float(result['atr']);price=float(result['price'])
    if not all(np.isfinite(x) for x in (vol,atr,price)) or atr<=0 or price<=0:
        raise ValueError('Invalid indicator values')
    if abs(price-float(row['ema21']))>1.5*atr:return None
    bull=int(sc['bull']);bear=int(sc['bear'])
    if vol<0.8 or max(bull,bear)<5 or bull==bear:return None
    direction='CE' if bull>bear else 'PE'
    pts=max(bull,bear)
    score=round(70*pts/mx+15*min(vol/1.5,1)+15*max(0,1-abs(price-float(row['ema21']))/(1.5*atr)),1)
    return {'Symbol':sym,'Direction':direction,'Close ₹':round(price,2),'Score':score,'Bull':bull,'Bear':bear,'Max':int(mx),'Volume ×':round(vol,2),'Candle IST':str(df.index[-1]),'Source':'Dhan 5m'}


def scan(mapping,limit):
    results=[];errors=[];valid=0
    for sym in list(sorted(mapping))[:limit]:
        try:
            df=candles(sym,mapping[sym][1]);valid+=1
            r=rank_one(sym,df)
            if r:results.append(r)
        except Exception as exc:
            errors.append({'Symbol':sym,'Reason':str(exc)[:180]})
    return results,errors,valid


@st.fragment(run_every='60s')
def render():
    st.subheader('🎯 Live F&O Radar | Tab 1')
    st.caption('Dhan historical 5-minute candles; refreshes at most every 5 minutes. Not tick-by-tick live and not an order signal.')
    selection=st.selectbox('Universe',['Top 50 (alphabetical sample; not liquidity-ranked)','All verified F&O stocks'],key='tab1_universe')
    if st.button('Refresh Tab 1',key='tab1_refresh'):
        discover.clear();candles.clear()
    try:
        with st.spinner('Loading NSE F&O membership and Dhan cash security IDs...'):
            mapping,nse_count=discover(tuple())
    except Exception as exc:
        st.error('Universe discovery failed: '+str(exc))
        st.info('Other tabs are unchanged. No unsupported IDs or fabricated results are used.')
        return
    count=min(50,len(mapping)) if selection.startswith('Top 50') else len(mapping)
    st.info(f'NSE F&O symbols: {nse_count} · Verified Dhan cash IDs: {len(mapping)} · Selected for scan: {count}')
    if selection.startswith('All'):
        st.warning('Full-universe scanning may take several minutes and consume Dhan API requests. Use Top 50 to diagnose first.')
    with st.spinner(f'Checking {count} stocks using Dhan candles...'):
        rows,errors,valid=scan(mapping,count)
    st.caption(f'Valid candle responses: {valid}/{count} · Errors: {len(errors)} · Ranked setups: {len(rows)}')
    for side,title in [('CE','🟢 Bullish CE candidates'),('PE','🔴 Bearish PE candidates')]:
        subset=sorted((r for r in rows if r['Direction']==side),key=lambda x:-x['Score'])[:10]
        st.markdown('### '+title)
        if subset:st.dataframe(pd.DataFrame(subset),hide_index=True,use_container_width=True)
        else:st.info('No qualifying candidates; do not force trades.')
    with st.expander('Data diagnostics'):
        if errors:st.dataframe(pd.DataFrame(errors),hide_index=True,use_container_width=True)
        else:st.success('No per-symbol errors in this scan.')
    st.caption('Ranking is research-only. F&O eligibility, actual option premium, expiry, liquidity and next-candle confirmation require separate verification.')
