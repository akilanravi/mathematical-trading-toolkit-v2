#!/usr/bin/env python3
"""
Mathematical Trading Toolkit V2
===============================
NSE index + F&O stock options-buying toolkit (CE / PE), Yahoo Finance data.

What it does
  * Corrected CPR + Camarilla maths (levels come only from COMPLETED sessions)
  * 10-point confirmation score for bullish and bearish side separately
  * Signal: BUY CE / BUY PE / WAIT / NO TRADE, with the reason for every point
  * Option plan: strike, model premium, SL, T1, T2 (Black-Scholes repricing)
  * Risk-based position sizing for your capital + realistic Zerodha costs
  * Backtest on the UNDERLYING signals with MODELLED option P&L, net of costs,
    with an in-sample / out-of-sample split and an evidence verdict

Honest limits (read these)
  * Yahoo has no historical option prices. Option P&L in the backtest is a
    Black-Scholes MODEL (realised-vol based), not real premiums.
  * Yahoo intraday history is ~60 days, so 5m/15m backtests have few trades.
    The verdict will say INSUFFICIENT EVIDENCE until you have 30+ trades.
  * Lot sizes / strike steps for stocks are placeholders: verify with NSE.
  * Scoring thresholds are heuristics until your backtest supports them.

Usage
  python mtt_v2.py signal NIFTY --tf 15m
  python mtt_v2.py signal NIFTY --tf 15m --mode next          # evening plan
  python mtt_v2.py signal RELIANCE --tf 1d                    # swing
  python mtt_v2.py scan --tf 15m
  python mtt_v2.py backtest NIFTY BANKNIFTY RELIANCE --tf 15m
  python mtt_v2.py backtest NIFTY --tf 1d --period 10y
  python mtt_v2.py costs --buy 120 --sell 140 --qty 65
  python mtt_v2.py selftest
Not investment advice. Options buying can lose 100% of the premium.
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, field
from datetime import time as dtime

import numpy as np
import pandas as pd

IST = "Asia/Kolkata"
SESSION_CLOSE = dtime(15, 30)
SESSION_MINUTES = 375


# ----------------------------------------------------------------------------
# 1. CONFIGURATION
# ----------------------------------------------------------------------------
@dataclass
class Costs:
    """Zerodha options-buying charges (verified Oct-Nov 2026; re-check often)."""
    brokerage_per_order: float = 20.0      # flat Rs per executed order
    stt_sell_pct: float = 0.15             # % of premium, sell side (from 1-Apr-2026)
    exch_pct: float = 0.03553              # % NSE options transaction charge on premium
    sebi_per_crore: float = 10.0           # Rs per crore turnover
    stamp_buy_pct: float = 0.003           # % on buy premium
    gst_pct: float = 18.0                  # on brokerage + exchange + SEBI
    slippage_per_unit: float = 0.50        # Rs per unit per side (your estimate)


@dataclass
class Config:
    capital: float = 25_000.0
    risk_per_trade_pct: float = 3.0        # see README: 1% cannot fit one Nifty lot
    daily_loss_limit_pct: float = 6.0
    max_capital_per_trade_pct: float = 60.0
    max_trades_per_day: int = 2
    atr_len: int = 14
    atr_sl_mult: float = 1.0               # SL distance = mult x ATR (underlying)
    rr_t1: float = 1.5
    rr_t2: float = 2.5
    min_score_pct: float = 0.60            # leader must reach 60% of available points
    min_margin_pct: float = 0.25           # and lead the other side by 25% of points
    cam_factor: float = 1.1
    narrow_cpr_pct: float = 0.25
    wide_cpr_pct: float = 0.60
    rsi_bull: tuple = (55, 75)
    rsi_bear: tuple = (25, 45)
    vol_ratio_min: float = 1.2
    risk_free: float = 0.065
    no_entry_before: dtime = dtime(9, 30)
    no_entry_after: dtime = dtime(14, 45)
    square_off: dtime = dtime(15, 15)
    max_hold_days: int = 5
    strike_itm_steps: int = 0              # 0 = ATM, 1 = one strike ITM ...
    costs: Costs = field(default_factory=Costs)

    @property
    def risk_budget(self) -> float:
        return self.capital * self.risk_per_trade_pct / 100.0

    @property
    def daily_loss_limit(self) -> float:
        return self.capital * self.daily_loss_limit_pct / 100.0


# Lot sizes: NIFTY 65 / BANKNIFTY 30 (NSE circular NSE/FAOP/70616, from Jan-2026).
# Stock lots and strike steps are PLACEHOLDERS -> verify with NSE before trading.
INSTRUMENTS = {
    "NIFTY":      dict(yf="^NSEI",                  lot=65,  step=50,  kind="index", dte_i=3,  dte_s=10, vol_floor=0.12, vix="^INDIAVIX"),
    "BANKNIFTY":  dict(yf="^NSEBANK",               lot=30,  step=100, kind="index", dte_i=10, dte_s=20, vol_floor=0.12, vix="^INDIAVIX"),
    "FINNIFTY":   dict(yf="NIFTY_FIN_SERVICE.NS",   lot=65,  step=50,  kind="index", dte_i=3,  dte_s=10, vol_floor=0.12, vix="^INDIAVIX"),
    "MIDCPNIFTY": dict(yf="^NSEMDCP",               lot=120, step=25,  kind="index", dte_i=3,  dte_s=10, vol_floor=0.12, vix="^INDIAVIX"),
    "RELIANCE":   dict(yf="RELIANCE.NS",            lot=500, step=10,  kind="stock", dte_i=10, dte_s=20, vol_floor=0.18, verify=True),
    "HDFCBANK":   dict(yf="HDFCBANK.NS",            lot=550, step=10,  kind="stock", dte_i=10, dte_s=20, vol_floor=0.18, verify=True),
    "ICICIBANK":  dict(yf="ICICIBANK.NS",           lot=700, step=10,  kind="stock", dte_i=10, dte_s=20, vol_floor=0.18, verify=True),
    "SBIN":       dict(yf="SBIN.NS",                lot=750, step=5,   kind="stock", dte_i=10, dte_s=20, vol_floor=0.20, verify=True),
    "TCS":        dict(yf="TCS.NS",                 lot=175, step=20,  kind="stock", dte_i=10, dte_s=20, vol_floor=0.18, verify=True),
    "INFY":       dict(yf="INFY.NS",                lot=400, step=20,  kind="stock", dte_i=10, dte_s=20, vol_floor=0.20, verify=True),
}
DEFAULT_WATCHLIST = list(INSTRUMENTS)


def resolve_instrument(name, lot=None, step=None, dte=None):
    key = name.upper().replace(".NS", "")
    base = dict(INSTRUMENTS.get(key, dict(yf=f"{key}.NS", lot=None, step=None, kind="stock",
                                          dte_i=10, dte_s=20, vol_floor=0.20, verify=True)))
    if lot:
        base["lot"] = int(lot)
    if step:
        base["step"] = float(step)
    if dte:
        base["dte_i"] = base["dte_s"] = float(dte)
    if base["lot"] is None:
        # Fallback default lot and step estimation for generic F&O scanning
        base["lot"] = 500
        base["step"] = default_step(2000)
    base["name"] = key
    return base


def default_step(price):
    """Approximate strike interval for stocks when none is configured (verify!)."""
    for limit, step in ((100, 2.5), (500, 5), (1500, 10), (3000, 20)):
        if price <= limit:
            return step
    return 50


# ----------------------------------------------------------------------------
# 2. DATA (Yahoo Finance)
# ----------------------------------------------------------------------------
def fetch(symbol: str, interval: str, period: str = None, start=None, end=None) -> pd.DataFrame:
    # 1. Try Dhan Fast Feed if configured (for intraday 1m/5m/15m)
    if interval in ("1m", "5m", "15m", "25m", "60m") and not (start and end):
        try:
            import dhan_feed
            mins = interval.replace("m", "")
            dhan_df = dhan_feed.fetch_dhan_candles(symbol, interval=mins, days=10)
            if dhan_df is not None and not dhan_df.empty:
                return dhan_df
        except Exception:
            pass

    # 2. Fallback to Yahoo Finance

    try:
        import yfinance as yf
    except ImportError:
        raise SystemExit("Install dependencies first:  pip install yfinance pandas numpy")
    if start and end:
        # Format dates as YYYY-MM-DD
        s_str = start.strftime("%Y-%m-%d") if hasattr(start, "strftime") else str(start)[:10]
        e_str = end.strftime("%Y-%m-%d") if hasattr(end, "strftime") else str(end)[:10]
        df = yf.download(symbol, interval=interval, start=s_str, end=e_str, auto_adjust=False, progress=False)
    else:
        df = yf.download(symbol, interval=interval, period=period or "1mo", auto_adjust=False, progress=False)
    if df is None or df.empty:
        raise RuntimeError(f"No data returned for {symbol} ({interval}, period={period}, start={start}, end={end})")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df = df.dropna(subset=["open", "high", "low", "close"])
    idx = df.index
    idx = idx.tz_localize(IST) if idx.tz is None else idx.tz_convert(IST)
    df.index = idx
    return df[~df.index.duplicated()].sort_index()


def fetch_batch(symbols: list[str], interval: str, period: str = None) -> dict[str, pd.DataFrame]:
    """
    Downloads data for all symbols in a SINGLE batch HTTP request.
    Reduces 200+ network calls down to 1 call (completing in ~4-6 seconds).
    """
    try:
        import yfinance as yf
    except ImportError:
        raise SystemExit("Install dependencies first: pip install yfinance pandas numpy")
        
    style = "intraday" if interval in ("1m", "5m", "15m", "30m", "60m") else "swing"
    period = period or ("30d" if style == "intraday" else "2y")
    
    ticker_map = {}
    for s in symbols:
        inst = resolve_instrument(s)
        ticker_map[inst["yf"]] = s
        
    tickers_list = list(ticker_map.keys())
    if not tickers_list:
        return {}
        
    try:
        raw_df = yf.download(
            tickers=" ".join(tickers_list),
            interval=interval,
            period=period,
            group_by="ticker",
            auto_adjust=False,
            threads=True,
            progress=False
        )
    except Exception:
        return {}

    out = {}
    if raw_df is None or raw_df.empty:
        return out
        
    for yf_tick, sym in ticker_map.items():
        try:
            if len(tickers_list) == 1:
                sub = raw_df.copy()
            else:
                if yf_tick in raw_df.columns.levels[0]:
                    sub = raw_df[yf_tick].dropna(how="all")
                else:
                    continue
            if isinstance(sub.columns, pd.MultiIndex):
                sub.columns = sub.columns.get_level_values(0)
            sub = sub.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
            sub = sub.dropna(subset=["open", "high", "low", "close"])
            if len(sub) < 5:
                continue
            idx = sub.index
            idx = idx.tz_localize(IST) if idx.tz is None else idx.tz_convert(IST)
            sub.index = idx
            out[sym] = sub[~sub.index.duplicated()].sort_index()
        except Exception:
            continue
    return out


# ----------------------------------------------------------------------------
# 3. INDICATORS
# ----------------------------------------------------------------------------
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(dn != 0, 100.0)


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def supertrend(df, n=10, mult=3.0):
    """Returns +1 (uptrend) / -1 (downtrend)."""
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    a = atr(df, n).to_numpy()
    c = df["close"].to_numpy()
    ub, lb = hl2 + mult * a, hl2 - mult * a
    fub, flb = ub.copy(), lb.copy()
    direction = np.ones(len(df))
    for i in range(1, len(df)):
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1]) else flb[i - 1]
        if direction[i - 1] == 1:
            direction[i] = -1 if c[i] < flb[i] else 1
        else:
            direction[i] = 1 if c[i] > fub[i] else -1
    return pd.Series(direction, index=df.index)


def build_features(df, style, cfg):
    f = df.copy()
    c = f["close"]
    f["ema9"], f["ema21"], f["ema50"], f["ema200"] = ema(c, 9), ema(c, 21), ema(c, 50), ema(c, 200)
    f["rsi"], f["atr"], f["st"] = rsi(c, 14), atr(f, cfg.atr_len), supertrend(f, 10, 3.0)
    vol_ok = float(f["volume"].fillna(0).sum()) > 0
    f["volr"] = f["volume"] / f["volume"].rolling(20).mean() if vol_ok else np.nan
    f["vwap"] = np.nan
    f.attrs["vwap_is_proxy"] = not vol_ok
    if style == "intraday":
        day = f.index.tz_localize(None).normalize()
        tp = (f["high"] + f["low"] + f["close"]) / 3
        if vol_ok:
            f["vwap"] = (tp * f["volume"]).groupby(day).cumsum() / f["volume"].groupby(day).cumsum().replace(0, np.nan)
        else:  # Yahoo index volume is 0 -> equal-weight session average (proxy)
            f["vwap"] = tp.groupby(day).transform(lambda s: s.expanding().mean())
    return f


# ----------------------------------------------------------------------------
# 4. MARKET MATHEMATICS: CPR + CAMARILLA (corrected)
# ----------------------------------------------------------------------------
def cpr(h, l, c):
    """Central Pivot Range. TC/BC are normalised so TC is always the TOP."""
    p = (h + l + c) / 3.0
    bc_raw = (h + l) / 2.0
    tc_raw = 2.0 * p - bc_raw
    top, bot = max(tc_raw, bc_raw), min(tc_raw, bc_raw)
    return dict(P=p, TC=top, BC=bot, width=top - bot, width_pct=(top - bot) / p * 100.0)


def camarilla(h, l, c, f=1.1):
    r = h - l
    return {"H4": c + r * f / 2, "H3": c + r * f / 4, "H2": c + r * f / 6, "H1": c + r * f / 12,
            "L1": c - r * f / 12, "L2": c - r * f / 6, "L3": c - r * f / 4, "L4": c - r * f / 2}


def levels_from(p1, p2, cfg):
    """Levels for the NEXT period, from the last completed period p1 (and the one before, p2)."""
    c1, c2 = cpr(p1["high"], p1["low"], p1["close"]), cpr(p2["high"], p2["low"], p2["close"])
    d = dict(P=c1["P"], TC=c1["TC"], BC=c1["BC"], width_pct=c1["width_pct"],
             pTC=c2["TC"], pBC=c2["BC"], PH=p1["high"], PL=p1["low"])
    d.update(camarilla(p1["high"], p1["low"], p1["close"], cfg.cam_factor))
    return d


def cpr_relation(tc, bc, ptc, pbc):
    if bc > ptc:
        return "HIGHER (bullish)", 2, 0
    if tc < pbc:
        return "LOWER (bearish)", 0, 2
    if bc > pbc and tc > ptc:
        return "OVERLAP HIGHER (mild bullish)", 1, 0
    if bc < pbc and tc < ptc:
        return "OVERLAP LOWER (mild bearish)", 0, 1
    return "INSIDE/OUTSIDE (neutral)", 0, 0


def period_ohlc(df, style):
    d = df.copy()
    d.index = d.index.tz_localize(None)
    g = d.groupby(d.index.normalize()) if style == "intraday" else d.resample("W-FRI")
    out = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                close=("close", "last"), volume=("volume", "sum"))
    return out.dropna(subset=["close"])


def level_table(per, cfg):
    rows = {per.index[k]: levels_from(per.iloc[k - 1], per.iloc[k - 2], cfg) for k in range(2, len(per))}
    return pd.DataFrame.from_dict(rows, orient="index")


# ----------------------------------------------------------------------------
# 5. SCORING + SIGNAL
# ----------------------------------------------------------------------------
def score_setup(x, cfg, style):
    """x: dict(price, TC, BC, pTC, pBC, H3, H4, L3, L4, ema9, ema21, ema50, vwap, rsi, st, volr, candle)."""
    p, ok = x["price"], pd.notna
    comps = []
    rel, rb, rs_ = cpr_relation(x["TC"], x["BC"], x["pTC"], x["pBC"])
    comps.append(("CPR vs previous CPR", 2, rb, rs_, True, rel))
    comps.append(("Price vs CPR", 1, int(p > x["TC"]), int(p < x["BC"]), True,
                  f"price {p:.2f} | BC {x['BC']:.2f} | TC {x['TC']:.2f}"))
    cb = 2 if p > x["H4"] else (1 if p > x["H3"] else 0)
    cs = 2 if p < x["L4"] else (1 if p < x["L3"] else 0)
    comps.append(("Camarilla H3/H4, L3/L4", 2, cb, cs, True, f"H3 {x['H3']:.2f} H4 {x['H4']:.2f} | L3 {x['L3']:.2f} L4 {x['L4']:.2f}"))
    e_ok = ok(x["ema9"]) and ok(x["ema21"])
    comps.append(("EMA 9/21", 1, int(e_ok and x["ema9"] > x["ema21"] and p > x["ema21"]),
                  int(e_ok and x["ema9"] < x["ema21"] and p < x["ema21"]), e_ok,
                  f"EMA9 {x['ema9']:.2f} EMA21 {x['ema21']:.2f}"))
    name, ref = ("VWAP", x["vwap"]) if style == "intraday" else ("EMA50", x["ema50"])
    r_ok = ok(ref)
    comps.append((f"Trend filter ({name})", 1, int(r_ok and p > ref), int(r_ok and p < ref), r_ok,
                  f"{name} {ref:.2f}" if r_ok else "n/a"))
    rv, rk = x["rsi"], ok(x["rsi"])
    comps.append(("RSI(14)", 1, int(rk and cfg.rsi_bull[0] <= rv <= cfg.rsi_bull[1]),
                  int(rk and cfg.rsi_bear[0] <= rv <= cfg.rsi_bear[1]), rk,
                  f"RSI {rv:.1f} (bull {cfg.rsi_bull}, bear {cfg.rsi_bear})" if rk else "n/a"))
    s_ok = ok(x["st"])
    comps.append(("Supertrend(10,3)", 1, int(s_ok and x["st"] > 0), int(s_ok and x["st"] < 0), s_ok,
                  "UP" if s_ok and x["st"] > 0 else "DOWN"))
    v_ok = ok(x["volr"])
    hv = v_ok and x["volr"] >= cfg.vol_ratio_min
    comps.append(("Volume spike + candle", 1, int(hv and x["candle"] > 0), int(hv and x["candle"] < 0), v_ok,
                  f"vol x{x['volr']:.2f}" if v_ok else "volume unavailable (excluded from max)"))
    mx = sum(c[1] for c in comps if c[4])
    bull = sum(c[2] for c in comps if c[4])
    bear = sum(c[3] for c in comps if c[4])
    return dict(comps=comps, bull=bull, bear=bear, max=mx)


def decide(sc, cfg, window_ok=True, stop_reason=None):
    mx, b, s = sc["max"], sc["bull"], sc["bear"]
    lead = max(b, s)
    pct = lead / mx if mx else 0.0
    margin_ok = abs(b - s) >= cfg.min_margin_pct * mx
    if stop_reason:
        action = f"NO TRADE ({stop_reason})"
    elif not window_ok:
        action = "WAIT (outside entry window)"
    elif pct >= cfg.min_score_pct and margin_ok:
        action = "BUY CE" if b > s else "BUY PE"
    elif pct >= 0.40:
        action = "WAIT"
    else:
        action = "NO TRADE"
    grade = "STRONG" if pct >= 0.8 else "GOOD" if pct >= 0.6 else "WAIT FOR CONFIRMATION" if pct >= 0.4 else "NO TRADE"
    return dict(action=action, leader_pct=pct, grade=grade, margin_ok=margin_ok,
                direction=("CE" if b > s else "PE" if s > b else None))


# ----------------------------------------------------------------------------
# 6. OPTIONS MATHEMATICS + COSTS + POSITION SIZING
# ----------------------------------------------------------------------------
def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S, K, T, sigma, r, kind):
    if T <= 1e-9 or sigma <= 0:
        return max(S - K, 0.0) if kind == "CE" else max(K - S, 0.0)
    d1 = (math.log(S / K) + (r + sigma ** 2 / 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if kind == "CE":
        return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    return K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)


def bs_delta(S, K, T, sigma, r, kind):
    if T <= 1e-9 or sigma <= 0:
        return (1.0 if S > K else 0.0) if kind == "CE" else (-1.0 if S < K else 0.0)
    d1 = (math.log(S / K) + (r + sigma ** 2 / 2) * T) / (sigma * math.sqrt(T))
    return _ncdf(d1) if kind == "CE" else _ncdf(d1) - 1.0


def option_costs(buy_px, sell_px, qty, c: Costs):
    bt, st_ = buy_px * qty, sell_px * qty
    brokerage = 2 * c.brokerage_per_order
    stt = st_ * c.stt_sell_pct / 100
    exch = (bt + st_) * c.exch_pct / 100
    sebi = (bt + st_) * c.sebi_per_crore / 1e7
    stamp = bt * c.stamp_buy_pct / 100
    gst = (brokerage + exch + sebi) * c.gst_pct / 100
    slip = 2 * c.slippage_per_unit * qty
    total = brokerage + stt + exch + sebi + stamp + gst + slip
    return dict(brokerage=brokerage, stt=stt, exchange=exch, sebi=sebi, stamp=stamp, gst=gst,
                slippage=slip, total=total)


def plan_trade(direction, spot, atr_v, inst, cfg, sigma, dte_days, premium=None, equity=None):
    """Strike, premium ladder (SL/T1/T2), whole lots from the risk budget, net P&L."""
    equity = equity or cfg.capital
    step = inst.get("step") or default_step(spot)
    atm = round(spot / step) * step
    K = atm - cfg.strike_itm_steps * step if direction == "CE" else atm + cfg.strike_itm_steps * step
    T = dte_days / 365.0
    sgn = 1 if direction == "CE" else -1
    sl_dist = cfg.atr_sl_mult * atr_v
    S_sl, S_t1, S_t2 = spot - sgn * sl_dist, spot + sgn * cfg.rr_t1 * sl_dist, spot + sgn * cfg.rr_t2 * sl_dist
    model_entry = bs_price(spot, K, T, sigma, cfg.risk_free, direction)
    entry = premium if premium else model_entry

    def prem_at(S):  # reprice with the model, shift onto the entry actually used
        return max(0.05, entry + bs_price(S, K, T, sigma, cfg.risk_free, direction) - model_entry)

    sl_p, t1_p, t2_p = prem_at(S_sl), prem_at(S_t1), prem_at(S_t2)
    lot = inst["lot"]

    def net(exit_p, qty):
        return (exit_p - entry) * qty - option_costs(entry, exit_p, qty, cfg.costs)["total"]

    loss_lot = -net(sl_p, lot)
    budget = equity * cfg.risk_per_trade_pct / 100
    lots_risk = int(budget // loss_lot) if loss_lot > 0 else 0
    lots_cap = int(equity * cfg.max_capital_per_trade_pct / 100 // (entry * lot))
    lots = max(0, min(lots_risk, lots_cap))
    qty = lots * lot
    return dict(direction=direction, strike=K, step=step, spot=spot, sigma=sigma, dte=dte_days,
                model_entry=model_entry, entry=entry, entry_is_model=premium is None,
                S_sl=S_sl, S_t1=S_t1, S_t2=S_t2, sl_prem=sl_p, t1_prem=t1_p, t2_prem=t2_p,
                delta=bs_delta(spot, K, T, sigma, cfg.risk_free, direction),
                lot=lot, lots=lots, qty=qty, budget=budget, loss_per_lot=loss_lot,
                lots_by_risk=lots_risk, lots_by_capital=lots_cap,
                capital_used=qty * entry,
                net_loss_sl=net(sl_p, qty) if qty else 0.0,
                net_t1=net(t1_p, qty) if qty else 0.0, net_t2=net(t2_p, qty) if qty else 0.0,
                breakeven_exit=entry + option_costs(entry, entry, lot, cfg.costs)["total"] / lot,
                status="OK" if lots > 0 else
                f"SKIP: 1 lot risks Rs {loss_lot:,.0f} (net, incl. costs) > budget Rs {budget:,.0f}")


# ----------------------------------------------------------------------------
# 7. LIVE ANALYSIS
# ----------------------------------------------------------------------------
def _sigma_for(inst, feat_daily_close, use_vix=True, vix_val=None):
    if vix_val is not None and inst.get("vix"):
        return float(max(vix_val, 0.05)), "India VIX"
    if use_vix and inst.get("vix"):
        try:
            v = fetch(inst["vix"], "1d", "10d")["close"].iloc[-1] / 100.0
            if v > 0:
                return float(max(v, 0.05)), "India VIX"
        except Exception:
            pass
    rv = np.log(feat_daily_close).diff().rolling(20).std().iloc[-1] * math.sqrt(252)
    rv = float(rv) if pd.notna(rv) else inst["vol_floor"]
    return max(rv, inst["vol_floor"]), "realised vol (floored)"


def analyse(name, tf="15m", mode="live", cfg=None, premium=None, dte=None, lot=None, step=None,
            day_pnl=0.0, period=None, df=None, vix_val=None):
    cfg = cfg or Config()
    inst = resolve_instrument(name, lot, step, dte)
    style = "intraday" if tf in ("1m", "5m", "15m", "30m", "60m") else "swing"
    period = period or ("30d" if style == "intraday" else "3y")
    if df is None:
        df = fetch(inst["yf"], tf, period)
    now = pd.Timestamp.now(tz=IST)
    warnings = []
    if style == "intraday" and mode == "live":
        if df.index[-1] + pd.Timedelta(minutes=int(tf[:-1])) > now:
            df = df.iloc[:-1]  # never score the in-progress candle
    elif style == "swing" and mode == "live":
        if df.index[-1].normalize() == now.normalize() and now.time() < SESSION_CLOSE:
            df = df.iloc[:-1]
    feat = build_features(df, style, cfg)
    per = period_ohlc(df, style)
    last_date = feat.index[-1].tz_localize(None).normalize() if False else pd.Timestamp(feat.index[-1].date())
    completed = per[per.index < last_date] if mode == "live" else per[per.index <= last_date]
    if len(completed) < 2:
        raise RuntimeError("Not enough completed sessions/weeks to build CPR levels.")
    lv = levels_from(completed.iloc[-1], completed.iloc[-2], cfg)
    row = feat.iloc[-1]
    candle = int(np.sign(row["close"] - row["open"]))
    x = dict(price=float(row["close"]), candle=candle, **{k: row[k] for k in ("ema9", "ema21", "ema50", "vwap", "rsi", "st", "volr")}, **lv)
    sc = score_setup(x, cfg, style)
    t = feat.index[-1].time()
    window_ok = True if (style == "swing" or mode == "next") else (cfg.no_entry_before <= t <= cfg.no_entry_after)
    stop = "daily loss limit reached" if day_pnl <= -cfg.daily_loss_limit else None
    dec = decide(sc, cfg, window_ok, stop)
    plan = None
    sigma, sigma_src = _sigma_for(inst, df["close"].resample("1D").last().dropna() if style == "intraday" else df["close"],
                                  use_vix=True, vix_val=vix_val)
    if dec["action"] in ("BUY CE", "BUY PE"):
        d_days = inst["dte_i"] if style == "intraday" else inst["dte_s"]
        plan = plan_trade(dec["direction"], x["price"], float(row["atr"]), inst, cfg, sigma, d_days, premium)
    if inst.get("verify"):
        warnings.append("Lot size / strike step for this stock are placeholders: verify with NSE.")
    if f"{feat.attrs.get('vwap_is_proxy')}" == "True" and style == "intraday":
        warnings.append("VWAP is an equal-weight session average (Yahoo index volume is 0).")
    if plan and plan["entry_is_model"]:
        warnings.append("Entry premium is a Black-Scholes ESTIMATE. Pass --premium with the live LTP of the strike.")
    if lv["width_pct"] < cfg.narrow_cpr_pct:
        warnings.append(f"Narrow CPR ({lv['width_pct']:.2f}%): trend-day bias.")
    elif lv["width_pct"] > cfg.wide_cpr_pct:
        warnings.append(f"Wide CPR ({lv['width_pct']:.2f}%): range-day bias, fade breakouts carefully.")
    return dict(inst=inst, tf=tf, style=style, mode=mode, time=feat.index[-1], price=x["price"], atr=float(row["atr"]),
                levels=lv, score=sc, decision=dec, plan=plan, sigma=sigma, sigma_src=sigma_src,
                warnings=warnings, features=feat, cfg=cfg)


def print_report(r):
    inst, lv, sc, dec, plan, cfg = r["inst"], r["levels"], r["score"], r["decision"], r["plan"], r["cfg"]
    line = "=" * 74
    print(line)
    print(f" {inst['name']}  |  {r['tf']} ({r['style']})  |  mode: {r['mode']}  |  bar: {r['time']:%Y-%m-%d %H:%M}")
    print(line)
    print(f" Price {r['price']:.2f}   ATR({cfg.atr_len}) {r['atr']:.2f}")
    print(f" CPR  TC {lv['TC']:.2f} | P {lv['P']:.2f} | BC {lv['BC']:.2f}  (width {lv['width_pct']:.2f}%)")
    print(f" Prev CPR  TC {lv['pTC']:.2f} | BC {lv['pBC']:.2f}   PrevHigh {lv['PH']:.2f}  PrevLow {lv['PL']:.2f}")
    print(f" Camarilla  H4 {lv['H4']:.2f}  H3 {lv['H3']:.2f} | L3 {lv['L3']:.2f}  L4 {lv['L4']:.2f}")
    print("-" * 74)
    print(f" {'Component':<26}{'Max':>4}{'Bull':>6}{'Bear':>6}  Note")
    for n, mx, b, s, av, note in sc["comps"]:
        print(f" {n:<26}{mx if av else 0:>4}{b if av else 0:>6}{s if av else 0:>6}  {note}")
    print(f" {'TOTAL':<26}{sc['max']:>4}{sc['bull']:>6}{sc['bear']:>6}")
    print("-" * 74)
    print(f" SIGNAL: {dec['action']}   (leader {dec['leader_pct']*100:.0f}% of {sc['max']} pts, grade {dec['grade']})")
    if plan:
        p = plan
        print(f" Contract : {inst['name']} {p['strike']:.0f} {p['direction']}   (delta {p['delta']:+.2f}, IV {p['sigma']*100:.1f}% from {r['sigma_src']}, DTE {p['dte']:.0f})")
        print(f" Entry    : Rs {p['entry']:.2f} {'(model estimate)' if p['entry_is_model'] else '(your premium)'}")
        print(f" SL       : Rs {p['sl_prem']:.2f}  (underlying {p['S_sl']:.2f})")
        print(f" Target 1 : Rs {p['t1_prem']:.2f}  (underlying {p['S_t1']:.2f})   Target 2: Rs {p['t2_prem']:.2f}  (underlying {p['S_t2']:.2f})")
        print(f" Sizing   : {p['lots']} lot(s) = {p['qty']} qty | capital Rs {p['capital_used']:,.0f} | risk budget Rs {p['budget']:,.0f}")
        if p["lots"]:
            print(f" Net P&L  : SL Rs {p['net_loss_sl']:,.0f} | T1 Rs {p['net_t1']:,.0f} | T2 Rs {p['net_t2']:,.0f}  (after costs)")
            print(f" Break-even exit premium: Rs {p['breakeven_exit']:.2f}")
        else:
            print(f" {p['status']}")
    for w in r["warnings"]:
        print(f" ! {w}")
    print(line)
    print(" Educational tool, not advice. Premiums are model estimates unless you supplied one.")


# ----------------------------------------------------------------------------
# 8. BACKTEST (underlying signals, modelled option P&L, net of costs)
# ----------------------------------------------------------------------------
def backtest_symbol(name, df, style, cfg, lot=None, step=None, bar_minutes=15, 
                    entry_strategy="Market (Next Bar)", exit_strategy="Dynamic ATR (1.5R)", 
                    min_score_filter=60, fixed_target_pts=20.0, fixed_sl_pts=10.0):
    inst = resolve_instrument(name, lot, step)
    f = build_features(df, style, cfg)
    per = period_ohlc(df, style)
    table = level_table(per, cfg)
    dates = pd.DatetimeIndex(f.index.tz_localize(None).normalize())
    if style == "intraday":
        keys = dates
    else:
        pos = np.clip(per.index.searchsorted(dates), 0, len(per) - 1)
        keys = per.index[pos]
    lv = table.reindex(keys)
    lv.index = f.index
    dclose = df["close"].copy()
    dclose.index = dates
    dclose = dclose.groupby(level=0).last()
    rv = (np.log(dclose).diff().rolling(20).std() * math.sqrt(252)).shift(1).reindex(dates).to_numpy()
    sigma_arr = np.where(np.isnan(rv), inst["vol_floor"], np.maximum(rv, inst["vol_floor"]))
    O, H, L, C = (f[c].to_numpy() for c in ("open", "high", "low", "close"))
    times = np.array(f.index.time)
    cols = ["ema9", "ema21", "ema50", "vwap", "rsi", "st", "volr", "atr"]
    arr = {c: f[c].to_numpy() for c in cols}
    lvarr = {c: lv[c].to_numpy() for c in lv.columns}
    dte = inst["dte_i"] if style == "intraday" else inst["dte_s"]
    trades, skipped, day_count = [], 0, {}
    n, i = len(f), 60
    while i < n - 1:
        d = dates[i]
        if style == "intraday":
            if not (cfg.no_entry_before <= times[i] <= cfg.no_entry_after) or dates[i + 1] != d \
                    or day_count.get(d, 0) >= cfg.max_trades_per_day:
                i += 1
                continue
        if np.isnan(lvarr["TC"][i]) or np.isnan(arr["atr"][i]):
            i += 1
            continue
        x = {c: arr[c][i] for c in cols}
        x.update({k: v[i] for k, v in lvarr.items()})
        x.update(price=C[i], candle=int(np.sign(C[i] - O[i])))
        sc = score_setup(x, cfg, style)
        dec = decide(sc, cfg)
        if dec["action"] not in ("BUY CE", "BUY PE"):
            i += 1
            continue
        
        # Check user-defined conviction filter (e.g. 60%, 70%, 80%)
        if (dec["leader_pct"] * 100) < min_score_filter:
            i += 1
            continue

        direction, e = dec["direction"], i + 1
        sg = (1 if direction == "CE" else -1)
        
        # Entry Strategy execution
        sig_high, sig_low = H[i], L[i]
        if entry_strategy == "50% Pullback Retest":
            pullback_price = (sig_high + sig_low) / 2.0
            if (sg == 1 and L[e] <= pullback_price) or (sg == -1 and H[e] >= pullback_price):
                spot = pullback_price
            else:
                i += 1
                continue # Pullback limit not reached
        elif entry_strategy == "Breakout Confirmation":
            break_price = sig_high if sg == 1 else sig_low
            if (sg == 1 and H[e] > break_price) or (sg == -1 and L[e] < break_price):
                spot = break_price
            else:
                i += 1
                continue # Breakout not confirmed
        else: # Standard Market (Next Bar Open)
            spot = O[e]

        plan = plan_trade(direction, spot, arr["atr"][i], inst, cfg, sigma_arr[i], dte)
        if plan["lots"] == 0:
            skipped += 1
            i += 1
            continue

        # Exit Strategy Levels
        if exit_strategy == "Fixed 20-Point Target / 10-Point SL":
            delta_val = max(abs(plan["delta"]), 0.40)
            underlying_t_pts = fixed_target_pts / delta_val
            underlying_sl_pts = fixed_sl_pts / delta_val
            S_sl = spot - sg * underlying_sl_pts
            S_t1 = spot + sg * underlying_t_pts
            S_t2 = spot + sg * (underlying_t_pts * 1.5)
        elif exit_strategy == "Trailing SL (Trail to Cost at T1)":
            S_sl = plan["S_sl"]
            S_t1 = plan["S_t1"]
            S_t2 = plan["S_t2"]
        else: # Standard Dynamic ATR (1.5R Target / 1.0R SL)
            S_sl, S_t1, S_t2 = plan["S_sl"], plan["S_t1"], plan["S_t2"]

        exit_S, reason, j = C[-1], "DATA_END", n - 1
        trailed_to_cost = False

        for j in range(e, n):
            hi, lo, op = H[j], L[j], O[j]
            
            # Trailing SL execution
            if exit_strategy == "Trailing SL (Trail to Cost at T1)" and not trailed_to_cost:
                if (sg == 1 and hi >= S_t1) or (sg == -1 and lo <= S_t1):
                    trailed_to_cost = True
                    S_sl = spot # Lock in break-even stop loss

            if sg == 1:
                if j > e and op <= S_sl:
                    exit_S, reason = op, "SL (Gap)"; break
                if lo <= S_sl:
                    exit_S, reason = S_sl, "SL (Hit)"; break
                if trailed_to_cost and hi >= S_t2:
                    exit_S, reason = max(S_t2, op) if j > e else S_t2, "T2 (Runner)"; break
                elif not trailed_to_cost and hi >= S_t1:
                    exit_S, reason = max(S_t1, op) if j > e else S_t1, "T1 (Target)"; break
            else:
                if j > e and op >= S_sl:
                    exit_S, reason = op, "SL (Gap)"; break
                if hi >= S_sl:
                    exit_S, reason = S_sl, "SL (Hit)"; break
                if trailed_to_cost and lo <= S_t2:
                    exit_S, reason = min(S_t2, op) if j > e else S_t2, "T2 (Runner)"; break
                elif not trailed_to_cost and lo <= S_t1:
                    exit_S, reason = min(S_t1, op) if j > e else S_t1, "T1 (Target)"; break

            if style == "intraday" and (times[j] >= cfg.square_off or j + 1 >= n or dates[j + 1] != dates[j]):
                exit_S, reason = C[j], "EOD Square-Off"; break
            if style == "swing" and (j - e + 1) >= cfg.max_hold_days:
                exit_S, reason = C[j], "Max Hold Time"; break
        else:
            j = n - 1

        held = ((j - e + 1) * bar_minutes / SESSION_MINUTES) if style == "intraday" else \
            float((dates[j] - dates[e]).days + 1)
        K, T0 = plan["strike"], dte / 365.0
        exit_p = max(0.05, bs_price(exit_S, K, max(T0 - held / 365.0, 1e-6), sigma_arr[i], cfg.risk_free, direction))
        qty, entry_p = plan["qty"], plan["entry"]
        gross = (exit_p - entry_p) * qty
        cost = option_costs(entry_p, exit_p, qty, cfg.costs)["total"]
        risk = plan["lots"] * plan["loss_per_lot"]
        net_val = round(gross - cost, 2)
        r_net_val = (gross - cost) / risk if risk else np.nan
        und_r_val = sg * (exit_S - spot) / (cfg.atr_sl_mult * arr["atr"][i])

        trades.append(dict(
            symbol=inst["name"], style=style, direction=direction,
            signal_time=f.index[i].strftime("%Y-%m-%d %H:%M"),
            entry_time=f.index[e].strftime("%Y-%m-%d %H:%M"),
            exit_time=f.index[j].strftime("%Y-%m-%d %H:%M"),
            strike=f"{int(K)} {direction}",
            entry_spot=round(spot, 2), exit_spot=round(exit_S, 2),
            entry_prem=round(entry_p, 2), exit_prem=round(exit_p, 2),
            pts_captured=round(exit_p - entry_p, 2),
            reason=reason, conviction=f"{round(dec['leader_pct']*100)}%",
            lots=plan["lots"], qty=qty,
            gross=round(gross, 2), costs=round(cost, 2), net=net_val,
            gross_pnl=round(gross, 2), zerodha_tax_costs=round(cost, 2), net_pnl=net_val,
            risk=round(risk, 2), r_net=r_net_val, und_R=und_r_val,
            capital_used=round(plan["capital_used"], 2),
            return_on_capital=f"{round(((gross - cost) / max(plan['capital_used'], 1)) * 100, 2)}%"
        ))
        day_count[dates[e]] = day_count.get(dates[e], 0) + 1
        i = max(j, i + 1)
        
    out = pd.DataFrame(trades)
    out.attrs["skipped"] = skipped
    return out

def perf_stats(t, capital):
    if t is None or len(t) == 0:
        return dict(n=0)
    t = t.sort_values("exit_time")
    net = t["net"]
    wins, losses = net[net > 0], net[net < 0]
    eq = capital + net.cumsum()
    dd = (eq - eq.cummax()).min()
    return dict(n=len(t), win_rate=len(wins) / len(t), avg_win=wins.mean() if len(wins) else 0.0,
                avg_loss=losses.mean() if len(losses) else 0.0,
                profit_factor=(wins.sum() / abs(losses.sum())) if len(losses) else float("inf"),
                expectancy=net.mean(), expectancy_R=t["r_net"].mean(), net=net.sum(), gross=t["gross"].sum(),
                costs=t["costs"].sum(), max_dd=dd, und_R_avg=t["und_R"].mean())


def verdict(all_s, oos_s):
    if all_s.get("n", 0) < 30:
        return "INSUFFICIENT EVIDENCE (fewer than 30 trades). Do not trust or reject the signals yet."
    if all_s["net"] <= 0 or all_s["profit_factor"] < 1.0:
        return "NOT PROFITABLE after costs in this sample (modelled premiums)."
    if all_s["profit_factor"] < 1.2 or oos_s.get("n", 0) < 10 or oos_s.get("net", 0) <= 0:
        return "UNCONFIRMED: positive in-sample but weak or negative out-of-sample. Do not trade real money."
    return "PASSES BASIC CHECK on modelled premiums. Still not proof: forward-test with real premiums first."


def print_stats(title, s):
    if s.get("n", 0) == 0:
        print(f" {title}: no trades")
        return
    print(f" {title}: trades {s['n']} | win {s['win_rate']*100:.0f}% | PF {s['profit_factor']:.2f} | "
          f"expectancy Rs {s['expectancy']:,.0f} ({s['expectancy_R']:.2f}R) | net Rs {s['net']:,.0f} | "
          f"costs Rs {s['costs']:,.0f} | maxDD Rs {s['max_dd']:,.0f}")


def run_backtest(symbols, tf, period, cfg, oos=0.3, csv=None, start=None, end=None,
                 entry_strategy="Market (Next Bar)", exit_strategy="Dynamic ATR (1.5R)",
                 min_score_filter=60, fixed_target_pts=20.0, fixed_sl_pts=10.0):
    style = "intraday" if tf in ("5m", "15m", "30m", "60m") else "swing"
    mins = int(tf[:-1]) if style == "intraday" else 375
    frames, skipped = [], 0
    for s in symbols:
        inst = resolve_instrument(s)
        try:
            df = fetch(inst["yf"], tf, period=period, start=start, end=end)
        except Exception as e:
            continue
        t = backtest_symbol(s, df, style, cfg, bar_minutes=mins,
                            entry_strategy=entry_strategy, exit_strategy=exit_strategy,
                            min_score_filter=min_score_filter, fixed_target_pts=fixed_target_pts, fixed_sl_pts=fixed_sl_pts)
        skipped += t.attrs.get("skipped", 0)
        if len(t):
            frames.append(t)
    trades = pd.concat(frames, ignore_index=True).sort_values("exit_time") if frames else pd.DataFrame()
    all_s = perf_stats(trades, cfg.capital) if len(trades) else dict(n=0)
    k = int(len(trades) * (1 - oos)) if len(trades) else 0
    is_s = perf_stats(trades.iloc[:k], cfg.capital) if k else {"n": 0}
    oos_s = perf_stats(trades.iloc[k:], cfg.capital) if len(trades) - k else {"n": 0}
    v = verdict(all_s, oos_s)
    if csv and len(trades):
        trades.to_csv(csv, index=False)
    return dict(trades=trades, all=all_s, is_=is_s, oos=oos_s, verdict=v, skipped=skipped)

# 9. SELF TEST (no internet needed)
# ----------------------------------------------------------------------------
def _synthetic(days=60, bars=25, seed=7, volume=False):
    rng = np.random.default_rng(seed)
    rows, idx, price = [], [], 25000.0
    day = pd.Timestamp("2026-01-01", tz=IST)
    d = 0
    while d < days:
        day += pd.Timedelta(days=1)
        if day.weekday() >= 5:
            continue
        d += 1
        drift = rng.normal(0, 0.0004)
        for b in range(bars):
            o = price
            c = o * (1 + drift + rng.normal(0, 0.0012))
            h, l = max(o, c) * (1 + abs(rng.normal(0, 0.0004))), min(o, c) * (1 - abs(rng.normal(0, 0.0004)))
            rows.append((o, h, l, c, rng.integers(1e5, 5e5) if volume else 0))
            idx.append(day + pd.Timedelta(hours=9, minutes=15 + 15 * b))
            price = c
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx), columns=["open", "high", "low", "close", "volume"])


def selftest():
    cfg = Config()
    ok = lambda c, m: (print(("PASS " if c else "FAIL ") + m), c)[1]
    allok = True
    c = cpr(25150, 24980, 25010)
    allok &= ok(abs(c["P"] - 25046.67) < .01 and abs(c["TC"] - 25065) < .01 and abs(c["BC"] - 25028.33) < .01, "CPR matches hand calc (TC normalised to top)")
    cam = camarilla(25150, 24980, 25010)
    allok &= ok(abs(cam["H3"] - 25056.75) < .01 and abs(cam["L3"] - 24963.25) < .01 and abs(cam["H4"] - 25103.5) < .01, "Camarilla H3/L3/H4 match")
    S, K, T, s, r = 25000, 25000, 0.02, 0.14, 0.065
    parity = bs_price(S, K, T, s, r, "CE") - bs_price(S, K, T, s, r, "PE") - (S - K * math.exp(-r * T))
    allok &= ok(abs(parity) < 1e-6, "Black-Scholes put-call parity")
    zc = Costs(slippage_per_unit=0.0)
    allok &= ok(abs(option_costs(100, 100, 65, zc)["total"] - 62.61) < 0.02, "Costs: 65 qty @100 round trip = Rs 62.61 (matches published example)")
    x = dict(price=25010, candle=-1, TC=25065.0, BC=25028.33, pTC=25210.0, pBC=25190.0, H3=25056.75, H4=25103.5,
             L3=24963.25, L4=24916.5, ema9=25005, ema21=25030, ema50=np.nan, vwap=25040, rsi=41, st=-1, volr=np.nan)
    sc = score_setup(x, cfg, "intraday")
    dec = decide(sc, cfg)
    allok &= ok((sc["bull"], sc["bear"], sc["max"]) == (0, 7, 9) and dec["action"] == "BUY PE", "Scoring example = bear 7 / 9 -> BUY PE (same as Excel Scorecard)")
    plan = plan_trade("PE", 25010, 15.0, resolve_instrument("NIFTY"), cfg, 0.14, 3)
    allok &= ok(plan["lots"] >= 1 and plan["sl_prem"] < plan["entry"] < plan["t1_prem"], f"Trade plan sane (lots {plan['lots']}, entry {plan['entry']:.1f})")
    df = _synthetic()
    big = Config(risk_per_trade_pct=10.0)  # synthetic ATR is large; raise risk so lots >= 1
    t = backtest_symbol("NIFTY", df, "intraday", big, bar_minutes=15)
    allok &= ok(len(t) > 0 and np.isfinite(t["net"]).all(), f"Backtest pipeline runs on SYNTHETIC data ({len(t)} trades; results meaningless)")
    print("ALL TESTS PASSED" if allok else "SOME TESTS FAILED")
    return allok


# ----------------------------------------------------------------------------
# 10. CLI
# ----------------------------------------------------------------------------
def _cfg_from(a):
    cfg = Config()
    for k, v in (("capital", a.capital), ("risk_per_trade_pct", a.risk_pct), ("daily_loss_limit_pct", a.daily_pct),
                 ("strike_itm_steps", a.itm)):
        if v is not None:
            setattr(cfg, k, v)
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mathematical Trading Toolkit V2")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--tf", default="15m", choices=["5m", "15m", "30m", "1d"])
        p.add_argument("--capital", type=float)
        p.add_argument("--risk-pct", type=float, dest="risk_pct")
        p.add_argument("--daily-pct", type=float, dest="daily_pct")
        p.add_argument("--itm", type=int, help="strikes in-the-money (0 = ATM)")
        p.add_argument("--period")

    p = sub.add_parser("signal"); p.add_argument("symbol"); common(p)
    p.add_argument("--mode", default="live", choices=["live", "next"])
    p.add_argument("--premium", type=float, help="live premium (LTP) of the strike")
    p.add_argument("--dte", type=float); p.add_argument("--lot", type=int); p.add_argument("--step", type=float)
    p.add_argument("--day-pnl", type=float, default=0.0, dest="day_pnl")
    p = sub.add_parser("scan"); common(p)
    p.add_argument("--symbols", nargs="*", default=DEFAULT_WATCHLIST)
    p.add_argument("--mode", default="live", choices=["live", "next"])
    p = sub.add_parser("backtest"); p.add_argument("symbols", nargs="+"); common(p)
    p.add_argument("--oos", type=float, default=0.3); p.add_argument("--csv")
    p = sub.add_parser("costs")
    p.add_argument("--buy", type=float, required=True); p.add_argument("--sell", type=float, required=True)
    p.add_argument("--qty", type=int, required=True)
    sub.add_parser("selftest")
    a = ap.parse_args(argv)

    if a.cmd == "selftest":
        return 0 if selftest() else 1
    if a.cmd == "costs":
        c = option_costs(a.buy, a.sell, a.qty, Costs())
        gross = (a.sell - a.buy) * a.qty
        for k, v in c.items():
            print(f" {k:<10} Rs {v:>10,.2f}")
        print(f" gross P&L  Rs {gross:>10,.2f}\n net P&L    Rs {gross - c['total']:>10,.2f}")
        return 0
    cfg = _cfg_from(a)
    if a.cmd == "signal":
        print_report(analyse(a.symbol, a.tf, a.mode, cfg, a.premium, a.dte, a.lot, a.step, a.day_pnl, a.period))
    elif a.cmd == "scan":
        rows = []
        for s in a.symbols:
            try:
                r = analyse(s, a.tf, a.mode, cfg, period=a.period)
                rows.append((s, r["decision"]["action"], r["score"]["bull"], r["score"]["bear"], r["score"]["max"],
                             r["decision"]["leader_pct"], r["price"], r["plan"]["lots"] if r["plan"] else "-"))
            except Exception as e:
                print(f" ! {s}: {e}")
        rows.sort(key=lambda z: -z[5])
        print(f" {'Symbol':<11}{'Signal':<30}{'Bull':>5}{'Bear':>5}{'Max':>4}{'Lead%':>7}{'Price':>11}{'Lots':>6}")
        for s, act, b, be, mx, pc, pr, lots in rows:
            print(f" {s:<11}{act:<30}{b:>5}{be:>5}{mx:>4}{pc*100:>6.0f}%{pr:>11.2f}{lots:>6}")
    elif a.cmd == "backtest":
        run_backtest(a.symbols, a.tf, a.period, cfg, a.oos, a.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
