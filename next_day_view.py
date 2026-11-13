"""Next-Day Desk only. Reads a saved after-close snapshot; no on-tab API bursts."""
import json
from pathlib import Path
import pandas as pd
import streamlit as st

SNAPSHOT=Path(__file__).parent/'snapshots'/'next_day.json'

def render():
    st.subheader('🏛️ Next-Day Institutional Research Desk')
    st.caption('Automatically generated after close by GitHub Actions. Watchlist only — wait for next-session entry confirmation.')
    if not SNAPSHOT.exists():
        st.warning('No completed after-close Dhan snapshot yet. Check GitHub Actions → Next-Day Desk Snapshot. No candidates are fabricated.')
        return
    try:data=json.loads(SNAPSHOT.read_text(encoding='utf-8'))
    except Exception as exc:st.error(f'Snapshot unreadable: {exc}');return
    st.info(f"Session: {data.get('session_date','N/A')} · Generated: {data.get('generated_ist','N/A')} · Dhan symbols checked: {data.get('scanned',0)} · Qualified: {data.get('eligible',0)}")
    st.warning('Research-only. FII/DII figures are aggregate cash-market data, not proof of stock-wise institutional buying. News headlines are unverified until checked against full articles and exchange filings.')
    for key,title in [('bull','🟢 Bullish CE Watchlist'),('bear','🔴 Bearish PE Watchlist')]:
        st.markdown('### '+title)
        items=data.get(key,[])
        if not items:st.info('No qualified candidates; do not force ten.');continue
        table=[]
        for i,r in enumerate(items,1):
            table.append({'Rank':i,'Stock':r['symbol'],'Close ₹':r['price'],'Score':r['score'],'Technical':f"{r['technical_points']} vs {r['opposite_points']}",'Volume ×':r['volume_ratio'],'Candle IST':r['candle_ist'],'Source':r['source'],'Strike':'Unverified'})
        st.dataframe(pd.DataFrame(table),hide_index=True,use_container_width=True)
        for r in items:
            with st.expander(f"{r['symbol']} — advantages, disadvantages and news"):
                st.write('Advantages: '+'; '.join(r['advantages']))
                st.write('Risks: '+'; '.join(r['disadvantages']))
                st.write('Entry: '+r['entry_rule'])
                for label,field in [('Google News','news'),('Moneycontrol discoveries','moneycontrol_news')]:
                    st.markdown('**'+label+'**')
                    if not r.get(field):st.caption('No accessible matching headlines; not evidence of no news')
                    for article in r.get(field,[]):st.markdown(f"- [{article['title']}]({article['url']}) — {article['published']}")
    st.markdown('### FII / DII — market-wide cash flows')
    flow=data.get('fii_dii',{})
    if flow.get('verified') and flow.get('rows'):st.dataframe(pd.DataFrame(flow['rows']),use_container_width=True,hide_index=True)
    else:st.warning('Official NSE FII/DII feed unavailable or unverified; no figures substituted.')
    st.markdown('### Market news')
    for a in data.get('market_news',[]):st.markdown(f"- [{a['title']}]({a['url']}) — {a['published']}")
    if data.get('errors'):
        with st.expander('Excluded symbols and Dhan diagnostics'):
            st.dataframe(pd.DataFrame(data['errors']),use_container_width=True,hide_index=True)
