# MagicSTG 数据源与独立量化回测开发手册 (AI & Developer Data Handbook)

> **致其他 AI 助手与量化开发者 (For AI Coding Assistants & Quantitative Developers)**:
> 
> 本文档是 **MagicSTG A股/可转债量化系统底层数据源 (TiDB Cloud)** 的**官方技术与接入规范手册**。
> 当您需要编写独立 Python 回测脚本、验证策略模型或进行跨引擎对比测试时，**请严格遵循本文档所定义的数据库结构、未来函数规避规范、确定性排序准则与交易撮合铁律**。

---

## 1. 数据库环境与连接规范 (Database Connection & SSL)

### 1.1 数据库基本信息
* **数据库引擎**: **TiDB Cloud**（分布式 Serverless MySQL 8.0 兼容协议）
* **数据库名称**: `asharedb`
* **默认端口**: `4000`
* **字符集**: `utf8mb4`
* **SSL 安全连接**: **必须启用 SSL**（使用 `MagicSTG/dbconfig/isrgrootx1.pem` 证书或系统内置 CA 证书）

### 1.2 环境变量配置与解析
在项目根目录下的 `.env` 文件或系统环境变量中读取以下配置：
```env
DB_HOST=gateway01.ap-southeast-1.prod.aws.tidbcloud.com
DB_PORT=4000
DB_USER=your_username.root
DB_PASSWORD=your_password
DB_NAME=asharedb
DB_SSL_CA=MagicSTG/dbconfig/isrgrootx1.pem
```

### 1.3 具备自动重试与心跳保活的连接代码 (Python 示范)
由于 TiDB Cloud 部署在云端，网络存在瞬时抖动风险，**禁止使用裸连接，必须封装指数退避重试与心跳保活**：

```python
import os
import time
import pymysql
import pandas as pd
from typing import Tuple, Any

def get_db_connection() -> pymysql.Connection:
    """获取与 TiDB Cloud 的安全 SSL 连接"""
    curr_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = curr_dir
    while project_root and not os.path.exists(os.path.join(project_root, 'MagicSTG')):
        parent = os.path.dirname(project_root)
        if parent == project_root:
            break
        project_root = parent

    ca_path = os.path.join(project_root, "MagicSTG", "dbconfig", "isrgrootx1.pem")
    
    conn_params = {
        "host": os.getenv("DB_HOST", "gateway01.ap-southeast-1.prod.aws.tidbcloud.com"),
        "port": int(os.getenv("DB_PORT", 4000)),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "database": os.getenv("DB_NAME", "asharedb"),
        "charset": "utf8mb4",
        "autocommit": True,
        "connect_timeout": 15,
        "read_timeout": 90,
    }
    if os.path.exists(ca_path):
        conn_params["ssl"] = {"ca": ca_path}
        
    return pymysql.connect(**conn_params)

def safe_read_sql_with_retry(query: str, conn: pymysql.Connection, params=None, max_retries: int = 5) -> Tuple[pd.DataFrame, pymysql.Connection]:
    """
    具备 5 次指数退避重试的安全 SQL 读取器，彻底防止因网络瞬断发生静默漏查/丢数据。
    """
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            if conn is None:
                conn = get_db_connection()
            else:
                conn.ping(reconnect=True)
            df = pd.read_sql(query, conn, params=params)
            return df, conn
        except Exception as e:
            last_err = e
            wait_sec = min(1.0 * (2 ** (attempt - 1)), 10.0)
            time.sleep(wait_sec)
            try:
                conn = get_db_connection()
            except Exception:
                conn = None
    raise RuntimeError(f"数据库查询失败 (已重试 {max_retries} 次): {last_err}")
```

---

## 2. 核心数据表结构与字段字典 (Data Schema & Tables)

### 2.1 股票日 K 线行情表 (`stock_kline_day`)
* **主键**: `(date, code)`
* **股票代码标准格式**: `sh.600000` (上交所), `sz.000001` (深交所), `bj.830000` (北交所)。
* **核心字段**:
  | 字段名 | 类型 | 说明 | 回测用途 |
  | :--- | :--- | :--- | :--- |
  | `date` | `DATE` | 交易日期 (YYYY-MM-DD) | 时间索引 |
  | `code` | `VARCHAR(20)` | 股票代码 (如 `sh.600519`) | 标的唯一识别码 |
  | `open` | `DECIMAL(12,4)` | 当日开盘价 | T+1 早盘撮合买入/卖出基准价 |
  | `high` | `DECIMAL(12,4)` | 当日最高价 | 移动止盈最高点追踪 |
  | `low` | `DECIMAL(12,4)` | 当日最低价 | 盘中硬止损触发价 |
  | `close` | `DECIMAL(12,4)` | 当日收盘价 | 资产估值与技术指标计算 (MA/MACD) |
  | `volume`| `BIGINT` | 成交量 (股) | 量比/均量计算 |
  | `amount`| `DECIMAL(20,4)` | 成交额 (元) | 流动性过滤 |
  | `turn` | `DECIMAL(12,6)` | 换手率 (%) | 活跃度过滤 |
  | `pctChg`| `DECIMAL(12,6)` | 当日涨跌幅 (%) | 收益率与涨跌停辅助判断 |
  | `peTTM` | `DECIMAL(20,6)` | 滚动市盈率 (PE-TTM) | 估值因子排序 |
  | `pbMRQ` | `DECIMAL(20,6)` | 市净率 (PB-MRQ) | 估值因子排序 |
  | `psTTM` | `DECIMAL(20,6)` | 滚动市销率 (PS-TTM) | 估值因子排序 |
  | `tradestatus` | `VARCHAR(5)` | 交易状态 ('1': 正常, '0': 停牌) | 剔除停牌股 |
  | `isST` | `VARCHAR(5)` | 是否 ST 股 ('1': 是, '0': 否) | 剔除风险股 |

---

### 2.2 股票季度财务报表（杜绝未来函数）
A 股上市公司财报包含两个至关重要的日期字段：
1. `stat_date` (报表期末日): 如 `2025-12-31` (2025年报), `2026-03-31` (2026一季报)。
2. `pub_date` (实际法定披露公告日): 如 `2026-04-28`。

> ⚠️ **未来函数核心铁律**：
> 在回测历史上的某一天（如 `2026-03-15`）进行选股打分时，**绝对不能使用 `stat_date == '2025-12-31'` 的年报数据**（除非该年报在 `2026-03-15` 或之前已经发布披露！）。
> **必须严格使用且仅使用 `pub_date <= current_date` 的已披露最新财报！**

#### 四大财务表结构：
1. **`stock_profit_quarterly`** (盈利能力): `roe_avg` (平均 ROE %), `net_profit` (净利润), `eps_ttm` (每股收益).
2. **`stock_growth_quarterly`** (成长能力): `YOYNI` (净利润同比增长率 %), `YOYAsset` (总资产增长率 %).
3. **`stock_balance_quarterly`** (偿债能力): `liabilityToAsset` (资产负债率 0.0~1.0).
4. **`stock_cash_flow_quarterly`** (现金流质量): `CFOToNP` (经营现金流 / 净利润比率).

#### 财务四表高效多表对齐 SQL:
```sql
SELECT 
    p.code, p.stat_date, p.pub_date, p.roe_avg,
    g.YOYNI,
    b.liabilityToAsset,
    c.CFOToNP
FROM stock_profit_quarterly p
LEFT JOIN stock_growth_quarterly g ON p.code = g.code AND p.stat_date = g.stat_date
LEFT JOIN stock_balance_quarterly b ON p.code = b.code AND p.stat_date = b.stat_date
LEFT JOIN stock_cash_flow_quarterly c ON p.code = c.code AND p.stat_date = c.stat_date
WHERE p.code IN %s
ORDER BY p.code ASC, p.pub_date ASC, p.stat_date ASC;
```

---

### 2.3 可转债主表与衍生指标表
* **`cb_basic`** (可转债基础信息): `code` (`sh.113008`), `name`, `stock_code` (`sh.600000`), `convert_price`, `issue_size`, `rating`.
* **`cb_kline_day`** (可转债日 K 线): `code`, `date`, `open`, `high`, `low`, `close`, `volume`, `amount`, `pctChg`.
* **`cb_daily_indicator`** (可转债专有指标):
  * `cb_price`: 转债收盘现价
  * `stock_price`: 正股收盘价
  * `convert_price`: 最新转股价
  * `convert_value`: 转股价值 $= (\text{stock\_price} / \text{convert\_price}) \times 100$
  * `convert_premium_rate`: 转股溢价率 $(\%)$ $= ((\text{cb\_price} - \text{convert\_value}) / \text{convert\_value}) \times 100$
  * `db_low_value`: 双低值 $= \text{cb\_price} + \text{convert\_premium\_rate}$

---

### 2.4 大盘指数表 (`index_kline_day`)
* **字段**: `code` (`sh.000300` 沪深300, `sh.000001` 上证指数), `date`, `open`, `close`, `high`, `low`, `volume`, `pctChg`.
* **用途**: 市场多空大势过滤器（如仅当大盘收盘价站在 20 日均线上方时才建仓）。

---

## 3. 回测开发必须遵守的四大算法规范 (Quant Coding Rules)

### 3.1 杜绝未来函数：二分查找披露日 (Binary Search on `pub_date`)
为了在纳秒级定位历史任意交易日可获取的最新财报，预先将各股票财报按 `(pub_date ASC, stat_date ASC)` 排序并提取 NumPy 数组：

```python
import numpy as np

# 预先构建字典: { "sh.600519": (np.array(pub_dates, dtype='datetime64[D]'), [record_dict, ...]) }
def get_latest_financial_record(financial_dict, stock_code: str, trade_date: np.datetime64) -> dict:
    if stock_code not in financial_dict:
        return None
    pub_dates, records = financial_dict[stock_code]
    # 二分查找：找到最后一个 <= trade_date 的财报记录索引
    idx = np.searchsorted(pub_dates, trade_date, side='right') - 1
    if idx >= 0:
        return records[idx]
    return None
```

---

### 3.2 严禁内存指针复用（Memory Safety）
> ❌ **致命陷阱（严禁使用）**: `cache[id(df)] = calculated_indicators`
> **原因**: 当使用 `del df; gc.collect()` 垃圾回收释放内存后，CPython 内存分配器会将相同的物理内存地址分配给后续的新股票 DataFrame。使用 `id(df)` 会直接导致后一批股票误读取前一批完全不同股票的指标，造成**“串票”与回测结果抖动**！
>
> ✅ **正确做法**: 直接在当前股票的 `df` 中新增列（如 `df['buy_signal'] = True`）或以业务主键 `(stock_code, trade_date)` 作为缓存键。

---

### 3.3 严格确定性排序与平局判定 (Deterministic Tie-Breaker)
当多只候选股票因子分值完全相同（例如两只股票的 ROE 均为 12.50% 或价格均为 5.00 元）时，必须引入股票代码升序作为二级键：
* **因子降序 (从大到小，如高 ROE、高动量)**:
  ```python
  candidates.sort(key=lambda x: (-x['factor_val'], str(x['stock_code'])))
  ```
* **因子升序 (从小到大，如低 PE、低股价、双低转债)**:
  ```python
  candidates.sort(key=lambda x: (x['factor_val'], str(x['stock_code'])))
  ```

---

### 3.4 真实交易规则与撮合执行规范 (A-Share Execution Constraints)
1. **A 股 T+1 交易交收约束**：
   - 股票在 $T$ 日买入后，**$T$ 日盘中绝对不可卖出**（禁止股票当日 T+0 平仓）。最早只能在 $T+1$ 日平仓。
   - 可转债品种不受此限制，支持 $T+0$ 交易。
2. **涨跌停板限制 (Price Limit-Up/Down)**：
   - 当日涨停（主板 $\ge 9.9\%$，创业板/科创板 $\ge 19.9\%$，北交所 $\ge 29.9\%$）标的：**禁止买入开仓**。
   - 当日跌停标的：**禁止卖出平仓**（必须延后至下一交易日开板后撮合）。
3. **真实交易费率与摩擦成本**：
   - **印花税**: 卖出单边收取 **0.05%**（买入不收）。
   - **券商佣金**: 买卖双边收取 **0.025%**（单笔最低 5 元）。
   - **撮合滑点**: 模拟实盘市价单买入通常加计 **0.1%** 滑点（即买入按 `Open * 1.001` 成交）。
4. **等额分仓管理 (Equal-Slot Allocation)**：
   - 资金划分为固定的 $N$ 个槽位（如 5 仓，各占初始资金 $20\%$）。
   - 单槽位持有一只标的，平仓后回笼的本金+利润留在该槽位内，或封顶复投。

---

## 4. 开箱即用：完整独立 Python 回测脚本模板 (Self-Contained Backtest Script)

以下脚本为**零依赖外部工程框架的纯独立 Python 脚本**，任何 AI 助手可以直接复制代码在本地运行并输出标准回测报告：

```python
# -*- coding: utf-8 -*-
"""
Standalone A-Share Quantitative Backtest Script
数据源: MagicSTG TiDB Cloud
"""

import os
import gc
import time
import pymysql
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List, Tuple, Optional

# ==============================================================================
# 1. 数据库安全连接与数据加载器
# ==============================================================================

def get_db_connection():
    ca_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "MagicSTG", "dbconfig", "isrgrootx1.pem"))
    conn_params = {
        "host": os.getenv("DB_HOST", "gateway01.ap-southeast-1.prod.aws.tidbcloud.com"),
        "port": int(os.getenv("DB_PORT", 4000)),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "database": os.getenv("DB_NAME", "asharedb"),
        "charset": "utf8mb4",
        "autocommit": True,
        "connect_timeout": 15,
        "read_timeout": 90,
    }
    if os.path.exists(ca_path):
        conn_params["ssl"] = {"ca": ca_path}
    return pymysql.connect(**conn_params)

def safe_read_sql(query: str, conn, params=None, max_retries: int = 5):
    for attempt in range(1, max_retries + 1):
        try:
            if conn is None:
                conn = get_db_connection()
            else:
                conn.ping(reconnect=True)
            return pd.read_sql(query, conn, params=params), conn
        except Exception as e:
            time.sleep(min(1.0 * (2 ** (attempt - 1)), 10.0))
            try:
                conn = get_db_connection()
            except Exception:
                conn = None
    raise RuntimeError(f"SQL 查询失败: {query[:80]}")

def load_stock_universe(limit_to_csi300: bool = True) -> List[str]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if limit_to_csi300:
                cur.execute("SELECT DISTINCT code FROM stock_profit_quarterly ORDER BY code ASC LIMIT 300")
            else:
                cur.execute("SELECT DISTINCT code FROM stock_kline_day ORDER BY code ASC")
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

def load_all_financials(target_codes: List[str]) -> Dict[str, Tuple[np.ndarray, List[dict]]]:
    """批量加载上市公司历史财报并建立基于 pub_date 的二分查找索引"""
    print(f"[数据中心] 正在加载 {len(target_codes)} 只股票的季度财务数据...", flush=True)
    conn = get_db_connection()
    try:
        batch_size = 400
        all_dfs = []
        for i in range(0, len(target_codes), batch_size):
            batch = target_codes[i:i+batch_size]
            db_codes = tuple(set([c.replace('.', '_') for c in batch] + [c.replace('_', '.') for c in batch]))
            query = """
            SELECT 
                p.code, p.stat_date, p.pub_date, p.roe_avg,
                g.YOYNI,
                b.liabilityToAsset,
                c.CFOToNP
            FROM stock_profit_quarterly p
            LEFT JOIN stock_growth_quarterly g ON p.code = g.code AND p.stat_date = g.stat_date
            LEFT JOIN stock_balance_quarterly b ON p.code = b.code AND p.stat_date = b.stat_date
            LEFT JOIN stock_cash_flow_quarterly c ON p.code = c.code AND p.stat_date = c.stat_date
            WHERE p.code IN %s
            ORDER BY p.code ASC, p.pub_date ASC, p.stat_date ASC
            """
            b_df, conn = safe_read_sql(query, conn, params=(db_codes,))
            if not b_df.empty:
                all_dfs.append(b_df)
        
        if not all_dfs:
            return {}
        
        df = pd.concat(all_dfs, ignore_index=True)
        df['pub_date'] = pd.to_datetime(df['pub_date'])
        df['stat_date'] = pd.to_datetime(df['stat_date'])
        df['code'] = df['code'].str.replace('_', '.', regex=False)
        df['roe_avg'] = pd.to_numeric(df['roe_avg'], errors='coerce').astype(np.float32)
        
        df.sort_values(['code', 'pub_date', 'stat_date'], ascending=[True, True, True], inplace=True)
        df.drop_duplicates(subset=['code', 'pub_date', 'stat_date'], keep='last', inplace=True)
        
        fin_dict = {}
        for code, group in df.groupby('code', sort=False):
            fin_dict[code] = (group['pub_date'].values, group.to_dict('records'))
        
        print(f"[数据中心] 财务数据加载完成，覆盖 {len(fin_dict)} 只标的。", flush=True)
        return fin_dict
    finally:
        conn.close()

def load_stock_klines(target_codes: List[str], start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
    """分批次拉取股票日 K 线行情数据"""
    print(f"[数据中心] 正在拉取日 K 线行情 ({start_date} ~ {end_date})...", flush=True)
    conn = get_db_connection()
    try:
        batch_size = 300
        all_dfs = []
        for i in range(0, len(target_codes), batch_size):
            batch = target_codes[i:i+batch_size]
            fmt_str = ','.join(['%s'] * len(batch))
            query = f"""
            SELECT code, date, open, high, low, close, volume, turn, peTTM, pbMRQ
            FROM stock_kline_day
            WHERE code IN ({fmt_str}) AND date >= %s AND date <= %s
            ORDER BY code ASC, date ASC
            """
            params = batch + [start_date, end_date]
            b_df, conn = safe_read_sql(query, conn, params=params)
            if not b_df.empty:
                all_dfs.append(b_df)
                
        if not all_dfs:
            return {}
            
        merged = pd.concat(all_dfs, ignore_index=True)
        merged['code'] = merged['code'].str.replace('_', '.', regex=False)
        merged['date'] = pd.to_datetime(merged['date'])
        for col in ['open', 'high', 'low', 'close', 'volume', 'turn', 'peTTM', 'pbMRQ']:
            merged[col] = pd.to_numeric(merged[col], errors='coerce').astype(np.float32)
            
        merged.sort_values(['code', 'date'], inplace=True)
        merged.drop_duplicates(subset=['code', 'date'], keep='last', inplace=True)
        
        klines = {}
        for code, group in merged.groupby('code', sort=False):
            group.set_index('date', inplace=True)
            klines[code] = group
        return klines
    finally:
        conn.close()

# ==============================================================================
# 2. 策略逻辑：5日均线金叉 + ROE >= 5% 多因子选股
# ==============================================================================

def run_standalone_backtest(
    start_date: str = "2026-07-24",
    end_date: str = "2026-08-24",
    initial_capital: float = 100000.0,
    max_holdings: int = 5,
    stop_loss_pct: float = -8.0,
    trailing_stop_pct: float = -5.0,
    slippage: float = 0.001
):
    # 1. 标的池与数据准备
    codes = load_stock_universe(limit_to_csi300=False)
    fin_data = load_all_financials(codes)
    klines_data = load_stock_klines(codes, start_date=start_date, end_date=end_date)
    
    # 提取所有交易日
    all_dates = sorted(list(set(d for df in klines_data.values() for d in df.index)))
    date_strs = [d.strftime('%Y-%m-%d') for d in all_dates]
    
    print(f"\n[回测引擎] 启动回测: 区间 {start_date} ~ {end_date} (共 {len(date_strs)} 交易日), 槽位 {max_holdings} 仓, 初始资金 {initial_capital:,.2f} 元")

    # 2. 预计算技术指标 (MA5, MA20)
    for code, df in klines_data.items():
        df['ma5'] = df['close'].rolling(5).mean()
        df['ma20'] = df['close'].rolling(20).mean()
        # 金叉买入条件: 当日 MA5 > MA20
        df['golden_cross'] = df['ma5'] > df['ma20']

    # 3. 撮合与持仓状态机
    per_slot_cash = initial_capital / max_holdings
    slot_cash = [per_slot_cash] * max_holdings
    active_positions: Dict[int, dict] = {} # {slot_idx: pos_dict}
    trade_logs: List[dict] = []
    
    for current_dt in all_dates:
        d_str = current_dt.strftime('%Y-%m-%d')
        dt_np = np.datetime64(current_dt)

        # -------------------------------------------------------------
        # Phase A: 评估当前持仓与风控平仓 (T+1 保护)
        # -------------------------------------------------------------
        for slot_idx in list(active_positions.keys()):
            pos = active_positions[slot_idx]
            code = pos['stock_code']
            if code not in klines_data or current_dt not in klines_data[code].index:
                continue
            
            bar = klines_data[code].loc[current_dt]
            c_high = float(bar['high'])
            c_low = float(bar['low'])
            c_close = float(bar['close'])
            
            # 更新历史最高价
            if c_high > pos['highest_price']:
                pos['highest_price'] = c_high
                
            # 严格遵守 A 股 T+1 交易规则：买入当日禁止平仓
            if pos['buy_date'] == d_str:
                continue
                
            # 1. 移动止盈回撤检查
            drawdown = (c_close - pos['highest_price']) / pos['highest_price'] * 100.0
            # 2. 硬止损检查
            hard_loss = (c_close - pos['buy_price']) / pos['buy_price'] * 100.0
            
            sell_reason = None
            if trailing_stop_pct and drawdown <= trailing_stop_pct:
                sell_reason = f"移动止盈 (回撤 {drawdown:.2f}%)"
            elif stop_loss_pct and hard_loss <= stop_loss_pct:
                sell_reason = f"硬止损 (亏损 {hard_loss:.2f}%)"
                
            if sell_reason:
                # 撮合平仓
                sell_price = c_close
                gross_proceeds = pos['shares'] * sell_price
                stamp_tax = gross_proceeds * 0.0005 # 印花税 0.05%
                commission = max(5.0, gross_proceeds * 0.00025) # 佣金 0.025%
                net_proceeds = gross_proceeds - stamp_tax - commission
                pnl = net_proceeds - pos['cost_total']
                pnl_pct = (pnl / pos['cost_total']) * 100.0
                
                slot_cash[slot_idx] += net_proceeds
                trade_logs.append({
                    'date': d_str, 'code': code, 'action': 'SELL', 'price': sell_price,
                    'shares': pos['shares'], 'pnl': pnl, 'pnl_pct': pnl_pct, 'reason': sell_reason
                })
                del active_positions[slot_idx]

        # -------------------------------------------------------------
        # Phase B: 寻找今日买入候选标的 (多因子选股)
        # -------------------------------------------------------------
        idle_slots = [i for i in range(max_holdings) if i not in active_positions]
        if idle_slots:
            daily_candidates = []
            for code, df in klines_data.items():
                if current_dt not in df.index:
                    continue
                bar = df.loc[current_dt]
                # 因子 1: 均线金叉
                if not bar['golden_cross']:
                    continue
                # 因子 2: 财报 ROE >= 5.0%
                fin_rec = None
                if code in fin_data:
                    p_dates, r_list = fin_data[code]
                    idx = np.searchsorted(p_dates, dt_np, side='right') - 1
                    if idx >= 0:
                        fin_rec = r_list[idx]
                
                if fin_rec and fin_rec.get('roe_avg') is not None and fin_rec['roe_avg'] >= 5.0:
                    roe_val = float(fin_rec['roe_avg'])
                    # 记录候选标的 (排序主键: ROE 降序, 二级确定性主键: 代码 A-Z 升序)
                    daily_candidates.append({
                        'code': code,
                        'price': float(bar['close']),
                        'roe': roe_val
                    })
            
            # 严格确定性排序
            daily_candidates.sort(key=lambda x: (-x['roe'], str(x['code'])))
            
            # 挑选 Top N 买入建仓
            for cand in daily_candidates:
                if not idle_slots:
                    break
                slot_idx = idle_slots.pop(0)
                budget = min(slot_cash[slot_idx], per_slot_cash)
                buy_price = cand['price'] * (1.0 + slippage) # 加计滑点
                shares = int(budget / buy_price / 100) * 100 # 整百股
                if shares < 100:
                    idle_slots.append(slot_idx)
                    continue
                
                actual_cost = shares * buy_price
                commission = max(5.0, actual_cost * 0.00025)
                total_spent = actual_cost + commission
                slot_cash[slot_idx] -= total_spent
                
                active_positions[slot_idx] = {
                    'stock_code': cand['code'],
                    'buy_date': d_str,
                    'buy_price': buy_price,
                    'highest_price': buy_price,
                    'shares': shares,
                    'cost_total': total_spent
                }
                trade_logs.append({
                    'date': d_str, 'code': cand['code'], 'action': 'BUY', 'price': buy_price,
                    'shares': shares, 'pnl': 0.0, 'pnl_pct': 0.0, 'reason': f"金叉+ROE({cand['roe']:.1f}%)"
                })

    # ==============================================================================
    # 3. 统计指标输出
    # ==============================================================================
    final_equity = sum(slot_cash)
    for slot_idx, pos in active_positions.items():
        last_close = klines_data[pos['stock_code']].iloc[-1]['close']
        final_equity += pos['shares'] * last_close

    total_return = (final_equity - initial_capital) / initial_capital * 100.0
    closed_trades = [t for t in trade_logs if t['action'] == 'SELL']
    win_trades = [t for t in closed_trades if t['pnl'] > 0]
    win_rate = (len(win_trades) / len(closed_trades) * 100.0) if closed_trades else 0.0
    
    print("\n" + "=" * 60)
    print(f"📊 回测结果统计报告 ({start_date} ~ {end_date})")
    print("=" * 60)
    print(f"  初始资产: {initial_capital:,.2f} 元")
    print(f"  最终资产: {final_equity:,.2f} 元")
    print(f"  累计回报率: {total_return:+.2f}%")
    print(f"  交易总笔数: {len(trade_logs)} 笔 (买入: {len(trade_logs)-len(closed_trades)}, 卖出平仓: {len(closed_trades)})")
    print(f"  平仓胜率: {win_rate:.2f}%")
    print("=" * 60)

if __name__ == "__main__":
    run_standalone_backtest()
```

---

## 5. 总结与合规清单 (AI Checklist)

当指导其他 AI 编写针对本项目数据源的脚本时，请务必核对以下清单：
1. [x] **连接安全**: 是否配置了 SSL CA 证书并封装了 5 次网络重试？
2. [x] **杜绝未来函数**: 季度财报是否使用 `pub_date <= trade_date` 二分查找进行对齐？
3. [x] **内存安全**: 是否绝对排除了 `id(df)` 缓存指针？
4. [x] **确定性排序**: 候选标的排序是否包含了 `str(code)` 二级升序作为 Tie-Breaker？
5. [x] **T+1 规则**: 股票回测是否禁止了买入当天的平仓动作？
