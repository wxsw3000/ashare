import sys
sys.path.insert(0, 'E:/my_big_A/myproject')

import os
import pandas as pd
from run_backtest import load_config, load_all_data
from signals.generator_price import SignalGenerator

config = load_config()
all_data = load_all_data(config['paths']['data_dir'])
generator = SignalGenerator(config)

date = pd.Timestamp('2026-02-24')
buy_candidates, sell_signals = generator.get_signals(all_data, date)
print("Buy Candidates from Generator on 2026-02-24:")
print(buy_candidates)
