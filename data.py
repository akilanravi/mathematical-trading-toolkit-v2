"""Dhan-first candles and quotes. Fallback is explicit and never called 'live'."""
from __future__ import annotations
import os, time, logging, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests, pandas as pd, yfinance as yf
LOG=logging.getLogger(__name__);IST=ZoneInfo('Asia/Kolkata')
INDEX={'NIFTY 50':'^NSEI','BANK NIFTY':'^NSEBANK','SENSEX':'^BSESN','FINNIFTY':'NIFTY_FIN_SERVICE.NS','MIDCAP NIFTY':'NIFTY_MID_SELECT.NS','INDIA VIX':'^INDIAVIX','GIFT NIFTY':'NQ=F'}
# GIFT NIFTY deliberately has no proxy: NQ futures is not GIFT NIFTY.
INDEX['GIFT NIFTY']=None
class DataProvider:
 def __init__(self,client_id='',token=''):
  self.client_id=str(client_id or os.getenv('DHAN_CLIENT_ID','')).strip();self.token=str(token or os.getenv('DHAN_ACCESS_TOKEN','')).strip();self.blocked_until=0;self.error='';self.master=None
 def request(self,path,payload=None,method='POST',timeout=5):
  if not self.token or not self.client_id:raise RuntimeError('Dhan credentials missing')
  if time.monotonic()<self.blocked_until:raise RuntimeError(self.error or 'Dhan temporarily unavailable')
  headers={'access-token':self.token,'client-id':self.client_id,'Content-Type':'application/json','Accept':'application/json'}
  try:
   r=requests.request(method,'https://api.dhan.co/v2/'+path.lstrip('/'),json=payload if method!='GET' else None,headers=headers,timeout=timeout)
   if r.status_code in (401,403,429):
    self.error=f'Dhan HTTP {r.status_code}: {r.text[:200]}';self.blocked_until=time.monotonic()+(300 if r.status_code in (401,403) else 30)
    raise RuntimeError(self.error)
   r.raise_for_status();return r.json()
  except requests.RequestException as exc:raise RuntimeError(f'Dhan network/API error: {exc}') from exc
 def instrument_master(self):
  if self.master is not None:return self.master
  # Public Dhan instrument master CSV; downloaded once per process.
  try:
   df=pd.read_csv('https://images.dhan.co/api-data/api-scrip-master.csv',low_memory=False)
   self.master=df;return df
  except Exception as exc:raise RuntimeError(f'Instrument master unavailable: {exc}')
 def resolve_index(self,symbol):
  """Resolve identifiers from Dhan master, never guess security IDs."""
  aliases={
   'NIFTY 50':('NIFTY 50','NIFTY'),
   'BANK NIFTY':('NIFTY BANK','BANKNIFTY'),
   'SENSEX':('SENSEX',),
   'FINNIFTY':('NIFTY FIN SERVICE','FINNIFTY','NIFTY FINANCIAL SERVICES'),
   'MIDCAP NIFTY':('NIFTY MID SELECT','MIDCPNIFTY'),
   'INDIA VIX':('INDIA VIX',),
   'GIFT NIFTY':('GIFT NIFTY',),
  }
  df=self.instrument_master()
  cols={re.sub(r'[^a-z0-9]','',str(c).lower()):c for c in df.columns}
  def col(*names):
   for n in names:
    key=re.sub(r'[^a-z0-9]','',n.lower())
    if key in cols:return cols[key]
   return None
  idcol=col('SEM_SMST_SECURITY_ID','SECURITY_ID','SECURITYID')
  namecol=col('SEM_CUSTOM_SYMBOL','DISPLAY_NAME','SYMBOL_NAME','SEM_TRADING_SYMBOL','TRADING_SYMBOL')
  segcol=col('SEM_EXM_EXCH_ID','EXCH_ID','EXCHANGE')
  typecol=col('SEM_INSTRUMENT_NAME','INSTRUMENT','INSTRUMENT_TYPE')
  if not idcol or not namecol:raise RuntimeError('Instrument master columns not recognized: '+','.join(map(str,df.columns[:20])))
  names=df[namecol].astype(str).str.upper().str.strip()
  for alias in aliases.get(symbol,(symbol,)):
   matched=df.loc[names.eq(alias)]
   if matched.empty:continue
   for _,row in matched.iterrows():
    ex=str(row[segcol]).upper() if segcol else ''
    inst=str(row[typecol]).upper() if typecol else ''
    if symbol=='SENSEX':segment='BSE_IDX'
    elif symbol=='GIFT NIFTY':segment='NSE_FNO' if ex=='NSE' else None
    else:segment='NSE_IDX'
    if segment is None:continue
    try:return int(row[idcol]),segment,inst
    except (ValueError,TypeError):continue
  raise LookupError('No verified Dhan instrument mapping for '+symbol)

 def index_quote(self,symbol):
  reason=''
  if self.client_id and self.token:
   try:
    sid,segment,_=self.resolve_index(symbol)
    q=self.quote(symbol,security_id=sid,segment=segment)
    if q['source']=='Dhan':return q
    reason=q.get('reason','Dhan quote unavailable')
   except Exception as exc:reason=str(exc);LOG.warning('Dhan index %s: %s',symbol,exc)
  else:reason='Dhan credentials missing in Streamlit Secrets'
  # The fallback quote deliberately does not fabricate GIFT NIFTY from Nasdaq futures.
  q=self.quote(symbol)
  if q['source']=='N/A':q['reason']=reason+'; '+q.get('reason','')
  else:q['reason']='Dhan unavailable: '+reason
  return q

 def candles(self,symbol,interval='5m',period='5d',security_id=None,segment='NSE_EQ',instrument='EQUITY'):
  if security_id:
   try:
    now=datetime.now(IST);days=70 if interval=='5m' else 420
    path='charts/intraday' if interval!='1d' else 'charts/historical'
    payload={'securityId':str(security_id),'exchangeSegment':segment,'instrument':instrument,'fromDate':(now-timedelta(days=days)).strftime('%Y-%m-%d'),'toDate':now.strftime('%Y-%m-%d')}
    if interval!='1d':payload['interval']=int(interval.replace('m',''))
    obj=self.request(path,payload)
    if not isinstance(obj,dict) or not obj.get('close'):raise ValueError('empty Dhan candles')
    df=pd.DataFrame({'Open':obj['open'],'High':obj['high'],'Low':obj['low'],'Close':obj['close'],'Volume':obj['volume']},index=pd.to_datetime(obj['start_Time'],unit='s',utc=True).tz_convert(IST))
    return df.sort_index(),'Dhan'
   except Exception as exc:LOG.warning('Dhan candles %s: %s',symbol,exc)
  ticker=INDEX.get(symbol,symbol+'.NS' if not symbol.startswith('^') and not symbol.endswith('.NS') else symbol)
  if not ticker:return pd.DataFrame(),'N/A'
  try:
   df=yf.download(ticker,period=period,interval=interval,progress=False,auto_adjust=False,threads=False,timeout=7)
   if isinstance(df.columns,pd.MultiIndex):df.columns=df.columns.get_level_values(0)
   if df.empty:return df,'N/A'
   return df[['Open','High','Low','Close','Volume']].dropna(),'Yahoo (delayed/unverified)'
  except Exception as exc:LOG.warning('Yahoo %s: %s',symbol,exc);return pd.DataFrame(),'N/A'
 def quote(self,symbol,security_id=None,segment='NSE_EQ'):
  if security_id:
   try:
    obj=self.request('marketfeed/ltp',{segment:[int(security_id)]})
    item=obj['data'][segment][str(security_id)];return {'ltp':float(item['last_price']),'change':None,'source':'Dhan','reason':''}
   except Exception as exc:LOG.warning('Dhan quote %s: %s',symbol,exc)
  df,source=self.candles(symbol,'1d','5d')
  if df.empty:return {'ltp':None,'change':None,'source':'N/A','reason':'Dhan unavailable and Yahoo returned no data'}
  last=float(df.Close.iloc[-1]);prev=float(df.Close.iloc[-2]) if len(df)>1 else None
  return {'ltp':last,'change':(last/prev-1)*100 if prev else None,'source':source,'reason':'Yahoo may be delayed'}
 def option_chain(self,underlying_id,segment='NSE_EQ',expiry=None):
  if not expiry:raise ValueError('Expiry required from instrument master')
  return self.request('optionchain',{'UnderlyingScrip':int(underlying_id),'UnderlyingSeg':segment,'Expiry':expiry})
