import logging,streamlit as st
from data import DataProvider,INDEX
from db import init
import topbar,tab1_radar,tab2_desk,tab3_deep,tab4_paper
logging.basicConfig(level=logging.INFO)
st.set_page_config(page_title='Mathematical Trading Toolkit V2',layout='wide')
st.markdown('''<style>div[data-testid="stTabs"] button,div[data-testid="stTabs"] button p{color:#fff!important} .stApp{color:#f8fafc}</style>''',unsafe_allow_html=True)
@st.cache_resource
def provider(cid,token):return DataProvider(cid,token)
@st.cache_data(ttl=10,show_spinner=False)
def quotes(cid,token):
 p=provider(cid,token);return {symbol:p.quote(symbol) for symbol in INDEX}
init()
try:cid=st.secrets.get('DHAN_CLIENT_ID','');token=st.secrets.get('DHAN_ACCESS_TOKEN','')
except Exception:cid=token=''
p=provider(cid,token)
st.title('Mathematical Trading Toolkit · MTT V2')
if st.button('Refresh index quotes',key='quote_refresh'):
 quotes.clear()
# Refresh only when user requests it; no blocking market calls on tab change.
if 'top_quotes' not in st.session_state:st.session_state.top_quotes={}
if st.button('Fetch top bar values',key='quote_fetch'):st.session_state.top_quotes=quotes(cid,token)
topbar.show(p)
# Segment control avoids eager execution of all st.tabs content.
choice=st.segmented_control('Section',['Radar','Next-Day Desk','Deep Analysis','Paper Trader'],default='Radar')
if choice=='Radar':tab1_radar.render(p)
elif choice=='Next-Day Desk':tab2_desk.render(p)
elif choice=='Deep Analysis':tab3_deep.render(p)
else:tab4_paper.render(p)
st.caption('Educational use only. Not investment advice. Verify all prices, costs and exchange calendars before trading.')
