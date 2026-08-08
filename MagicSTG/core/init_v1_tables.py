# -*- coding: utf-8 -*-
"""
MagicSTG Table Initializer for Quant System v1.0
Creates custom_strategies table and populates initial default strategy configurations.
"""

import json
import pymysql
from MagicSTG.core.db import get_connection

DEFAULT_STRATEGIES = [
    {
        "strategy_id": "cb_double_low",
        "name": "可转债双低策略",
        "category": "convertible_bond",
        "description": "基于双低值 (转债价格 + 转股溢价率*100) 进行低位防守型选债，兼顾债性防守与股性弹性。",
        "factors_config": {
            "max_price": 130.0,
            "min_convert_premium": -20.0,
            "max_convert_premium": 50.0,
            "max_pure_bond_premium": 30.0,
            "min_ytm": 0.0,
            "top_n": 10,
            "sort_by": "db_low_value",
            "stock_scope": "all",
            "stock_linkage": {
                "enable_stock_roe": False,
                "stock_roe_min": 0.05,
                "enable_stock_pe": False,
                "stock_pe_min": 0.0,
                "stock_pe_max": 35.0,
                "enable_stock_ma": False,
                "short_ma": 5,
                "long_ma": 20
            }
        },
        "buy_signals_rule": "选出转债价格 <= 130 且转股溢价率在 [-20%, 50%] 区间内双低值最低的 Top 10 转债",
        "sell_signals_rule": "价格突破 130 元或双低排名跌出 Top 20 时触发平仓止盈"
    },
    {
        "strategy_id": "dynamic_factor",
        "name": "多因子动态股票策略",
        "category": "stock",
        "description": "综合考虑 ROE、PE、净利润增长率、短/长期均线金叉及 PDI/NDI 动量指标的股票量化策略。",
        "factors_config": {
            "pe_range": [0, 50],
            "min_roe": 0.08,
            "min_net_profit_yoy": 0.10,
            "max_debt_ratio": 0.70,
            "min_cash_ratio": 0.50,
            "short_ma": 5,
            "long_ma": 20,
            "buy_di_threshold": 0.70,
            "sort_by": "roe_desc",
            "stock_scope": "csi300",
            "top_n": 10
        },
        "buy_signals_rule": "符合基本面财务约束且 short_ma > long_ma 出现金拆、PDI 占优",
        "sell_signals_rule": "short_ma < long_ma 死叉或财务指标恶化"
    },
    {
        "strategy_id": "pe_strategy",
        "name": "低估值 PE 策略",
        "category": "stock",
        "description": "经典低估值选股策略，按 PE (TTM) 从低到高排序，优先买入估值最低的优质公司。",
        "factors_config": {
            "pe_range": [0, 30],
            "min_roe": 0.05,
            "sort_by": "pe_asc",
            "stock_scope": "all",
            "top_n": 10
        },
        "buy_signals_rule": "PE 从低到高排列选出前 10 只股票买入",
        "sell_signals_rule": "PE 超过阈值或估值分位数过高"
    },
    {
        "strategy_id": "roe_strategy",
        "name": "高 ROE 盈利策略",
        "category": "stock",
        "description": "巴菲特式长线盈利策略，筛选长期平均 ROE 最高的优质企业，以高资本回报率为核心筛选指标。",
        "factors_config": {
            "min_roe": 0.15,
            "sort_by": "roe_desc",
            "stock_scope": "csi300",
            "top_n": 10
        },
        "buy_signals_rule": "平均 ROE >= 15% 且按 ROE 降序排列前 10 买入",
        "sell_signals_rule": "连续两季度 ROE 降至 10% 以下"
    },
    {
        "strategy_id": "price_strategy",
        "name": "低股价策略",
        "category": "stock",
        "description": "低单价股票筛选策略，寻找低绝对股价且基本面无退市风险的股票。",
        "factors_config": {
            "max_price": 10.0,
            "sort_by": "price_asc",
            "stock_scope": "all",
            "top_n": 10
        },
        "buy_signals_rule": "单股价格低于 10 元且按价格升序排序前 10 买入",
        "sell_signals_rule": "股价上涨突破 15 元平仓"
    }
]


def init_v1_tables():
    """初始化 v1.0 策略管理表及默认数据"""
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            # 1. 创建 custom_strategies 表
            print("  [DB Init] Creating table custom_strategies...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS custom_strategies (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    strategy_id VARCHAR(64) NOT NULL UNIQUE,
                    name VARCHAR(128) NOT NULL UNIQUE,
                    category VARCHAR(32) NOT NULL DEFAULT 'stock',
                    description TEXT,
                    factors_config JSON,
                    buy_signals_rule TEXT,
                    sell_signals_rule TEXT,
                    is_active TINYINT(1) DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # 动态检查并添加 is_active 字段
            try:
                cursor.execute("SHOW COLUMNS FROM custom_strategies LIKE 'is_active'")
                if not cursor.fetchone():
                    cursor.execute("ALTER TABLE custom_strategies ADD COLUMN is_active TINYINT(1) DEFAULT 1")
            except Exception:
                pass

            # 2. 插入或更新默认策略
            print("  [DB Init] Populating default strategy records...")
            for stg in DEFAULT_STRATEGIES:
                cursor.execute("""
                    INSERT INTO custom_strategies 
                    (strategy_id, name, category, description, factors_config, buy_signals_rule, sell_signals_rule)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        name = VALUES(name),
                        category = VALUES(category),
                        description = VALUES(description),
                        factors_config = VALUES(factors_config),
                        buy_signals_rule = VALUES(buy_signals_rule),
                        sell_signals_rule = VALUES(sell_signals_rule);
                """, (
                    stg["strategy_id"],
                    stg["name"],
                    stg["category"],
                    stg["description"],
                    json.dumps(stg["factors_config"], ensure_ascii=False),
                    stg["buy_signals_rule"],
                    stg["sell_signals_rule"]
                ))
        conn.commit()
        print("  [DB Init] Custom strategies initialized successfully!")
    except Exception as e:
        print(f"  [DB Init ERROR] Initialization failed: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    init_v1_tables()
