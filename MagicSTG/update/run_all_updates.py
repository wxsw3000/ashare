# -*- coding: utf-8 -*-
"""
Compatibility Forwarder for GitHub Actions
Forwards execution to MagicSTG.data.runner
"""

import os
import sys

# Add project root (directory containing MagicSTG package) to sys.path
MAGICSTG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(MAGICSTG_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from MagicSTG.data.runner import main

if __name__ == '__main__':
    main()