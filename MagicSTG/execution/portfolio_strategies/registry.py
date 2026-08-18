# -*- coding: utf-8 -*-
"""
MagicSTG Portfolio Strategy Registry
Factory for looking up and instantiating portfolio & position management strategies.
"""

from typing import Dict, Type, Optional, Any
from MagicSTG.execution.portfolio_strategies.base import BasePortfolioStrategy
from MagicSTG.execution.portfolio_strategies.equal_slot import EqualSlotPortfolioStrategy


class PortfolioStrategyRegistry:
    """Registry factory for portfolio strategies."""

    _registry: Dict[str, Type[BasePortfolioStrategy]] = {
        'equal_slot': EqualSlotPortfolioStrategy,
        'slot_strategy': EqualSlotPortfolioStrategy,
        'slot': EqualSlotPortfolioStrategy,
        'default': EqualSlotPortfolioStrategy,
    }

    @classmethod
    def register(cls, name: str, strategy_cls: Type[BasePortfolioStrategy]):
        """Registers a new portfolio strategy implementation."""
        cls._registry[name.lower()] = strategy_cls

    @classmethod
    def get(cls, name_or_config: Optional[Any] = None, config: Optional[dict] = None) -> BasePortfolioStrategy:
        """
        Instantiates a portfolio strategy from a name string or a configuration dictionary.
        
        Examples:
            stg = PortfolioStrategyRegistry.get('equal_slot', {'initial_capital': 100000})
            stg = PortfolioStrategyRegistry.get({'portfolio_strategy': 'equal_slot', 'max_holdings': 5})
        """
        cfg = {}
        stg_name = 'equal_slot'

        if isinstance(name_or_config, str):
            stg_name = name_or_config
            cfg = config or {}
        elif isinstance(name_or_config, dict):
            cfg = name_or_config
            p_cfg = cfg.get('portfolio_config', cfg)
            stg_name = p_cfg.get('portfolio_strategy') or p_cfg.get('strategy_type') or 'equal_slot'
        elif config and isinstance(config, dict):
            cfg = config
            p_cfg = cfg.get('portfolio_config', cfg)
            stg_name = p_cfg.get('portfolio_strategy') or p_cfg.get('strategy_type') or 'equal_slot'

        stg_name_clean = str(stg_name).lower()
        strategy_cls = cls._registry.get(stg_name_clean)

        if not strategy_cls:
            print(f"[PortfolioStrategyRegistry] Notice: Strategy '{stg_name}' not found. Falling back to EqualSlotPortfolioStrategy.")
            strategy_cls = EqualSlotPortfolioStrategy

        return strategy_cls(name=stg_name_clean, config=cfg)
