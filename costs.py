"""Illustrative Zerodha equity option charges, configurable for tariff changes."""
def round_trip(buy:float,sell:float,qty:int,slippage_pct:float=0.1)->float:
 if min(buy,sell,qty)<0:raise ValueError('negative input')
 b=buy*qty;s=sell*qty;turnover=b+s
 brokerage=40.0 # ₹20 per executed order, two orders
 stt=s*0.001 # illustrative sell-side option STT; verify effective tariff
 exchange=turnover*0.0003503
 sebi=turnover*0.000001
 stamp=b*0.00003
 gst=.18*(brokerage+exchange+sebi)
 slip=turnover*slippage_pct/100
 return brokerage+stt+exchange+sebi+stamp+gst+slip
