from datetime import datetime,time
from zoneinfo import ZoneInfo
import streamlit as st
from data import INDEX
@st.fragment(run_every='10s')
def show(provider):
 now=datetime.now(ZoneInfo('Asia/Kolkata'))
 status='Closed' if now.weekday()>4 or not(time(9,0)<=now.time()<time(15,30)) else ('Pre-open' if now.time()<time(9,15) else 'Open')
 st.caption(f'IST {now:%Y-%m-%d %H:%M:%S} · NSE {status} · quote refresh ~10s · Yahoo quotes may be delayed')
 cols=st.columns(7)
 for col,symbol in zip(cols,INDEX):
  with col:
   # quote requests cached at app level, not seven per render
   q=st.session_state.get('top_quotes',{}).get(symbol,{'ltp':None,'source':'N/A','change':None})
   val='N/A' if q['ltp'] is None else f"{q['ltp']:,.2f}"
   change='' if q.get('change') is None else f"{q['change']:+.2f}%"
   st.metric(symbol,val,change)
   st.caption(q['source'])
 sources={q['source'] for q in st.session_state.get('top_quotes',{}).values()}
 st.caption('Data source: '+('DHAN (live)' if sources=={'Dhan'} else 'MIXED / YAHOO fallback' if sources else 'N/A (refresh quotes)'))
