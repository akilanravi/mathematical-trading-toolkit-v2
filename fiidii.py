"""Best effort public NSE FII/DII; never fabricate numbers."""
import concurrent.futures, datetime, requests
URL='https://www.nseindia.com/api/fiidiiTradeReact'
HEADERS={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0 Safari/537.36','Accept':'application/json,text/plain,*/*','Referer':'https://www.nseindia.com/'}
def nse():
 s=requests.Session();s.headers.update(HEADERS);s.get('https://www.nseindia.com/',timeout=3);r=s.get(URL,timeout=4);r.raise_for_status();data=r.json()
 if not isinstance(data,list) or not data:raise ValueError('Empty NSE response')
 return {'source':'NSE','rows':data[:10],'fetched':datetime.datetime.now(datetime.timezone.utc).isoformat(),'stale':False}
def moneycontrol():
 # No reliable documented JSON contract: refuse to fabricate a parsed figure.
 raise RuntimeError('Moneycontrol parser not configured: page format not verified')
def fetch():
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
  futures=[pool.submit(fn) for fn in (nse,moneycontrol)]
  for f in concurrent.futures.as_completed(futures):
   try:return f.result()
   except Exception:pass
 return {'source':'N/A','rows':[],'fetched':None,'stale':True,'reason':'Both public feeds unavailable'}
