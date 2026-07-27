# -*- coding: utf-8 -*-
"""
MagicSTG Strategy Abstract Base Class
Defines standard interface for all quantitative strategy implementations.
"""

from abc import ABC, abstractmethod
import pandas as pd
from typing import Dict, List, Tuple, Any, Optional


class BaseStrategy(ABC):
    """
    Abstract Base Class for quantitative strategies.
    All custom factor strategies or AI strategies should inherit from this class.
    """

    def __init__(self, name: str, config: Optional[Dict[str, Any]] = None):
        self.name = name
        self.config = config or {}

    @abstractmethod
    def generate_signals(self, date: pd.Timestamp, data: Dict[str, pd.DataFrame]) -> Tuple[List[Tuple], List[Tuple]]:
        """
        Generate buy and sell signals for a specific date.

        Parameters:
            date: Trading date timestamp
            data: Dictionary mapping stock_code -> K-line / Financial DataFrame

        Returns:
            Tuple of (buy_signals, sell_signals)
            where buy_signals / sell_signals are lists of tuples (stock_code, price, reason/extra)
        """
        pass

    def update_config(self, new_config: Dict[str, Any]):
        """Dynamically update strategy parameters."""
        self.config.update(new_config)
