import streamlit as st,pandas as pd,plotly.graph_objects as go
from conditions import evaluate_conditions,NAMES
@st.fragment(run_every='30s')
def render(provider):
 st.subheader('Deep Analysis')
 symbol=st.text_input('NSE symbol','RELIANCE',key='deep_symbol').strip().upper()
 security_id=st.text_input('Dhan security ID (optional; required for Dhan-first candles)','',key='deep_sid')
 capital=st.number_input('Capital (₹)',min_value=1000.,value=25000.,step=1000.)
 threshold=st.number_input('Narrow CPR threshold (%)',min_value=0.01,max_value=5.,value=0.15)
 if not st.button('Load / refresh analysis',key='deep_load'):return
 with st.spinner('Loading completed candles…'):
  candles,source=provider.candles(symbol,'5m','60d',security_id or None)
  daily,ds=provider.candles(symbol,'1d','3mo',security_id or None)
 if candles.empty or daily.empty:st.error('No candles returned from Dhan or Yahoo; analysis unavailable.');return
 # Remove potentially incomplete current session daily candle.
 from datetime import datetime
 from zoneinfo import ZoneInfo
 now=datetime.now(ZoneInfo('Asia/Kolkata'))
 if len(daily)>2 and pd.Timestamp(daily.index[-1]).date()==now.date():daily=daily.iloc[:-1]
 result=evaluate_conditions(symbol,candles,daily,threshold)
 st.caption(f'5-minute source: {source}; daily: {ds} · {result.get("reason") or "computed"}')
 if result['reason']:st.warning(result['reason'])
 else:
  st.metric('Bull conditions',f"{result['score']}/9");st.metric('Bear conditions',f"{result['bear_score']}/9")
  for i,name in enumerate(NAMES,1):
   key=f'C{i}';st.write(('✅' if result['bull'][key] else '❌'),name,result['values'][key])
  st.info('WAIT FOR ENTRY — entry/option premium not validated. Risk budget: ₹'+f'{capital*.03:,.2f}')
 fig=go.Figure(go.Candlestick(x=candles.index,open=candles.Open,high=candles.High,low=candles.Low,close=candles.Close))
 fig.update_layout(template='plotly_dark',height=420,xaxis_rangeslider_visible=False)
 st.plotly_chart(fig,use_container_width=True)
 st.button('Send to Trade Journal',disabled=True,help='Requires a validated entry, strike, expiry and premium')
