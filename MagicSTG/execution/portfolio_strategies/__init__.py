# -*- coding: utf-8 -*-
"""
MagicSTG Portfolio & Position Management Strategy Package
"""

from MagicSTG.execution.portfolio_strategies.base import (
    BasePortfolioStrategy,
    is_convertible_bond,
    calc_trade_cost_for_security
)
from MagicSTG.execution.portfolio_strategies.equal_slot import EqualSlotPortfolioStrategy
from MagicSTG.execution.portfolio_strategies.registry import PortfolioStrategyRegistry

__all__ = [
    'BasePortfolioStrategy',
    'EqualSlotPortfolioStrategy',
    'PortfolioStrategyRegistry',
    'is_convertible_bond',
    'calc_trade_cost_for_security'
]
