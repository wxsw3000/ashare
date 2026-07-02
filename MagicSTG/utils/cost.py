# ============ 交易费用计算 ============
import os
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_config():
    config_path = os.path.join(PROJECT_ROOT, 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

CONFIG = load_config()
COMMISSION_RATE = CONFIG['fee']['commission_rate']
MIN_COMMISSION = CONFIG['fee']['min_commission']
STAMP_TAX_RATE = CONFIG['fee']['stamp_tax_rate']

def calc_buy_cost(amount: float) -> float:
    return max(amount * COMMISSION_RATE, MIN_COMMISSION)

def calc_sell_cost(amount: float) -> float:
    commission = max(amount * COMMISSION_RATE, MIN_COMMISSION)
    stamp_tax = amount * STAMP_TAX_RATE
    return commission + stamp_tax

def calc_net_sell(amount: float) -> float:
    return amount - calc_sell_cost(amount)