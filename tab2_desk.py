import streamlit as st
from datetime import datetime
from zoneinfo import ZoneInfo
from db import save_desk,rows
from fiidii import fetch
@st.cache_data(ttl=60,show_spinner=False)
def fiidata():return fetch()
def render(provider):
 st.subheader('Next-Day Institutional Desk')
 now=datetime.now(ZoneInfo('Asia/Kolkata'))
 if st.button('Run now',key='desk_run'):
  result=fiidata();save_desk(str(now.date()),{'institutional':result,'candidates':[],'note':'No validated full-universe screening available'})
  st.success('Dated desk snapshot saved; no candidates were invented.')
 obj=fiidata();st.write('FII/DII public data',obj.get('source'), '·',obj.get('fetched') or 'not available')
 if obj['rows']:st.dataframe(obj['rows'],use_container_width=True)
 else:st.warning(obj.get('reason','No valid FII/DII data'))
 st.info('Delivery %, per-stock bulk/block evidence, shareholding, OI and option-chain metrics require separate validated feeds. Missing values remain N/A.')
 st.dataframe(rows('SELECT day,payload FROM desk_runs ORDER BY day DESC LIMIT 5'),use_container_width=True)
