# -*- coding: utf-8 -*-
"""
MagicSTG Strategy Registry & Factory
Allows dynamic registration and instantiation of strategies.
"""

from typing import Dict, Type, Optional, Any, List
from MagicSTG.strategies.base import BaseStrategy
from MagicSTG.strategies.dynamic_factor import DynamicFactorStrategy
from MagicSTG.strategies.cb_double_low import CBDoubleLowStrategy


class StrategyRegistry:
    """
    Registry for strategy classes.
    """
    _registry: Dict[str, Type[BaseStrategy]] = {}

    @classmethod
    def register(cls, name: str, strategy_cls: Type[BaseStrategy]):
        """Registers a new strategy class under a specific name."""
        cls._registry[name] = strategy_cls

    @classmethod
    def get(cls, name: str, config: Optional[Dict[str, Any]] = None) -> BaseStrategy:
        """Instantiates and returns a registered strategy by name."""
        if name in cls._registry:
            return cls._registry[name](name=name, config=config)

        # Fallback for convertible bond strategies
        if name == 'cb_double_low' or name.startswith('cb_') or (config and config.get('category') == 'convertible_bond'):
            return CBDoubleLowStrategy(name=name, config=config)

        # Default fallback for stock multi-factor strategies
        return DynamicFactorStrategy(name=name, config=config)

    @classmethod
    def list_strategies(cls) -> List[str]:
        """Returns list of registered strategy names."""
        return list(cls._registry.keys())


# Automatically register default strategies
StrategyRegistry.register('dynamic_factor', DynamicFactorStrategy)
StrategyRegistry.register('dynamic', DynamicFactorStrategy)
StrategyRegistry.register('pe_strategy', DynamicFactorStrategy)
StrategyRegistry.register('roe_strategy', DynamicFactorStrategy)
StrategyRegistry.register('price_strategy', DynamicFactorStrategy)
StrategyRegistry.register('cb_double_low', CBDoubleLowStrategy)
StrategyRegistry.register('cb_double_low_strategy', CBDoubleLowStrategy)
