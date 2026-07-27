# -*- coding: utf-8 -*-
"""
MagicSTG Strategy Registry & Factory
Allows dynamic registration and instantiation of strategies.
"""

from typing import Dict, Type, Optional, Any
from MagicSTG.strategies.base import BaseStrategy
from MagicSTG.strategies.dynamic_factor import DynamicFactorStrategy


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
        if name not in cls._registry:
            # Fallback to dynamic factor if strategy name is dynamic or default
            if name in ['dynamic', 'dynamic_factor', 'default']:
                return DynamicFactorStrategy(name="dynamic_factor", config=config)
            raise ValueError(f"Strategy '{name}' is not registered. Registered strategies: {list(cls._registry.keys())}")

        return cls._registry[name](name=name, config=config)

    @classmethod
    def list_strategies(cls) -> List[str]:
        """Returns list of registered strategy names."""
        return list(cls._registry.keys())


# Automatically register default strategies
StrategyRegistry.register('dynamic_factor', DynamicFactorStrategy)
StrategyRegistry.register('dynamic', DynamicFactorStrategy)
