# -*- coding: utf-8 -*-
"""
MagicSTG CLI Backtest Forwarder
Forwards execution to MagicSTG.backtests.runner.main()
"""

import os
import sys

MAGICSTG_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(MAGICSTG_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from MagicSTG.backtests.runner import main

if __name__ == '__main__':
    main()
