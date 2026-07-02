import os
import sys
import time

# Ensure MagicSTG is in path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.db import load_all_data_db, load_roe_data_db

print("Starting DB Loader tests...")

# 1. Test loading latest 250 days (Daily Run mode)
t0 = time.time()
all_data = load_all_data_db(limit_days=250)
t1 = time.time()
print(f"Daily mode loading took {t1 - t0:.2f} seconds.")
print(f"Loaded {len(all_data)} stocks.")
if all_data:
    first_code = list(all_data.keys())[0]
    print(f"Sample data for {first_code}:")
    print(all_data[first_code].tail(3))
    print(f"Columns: {all_data[first_code].columns.tolist()}")

print("=" * 60)

# 2. Test loading ROE data
t0 = time.time()
roe_data = load_roe_data_db()
t1 = time.time()
print(f"ROE loading took {t1 - t0:.2f} seconds.")
print(f"Loaded {len(roe_data)} stocks with ROE.")
if roe_data:
    first_code = list(roe_data.keys())[0]
    print(f"Sample ROE data for {first_code}:")
    print(roe_data[first_code].tail(3))
    print(f"Columns: {roe_data[first_code].columns.tolist()}")
