import sqlite3, json, os
DB=os.getenv('MTT_DB','trades.db')
def connect():
 c=sqlite3.connect(DB,timeout=10,check_same_thread=False);c.execute('PRAGMA journal_mode=WAL');return c
def init():
 with connect() as c:
  c.execute('''CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY,trade_date TEXT,entry_time TEXT,exit_time TEXT,strategy_id INTEGER,symbol TEXT,side TEXT,strike REAL,expiry TEXT,lot_size INTEGER,qty INTEGER,entry_premium REAL,exit_premium REAL,premium_source TEXT,exit_reason TEXT,entry_snapshot TEXT,exit_snapshot TEXT,gross REAL,costs REAL,net REAL,status TEXT)''')
  c.execute('CREATE TABLE IF NOT EXISTS daily_summary(day TEXT,strategy_id INTEGER,net REAL,PRIMARY KEY(day,strategy_id))')
  c.execute('CREATE TABLE IF NOT EXISTS equity_curve(ts TEXT,strategy_id INTEGER,equity REAL)')
  c.execute('CREATE TABLE IF NOT EXISTS desk_runs(day TEXT PRIMARY KEY,payload TEXT)')
def rows(query,params=()):
 with connect() as c:
  c.row_factory=sqlite3.Row;return [dict(r) for r in c.execute(query,params)]
def save_desk(day,payload):
 with connect() as c:c.execute('INSERT OR REPLACE INTO desk_runs VALUES (?,?)',(day,json.dumps(payload,default=str)))
