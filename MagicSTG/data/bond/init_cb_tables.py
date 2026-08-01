#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
初始化可转债数据库表结构（cb_basic, cb_kline_day, cb_daily_indicator）
"""

import sys
import os

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from MagicSTG.core.db import get_connection


CREATE_CB_BASIC_SQL = """
CREATE TABLE IF NOT EXISTS `cb_basic` (
  `code` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '可转债代码 (如: 113008.SH)',
  `name` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL DEFAULT NULL COMMENT '可转债名称',
  `stock_code` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL DEFAULT NULL COMMENT '正股代码 (如: 603035.SH)',
  `stock_name` varchar(50) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL DEFAULT NULL COMMENT '正股名称',
  `convert_price` decimal(12, 4) NULL DEFAULT NULL COMMENT '最新转股价(元)',
  `issue_size` decimal(20, 4) NULL DEFAULT NULL COMMENT '发行规模(亿元)',
  `curr_issue_size` decimal(20, 4) NULL DEFAULT NULL COMMENT '剩余规模(亿元)',
  `list_date` date NULL DEFAULT NULL COMMENT '上市日期',
  `delist_date` date NULL DEFAULT NULL COMMENT '退市/摘牌日期',
  `maturity_date` date NULL DEFAULT NULL COMMENT '到期日期',
  `rating` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL DEFAULT NULL COMMENT '债券评级',
  `update_date` date NULL DEFAULT NULL COMMENT '最后更新日期',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
  PRIMARY KEY (`code`) USING BTREE,
  INDEX `idx_stock_code`(`stock_code` ASC) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_bin COMMENT = '可转债基础信息表';
"""

CREATE_CB_KLINE_DAY_SQL = """
CREATE TABLE IF NOT EXISTS `cb_kline_day` (
  `code` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '可转债代码',
  `date` date NOT NULL COMMENT '交易日期',
  `open` decimal(12, 4) NULL DEFAULT NULL COMMENT '开盘价',
  `high` decimal(12, 4) NULL DEFAULT NULL COMMENT '最高价',
  `low` decimal(12, 4) NULL DEFAULT NULL COMMENT '最低价',
  `close` decimal(12, 4) NULL DEFAULT NULL COMMENT '收盘价',
  `volume` bigint NULL DEFAULT NULL COMMENT '成交量(手/张)',
  `amount` decimal(20, 4) NULL DEFAULT NULL COMMENT '成交额(元)',
  `pctChg` decimal(12, 6) NULL DEFAULT NULL COMMENT '涨跌幅(%)',
  `turn` decimal(12, 6) NULL DEFAULT NULL COMMENT '换手率(%)',
  `update_date` date NULL DEFAULT NULL COMMENT '最后更新日期',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
  PRIMARY KEY (`code`, `date`) USING BTREE,
  INDEX `idx_date`(`date` ASC) USING BTREE,
  INDEX `idx_code`(`code` ASC) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_bin COMMENT = '可转债日线K线表';
"""

CREATE_CB_DAILY_INDICATOR_SQL = """
CREATE TABLE IF NOT EXISTS `cb_daily_indicator` (
  `code` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL COMMENT '可转债代码',
  `date` date NOT NULL COMMENT '交易日期',
  `cb_price` decimal(12, 4) NULL DEFAULT NULL COMMENT '转债收盘价',
  `stock_code` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL DEFAULT NULL COMMENT '正股代码',
  `stock_price` decimal(12, 4) NULL DEFAULT NULL COMMENT '正股收盘价',
  `convert_price` decimal(12, 4) NULL DEFAULT NULL COMMENT '转股价格',
  `convert_value` decimal(12, 4) NULL DEFAULT NULL COMMENT '转股价值(平价)',
  `convert_premium_rate` decimal(12, 6) NULL DEFAULT NULL COMMENT '转股溢价率(%)',
  `bond_value` decimal(12, 4) NULL DEFAULT NULL COMMENT '纯债价值',
  `bond_premium_rate` decimal(12, 6) NULL DEFAULT NULL COMMENT '纯债溢价率(%)',
  `db_low_value` decimal(12, 4) NULL DEFAULT NULL COMMENT '双低值(转债价格+转股溢价率*100)',
  `update_date` date NULL DEFAULT NULL COMMENT '最后更新日期',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
  PRIMARY KEY (`code`, `date`) USING BTREE,
  INDEX `idx_date`(`date` ASC) USING BTREE,
  INDEX `idx_code`(`code` ASC) USING BTREE,
  INDEX `idx_db_low`(`date` ASC, `db_low_value` ASC) USING BTREE
) ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_bin COMMENT = '可转债每日衍生指标表';
"""


def init_tables():
    print("=" * 60)
    print("🚀 开始创建可转债数据库表 (cb_basic, cb_kline_day, cb_daily_indicator)...")
    print("=" * 60)
    
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            print("  [1/3] 创建 cb_basic 表...")
            cur.execute(CREATE_CB_BASIC_SQL)
            print("  ✅ cb_basic 创建成功/已存在")

            print("  [2/3] 创建 cb_kline_day 表...")
            cur.execute(CREATE_CB_KLINE_DAY_SQL)
            print("  ✅ cb_kline_day 创建成功/已存在")

            print("  [3/3] 创建 cb_daily_indicator 表...")
            cur.execute(CREATE_CB_DAILY_INDICATOR_SQL)
            print("  ✅ cb_daily_indicator 创建成功/已存在")

        conn.commit()
        print("\n🎉 可转债表结构初始化完毕！")
    except Exception as e:
        print(f"❌ 创建表结构失败: {e}")
        conn.rollback()
        raise e
    finally:
        conn.close()


if __name__ == '__main__':
    init_tables()
