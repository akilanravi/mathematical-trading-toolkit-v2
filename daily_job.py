"""After-close data snapshot. Intended for scheduled GitHub Actions execution."""
import json, os, sys, logging
from pathlib import Path
from datetime import datetime, time
from zoneinfo import ZoneInfo
from data import DataProvider, INDEX
from fiidii import fetch

logging.basicConfig(level=logging.INFO)
IST=ZoneInfo('Asia/Kolkata')
WATCHLIST=[s.strip().upper() for s in os.getenv('MTT_DESK_WATCHLIST','RELIANCE,HDFCBANK,INFY,TCS,ICICIBANK').split(',') if s.strip()]

def run(force=False):
 now=datetime.now(IST)
 if not force and (now.weekday()>4 or now.time()<time(15,45)):
  print('Not after close on an NSE weekday; skipping.');return False
 provider=DataProvider(os.getenv('DHAN_CLIENT_ID',''),os.getenv('DHAN_ACCESS_TOKEN',''))
 result={'created_ist':now.isoformat(),'market_date':now.date().isoformat(), 'status':'after-close snapshot, not a full-universe ranking', 'indices':{}, 'watchlist':{},'fiidii':{},'warnings':[]}
 for name in INDEX:
  q=provider.index_quote(name)
  result['indices'][name]={'ltp':q.get('ltp'),'change_pct':q.get('change'),'source':q.get('source'),'reason':q.get('reason')}
 for symbol in WATCHLIST:
  candles,source=provider.candles(symbol,'1d','1mo')
  result['watchlist'][symbol]={'source':source,'last_close':float(candles.Close.iloc[-1]) if not candles.empty else None,'last_bar':str(candles.index[-1]) if not candles.empty else None,'reason':None if not candles.empty else 'No daily candles'}
 try:result['fiidii']=fetch()
 except Exception as exc:result['warnings'].append('FII/DII unavailable: '+str(exc)[:150])
 folder=Path('desk_snapshots');folder.mkdir(exist_ok=True)
 target=folder/(now.date().isoformat()+'.json')
 target.write_text(json.dumps(result,indent=2,default=str),encoding='utf-8')
 print('Saved',target,'watchlist:',len(WATCHLIST));return True

if __name__=='__main__':sys.exit(0 if run('--force' in sys.argv) else 0)
