# -*- coding: utf-8 -*-
"""
MagicSTG Base Portfolio Strategy Interface
Defines abstract base contract and common utilities for portfolio & position management strategies.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Tuple
import math

from MagicSTG.core.cost import calc_buy_cost, calc_sell_cost


def is_convertible_bond(code: str) -> bool:
    """Checks if security code is a convertible bond."""
    if not code:
        return False
    clean_code = str(code).lower()
    return clean_code.startswith('sh.11') or clean_code.startswith('sz.12') or clean_code.startswith('11') or clean_code.startswith('12')


def calc_trade_cost_for_security(code: str, price: float, shares_or_bonds: int, is_buy: bool) -> Tuple[float, float]:
    """
    Calculates friction fee and total amount for stocks or convertible bonds.
    Returns (fee, total_cost_if_buy_or_net_proceeds_if_sell).
    """
    gross_amount = price * shares_or_bonds
    if is_convertible_bond(code):
        # CB Commission: 0.005%, No min limit, 0% stamp tax
        fee = gross_amount * 0.00005
        if is_buy:
            return fee, gross_amount + fee
        else:
            return fee, gross_amount - fee
    else:
        # Stock: 0.025% commission (min 5 CNY rule) + 0.05% sell stamp tax
        if is_buy:
            fee = calc_buy_cost(gross_amount)
            return fee, gross_amount + fee
        else:
            fee = calc_sell_cost(gross_amount)
            return fee, gross_amount - fee


class BasePortfolioStrategy(ABC):
    """
    Abstract base class for all portfolio & position management strategies.
    
    A portfolio strategy dictates:
    1. How capital is partitioned or allocated across active positions.
    2. How risk controls (stop-loss, trailing stop-profit, holding limits) are enforced.
    3. How recommendation signals are converted into trades and order sizes.
    4. Execution timing (T+1 morning open vs T close).
    """

    def __init__(self, name: str, config: Optional[dict] = None):
        self.name = name
        self.config = config or {}
        stg_cfg = {}
        if self.config:
            stg_cfg = self.config.get('portfolio_config') or self.config.get('strategy') or self.config
        self.initial_capital = float(stg_cfg.get('initial_capital', 100000.0))
        self.max_holdings = int(stg_cfg.get('max_holdings', 5))
        self.execution_timing = stg_cfg.get('execution_timing', 'T+1_OPEN')  # 'T+1_OPEN' or 'T_CLOSE'
        self.slippage = float(stg_cfg.get('slippage', 0.001 if self.execution_timing == 'T+1_OPEN' else 0.0))

    @abstractmethod
    def reset(self):
        """Resets all internal strategy state prior to running a full simulation."""
        pass

    @abstractmethod
    def evaluate_day(
        self,
        d_str: str,
        day_recs: Dict[str, List[dict]],
        price_map: Dict[Tuple[str, str], Dict[str, float]],
        name_map: Dict[str, str],
        active_positions: Dict[int, dict],
        slot_cash: List[float],
        pending_orders: Optional[List[dict]] = None
    ) -> Tuple[Dict[int, dict], List[dict], List[float], List[dict]]:
        """
        Evaluates position management logic for a single trading day.

        Args:
            d_str: Trading date YYYY-MM-DD.
            day_recs: Today's post-market recommendation signals {'BUY': [...], 'SELL': [...]}.
            price_map: Price lookup (code, date) -> {'close': p, 'open': p, 'high': p, 'low': p}.
            name_map: Code to name lookup.
            active_positions: Current active positions dict (slot_idx -> position dict).
            slot_cash: List of cash available in each slot.
            pending_orders: Optional queued signals from previous day (used for T+1 execution).

        Returns:
            Tuple of:
            (updated_active_positions, closed_trades_today, updated_slot_cash, new_pending_orders)
        """
        pass
