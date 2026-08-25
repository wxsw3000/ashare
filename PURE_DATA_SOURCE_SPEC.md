# A股与可转债量化数据库说明与开发手册 (Data Source & API Specification)

> **致开发者 / AI 助手**：
> 
> 本文档是独立量化研究数据库的**纯净版数据字典与接入规范**。
> 本项目仅包含数据库连接凭据（`.env`）、SSL 证书（`isrgrootx1.pem`）及由您编写的独立量化脚本，**无任何既有框架依赖**。
> 请根据本文档提供的表结构与连接方式，直接编写独立的 Python 回测与分析脚本。

---

## 1. 独立运行目录与环境配置

在一个完全独立的新项目目录中，仅需以下 3 个文件：

```text
my_standalone_project/
├── .env                  # 数据库连接配置文件 (主机、端口、用户名、密码)
├── isrgrootx1.pem        # TiDB Cloud 强制要求的 SSL 根证书
└── my_backtest_script.py # 您编写的独立量化策略与回测脚本
```

### 1.1 `.env` 配置文件格式
```env
DB_HOST=gateway01.ap-southeast-1.prod.aws.tidbcloud.com
DB_PORT=4000
DB_USER=your_username.root
DB_PASSWORD=your_password
DB_NAME=asharedb
```

### 1.2 Python 依赖安装
```bash
pip install pymysql pandas numpy cryptography python-dotenv
```

### 1.3 独立数据库连接与重试代码模板 (可直接复制)
```python
import os
import time
import pymysql
import pandas as pd
from dotenv import load_dotenv

# 自动读取同目录下的 .env 文件
load_dotenv()

def get_connection() -> pymysql.Connection:
    """获取带 SSL 加密的数据库连接"""
    ca_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "isrgrootx1.pem")
    
    conn_params = {
        "host": os.getenv("DB_HOST", "gateway01.ap-southeast-1.prod.aws.tidbcloud.com"),
        "port": int(os.getenv("DB_PORT", 4000)),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "database": os.getenv("DB_NAME", "asharedb"),
        "charset": "utf8mb4",
        "autocommit": True,
        "connect_timeout": 15,
        "read_timeout": 90
    }
    if os.path.exists(ca_path):
        conn_params["ssl"] = {"ca": ca_path}
        
    return pymysql.connect(**conn_params)

def safe_query(sql: str, params=None, max_retries: int = 5) -> pd.DataFrame:
    """带自动指数退避重试的安全 SQL 查询器，防止网络波动漏查数据"""
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            with get_connection() as conn:
                return pd.read_sql(sql, conn, params=params)
        except Exception as e:
            last_err = e
            wait_sec = min(1.0 * (2 ** (attempt - 1)), 10.0)
            time.sleep(wait_sec)
    raise RuntimeError(f"数据库查询失败 (已重试 {max_retries} 次): {last_err}")
```

---

## 2. 数据库表结构字典 (Data Dictionary)

数据库 `asharedb` 包含以下核心数据表：

### 2.1 股票日 K 线行情表：`stock_kline_day`
全市场 5,200+ 股票的历史日线数据。
* **主键**: `(date, code)`
* **股票代码标准格式**: 
  - 上交所: `sh.600000` / `sh.600519`
  - 深交所: `sz.000001` / `sz.002594` / `sz.300750`
  - 北交所: `bj.830000`
* **字段说明**:
  | 字段名 | 类型 | 说明 | 取值示例 |
  | :--- | :--- | :--- | :--- |
  | `date` | `DATE` | 交易日期 | `2026-07-24` |
  | `code` | `VARCHAR(20)` | 股票代码 | `sh.600519` |
  | `open` | `DECIMAL(12,4)` | 当日开盘价 (元) | `1650.0000` |
  | `high` | `DECIMAL(12,4)` | 当日最高价 (元) | `1680.0000` |
  | `low` | `DECIMAL(12,4)` | 当日最低价 (元) | `1642.0000` |
  | `close` | `DECIMAL(12,4)` | 当日收盘价 (元) | `1675.0000` |
  | `preclose` | `DECIMAL(12,4)` | 前收盘价 (元) | `1648.0000` |
  | `volume` | `BIGINT` | 当日成交量 (股) | `2500000` |
  | `amount` | `DECIMAL(20,4)` | 当日成交金额 (元) | `4187500000.0000` |
  | `turn` | `DECIMAL(12,6)` | 当日换手率 (%) | `0.250000` |
  | `pctChg` | `DECIMAL(12,6)` | 当日涨跌幅 (%) | `1.638350` |
  | `peTTM` | `DECIMAL(20,6)` | 滚动市盈率 (PE-TTM) | `28.540000` |
  | `pbMRQ` | `DECIMAL(20,6)` | 市净率 (PB-MRQ) | `8.210000` |
  | `psTTM` | `DECIMAL(20,6)` | 滚动市销率 (PS-TTM) | `12.100000` |
  | `tradestatus` | `VARCHAR(5)` | 交易状态 (`'1'`: 正常交易, `'0'`: 停牌) | `'1'` |
  | `isST` | `VARCHAR(5)` | 是否 ST 风险警示 (`'1'`: 是, `'0'`: 否) | `'0'` |

---

### 2.2 股票季度财务四表（杜绝未来函数）
上市公司财报包含两个核心日期：
1. `stat_date` (统计截止日/财报期): 如 `2025-12-31` (年报), `2026-03-31` (一季报)。
2. `pub_date` (实际公告披露日期): 如 `2026-04-28`。

> ⚠️ **未来函数规避铁律**：
> 在回测某个历史交易日 $T$（如 `2026-03-15`）时，**绝对禁止**使用尚未披露的财报！
> **必须严格使用 `pub_date <= T` 的最新已披露财报记录。**

#### 财务表详情：
1. **盈利能力表 `stock_profit_quarterly`**
   - 字段: `code`, `stat_date`, `pub_date`, `roe_avg` (平均净资产收益率 %), `net_profit` (净利润/元), `eps_ttm` (每股收益).
2. **成长能力表 `stock_growth_quarterly`**
   - 字段: `code`, `stat_date`, `pub_date`, `YOYNI` (净利润同比增长率 %).
3. **偿债能力表 `stock_balance_quarterly`**
   - 字段: `code`, `stat_date`, `pub_date`, `liabilityToAsset` (资产负债率 0.0~1.0).
4. **现金流表 `stock_cash_flow_quarterly`**
   - 字段: `code`, `stat_date`, `pub_date`, `CFOToNP` (经营现金流 / 净利润比率).

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

### 2.3 可转债行情与专有指标表
全市场 1,000+ 只可转债历史数据。
* **`cb_basic`** (可转债基础信息):
  - `code`: 转债代码 (如 `sh.113008`, `sz.128000`)
  - `name`: 转债名称
  - `stock_code`: 对应正股代码 (如 `sh.600000`)
  - `convert_price`: 最新转股价 (元)
  - `rating`: 评级 (如 `AAA`, `AA+`)
* **`cb_kline_day`** (转债日 K 线):
  - `code`, `date`, `open`, `high`, `low`, `close`, `volume`, `amount`, `pctChg`, `turn`.
* **`cb_daily_indicator`** (转债专有指标):
  - `cb_price`: 转债收盘价 (元)
  - `stock_price`: 正股收盘价 (元)
  - `convert_price`: 转股价 (元)
  - `convert_value`: 转股价值 $= (\text{stock\_price} / \text{convert\_price}) \times 100$
  - `convert_premium_rate`: 转股溢价率 $(\%)$ $= ((\text{cb\_price} - \text{convert\_value}) / \text{convert\_value}) \times 100$
  - `db_low_value`: 双低值 $= \text{cb\_price} + \text{convert\_premium\_rate}$

---

### 2.4 大盘指数表：`index_kline_day`
主要宽基指数日 K 线。
* **字段**: `code`, `date`, `open`, `high`, `low`, `close`, `volume`, `pctChg`.
* **常见指数代码**:
  - `sh.000300`: 沪深300指数
  - `sh.000001`: 上证指数
  - `sz.399001`: 深证成指
  - `sz.399006`: 创业板指

---

## 3. 量化回测核心业务约束 (A-Share Execution Constraints)

在编写策略回测模拟时，请务必贯彻以下真实 A 股市场规则以保证回测精确性：

1. **A 股 T+1 交易交收约束**：
   - 股票在 $T$ 日买入后，**$T$ 日盘中禁止平仓卖出**（股票不可 T+0）。最早只能在 $T+1$ 日平仓。
   - 可转债支持 $T+0$ 交易。
2. **涨跌停板买卖限制**：
   - 当日涨停（主板 $\ge 9.9\%$，创业板/科创板 $\ge 19.9\%$，北交所 $\ge 29.9\%$）：**禁止买入开仓**。
   - 当日跌停：**禁止卖出平仓**。
3. **真实交易费率与滑点**：
   - **印花税**: 卖出时单边收取 **0.05%**（买入不收）。
   - **券商佣金**: 买卖双边收取 **0.025%**（单笔最低 5 元）。
   - **市价滑点**: 买入撮合建议加计 **0.1%** 滑点（即买入成交价按 `Open * 1.001` 或 `Close * 1.001` 计算）。
4. **确定性同分平局排序 (Deterministic Tie-Breaker)**：
   - 当多只候选股票因子分值相同时（如两只股票 ROE 相同），必须使用**股票代码升序**作为第二排序键（如 `(-factor_val, str(code))` 或 `(factor_val, str(code))`），杜绝多次回测结果出现随机漂移。
5. **等额分仓资金管理**：
   - 初始资金等分为 $N$ 个槽位（如 5 仓，每仓占 $20\%$ 初始资金）。
   - 股票买入数量必须为 **100 股（1 手）的整数倍**。
