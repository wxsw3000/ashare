# MagicSTG 系统开发与策略配置全景指南 (AI Developer & Strategy Handbook)

> **致 AI 助手/开发者**：
> 本文档是 **MagicSTG A股与可转债量化选股、回测与模拟实盘系统** 的全景技术规范与开发指南。
> 请阅读本文档以快速了解系统的**目录结构、数据库 Schema、动态策略配置规范、代码扩展接口及风控规范**，从而编写出完美兼容本系统的策略或扩展代码。

---

## 1. 项目整体架构与算力隔离原则 (Core System Architecture)

> ⚠️ **核心架构铁律（必须时刻遵循）**：
> 1. **Render Node (前端与API层)**：仅运行 Web 仪表盘与轻量级 REST API，**绝不上线运行全市场 5,200+ 股票的 CPU/内存密集型计算循环**（如选股推演、历史回测等），防止触发 Render 免费节点 100 秒 HTTP 502/504 响应超时。
> 2. **GitHub Actions Runner (离线算力引擎层)**：所有全市场 K 线扫描、因子计算、盘后选股推演和历史回测计算，**100% 分发给 GitHub Actions 7GB 内存 + 高性能 CPU 节点**运行，计算结果异步持久化写入 TiDB Cloud 数据库。
> 3. **交互调度流**：Render 接收用户操作 ➔ 0.1 秒内触发 `repository_dispatch` (或 GitHub API) 下发任务并返回 200/202 响应 ➔ GitHub Actions 跑完结果落库 ➔ 前端 3 秒探针轮询并渲染图表。

### 1.1 目录结构与模块划分

```text
E:\ashare\
├── MagicSTG/
│   ├── config/             # 配置中心 (settings.py, config.yaml, 环境配置解析)
│   ├── core/               # 核心底层引擎
│   │   ├── db.py           # TiDB 数据库连接、网络重连重试、数据加载器 (load_all_data_db)
│   │   ├── db_writer.py    # 推荐、持仓与回测结果写入器
│   │   ├── cost.py         # 交易佣金与印花税计算 (股票/可转债分品种成本)
│   │   ├── limit.py        # 涨跌停板检查逻辑
│   │   └── email_notifier.py # 每日盘后选股 HTML 邮件推送引擎
│   ├── data/               # 数据拉取与更新模块
│   │   ├── stock/          # 股票 K 线、财务四表拉取更新
│   │   ├── bond/           # 可转债基础信息、行情与双低指标更新
│   │   └── runner.py       # 每日盘后数据全量增量更新调度器 (run_all_updates.py)
│   ├── strategies/         # 策略框架与引擎
│   │   ├── base.py         # 策略抽象基类 (BaseStrategy)
│   │   ├── dynamic_factor.py # 动态多因子组合策略引擎 (DynamicFactorStrategy)
│   │   ├── cb_double_low.py # 可转债双低策略 (CBDoubleLowStrategy)
│   │   ├── registry.py     # 策略注册与工厂模式 (StrategyRegistry)
│   │   └── runner.py       # 每日策略推荐执行器 (run_all_strategy_recommendations)
│   ├── execution/          # 交易执行与模拟实盘
│   │   ├── portfolio_engine.py # 模拟实盘持仓与资金曲线追踪引擎 (PortfolioEngine)
│   │   ├── decisions.py    # 买卖交易决策器
│   │   └── position_manager.py # 持仓状态管理器
│   └── web/                # Web 展示与 API 服务 (Flask + Chart.js Dashboard)
│       ├── server.py       # Flask RESTful API 服务器
│       ├── static/         # 前端静态资源
│       │   ├── css/main.css # [模块化样式] 包含卡片、布局、模态框与控制台 CSS
│       │   └── js/app.js   # [模块化引擎] 包含 Tab 切换、策略配置、回测与实盘图表逻辑
│       └── templates/index.html # Web 仪表盘主 HTML DOM 骨架
├── examples/
│   └── backtest_standalone_template.py # 独立运行的本地策略与回测 Python 模板
├── AI_DEVELOPMENT_GUIDE.md # 给其他 AI 的策略开发与系统全景指南
├── PROJECT_MEMORY.md       # 项目持久化上下文与历史变更日志
└── requirements.txt        # 依赖列表 (pandas, pymysql, sqlalchemy, flask, akshare, baostock)
```

---

## 2. TiDB Cloud 数据库结构 (Database Schemas)

数据库为 `asharedb`，原生支持**股票**与**可转债**两套行情体系，并通过 `JSON` 动态扩展字段赋予未来新资产/新因子无限的兼容性：

### 2.1 股票行情与财务表
1. **`stock_kline_day`** (股票日 K 线)
   - 主键/索引: `(code, date)`
   - 字段: `code` (`sh.600000`), `date` (`YYYY-MM-DD`), `open`, `high`, `low`, `close`, `volume`, `amount`, `peTTM` (滚动市盈率), `pbMRQ` (市净率), `turn` (换手率 %), `psTTM` (市销率), `pctChg` (涨跌幅).
2. **`stock_profit_quarterly`** (盈利能力表)
   - 关键字段: `code`, `stat_date` (财报期), `pub_date` (实际披露日), `roe_avg` (平均 ROE %).
3. **`stock_growth_quarterly`** (成长能力表)
   - 关键字段: `code`, `stat_date`, `pub_date`, `YOYNI` (净利润同比增长 %).
4. **`stock_balance_quarterly`** (偿债能力表)
   - 关键字段: `code`, `stat_date`, `pub_date`, `liabilityToAsset` (资产负债率 0.0~1.0).
5. **`stock_cash_flow_quarterly`** (现金流质量表)
   - 关键字段: `code`, `stat_date`, `pub_date`, `CFOToNP` (经营现金流/净利润).

### 2.2 可转债行情与特有指标表
1. **`cb_basic`** (可转债主表)
   - 字段: `code` (`sh.113008`), `name`, `stock_code` (正股代码 `sh.600000`), `stock_name`, `convert_price` (转股价), `rating` (评级).
2. **`cb_kline_day`** (可转债日 K 线)
   - 字段: `code`, `date`, `open`, `high`, `low`, `close`, `volume`, `amount`, `pctChg`.
3. **`cb_daily_indicator`** (可转债专有指标)
   - 字段: `code`, `date`, `cb_price` (现价), `convert_value` (转股价值), `convert_premium_rate` (转股溢价率 %), `db_low_value` (双低值 = 现价 + 溢价率 * 100).

### 2.3 市场资金流向与情绪表
1. **`market_money_flow`** (北向资金与两融余额表)
   - 字段: `date` (`YYYY-MM-DD`), `north_net_inflow` (北向资金净流入/万元), `margin_balance` (沪深两融余额/元), `margin_buy` (融资买入额/元).

### 2.4 系统推荐与模拟实盘表
1. **`recommendations`** (每日策略生成的买卖信号)
   - 字段: `strategy` (策略名), `stock_code`, `action` (`BUY`/`SELL`), `price`, `reason`, `signal_date`, **`factor_data` (JSON)**.
   - *注：`factor_data` 为 JSON 格式，可灵活存储任何股票/可转债/新因子的快照，无需加列！*
2. **`positions`** (模拟持仓明细)
   - 字段: `strategy`, `stock_code`, `buy_date`, `buy_price`, `shares`, `cost_total`, `current_price`, `market_value`, `pnl`, `pnl_pct`, `status` (`HOLDING`/`SOLD`).
3. **`portfolio_daily_history`** (资金曲线历史表)
   - 字段: `strategy`, `date`, `total_equity` (总资产), `cash` (可用现金), `market_value` (持仓市值), `cum_return` (累计收益率).
4. **`custom_strategies`** (策略配置表)
   - 字段: `id`, `name`, `is_active` (是否开启每日自动化工作流 0/1), **`config` (JSON)**.

---

## 3. 策略配置与开发模式 (How Strategies Work - Dual-Module Architecture)

在 MagicSTG 中，每一个完整的量化策略均由 **“选股/信号策略 (Selection Strategy)”** 与 **“持仓/资金风控策略 (Portfolio Strategy)”** 构成的**二元组**共同定义：

$$ \text{完整交易策略} = \text{选股策略 (选什么标的/买卖信号)} \ \bigotimes \ \text{持仓策略 (怎么分仓/执行/止损止盈)} $$

策略可以通过 **“声明式 JSON 配置模式”** 和 **“代码级插件扩展模式”** 两种方式进行定义和扩展：

### 3.1 声明式双模块 JSON 配置规范 (Dual-Module JSON Spec)

系统原生支持包含 `portfolio_config`（持仓与风控）及选股因子的完整 JSON 配置规范：

#### 1. 股票双模块策略 JSON 完整模版 (Stock Dual-Module JSON Spec)
```json
{
  "top_n": 10,                 // 选股推荐 Top N 标的数量限制
  "stock_scope": "csi300",     // 标的股票池: "csi300" (沪深300) 或 "all" (全市场A股)
  "sort_by": "pe",             // 主因子排序规则: "pe" (估值), "pb" (市净率), "roe" (盈利), "growth" (成长), "price" (低股价)

  // 1. 持仓与资金风控策略配置 (Portfolio & Risk Management Config)
  "portfolio_config": {
    "strategy_type": "equal_slot",     // 持仓策略插件: "equal_slot" (等额分仓)
    "initial_capital": 100000.0,       // 初始资金 (元)
    "max_holdings": 5,                 // 持仓槽位数 / 最大持仓只数 (5 仓)
    "allocation_mode": "EQUAL_SLOT",   // 资金分配模式: "EQUAL_SLOT" (平仓重置) 或 "COMPOUNDING" (复利)
    "stop_loss_pct": -8.0,             // 硬止损线 (%)，小于该值触发平仓，null 或 0 为关闭
    "trailing_stop_pct": -5.0,         // 移动止盈回撤 (%)，最高价回撤达到该值触发平仓，null 或 0 为关闭
    "max_holding_days": null,          // 最大持仓天数，到达天数强平，null 为不限制
    "execution_timing": "T+1_OPEN",    // 撮合执行时机: "T+1_OPEN" (T+1日开盘价+0.1%滑点，严格无未来函数) 或 "T_CLOSE" (T日收盘价)
    "slippage": 0.001                  // 撮合滑点 (0.1%)
  },

  // 2. 选股技术面指标 (Tech Factors)
  "tech": {
    "enable_golden_cross": true, // 均线金叉开关 (短期均线 > 长期均线)
    "short_ma": 5,              // 短期均线周期 (如 MA5)
    "long_ma": 20,              // 长期均线周期 (如 MA20)
    "enable_volume_surge": true, // 成交量放量开关
    "volume_surge_factor": 1.2, // 放量倍数门槛 (大于均量 1.2 倍)
    "enable_di_ratio": true,    // DMI 趋势比率开关
    "buy_di_threshold": 0.70,   // PDI/NDI 比率门槛
    "enable_turnover": true,    // 换手率 % 过滤开关
    "min_turnover": 1.0,        // 最低换手率 (%)
    "max_turnover": 15.0,       // 最高换手率 (%)
    "enable_rsi": true,         // RSI 超买超卖过滤开关
    "rsi_min": 30.0,            // RSI 下限
    "rsi_max": 70.0,            // RSI 上限
    "enable_macd": true,        // MACD 金叉过滤开关 (DIF > DEA)
    "enable_boll": false,       // BOLL 突破上轨开关
    "enable_kdj": false         // KDJ 低位金叉开关 (K > D 且 K < 30)
  },
  "financial": {
    "enable_roe": true,         // 盈利能力 ROE 过滤开关
    "roe_min": 0.05,            // 最低 ROE 门槛 (5%)
    "enable_pe": true,          // PE 动态估值区间开关
    "pe_min": 0.0,              // PE 最小值
    "pe_max": 35.0,             // PE 最大值
    "enable_pb": true,          // PB 市净率区间开关
    "pb_min": 0.5,              // PB 最小值
    "pb_max": 5.0,              // PB 最大值
    "enable_growth": true,      // 净利润同比增长 (YoY) 开关
    "growth_min": 0.10,         // 最低净利润增长率 (10%)
    "enable_debt_limit": true,  // 资产负债率上限开关
    "debt_max": 0.70,           // 最高资产负债率 (70%)
    "enable_cash_quality": true,// 现金流质量开关 (经营现金流/净利润)
    "cfo_np_min": 0.80          // 最低现金保障率 (0.80)
  }
}
```

#### 2. 可转债策略 JSON 完整字段模版 (Convertible Bond Strategy JSON Spec)
```json
{
  "top_n": 10,                      // 选债推荐 Top N 标的数量限制
  "stock_scope": "all",             // 标的池: "all" (全市场可转债)
  "sort_by": "db_low_value",        // 转债排序算法: "db_low_value" (双低优先), "cb_price" (价格优先), "convert_premium_rate" (溢价率优先), "ytm" (到期收益率优先)
  "max_price": 130.0,               // 最高转债价格限制 (元)
  "min_convert_premium": -20.0,     // 最低转股溢价率 (%)
  "max_convert_premium": 50.0,      // 最高转股溢价率 (%)
  "max_pure_bond_premium": 30.0,    // 纯债溢价率上限 (%, null 为不限制)
  "min_ytm": 0.0,                   // 到期收益率 YTM 下限 (%, null 为不限制)
  "stock_linkage": {
    "enable_stock_roe": true,       // 正股盈利 ROE 联动限制开关
    "stock_roe_min": 0.05,          // 发行正股最低 ROE (5%)
    "enable_stock_pe": true,        // 正股 PE 估值联动限制开关
    "stock_pe_min": 0.0,            // 正股 PE 下限
    "stock_pe_max": 35.0,           // 正股 PE 上限
    "enable_stock_ma": false,       // 正股均线趋势联动开关 (短期均线 > 长期均线)
    "short_ma": 5,                  // 正股短期均线
    "long_ma": 20                   // 正股长期均线
  }
}
```

---

### 模式二：代码级插件扩展模式 (Plugin Code Mode)
当策略需要非线性逻辑（如复杂形态识别、MACD/RSI 择时、正股与转债对冲等）时，可以继承 `BaseStrategy` 并使用注册器。

#### 插件开发步骤：
1. 在 `MagicSTG/strategies/` 下新建策略文件（或在独立脚本中）。
2. 继承 `BaseStrategy` 并实现 `generate_signals(self, date, data)` 方法。
3. 使用 `@StrategyRegistry.register("your_strategy_name")` 装饰器注册。

#### 标准代码模板：
```python
import pandas as pd
from typing import Dict, List, Any
from MagicSTG.strategies.base import BaseStrategy
from MagicSTG.strategies.registry import StrategyRegistry

@StrategyRegistry.register("my_custom_strategy")
class MyCustomStrategy(BaseStrategy):
    def __init__(self, name: str = "my_custom_strategy", config: dict = None):
        super().__init__(name, config)
        # 读取配置中的自定义参数
        self.top_n = self.config.get("top_n", 5)

    def generate_signals(self, date: str, data: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
        """
        生成买入/卖出信号
        :param date: 当前计算日期 'YYYY-MM-DD'
        :param data: K线数据字典 { "sh.600000": DataFrame, ... }
        :return: 信号列表 [ { "stock_code": "sh.600000", "action": "BUY", "price": 10.5, "reason": "...", "factor_data": {...} } ]
        """
        signals = []
        candidates = []

        for code, df in data.items():
            if df.empty or len(df) < 20:
                continue
            
            # 取截至 date 的行情切片（防止未来数据）
            df_slice = df[df['date'] <= date]
            if df_slice.empty:
                continue
            
            latest = df_slice.iloc[-1]
            
            # 【策略逻辑】示例：突破 20 日均线且 PE < 25
            ma20 = df_slice['close'].tail(20).mean()
            current_price = latest['close']
            pe = latest.get('peTTM', 999)

            if current_price > ma20 and 0 < pe < 25:
                candidates.append({
                    "code": code,
                    "price": current_price,
                    "pe": pe,
                    "reason": f"突破均线且PE={pe:.1f}"
                })

        # 按 PE 升序排序取 Top N
        candidates.sort(key=lambda x: x['pe'])
        for item in candidates[:self.top_n]:
            signals.append({
                "strategy": self.name,
                "stock_code": item['code'],
                "action": "BUY",
                "price": item['price'],
                "reason": item['reason'],
                "signal_date": date,
                "factor_data": {"pe": item['pe'], "price": item['price']}
            })

        return signals
```

---

## 4. 核心公共 API 工具箱 (Public Utilities)

在编写脚本时，可以直接调用以下底层公共模块，避免重复造轮子：

```python
# 1. 数据库数据拉取 (支持股票日K线全量拉取，支持限制为沪深300加速测试)
from MagicSTG.core.db import load_all_data_db, get_connection, safe_read_sql_with_retry

# 加载行情
klines = load_all_data_db(start_date="2024-01-01", limit_to_csi300=True)

# 2. 防未来数据泄露的财务数据 SQL 提取范例
def get_valid_financials(date_str: str) -> pd.DataFrame:
    conn = get_connection()
    query = f"""
    SELECT p.code, p.roe_avg, g.YOYNI, b.liabilityToAsset
    FROM stock_profit_quarterly p
    LEFT JOIN stock_growth_quarterly g ON p.code = g.code AND p.stat_date = g.stat_date
    LEFT JOIN stock_balance_quarterly b ON p.code = b.code AND p.stat_date = b.stat_date
    WHERE p.pub_date <= '{date_str}'
    """
    return safe_read_sql_with_retry(query, conn)

# 3. 交易费用计算 (股票 0.05% 印花税 + 0.025% 佣金/最低5元; 可转债免印花税)
from MagicSTG.core.cost import calc_buy_cost, calc_sell_cost
buy_cost = calc_buy_cost(price=10.0, shares=1000, asset_type='stock')
sell_cost = calc_sell_cost(price=12.0, shares=1000, asset_type='stock')

# 4. 涨跌停板监测 (A股 10%/20% 规则)
from MagicSTG.core.limit import check_limit_up, check_limit_down
is_limit_up = check_limit_up(code="sh.600000", current_price=11.0, prev_close=10.0)

# 5. 模拟实盘与持仓引擎
from MagicSTG.execution.portfolio_engine import PortfolioEngine
engine = PortfolioEngine(strategy_name="dynamic_factor")
engine.run_daily_rollover(target_date="2026-08-15") # 自动撮合买卖、扣除交易成本、更新持仓与资金曲线
```

---

## 5. 量化设计与风控铁律 (Strict Constraints)

1. **防范未来数据 (No Future-data Bias)**：
   在回测或实时筛选任何季度财务指标（ROE、净利润增长等）时，**必须且只能使用 `pub_date` (实际披露日) `<= 当前计算日期` 的数据**。严禁按 `stat_date` (财报期末) 关联！
2. **交易手数约束**：
   - 股票买入必须为 **100 股的整数倍** (`floor(budget / price / 1.0003 / 100) * 100`)。
   - 可转债买入必须为 **10 张的整数倍**（单张面值 100 元）。
3. **分品种交易成本**：
   - 股票：卖出收取 0.05% 印花税；买卖收取 0.025% 佣金（单笔最低 5 元）。
   - 可转债：免收印花税；买卖收取 0.005% 佣金（无最低 5 元限制）。
4. **盘后数据更新机制**：
   盘后全市场数据在每日 **18:30 (北京时间)** 准备就绪，自动化选股任务在 18:30 以后触发方可保障数据完整。

---

> **总结**：
> 在开发或编写策略时，你可以选择使用 `DynamicFactorStrategy` 的 JSON 参数快速试验，也可以通过继承 `BaseStrategy` 实现复杂的交易逻辑。所有数据拉取、交易费用扣除、持仓撮合及图表展示均已由底层基础设施全自动处理！
