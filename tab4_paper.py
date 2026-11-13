import streamlit as st,pandas as pd
from datetime import date
from paper_engine import ENGINE
from db import rows
from costs import round_trip

def render(provider):
 st.subheader('Auto Paper Trader')
 st.warning('SAFE MODE: Automatic paper entries are disabled until option-chain premiums, F&O mappings, exchange holidays and persistent worker hosting are verified. No trades are fabricated.')
 a,b=st.columns(2)
 with a:
  if st.button('Start worker'):ENGINE.start()
 with b:
  if st.button('Stop worker'):ENGINE.stop()
 st.write('Worker:',ENGINE.status)
 selected=st.date_input('Trade date',date.today())
 trades=rows('SELECT * FROM trades WHERE trade_date=? ORDER BY id DESC',(str(selected),))
 if trades:
  df=pd.DataFrame(trades);st.dataframe(df,use_container_width=True)
  st.download_button('Export CSV',df.to_csv(index=False),'trades.csv')
 else:st.info('No trades recorded for this date.')
 st.subheader('Strategies 1–10')
 st.dataframe(pd.DataFrame({'Strategy':list(range(1,11)),'Trades':[0]*10,'Net P&L':[None]*10,'Win rate':[None]*10}),hide_index=True)
