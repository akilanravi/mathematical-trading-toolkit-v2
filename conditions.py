"""Shared, lookahead-free nine-condition evaluator. Only completed candles should be passed."""
from __future__ import annotations
import numpy as np
import pandas as pd

NAMES = ('EMA9 > EMA21','Supertrend (10,2)','RSI > 60','Above R1','Above upper BB','Narrow prior CPR','Above VWAP','Volume >= 1.25x','Above EMA200')

def atr(df: pd.DataFrame, period: int=14) -> pd.Series:
    h,l,c=df.High,df.Low,df.Close
    tr=pd.concat([(h-l),(h-c.shift()).abs(),(l-c.shift()).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1/period,adjust=False,min_periods=period).mean()

def supertrend(df: pd.DataFrame, period: int=10, multiplier: float=2.) -> pd.Series:
    mid=(df.High+df.Low)/2; a=atr(df,period)
    upper=mid+multiplier*a; lower=mid-multiplier*a
    trend=pd.Series(np.nan,index=df.index)
    if len(df)<period+2:return trend
    fu=upper.copy();fl=lower.copy(); start=period
    trend.iloc[start]=1 if df.Close.iloc[start]>=mid.iloc[start] else -1
    for i in range(start+1,len(df)):
        fu.iloc[i]=upper.iloc[i] if upper.iloc[i]<fu.iloc[i-1] or df.Close.iloc[i-1]>fu.iloc[i-1] else fu.iloc[i-1]
        fl.iloc[i]=lower.iloc[i] if lower.iloc[i]>fl.iloc[i-1] or df.Close.iloc[i-1]<fl.iloc[i-1] else fl.iloc[i-1]
        trend.iloc[i]= -1 if trend.iloc[i-1]==1 and df.Close.iloc[i]<fl.iloc[i] else (1 if trend.iloc[i-1]==-1 and df.Close.iloc[i]>fu.iloc[i] else trend.iloc[i-1])
    return trend

def evaluate_conditions(symbol: str, candles: pd.DataFrame, daily: pd.DataFrame, threshold: float=.15) -> dict:
    """CPR threshold is percentage of previous-day close; no missing input counts as a pass."""
    out={'symbol':symbol,'bull':{},'bear':{},'values':{},'score':0,'bear_score':0,'reason':''}
    if candles is None or daily is None or len(candles)<205 or len(daily)<2:
        out['reason']='Insufficient completed candles (205 intraday, 2 daily)';return out
    x=candles.copy(); d=daily.iloc[-1] # caller passes completed daily bars only
    close=x.Close; price=float(close.iloc[-1]);ema9=close.ewm(span=9,adjust=False).mean().iloc[-1]
    ema21=close.ewm(span=21,adjust=False).mean().iloc[-1];ema200=close.ewm(span=200,adjust=False).mean().iloc[-1]
    delta=close.diff(); gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    rsi=float(100-100/(1+gain.iloc[-1]/max(loss.iloc[-1],1e-12)))
    p=(float(d.High)+float(d.Low)+float(d.Close))/3
    r1=2*p-float(d.Low);s1=2*p-float(d.High)
    tc=(float(d.High)+float(d.Low))/2;bc=2*p-tc
    width=abs(tc-bc)/max(float(d.Close),1e-9)*100
    ma=close.rolling(20).mean().iloc[-1];std=close.rolling(20).std(ddof=0).iloc[-1]
    upper=ma+2*std;lower=ma-2*std
    volavg=x.Volume.shift(1).rolling(20).mean().iloc[-1]
    volmult=float(x.Volume.iloc[-1]/volavg) if volavg>0 else np.nan
    # session VWAP resets at date boundary; for daily candles use volume-weighted proxy
    dates=pd.Series(pd.to_datetime(x.index).date,index=x.index)
    typical=(x.High+x.Low+x.Close)/3
    vwap=((typical*x.Volume).groupby(dates).cumsum()/x.Volume.groupby(dates).cumsum().replace(0,np.nan)).iloc[-1]
    st=supertrend(x).iloc[-1]
    bull=[ema9>ema21,st==1,rsi>60,price>r1,price>upper,width<=threshold,price>vwap,volmult>=1.25,price>ema200]
    bear=[ema9<ema21,st==-1,rsi<40,price<s1,price<lower,width<=threshold,price<vwap,volmult>=1.25,price<ema200]
    vals=[f'{ema9:.2f} / {ema21:.2f}',f'{st:+.0f}',f'{rsi:.1f}',f'R1 {r1:.2f} / S1 {s1:.2f}',f'BB {lower:.2f}–{upper:.2f}',f'{width:.3f}% (max {threshold:.3f}%)',f'{vwap:.2f}',f'{volmult:.2f}x',f'{ema200:.2f}']
    for i,(b,s,v) in enumerate(zip(bull,bear,vals),1):
        out[f'C{i}']=bool(b);out['bull'][f'C{i}']=bool(b);out['bear'][f'C{i}']=bool(s);out['values'][f'C{i}']=v
    out.update(score=sum(bull),bear_score=sum(bear),price=price,volume_multiple=volmult,r1=r1,s1=s1,vwap=float(vwap),atr=float(atr(x).iloc[-1]),cpr_width=width)
    return out
