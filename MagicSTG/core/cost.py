# -*- coding: utf-8 -*-
"""
MagicSTG Core Transaction Fee Calculator
Calculates buying and selling costs based on config parameters.
"""

from MagicSTG.config import load_yaml_config

CONFIG = load_yaml_config()
COMMISSION_RATE = CONFIG.get('fee', {}).get('commission_rate', 0.00025)
MIN_COMMISSION = CONFIG.get('fee', {}).get('min_commission', 5.0)
STAMP_TAX_RATE = CONFIG.get('fee', {}).get('stamp_tax_rate', 0.0005)


def calc_buy_cost(amount: float) -> float:
    """Calculates buying commission cost."""
    return max(amount * COMMISSION_RATE, MIN_COMMISSION)


def calc_sell_cost(amount: float) -> float:
    """Calculates selling total fee (commission + stamp tax)."""
    commission = max(amount * COMMISSION_RATE, MIN_COMMISSION)
    stamp_tax = amount * STAMP_TAX_RATE
    return commission + stamp_tax


def calc_net_sell(amount: float) -> float:
    """Calculates net revenue from selling after deducting fees."""
    return amount - calc_sell_cost(amount)
