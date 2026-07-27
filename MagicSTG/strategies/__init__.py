# -*- coding: utf-8 -*-
from MagicSTG.strategies.base import BaseStrategy
from MagicSTG.strategies.dynamic_factor import DynamicFactorStrategy, SignalGeneratorDynamic
from MagicSTG.strategies.registry import StrategyRegistry

__all__ = [
    'BaseStrategy',
    'DynamicFactorStrategy',
    'SignalGeneratorDynamic',
    'StrategyRegistry'
]
