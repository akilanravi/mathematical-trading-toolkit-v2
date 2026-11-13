"""After-close Dhan-only F&O watchlist. No orders and no invented FII/DII holdings."""
import datetime as dt, json, os, time, xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo
import requests, pandas as pd

IST=ZoneInfo('Asia/Kolkata')
OUT=Path(__file__).parent/'snapshots'/'next_day.json'
INDICES={'NIFTY','BANKNIFTY','FINNIFTY','MIDCPNIFTY','NIFTYNXT50','SENSEX'}
HEAD={'User-Agent':'Mozilla/5.0 (compatible; ResearchWatchlist/1.0)'}

def headlines(query,limit=3):
    url='https://news.google.com/rss/search?q='+quote(query)+'&hl=en-IN&gl=IN&ceid=IN:en'
    try:
        r=requests.get(url,headers=HEAD,timeout=12);r.raise_for_status()
        root=ET.fromstring(r.content);out=[]
        for item in root.findall('./channel/item')[:limit]:
            title=item.findtext('title','').strip();link=item.findtext('link','').strip();pub=item.findtext('pubDate','').strip()
            if title and link:out.append({'title':title,'url':link,'published':pub,'discovery':'Google News RSS; article not independently verified'})
        return out
    except Exception:return []

def institutional():
    """Official NSE aggregate FII/DII cash-flow context, NOT stock-level holdings."""
    try:
        s=requests.Session();s.headers.update({'User-Agent':'Mozilla/5.0','Referer':'https://www.nseindia.com/','Accept':'application/json,text/plain,*/*'})
        s.get('https://www.nseindia.com/',timeout=10)
        r=s.get('https://www.nseindia.com/api/fiidiiTradeReact',timeout=12);r.raise_for_status()
        data=r.json()
        if not isinstance(data,list) or not data:raise ValueError('No data')
        return {'source':'NSE aggregate cash market (not stock-specific)','rows':data[:8],'verified':True}
    except Exception as exc:return {'source':'NSE','rows':[],'verified':False,'reason':str(exc)[:120]}

def build():
    import dhan_feed
    import mtt_v2 as m
    now=dt.datetime.now(IST)
    cid,token=dhan_feed.get_dhan_credentials()
    if not cid or not token:raise RuntimeError('Dhan credentials missing; previous snapshot preserved')
    # Historical Dhan data only. Symbols outside verified map are not ranked.
    symbols=[s for s in dhan_feed.DHAN_SECURITY_MAP if s not in INDICES]
    rows=[];errors=[];validated=0;session_dates=[]
    for sym in symbols:
        try:
            df=dhan_feed.fetch_dhan_candles(sym,interval='5',days=12)
            if df is None or len(df)<70:raise ValueError('insufficient Dhan candles')
            df=df.sort_index()
            ts=pd.Timestamp(df.index[-1]);ts=ts.tz_localize(IST) if ts.tzinfo is None else ts.tz_convert(IST)
            # A previous trading session is acceptable on weekends/holidays, but never older than 5 calendar days.
            if ts.date()>now.date() or (now.date()-ts.date()).days>5:raise ValueError('candle outside recent trading sessions')
            if ts.time()<dt.time(15,10):raise ValueError('last candle before session close')
            # Reject a session that has not finished, rather than treating partial data as after-close.
            if ts.date()==now.date() and now.time()<dt.time(15,30):raise ValueError('market session still open')
            session_dates.append(ts.date())
            # No future/incomplete candle and no 15:30 timestamp assumed.
            analysis=m.analyse(sym,'5m','next',m.Config(),df=df)
            sc=analysis['score'];mx=float(sc.get('max') or 0)
            if mx<=0:raise ValueError('invalid score denominator')
            bull=int(sc['bull']);bear=int(sc['bear'])
            if not (0<=bull<=mx and 0<=bear<=mx):raise ValueError('invalid technical score')
            last=analysis['features'].iloc[-1]
            vr=float(last.get('volr',0)) if pd.notna(last.get('volr')) else 0
            price=float(analysis['price'])
            atr=float(analysis['atr'])
            if price<=0 or atr<=0:raise ValueError('invalid price/ATR')
            validated+=1
            ema21=float(last.get('ema21',price))
            if abs(price-ema21)>1.5*atr:continue
            # Technical directional dominance, rather than guaranteed next-day move.
            for side,points,opposite in [('Bull',bull,bear),('Bear',bear,bull)]:
                if points<5 or points<=opposite or vr<0.8:continue
                score=round(70*points/mx+15*min(vr/1.5,1)+15*max(0,1-abs(price-ema21)/(1.5*atr)),1)
                # Underlying-only planning levels; next-session confirmation required.
                prior_high=float(df['High'].iloc[-1]);prior_low=float(df['Low'].iloc[-1])
                if side=='Bull':
                    entry=prior_high+0.10*atr;stop=entry-1.5*atr;targets=[entry+1.5*atr,entry+3*atr]
                else:
                    entry=prior_low-0.10*atr;stop=entry+1.5*atr;targets=[entry-1.5*atr,entry-3*atr]
                if min(entry,stop,*targets)<=0:continue
                rows.append({'entry_trigger_underlying':round(entry,2),'stop_underlying':round(stop,2),'target1_underlying':round(targets[0],2),'target2_underlying':round(targets[1],2),'level_basis':'Last 5m candle high/low and ATR; indicative, not option premium levels','symbol':sym,'side':side,'score':score,'technical_points':points,'opposite_points':opposite,'price':round(price,2),'volume_ratio':round(vr,2),'candle_ist':ts.isoformat(),'source':'Dhan historical 5m','advantages':[f'{points}/{int(mx)} directional technical points',f'Volume {vr:.2f}x baseline'],'disadvantages':['Next-session direction not guaranteed','Option premium, expiry and strike not verified'],'entry_rule':'Next-session completed 5m close through entry trigger, with volume and directional confirmations; otherwise WAIT','strike':'Not verified'})
        except Exception as exc:errors.append({'symbol':sym,'reason':str(exc)[:90]})
        time.sleep(1.1) # Avoid request bursts; Dhan limits may require a longer interval.
    if validated==0:raise RuntimeError('No valid Dhan analyses; previous snapshot preserved. '+str(errors[:3]))
    # Never mix snapshots from different trading sessions.
    newest=max(session_dates)
    rows=[r for r in rows if pd.Timestamp(r['candle_ist']).date()==newest]
    bull=sorted((r for r in rows if r['side']=='Bull'),key=lambda r:-r['score'])[:10]
    bear=sorted((r for r in rows if r['side']=='Bear'),key=lambda r:-r['score'])[:10]
    # Fetch news only for shortlisted names; news is contextual, not a directional score.
    for row in bull+bear:
        sym=row['symbol']
        row['news']=headlines(f'"{sym}" NSE stock India when:2d',3)
        row['moneycontrol_news']=headlines(f'"{sym}" site:moneycontrol.com when:3d',2)
        row['news_risk']='Headlines unverified; review full articles and exchange filings'
    snapshot={'generated_ist':now.isoformat(),'session_date':newest.isoformat(),'status':'research_only','method':'Dhan-only technical ranking; news context unscored','fii_dii':institutional(),'market_news':headlines('NSE India stock market FII DII outlook when:2d',5),'bull':bull,'bear':bear,'scanned':len(symbols),'eligible':len(set(x['symbol'] for x in rows)),'errors':errors,'warnings':['No guarantee of next-day movement','FII/DII numbers are market-wide, not stock-wise','Google News and Moneycontrol headlines are discovery links, not verified sentiment','Actual F&O membership, expiry, strikes and premiums must be independently verified']}
    # No qualified candidates is a valid no-trade result if analyses were valid.
    if not bull and not bear:snapshot['warnings'].append('No qualifying candidates: no-trade watchlist')
    OUT.parent.mkdir(parents=True,exist_ok=True)
    temp=OUT.with_suffix('.tmp');temp.write_text(json.dumps(snapshot,indent=2,ensure_ascii=False),encoding='utf-8');temp.replace(OUT)
    print(f'Wrote {OUT}: {len(bull)} bull / {len(bear)} bear; {len(errors)} excluded')
if __name__=='__main__':build()
