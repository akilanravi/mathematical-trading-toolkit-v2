"""Underlying-only, explicitly simulated paper trading. No broker order API."""
import sqlite3
from pathlib import Path
import pandas as pd
import streamlit as st
import mtt_v2 as m

DB = Path(__file__).with_name('paper_trades.sqlite3')


def connect():
    conn = sqlite3.connect(DB, timeout=15)
    conn.execute('''CREATE TABLE IF NOT EXISTS simulated_positions (
        id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL,
        opened TEXT NOT NULL, entry REAL NOT NULL, stop REAL NOT NULL,
        target REAL NOT NULL, last_bar TEXT NOT NULL, status TEXT NOT NULL,
        closed TEXT, exit_price REAL, exit_reason TEXT, pnl_points REAL)''')
    conn.commit()
    return conn


def process(symbol, enabled=True):
    """Run only on completed 5-minute Dhan bars. Idempotent per candle."""
    import dhan_feed
    now = pd.Timestamp.now(tz='Asia/Kolkata')
    if not (now.weekday() < 5 and (now.hour, now.minute) >= (9, 30)):
        return 'Outside trading window; no new entry.'
    df = m.normalize_ohlcv(dhan_feed.fetch_dhan_candles(symbol, '5', 12))
    # Dhan bar timestamps are treated as bar starts. Drop unfinished bar.
    df = df.loc[df.index + pd.Timedelta(minutes=5) <= now]
    if len(df) < 200:
        return 'Insufficient completed 5-minute candles.'
    latest = df.iloc[-1]
    ts = df.index[-1]
    if ts.date() != now.date() or (now - ts) > pd.Timedelta(minutes=15):
        return 'Stale market candles; simulation paused.'
    conn = connect()
    try:
        conn.execute('BEGIN IMMEDIATE')
        open_pos = conn.execute('SELECT id,side,entry,stop,target,last_bar FROM simulated_positions WHERE symbol=? AND status=?', (symbol, 'OPEN')).fetchone()
        if open_pos:
            pid, side, entry, stop, target, last_bar = open_pos
            bars = df.loc[df.index > pd.Timestamp(last_bar)]
            for bar_ts, bar in bars.iterrows():
                exit_price = reason = None
                if side == 'CE':
                    if bar['low'] <= stop: exit_price, reason = stop, 'STOP'
                    elif bar['high'] >= target: exit_price, reason = target, 'TARGET'
                else:
                    if bar['high'] >= stop: exit_price, reason = stop, 'STOP'
                    elif bar['low'] <= target: exit_price, reason = target, 'TARGET'
                if exit_price is None and bar_ts.time() >= pd.Timestamp('15:10').time():
                    exit_price, reason = float(bar['close']), 'EOD'
                if exit_price is not None:
                    points = (exit_price-entry) * (1 if side == 'CE' else -1)
                    conn.execute('UPDATE simulated_positions SET status=?,closed=?,exit_price=?,exit_reason=?,pnl_points=?,last_bar=? WHERE id=?', ('CLOSED', bar_ts.isoformat(),exit_price,reason,points,bar_ts.isoformat(),pid))
                    conn.commit()
                    return f'Simulated {side} exit: {reason}, underlying {exit_price:.2f}; {points:+.2f} points (not option P&L).'
            conn.execute('UPDATE simulated_positions SET last_bar=? WHERE id=?',(ts.isoformat(),pid))
            conn.commit()
            return f'Open simulated {side} position monitored through {ts:%H:%M} IST.'
        if not enabled or not (pd.Timestamp('09:35').time() <= ts.time() <= pd.Timestamp('14:40').time()):
            conn.commit()
            return 'No open position; new entries disabled or outside entry window.'
        # Avoid repeat entries for the same symbol/session.
        prior = conn.execute('SELECT COUNT(*) FROM simulated_positions WHERE symbol=? AND substr(opened,1,10)=?', (symbol,str(ts.date()))).fetchone()[0]
        if prior >= 2:
            conn.commit()
            return 'Daily maximum of two simulated entries reached.'
        r = m.analyse(symbol, '5m', 'live', m.Config(), df=df, vix_val=0.14)
        dec = r['decision']['action']
        if dec not in ('BUY CE','BUY PE'):
            conn.commit()
            return f'WAIT: {dec}. No simulated entry.'
        atr = float(r['atr']); price = float(latest['close'])
        if atr <= 0:
            conn.commit(); return 'Invalid ATR; no entry.'
        side = dec.split()[-1]
        stop = price - atr if side == 'CE' else price + atr
        target = price + 1.5*atr if side == 'CE' else price - 1.5*atr
        conn.execute('INSERT INTO simulated_positions(symbol,side,opened,entry,stop,target,last_bar,status) VALUES (?,?,?,?,?,?,?,?)', (symbol,side,ts.isoformat(),price,stop,target,ts.isoformat(),'OPEN'))
        conn.commit()
        return f'Simulated {side} entry at underlying {price:.2f}; SL {stop:.2f}; target {target:.2f}. No broker order.'
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@st.fragment(run_every='60s')
def render_paper_tab():
    st.subheader('📝 Automatic Paper Trader — Underlying Simulation')
    st.caption('Simulates CE/PE direction using completed Dhan 5-minute candles. No option premiums, fills, broker orders or monetary option P&L are claimed. Updates only while the Streamlit session is running.')
    symbol = st.selectbox('Paper instrument', ['RELIANCE','HDFCBANK','ICICIBANK','INFY','SBIN','TCS'], key='paper_symbol')
    enabled = st.toggle('Allow new simulated entries', value=False, key='paper_auto_enabled')
    if st.button('Refresh paper engine now', key='paper_refresh'):
        pass
    try:
        msg = process(symbol, enabled)
        st.info(msg)
    except Exception as exc:
        st.error(f'Paper engine paused: {exc}')
    conn = connect()
    try:
        positions = pd.read_sql_query('SELECT id,symbol,side,opened,entry,stop,target,status,closed,exit_price,exit_reason,pnl_points FROM simulated_positions ORDER BY id DESC LIMIT 100', conn)
    finally:
        conn.close()
    if positions.empty:
        st.caption('No simulated positions yet.')
    else:
        st.dataframe(positions, hide_index=True, use_container_width=True)
    st.warning('SQLite storage may be ephemeral on Streamlit Community Cloud. The simulator does not run when no session is active; a persistent server/worker is required for continuous unattended operation.')
