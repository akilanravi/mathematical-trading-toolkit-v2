"""MTT V2 — Step 1: F&O-only Dhan connectivity, no index requests."""
import logging
import streamlit as st
from data import DataProvider
from db import init
import topbar, tab1_radar, tab2_desk, tab3_deep, tab4_paper

logging.basicConfig(level=logging.INFO)
st.set_page_config(page_title='Mathematical Trading Toolkit V2', layout='wide')
st.markdown('''<style>
.stApp {color:#f8fafc}
[data-testid="stSegmentedControl"] button, [data-testid="stSegmentedControl"] button p {color:#fff!important}
</style>''', unsafe_allow_html=True)

@st.cache_resource
def provider(cid, token):
    return DataProvider(cid, token)

@st.cache_data(ttl=86400, show_spinner=False)
def universe(cid, token):
    return provider(cid, token).fo_stocks()

@st.cache_data(ttl=30, show_spinner=False)
def stock_prices(cid, token, names):
    mapping = universe(cid, token)
    return provider(cid, token).stock_quotes({s: mapping[s] for s in names if s in mapping})

init()
try:
    cid = st.secrets.get('DHAN_CLIENT_ID', '')
    token = st.secrets.get('DHAN_ACCESS_TOKEN', '')
except Exception:
    cid = token = ''
p = provider(cid, token)
st.title('Mathematical Trading Toolkit · MTT V2')

@st.fragment(run_every='30s')
def status_panel():
    try:
        mapping = universe(cid, token)
        st.caption(f'F&O stock universe: {len(mapping)} verified NSE cash mappings · refresh 30 seconds')
        sample = list(mapping)[:5]
        if st.button('Test Dhan F&O batch', key='dhan_fo_test'):
            results = stock_prices(cid, token, tuple(sample))
            st.dataframe([{'Symbol': s, **q} for s, q in results.items()], hide_index=True)
    except Exception as exc:
        st.warning('F&O instrument master unavailable: ' + str(exc)[:240])

status_panel()
choice = st.segmented_control('Section', ['Radar', 'Next-Day Desk', 'Deep Analysis', 'Paper Trader'], default='Radar')
if choice == 'Radar':
    tab1_radar.render(p)
elif choice == 'Next-Day Desk':
    tab2_desk.render(p)
elif choice == 'Deep Analysis':
    tab3_deep.render(p)
else:
    tab4_paper.render(p)
st.caption('Educational only. Market prices and derivatives data may be delayed or unavailable.')
