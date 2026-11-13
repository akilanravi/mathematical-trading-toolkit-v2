import streamlit as st,pandas as pd
from conditions import NAMES
@st.fragment(run_every='30s')
def render(provider):
 st.subheader('Live Top 10 Bull / Bear Radar')
 st.caption('Ranking requires completed 5-minute candles and prior daily data. No score is generated for unavailable data.')
 if st.button('Run radar scan',key='radar_run'):
  st.session_state['radar_requested']=True
 if not st.session_state.get('radar_requested'):
  st.info('Click Run radar scan. Full F&O scanning needs validated Dhan instrument identifiers; no invented rankings are shown.');return
 st.warning('Full-universe scanner is disabled until F&O instrument master identifiers and Dhan historical permissions are validated.');st.session_state['radar_requested']=False
