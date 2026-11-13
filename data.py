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
def _column(df: pd.DataFrame, *candidates: str) -> str | None:
    """Find a Dhan master column despite case, spaces or underscores."""
    normalized = {re.sub(r"[^a-z0-9]", "", str(name).lower()): name for name in df.columns}
    for candidate in candidates:
        key = re.sub(r"[^a-z0-9]", "", candidate.lower())
        if key in normalized:
            return normalized[key]
    return None


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
    self.error=f'Dhan HTTP {r.status_code}: {r.text[:200]}';self.blocked_until=time.monotonic()+(300 if r.status_code in (401,403) else 600)
    raise RuntimeError(self.error)
   r.raise_for_status();return r.json()
  except requests.RequestException as exc:raise RuntimeError(f'Dhan network/API error: {exc}') from exc
 def instrument_master(self, detailed=False):
  """Load Dhan's public master, refreshing it each IST calendar day.

  The compact file does NOT contain UNDERLYING_SYMBOL; use SM_SYMBOL_NAME.
  Detailed format is fetched only when compact mapping cannot be completed.
  """
  today=datetime.now(IST).date()
  if getattr(self, '_master_day', None) != today:
   self.master=None
   self._detailed_master=None
   self._master_day=today
  attr='_detailed_master' if detailed else 'master'
  existing=getattr(self,attr,None)
  if existing is not None:return existing
  url='https://images.dhan.co/api-data/api-scrip-master' + ('-detailed' if detailed else '') + '.csv'
  try:
   df=pd.read_csv(url,low_memory=False)
   df.columns=[str(c).strip().lstrip('\ufeff') for c in df.columns]
   if df.empty:raise ValueError('CSV is empty')
   setattr(self,attr,df)
   return df
  except Exception as exc:
   raise RuntimeError(f'Dhan {"detailed" if detailed else "compact"} instrument master unavailable: {exc}') from exc
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

 def fo_stocks(self):
  """Discover current NSE FUTSTK underlyings and their verified NSE_EQ IDs.

  Compact Dhan master: futures base symbol = SM_SYMBOL_NAME.
  Detailed Dhan master: futures base symbol = UNDERLYING_SYMBOL.
  No parsing futures display names, no hardcoded security IDs.
  """
  def parse(df):
   exch=_column(df,'SEM_EXM_EXCH_ID','EXCH_ID','EXCHANGE')
   segment=_column(df,'SEM_SEGMENT','SEGMENT')
   inst=_column(df,'SEM_INSTRUMENT_NAME','INSTRUMENT')
   sid=_column(df,'SEM_SMST_SECURITY_ID','SECURITY_ID')
   cash_sym=_column(df,'SEM_TRADING_SYMBOL','TRADING_SYMBOL','SYMBOL_NAME')
   underlying=_column(df,'UNDERLYING_SYMBOL','SM_SYMBOL_NAME')
   under_sid=_column(df,'UNDERLYING_SECURITY_ID')
   expiry=_column(df,'SEM_EXPIRY_DATE','SM_EXPIRY_DATE')
   series=_column(df,'SEM_SERIES','SERIES')
   if not all((exch,inst,sid,cash_sym)) or not (underlying or under_sid):
    raise RuntimeError('Missing required master columns: '+', '.join(map(str,df.columns[:22])))
   exchange=df[exch].astype(str).str.upper().str.strip()
   instrument=df[inst].astype(str).str.upper().str.strip()
   segments=df[segment].astype(str).str.upper().str.strip() if segment else None
   is_nse=exchange.eq('NSE')
   futures=is_nse & instrument.eq('FUTSTK')
   cash=is_nse & instrument.isin(['EQUITY','EQ'])
   if segments is not None:
    futures &= segments.eq('D')
    cash &= segments.eq('E')
   fut=df.loc[futures].copy()
   if fut.empty:raise RuntimeError('No NSE FUTSTK contracts in instrument master')
   if expiry:
    exp=pd.to_datetime(fut[expiry],errors='coerce',utc=True)
    if exp.notna().any():
     fut=fut.loc[exp.dt.date >= datetime.now(IST).date()]
    if fut.empty:raise RuntimeError('No unexpired NSE FUTSTK contracts found')
   eq=df.loc[cash].copy()
   if series:
    eq=eq.loc[eq[series].astype(str).str.upper().str.strip().eq('EQ')]
   if eq.empty:raise RuntimeError('No NSE_EQ equity records in instrument master')
   eq['_sid']=pd.to_numeric(eq[sid],errors='coerce')
   eq=eq.loc[eq['_sid'].notna() & (eq['_sid']>0)]
   eq['_name']=eq[cash_sym].astype(str).str.upper().str.strip().str.replace(r'-EQ$', '',regex=True)
   eq=eq.loc[~eq['_name'].isin(['','NAN','NONE'])]
   if eq.empty:raise RuntimeError('No valid NSE_EQ symbol-to-ID mappings')

   # Prefer Dhan's explicit underlying cash security ID if supplied.
   if under_sid:
    ids=pd.to_numeric(fut[under_sid],errors='coerce').dropna().astype('int64')
    eligible_ids=set(ids[ids>0].tolist())
    matches=eq.loc[eq['_sid'].astype('int64').isin(eligible_ids)]
   else:
    matches=eq.iloc[0:0]
   # Compact SM_SYMBOL_NAME holds the futures underlying ticker; match it
   # to NSE equity trading symbols exactly (not to derivative display names).
   if underlying:
    names=fut[underlying].astype(str).str.upper().str.strip()
    eligible_names=set(names)-{'','NAN','NONE'}
    matches=pd.concat([matches,eq.loc[eq['_name'].isin(eligible_names)]],ignore_index=True)
   if matches.empty:raise RuntimeError('FUTSTK found, but no matching NSE_EQ cash IDs')
   matches=matches.drop_duplicates(subset=['_name','_sid'])
   out={}
   for name,group in matches.groupby('_name'):
    unique=group['_sid'].astype('int64').unique()
    if len(unique)==1:
     out[name]={'security_id':int(unique[0]),'segment':'NSE_EQ','instrument':'EQUITY'}
   if not out:raise RuntimeError('No unambiguous NSE_EQ mappings for F&O stocks')
   return dict(sorted(out.items()))

  compact_error=None
  try:
   return parse(self.instrument_master())
  except Exception as exc:
   compact_error=str(exc)
   LOG.warning('Compact Dhan F&O master mapping failed: %s',compact_error)
  try:
   return parse(self.instrument_master(detailed=True))
  except Exception as exc:
   raise RuntimeError(f'Compact: {compact_error}; Detailed: {exc}') from exc

 def stock_quotes(self, mapping, batch_size=500):
     """Batched Dhan quotes. Missing entries are explicitly N/A, never fabricated."""
     result = {s: {'ltp': None, 'change': None, 'source': 'N/A', 'reason': 'Not fetched'} for s in mapping}
     symbols = list(mapping)
     for start in range(0, len(symbols), batch_size):
         chunk = symbols[start:start+batch_size]
         try:
             ids = [int(mapping[s]['security_id']) for s in chunk]
             response = self.request('marketfeed/ltp', {'NSE_EQ': ids})
             group = response.get('data', {}).get('NSE_EQ', {})
             for s in chunk:
                 item = group.get(str(mapping[s]['security_id']), {})
                 price = item.get('last_price') if isinstance(item, dict) else None
                 if price is not None and float(price) > 0:
                     result[s] = {'ltp': float(price), 'change': None, 'source': 'Dhan', 'reason': ''}
                 else:
                     result[s]['reason'] = 'No valid Dhan LTP in response'
         except Exception as exc:
             for s in chunk:
                 result[s]['reason'] = str(exc)[:180]
     return result
