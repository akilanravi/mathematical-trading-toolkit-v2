"""Mathematical Trading Toolkit V2 entry point — safe diagnostics and lazy data loading."""
import logging
import streamlit as st
from data import DataProvider, INDEX
from db import init
import topbar
import tab1_radar
import tab2_desk
import tab3_deep
import tab4_paper

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger(__name__)
st.set_page_config(page_title='Mathematical Trading Toolkit V2', layout='wide')
st.markdown('''<style>
.stApp { color: #f8fafc; }
div[data-testid="stTabs"] button, div[data-testid="stTabs"] button p { color: #fff !important; }
</style>''', unsafe_allow_html=True)

@st.cache_resource
def provider(cid: str, token: str) -> DataProvider:
    return DataProvider(cid, token)

@st.cache_data(ttl=15, show_spinner=False)
def quotes(cid: str, token: str) -> dict:
    p = provider(cid, token)
    output = {}
    for symbol in INDEX:
        try:
            output[symbol] = p.index_quote(symbol)
        except Exception as exc:
            LOG.warning('Quote unavailable for %s: %s', symbol, type(exc).__name__)
            output[symbol] = {'ltp': None, 'change': None, 'source': 'N/A', 'reason': str(exc)[:150]}
    return output

def safe_response_summary(response: object, segment: str, sid: int) -> dict:
    """Show structural information only, never arbitrary response values or secrets."""
    if not isinstance(response, dict):
        return {'response_type': type(response).__name__}
    data = response.get('data')
    segment_data = data.get(segment) if isinstance(data, dict) else None
    item = segment_data.get(str(sid)) if isinstance(segment_data, dict) else None
    if item is None and isinstance(segment_data, dict):
        item = segment_data.get(sid)
    result = {
        'top_level_keys': list(response.keys()),
        'status': str(response.get('status', 'not supplied'))[:80],
        'data_type': type(data).__name__,
        'segment_keys': list(data.keys())[:12] if isinstance(data, dict) else [],
        'instrument_keys': list(segment_data.keys())[:8] if isinstance(segment_data, dict) else [],
        'instrument_found': isinstance(item, dict),
        'instrument_fields': list(item.keys()) if isinstance(item, dict) else [],
    }
    if isinstance(item, dict):
        value = item.get('last_price', item.get('ltp'))
        if isinstance(value, (int, float)):
            result['last_price'] = value
    # Avoid displaying arbitrary free-text API messages, which could contain sensitive data.
    return result

init()
try:
    cid = str(st.secrets.get('DHAN_CLIENT_ID', '')).strip()
    token = str(st.secrets.get('DHAN_ACCESS_TOKEN', '')).strip()
except Exception:
    cid = token = ''
p = provider(cid, token)
st.title('Mathematical Trading Toolkit · MTT V2')

c1, c2 = st.columns(2)
with c1:
    load_quotes = st.button('Fetch / refresh index quotes', key='quote_fetch')
with c2:
    st.caption('Quotes are fetched on request and cached for 15 seconds. Tab changes do not force a full scan.')
if 'top_quotes' not in st.session_state:
    st.session_state.top_quotes = {}
if load_quotes:
    with st.spinner('Fetching index quotes (Dhan first, Yahoo fallback)...'):
        st.session_state.top_quotes = quotes(cid, token)

topbar.show(p)

with st.expander('Dhan connection diagnostics', expanded=False):
    st.caption('A single Dhan test request. Never displays credentials or complete raw API responses.')
    if not cid or not token:
        st.warning('DHAN_CLIENT_ID or DHAN_ACCESS_TOKEN is missing from Streamlit Secrets.')
    if st.button('Test Dhan connection', key='dhan_test', disabled=not (cid and token)):
        try:
            sid, segment, instrument = p.resolve_index('NIFTY 50')
            st.write('NIFTY mapping:', {'security_id': sid, 'segment': segment, 'instrument': instrument})
            response = p.request('marketfeed/ltp', {segment: [int(sid)]})
            summary = safe_response_summary(response, segment, sid)
            st.json(summary)
            data = response.get('data', {}) if isinstance(response, dict) else {}
            segment_data = data.get(segment, {}) if isinstance(data, dict) else {}
            item = segment_data.get(str(sid), segment_data.get(sid)) if isinstance(segment_data, dict) else None
            price = item.get('last_price', item.get('ltp')) if isinstance(item, dict) else None
            if isinstance(price, (int, float)) and price > 0:
                st.success(f'Dhan NIFTY LTP returned: {price:,.2f}')
            else:
                st.warning('Dhan returned no usable NIFTY LTP. Inspect the structural summary above.')
        except Exception as exc:
            LOG.warning('Dhan diagnostic failed: %s', type(exc).__name__)
            # Only print exception class and a controlled short message; no request headers/tokens.
            message = str(exc)
            for secret in (token, cid):
                if secret:
                    message = message.replace(secret, '[REDACTED]')
            st.error(f'Dhan diagnostic: {type(exc).__name__}: {message[:300]}')

choice = st.segmented_control('Section', ['Radar', 'Next-Day Desk', 'Deep Analysis', 'Paper Trader'], default='Radar')
if choice == 'Radar':
    tab1_radar.render(p)
elif choice == 'Next-Day Desk':
    tab2_desk.render(p)
elif choice == 'Deep Analysis':
    tab3_deep.render(p)
else:
    tab4_paper.render(p)
st.caption('Educational use only. Not investment advice. Verify prices, costs and exchange calendars before trading.')
