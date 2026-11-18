"""指标的第二条计算路径（SQL）。只有审计使用，账本和任何角色都不调用它。

两条路径不共享任何计算代码：这里不 import indicators，那里也不 import 这里。
每个查询都以同一个 CTE 开头：只取分析日及以前的 K 线，并按日期编号。
函数签名统一为 fn(db, ticker, as_of, inputs, **args)，与 indicators.toml 中的声明一一对应。
SQL 中不使用 sqrt 等数学扩展函数（不是每个 SQLite 构建都带），开方在 Python 里做。
"""
from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterable, Sequence

COLUMNS = ("open", "high", "low", "close", "volume")

# {x} 是输入列名，只允许 COLUMNS 中的值（见 _q），所以这里的字符串拼接是安全的。
_B = """
WITH RECURSIVE b AS (
  SELECT row_number() OVER (ORDER BY date) AS n, high, low, close, {x} AS x
  FROM bars WHERE ticker = :t AND date <= :d
)"""

_LAST = _B + "\nSELECT x FROM b WHERE n = (SELECT max(n) FROM b) - :back"

_SMA = _B + "\nSELECT CASE WHEN count(*) = :n THEN avg(x) END FROM (SELECT x FROM b ORDER BY n DESC LIMIT :n)"

# EMA：种子是前 n 个值的平均，之后逐行递推。
_EMA = _B + """,
e(n, v) AS (
  SELECT :n, (SELECT avg(x) FROM b WHERE n <= :n) WHERE (SELECT count(*) FROM b) >= :n
  UNION ALL
  SELECT b.n, b.x * :k + e.v * (1 - :k) FROM e JOIN b ON b.n = e.n + 1
)
SELECT v FROM e ORDER BY n DESC LIMIT 1"""

# RSI：ch 是逐日变动拆成的涨幅 g 与跌幅 l（从第 2 根开始，编号 i 从 1 起），w 是 Wilder 平滑。
_RSI = _B + """,
c AS (
  SELECT n - 1 AS i,
         max(x - lag(x) OVER (ORDER BY n), 0) AS g,
         max(lag(x) OVER (ORDER BY n) - x, 0) AS l
  FROM b
),
ch AS (SELECT * FROM c WHERE i >= 1),
w(i, g, l) AS (
  SELECT :n, (SELECT avg(g) FROM ch WHERE i <= :n), (SELECT avg(l) FROM ch WHERE i <= :n)
  WHERE (SELECT count(*) FROM ch) >= :n
  UNION ALL
  SELECT ch.i, (w.g * (:n - 1) + ch.g) / :n, (w.l * (:n - 1) + ch.l) / :n
  FROM w JOIN ch ON ch.i = w.i + 1
)
SELECT CASE WHEN l = 0 THEN 100.0 ELSE 100 - 100 / (1 + g / l) END FROM w ORDER BY i DESC LIMIT 1"""

# ATR：真实波幅 tr 从第 2 根开始，之后 Wilder 平滑。固定使用 high / low / close 三列。
_ATR = _B + """,
t AS (
  SELECT n - 1 AS i,
         max(high - low,
             abs(high - lag(close) OVER (ORDER BY n)),
             abs(low - lag(close) OVER (ORDER BY n))) AS tr
  FROM b
),
tr AS (SELECT * FROM t WHERE i >= 1),
w(i, v) AS (
  SELECT :n, (SELECT avg(tr) FROM tr WHERE i <= :n) WHERE (SELECT count(*) FROM tr) >= :n
  UNION ALL
  SELECT tr.i, (w.v * (:n - 1) + tr.tr) / :n FROM w JOIN tr ON tr.i = w.i + 1
)
SELECT v FROM w ORDER BY i DESC LIMIT 1"""

# MACD：两条 EMA 相减得到 m（重新从 1 编号），再对 m 做一次 EMA 得到信号线。
_MACD = _B + """,
f(n, v) AS (
  SELECT :fast, (SELECT avg(x) FROM b WHERE n <= :fast) WHERE (SELECT count(*) FROM b) >= :fast
  UNION ALL
  SELECT b.n, b.x * :kf + f.v * (1 - :kf) FROM f JOIN b ON b.n = f.n + 1
),
s(n, v) AS (
  SELECT :slow, (SELECT avg(x) FROM b WHERE n <= :slow) WHERE (SELECT count(*) FROM b) >= :slow
  UNION ALL
  SELECT b.n, b.x * :ks + s.v * (1 - :ks) FROM s JOIN b ON b.n = s.n + 1
),
m AS (SELECT s.n - :slow + 1 AS i, f.v - s.v AS v FROM s JOIN f ON f.n = s.n),
g(i, v) AS (
  SELECT :sig, (SELECT avg(v) FROM m WHERE i <= :sig) WHERE (SELECT count(*) FROM m) >= :sig
  UNION ALL
  SELECT m.i, m.v * :kg + g.v * (1 - :kg) FROM g JOIN m ON m.i = g.i + 1
)
SELECT m.v, g.v, m.v - g.v FROM g JOIN m ON m.i = g.i ORDER BY g.i DESC LIMIT 1"""

# 布林带：先求均值，再求离差平方的均值（总体方差）。开方留给 Python。
_BOLL = _B + """,
w AS (SELECT x FROM b ORDER BY n DESC LIMIT :n),
a AS (SELECT avg(x) AS mid, count(*) AS c FROM w)
SELECT CASE WHEN a.c = :n THEN a.mid END, (SELECT avg((x - a.mid) * (x - a.mid)) FROM w) FROM a"""

_HIGH = _B + "\nSELECT CASE WHEN count(*) = :n THEN max(x) END FROM (SELECT x FROM b ORDER BY n DESC LIMIT :n)"
_LOW = _B + "\nSELECT CASE WHEN count(*) = :n THEN min(x) END FROM (SELECT x FROM b ORDER BY n DESC LIMIT :n)"

_RET = _B + """
SELECT (last.x / past.x - 1) * 100
FROM b AS last JOIN b AS past ON past.n = last.n - :n
WHERE last.n = (SELECT max(n) FROM b)"""


def load(bars: Iterable[tuple]) -> sqlite3.Connection:
    """bars: (ticker, date, open, high, low, close, volume)，date 为 ISO 字符串。返回内存数据库。"""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE bars (ticker TEXT, date TEXT, open REAL, high REAL, low REAL, "
               "close REAL, volume REAL, PRIMARY KEY (ticker, date))")
    db.executemany("INSERT INTO bars VALUES (?,?,?,?,?,?,?)", bars)
    return db


def _q(db: sqlite3.Connection, sql: str, t: str, d: str, inputs: Sequence[str], **params) -> tuple | None:
    col = inputs[0]
    if col not in COLUMNS:
        raise ValueError(f"未知的输入列：{col!r}")
    row = db.execute(sql.format(x=col), {"t": t, "d": d, **params}).fetchone()
    return None if row is None or row[0] is None else row


def last(db, t, d, inputs, back=0):
    r = _q(db, _LAST, t, d, inputs, back=back)
    return r and r[0]


def sma(db, t, d, inputs, n):
    r = _q(db, _SMA, t, d, inputs, n=n)
    return r and r[0]


def ema(db, t, d, inputs, n):
    r = _q(db, _EMA, t, d, inputs, n=n, k=2 / (n + 1))
    return r and r[0]


def rsi(db, t, d, inputs, n=14):
    r = _q(db, _RSI, t, d, inputs, n=n)
    return r and r[0]


def atr(db, t, d, inputs, n=14):
    r = _q(db, _ATR, t, d, ("close",), n=n)
    return r and r[0]


def macd(db, t, d, inputs, fast=12, slow=26, signal=9):
    return _q(db, _MACD, t, d, inputs, fast=fast, slow=slow, sig=signal,
              kf=2 / (fast + 1), ks=2 / (slow + 1), kg=2 / (signal + 1))


def bollinger(db, t, d, inputs, n=20, width=2.0):
    r = _q(db, _BOLL, t, d, inputs, n=n)
    if r is None:
        return None
    mid, sd = r[0], math.sqrt(r[1])
    return mid + width * sd, mid, mid - width * sd


def highest(db, t, d, inputs, n):
    r = _q(db, _HIGH, t, d, inputs, n=n)
    return r and r[0]


def lowest(db, t, d, inputs, n):
    r = _q(db, _LOW, t, d, inputs, n=n)
    return r and r[0]


def ret_pct(db, t, d, inputs, n):
    r = _q(db, _RET, t, d, inputs, n=n)
    return r and r[0]
