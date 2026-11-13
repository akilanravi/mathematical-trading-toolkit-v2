import streamlit as st
import pandas as pd
from datetime import date,datetime
from zoneinfo import ZoneInfo
from paper_engine import ENGINE
from db import rows

def render(provider):
 st.subheader('Auto Paper Trader')
 st.warning('SAFE MODE: No automatic entries until actual option-chain premiums, lot sizes and a persistent worker are validated. The quote monitor below is informational, not an executable option premium.')
 symbol=st.selectbox('Live underlying quote monitor',['NIFTY 50','BANK NIFTY','RELIANCE','HDFCBANK','INFY'],key='paper_monitor_symbol')
 enabled=st.toggle('Monitor latest underlying price (every 5 seconds)',value=False,key='paper_monitor_on')
 @st.fragment(run_every='5s')
 def monitor():
  if not st.session_state.get('paper_monitor_on',False):
   st.caption('Monitor paused; no market-data requests.');return
  q=provider.index_quote(symbol) if symbol in ('NIFTY 50','BANK NIFTY') else provider.quote(symbol)
  st.metric(symbol,'N/A' if q.get('ltp') is None else f"{q['ltp']:,.2f}")
  st.caption(f"Source: {q.get('source','N/A')} · Checked {datetime.now(ZoneInfo('Asia/Kolkata')):%H:%M:%S} IST · {q.get('reason','')}")
 monitor()
 a,b=st.columns(2)
 with a:
  if st.button('Start safe-mode worker'):ENGINE.start()
 with b:
  if st.button('Stop worker'):ENGINE.stop()
 st.write('Worker:',ENGINE.status)
 selected=st.date_input('Trade date',date.today())
 trades=rows('SELECT * FROM trades WHERE trade_date=? ORDER BY id DESC',(str(selected),))
 if trades:
  df=pd.DataFrame(trades);st.dataframe(df,use_container_width=True)
  st.download_button('Export CSV',df.to_csv(index=False),'trades.csv')
 else:st.info('No verified paper trades recorded for this date.')
