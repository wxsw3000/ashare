import os
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
UPDATE_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), 'update')

TARGET_FILES = [
    'update_index_kline_day.py',
    'update_stock_kline_weekly.py',
    'update_stock_kline_monthly.py',
    'update_stock_profit_quarterly.py',
    'update_stock_growth_quarterly.py',
    'update_stock_balance_quarterly.py',
    'update_stock_cash_flow_quarterly.py',
    'update_stock_dupont_quarterly.py',
    'update_stock_performance_express.py',
    'update_stock_forecast.py'
]

for filename in TARGET_FILES:
    filepath = os.path.join(UPDATE_DIR, filename)
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        continue
        
    print(f"Processing {filename}...")
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
        
    # 1. 优化导入
    if 'get_progress_prefix' not in content:
        if 'print_progress,' in content:
            content = content.replace('print_progress,', 'print_progress,\n    get_progress_prefix,')
        elif 'print_progress' in content:
            content = content.replace('print_progress', 'print_progress,\n    get_progress_prefix')
            
    # 2. 寻找 total 变量名
    total_var = "total_stocks"
    if 'total_indices' in content:
        total_var = "total_indices"
        
    # 3. 寻找循环起点并注入 prefix = get_progress_prefix(...)
    if 'stock_start_time = time.time()' in content:
        old_pattern = 'stock_start_time = time.time()'
        new_pattern = f'stock_start_time = time.time()\n            prefix = get_progress_prefix(idx, {total_var}, start_time)'
        if new_pattern not in content:
            content = content.replace(old_pattern, new_pattern)
        
    # 4. 替换单股详细打印语句
    # 匹配 print(f"  {code} | 
    content = content.replace('print(f"  {code} |', 'print(f"  {prefix} {code} |')
    # 匹配 print(f"  {stock['code']} | 
    content = content.replace("print(f\"  {stock['code']} |", "print(f\"  {prefix} {stock['code']} |")
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
        
print("All target files processed successfully!")
