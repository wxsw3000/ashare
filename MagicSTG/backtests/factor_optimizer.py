# -*- coding: utf-8 -*-
"""
MagicSTG Factor Grid Search & Optimizer
Loads full market data once and performs grid search to find the optimal factor combination.
"""

import os
import sys
import itertools
import pandas as pd
import numpy as np
from typing import Dict, List, Any

MAGICSTG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(MAGICSTG_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from MagicSTG.core.db import load_all_data_db
from MagicSTG.backtests.engine import run_backtest_engine
from MagicSTG.config import load_yaml_config


def optimize_factors(
    start_date: str = "2024-01-01",
    end_date: str = "2026-06-30",
    universe: str = "all",
    top_n: int = 10
) -> pd.DataFrame:
    """
    Performs grid search over key technical and fundamental factor thresholds to find top combinations.
    """
    base_config = load_yaml_config()


    # 1. Parameter Grid
    param_grid = {
        'buy_di_threshold': [0.50, 0.70, 0.90],
        'vol_surge_factor': [1.0, 1.2, 1.5],
        'enable_roe': [True, False],
        'roe_min': [0.05, 0.10],
        'enable_growth': [True, False],
        'growth_min': [0.10, 0.20],
        'primary_factor': ['price', 'roe', 'growth']
    }

    # Generate combination matrix
    keys, values = zip(*param_grid.items())
    combos = [dict(zip(keys, v)) for v in itertools.product(*values)]
    print(f"[Optimizer] 共生成 {len(combos)} 组因子组合进行评估对比...", flush=True)

    # Pre-load data ONCE to avoid repeated database queries
    limit_to_csi300 = (universe == 'csi300')
    print(f"[Optimizer] 开始一次性预加载股票数据 (universe={universe})...", flush=True)
    all_data = load_all_data_db(start_date=start_date, end_date=end_date, limit_to_csi300=limit_to_csi300)
    print(f"[Optimizer] 数据预加载完毕，共 {len(all_data)} 只股票。开始高速内存网格搜索...\n", flush=True)

    results = []

    for idx, combo in enumerate(combos, 1):
        test_config = dict(base_config)
        test_config['tech'] = {
            'buy_di_threshold': combo['buy_di_threshold'],
            'sell_di_threshold': 0.70,
            'short_ma': 5,
            'long_ma': 20,
            'volume_surge_factor': combo['vol_surge_factor'],
            'enable_golden_cross': True,
            'enable_volume_surge': True,
            'enable_di_ratio': True
        }
        test_config['financial'] = {
            'enable_roe': combo['enable_roe'],
            'roe_min': combo['roe_min'],
            'enable_pe': False,
            'enable_growth': combo['enable_growth'],
            'growth_min': combo['growth_min'],
            'enable_debt_limit': False,
            'enable_cash_quality': False
        }
        test_config['ranking'] = {
            'primary_factor': combo['primary_factor'],
            'reverse': (combo['primary_factor'] in ['roe', 'growth'])
        }

        # Run fast in-memory backtest iteration
        res = run_backtest_engine(
            config=test_config,
            start_date_str=start_date,
            end_date_str=end_date,
            strategy_name="dynamic_factor",
            universe=universe,
            save_to_db=False,
            all_data=all_data
        )

        if res.get('status') == 'success':
            tot_ret = res['total_return']
            max_dd = res['max_drawdown']
            win_rate = res['win_rate']
            ret_dd_ratio = abs(tot_ret / max_dd) if max_dd != 0 else 0.0

            results.append({
                'combo_id': idx,
                'di_threshold': combo['buy_di_threshold'],
                'vol_factor': combo['vol_surge_factor'],
                'enable_roe': combo['enable_roe'],
                'roe_min': combo['roe_min'] if combo['enable_roe'] else 0.0,
                'enable_growth': combo['enable_growth'],
                'growth_min': combo['growth_min'] if combo['enable_growth'] else 0.0,
                'rank_by': combo['primary_factor'],
                'total_return_%': tot_ret,
                'max_drawdown_%': max_dd,
                'win_rate_%': win_rate,
                'ret_dd_ratio': round(ret_dd_ratio, 2),
                'trades': res['total_buys'] + res['total_sells']
            })

            wr_show = f"{win_rate:>5.1f}%" if win_rate is not None else "    -%"
            print(f"  [Progress {idx:>2}/{len(combos)}] 收益: {tot_ret:>6.1f}% | 回撤: {max_dd:>5.1f}% | 胜率: {wr_show} | 排序: {combo['primary_factor']}", flush=True)

    res_df = pd.DataFrame(results)
    if not res_df.empty:
        res_df.sort_values(by='total_return_%', ascending=False, inplace=True)
        top_df = res_df.head(top_n)

        print("\n" + "=" * 85)
        print(f"  [RESULT] 最优因子组合 TOP {top_n} 排行榜 (按累计收益率排序)")
        print("=" * 85)
        print(top_df.to_string(index=False))
        print("=" * 85)
        return res_df

    return pd.DataFrame()


if __name__ == '__main__':
    optimize_factors(start_date="2024-01-01", end_date="2026-06-30", universe="csi300", top_n=10)
