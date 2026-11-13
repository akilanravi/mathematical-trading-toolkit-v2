"""Next-Day Desk only: read validated after-close snapshot, with GitHub fallback."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

SNAPSHOT = Path(__file__).parent / 'snapshots' / 'next_day.json'
DEFAULT_REPO = 'akilanravi/mathematical-trading-toolkit-v2'


def _settings():
    try:
        return st.secrets
    except Exception:
        return {}


@st.cache_data(ttl=120, show_spinner=False)
def load_snapshot(repo, branch):
    """Prefer committed GitHub snapshot; use local only if remote unavailable."""
    url = f'https://raw.githubusercontent.com/{repo}/{quote(branch, safe="")}/snapshots/next_day.json'
    errors = []
    try:
        response = requests.get(url, timeout=12, headers={'Cache-Control': 'no-cache'})
        response.raise_for_status()
        data = response.json()
        _validate(data)
        return data, 'GitHub snapshot', None
    except Exception as exc:
        errors.append(f'GitHub snapshot unavailable: {type(exc).__name__}')
    try:
        data = json.loads(SNAPSHOT.read_text(encoding='utf-8'))
        _validate(data)
        return data, 'Local snapshot (may be outdated)', '; '.join(errors)
    except Exception as exc:
        errors.append(f'Local snapshot unavailable: {type(exc).__name__}')
    return None, None, '; '.join(errors)


def _validate(data):
    if not isinstance(data, dict):
        raise ValueError('Snapshot must be an object')
    if not isinstance(data.get('bull'), list) or not isinstance(data.get('bear'), list):
        raise ValueError('Missing bull/bear arrays')
    if not data.get('session_date') or not data.get('generated_ist'):
        raise ValueError('Missing snapshot date')
    if data.get('status') != 'research_only':
        raise ValueError('Unexpected snapshot status')
    datetime.fromisoformat(data['generated_ist'])
    if len(data['bull']) > 10 or len(data['bear']) > 10:
        raise ValueError('More than 10 candidates per side')
    for side in ('bull', 'bear'):
        for item in data[side]:
            if not isinstance(item, dict) or not item.get('symbol'):
                raise ValueError('Invalid candidate')
            if item.get('side') != side.capitalize():
                raise ValueError('Candidate direction mismatch')


def _safe_news(articles):
    if not articles:
        st.caption('No matching headlines retrieved; this does not mean there is no news.')
    for article in articles or []:
        if isinstance(article, dict):
            title, url = str(article.get('title', 'Article')), str(article.get('url', ''))
            if url.startswith('https://'):
                st.markdown(f'- [{title}]({url}) — {article.get("published", "Date unknown")}')


def render():
    st.subheader('🏛️ Next-Day Institutional Research Desk')
    st.caption('After-close research watchlist. Confirm live price, liquidity, strike, expiry and entry conditions next session.')
    settings = _settings()
    repo = str(settings.get('SNAPSHOT_GITHUB_REPO', DEFAULT_REPO)).strip()
    branch = str(settings.get('SNAPSHOT_GITHUB_BRANCH', 'main')).strip()
    if st.button('🔄 Check latest after-close snapshot', key='next_day_refresh'):
        load_snapshot.clear()
        st.rerun()
    data, source, issue = load_snapshot(repo, branch)
    if data is None:
        st.warning('No validated after-close snapshot available. Check the GitHub Actions commit and snapshots/next_day.json. No candidates are fabricated.')
        st.caption(issue or '')
        return
    generated = datetime.fromisoformat(data['generated_ist'])
    now = datetime.now(timezone.utc)
    age = now - (generated.astimezone(timezone.utc) if generated.tzinfo else generated.replace(tzinfo=timezone.utc))
    st.info(f"Session: {data['session_date']} · Generated: {data['generated_ist']} · Source: {source} · Dhan symbols checked: {data.get('scanned', 0)} · Qualified: {data.get('eligible', 0)}")
    if issue:
        st.warning(issue + '. Showing fallback data; verify freshness before use.')
    if age > timedelta(days=4):
        st.error('Snapshot is more than four days old. Do not treat it as a current next-session watchlist.')
    elif age > timedelta(days=1):
        st.warning('Snapshot is older than 24 hours. Check trading holidays and whether a newer market session has completed.')
    st.caption('The snapshot is checked again every 120 seconds on the next Streamlit rerun. Use Refresh for an immediate check.')
    st.warning('Research only; FII/DII totals are market-wide, not evidence of stock-wise buying. Headlines are discovery links, not verified sentiment. No guaranteed trades.')
    for key, title in [('bull', '🟢 Bullish CE Watchlist'), ('bear', '🔴 Bearish PE Watchlist')]:
        st.markdown('### ' + title)
        items = data.get(key, [])
        if not items:
            st.info('No qualified candidates; do not force ten.')
            continue
        table = []
        for i, r in enumerate(items, 1):
            table.append({'Rank': i, 'Stock': r.get('symbol'), 'Close ₹': r.get('price'), 'Score': r.get('score'), 'Technical': f"{r.get('technical_points', '?')} vs {r.get('opposite_points', '?')}", 'Volume ×': r.get('volume_ratio'), 'Candle IST': r.get('candle_ist'), 'Source': r.get('source'), 'Strike': r.get('strike', 'Not verified')})
        st.dataframe(pd.DataFrame(table), hide_index=True, use_container_width=True)
        for r in items:
            with st.expander(f"{r.get('symbol', 'Unknown')} — technical case, risks and news"):
                st.write('Advantages: ' + '; '.join(map(str, r.get('advantages', []))))
                st.write('Risks: ' + '; '.join(map(str, r.get('disadvantages', []))))
                st.write('Entry: ' + str(r.get('entry_rule', 'Wait for confirmed next-session entry')))
                st.write('Exit: ' + str(r.get('exit_rule', 'Not calculated or verified; define invalidation and risk before trading')))
                st.markdown('**Google News**')
                _safe_news(r.get('news', []))
                st.markdown('**Moneycontrol discoveries**')
                _safe_news(r.get('moneycontrol_news', []))
    st.markdown('### FII / DII — market-wide cash flows')
    flow = data.get('fii_dii') or {}
    if flow.get('verified') and flow.get('rows'):
        st.dataframe(pd.DataFrame(flow['rows']), use_container_width=True, hide_index=True)
    else:
        st.warning('Official NSE FII/DII feed unavailable or unverified; no figures substituted.')
    st.markdown('### Market news')
    _safe_news(data.get('market_news', []))
    if data.get('errors'):
        with st.expander('Excluded symbols and Dhan diagnostics'):
            st.dataframe(pd.DataFrame(data['errors']), use_container_width=True, hide_index=True)
