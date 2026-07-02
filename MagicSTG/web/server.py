import os
import re
import sys
import json
import pymysql
import pandas as pd
from flask import Flask, jsonify, send_from_directory, render_template_string

# Add parent directory to sys.path so we can import utils.db
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.db import get_connection

app = Flask(__name__)
WEB_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(PROJECT_ROOT, 'reports')
POSITIONS_DIR = os.path.join(PROJECT_ROOT, 'positions')

def get_latest_prices():
    """Fetch the latest close price for all stocks from TiDB Cloud."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Get the latest date
            cur.execute("SELECT MAX(date) FROM stock_kline")
            latest_date = cur.fetchone()[0]
            if not latest_date:
                return {}
            
            # Fetch all prices for that date
            cur.execute("SELECT stock_code, close FROM stock_kline WHERE date = %s", (latest_date,))
            rows = cur.fetchall()
            
            # Convert sh_600000 -> sh.600000
            prices = {row[0].replace('_', '.'): float(row[1]) for row in rows}
            return prices
    except Exception as e:
        print(f"Error fetching latest prices: {e}")
        return {}
    finally:
        conn.close()

def parse_backtest_report(file_path):
    """Parse key performance metrics from a backtest txt report."""
    if not os.path.exists(file_path):
        return None
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
        
    metrics = {
        "filename": os.path.basename(file_path),
        "strategy": "Unknown",
        "date_range": "Unknown",
        "initial_equity": 0.0,
        "final_equity": 0.0,
        "total_return": 0.0,
        "annual_return": 0.0,
        "max_drawdown": 0.0,
        "total_buys": 0,
        "total_sells": 0,
        "win_rate": 0.0,
        "total_fees": 0.0
    }
    
    # Extract Strategy Name and Date Range
    match_strategy = re.search(r"量化回测分析报告.*-\s*(.*)", content)
    if match_strategy:
        metrics["strategy"] = match_strategy.group(1).strip()
        
    match_range = re.search(r"统计区间:\s*(.*)", content)
    if match_range:
        metrics["date_range"] = match_range.group(1).strip()
        
    # Extract numeric values
    def search_float(pattern, text):
        match = re.search(pattern, text)
        return float(match.group(1).replace(',', '')) if match else 0.0
        
    def search_int(pattern, text):
        match = re.search(pattern, text)
        return int(match.group(1)) if match else 0
        
    metrics["initial_equity"] = search_float(r"1\.\s*初始资金:\s*([\d,.]+)\s*元", content)
    metrics["final_equity"] = search_float(r"2\.\s*期末总净值:\s*([\d,.]+)\s*元", content)
    metrics["total_return"] = search_float(r"3\.\s*累计收益率:\s*(-?[\d,.]+)%", content)
    metrics["annual_return"] = search_float(r"4\.\s*年化收益率:\s*(-?[\d,.]+)%", content)
    metrics["max_drawdown"] = search_float(r"5\.\s*历史最大回撤:\s*(-?[\d,.]+)%", content)
    metrics["total_buys"] = search_int(r"6\.\s*总计买入笔数:\s*(\d+)", content)
    metrics["total_sells"] = search_int(r"7\.\s*已平仓笔数:\s*(\d+)", content)
    metrics["win_rate"] = search_float(r"8\.\s*交易胜率:\s*([\d,.]+)%", content)
    metrics["total_fees"] = search_float(r"9\.\s*累计交易税费:\s*([\d,.]+)\s*元", content)
    
    return metrics

@app.route('/')
def index():
    """Serve the dashboard page."""
    html_path = os.path.join(WEB_DIR, 'index.html')
    if os.path.exists(html_path):
        with open(html_path, 'r', encoding='utf-8') as f:
            return render_template_string(f.read())
    return "<h3>Error: index.html not found.</h3>"

@app.route('/api/positions')
def api_positions():
    """Get active holdings and cash details for price and pe strategies."""
    strategies = ['price', 'pe']
    result = {}
    
    latest_prices = get_latest_prices()
    
    # Load configuration
    config_path = os.path.join(PROJECT_ROOT, 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        import yaml
        config = yaml.safe_load(f)
        
    per_stock_capital = config['strategy']['per_stock_capital']
    max_holdings = config['strategy']['max_holdings']
    
    for stg in strategies:
        pos_file = os.path.join(POSITIONS_DIR, f'position_{stg}.csv')
        cash_file = os.path.join(POSITIONS_DIR, f'position_{stg}_cash.json')
        
        positions = []
        slot_cash = [float(per_stock_capital)] * max_holdings
        cash_remains = {}
        realized_pnl = 0.0
        
        # Load cash
        if os.path.exists(cash_file):
            try:
                with open(cash_file, 'r', encoding='utf-8') as f:
                    cash_data = json.load(f)
                    slot_cash = [float(x) for x in cash_data.get('slot_cash', [])]
            except Exception:
                pass
                
        # Load positions
        if os.path.exists(pos_file):
            try:
                df = pd.read_csv(pos_file, encoding='utf-8')
                for _, row in df.iterrows():
                    code = str(row['code'])
                    buy_price = float(row['buy_price'])
                    shares = int(row['shares'])
                    cost_total = float(row['cost_total'])
                    highest_price = float(row['highest_price'])
                    slot_idx = int(row.get('slot_idx', -1))
                    
                    cur_price = latest_prices.get(code, buy_price)
                    market_val = shares * cur_price
                    pnl = market_val - cost_total
                    pnl_pct = (pnl / cost_total * 100) if cost_total > 0 else 0.0
                    
                    positions.append({
                        "code": code,
                        "buy_date": str(row['buy_date']),
                        "buy_price": buy_price,
                        "shares": shares,
                        "cost_total": cost_total,
                        "highest_price": highest_price,
                        "current_price": cur_price,
                        "market_value": market_val,
                        "pnl": pnl,
                        "pnl_pct": pnl_pct,
                        "slot_idx": slot_idx
                    })
                    if slot_idx >= 0 and slot_idx < len(slot_cash):
                        cash_remains[code] = slot_cash[slot_idx]
            except Exception as e:
                print(f"Error reading position file {pos_file}: {e}")
                
        # Calculate totals
        active_codes = [p['code'] for p in positions]
        idle_slots = max_holdings - len(positions)
        total_market_value = sum(p['market_value'] for p in positions)
        
        # Total Cash
        total_cash = sum(cash_remains.values()) + idle_slots * per_stock_capital
        total_equity = total_cash + total_market_value
        total_pnl = total_equity - (max_holdings * per_stock_capital)
        total_pnl_pct = (total_pnl / (max_holdings * per_stock_capital)) * 100
        
        result[stg] = {
            "strategy_name": "价格优先策略" if stg == 'price' else "PE优先策略",
            "positions": positions,
            "cash_details": {
                "total_cash": total_cash,
                "slot_cash": slot_cash,
                "idle_slots": idle_slots
            },
            "summary": {
                "initial_capital": max_holdings * per_stock_capital,
                "current_equity": total_equity,
                "total_market_value": total_market_value,
                "total_pnl": total_pnl,
                "total_pnl_pct": total_pnl_pct,
                "holding_count": len(positions),
                "max_holdings": max_holdings
            }
        }
    return jsonify(result)

@app.route('/api/suggestions')
def api_suggestions():
    """Get the latest suggestions for price and pe strategies."""
    strategies = ['price', 'pe']
    result = {}
    
    for stg in strategies:
        sug_file = os.path.join(REPORTS_DIR, f'suggestions_{stg}.csv')
        sug_list = []
        if os.path.exists(sug_file):
            try:
                df = pd.read_csv(sug_file, encoding='utf-8')
                # Filter for the latest date in the CSV
                if not df.empty:
                    latest_date = df['date'].max()
                    df_latest = df[df['date'] == latest_date]
                    for _, row in df_latest.iterrows():
                        sug_list.append({
                            "date": str(row['date']),
                            "action": str(row['action']),
                            "code": str(row['code']),
                            "price": float(row['price']),
                            "shares": int(row['shares']),
                            "reason": str(row['reason'])
                        })
            except Exception as e:
                print(f"Error reading suggestions {sug_file}: {e}")
        result[stg] = sug_list
        
    return jsonify(result)

@app.route('/api/backtests')
def api_backtests():
    """List and parse all backtest txt reports."""
    reports = []
    if os.path.exists(REPORTS_DIR):
        for f in os.listdir(REPORTS_DIR):
            if f.startswith('backtest_') and f.endswith('.txt'):
                file_path = os.path.join(REPORTS_DIR, f)
                metrics = parse_backtest_report(file_path)
                if metrics:
                    reports.append(metrics)
                    
    # Sort by filename descending
    reports.sort(key=lambda x: x['filename'], reverse=True)
    return jsonify(reports)

@app.route('/api/backtest/trades/<strategy>')
def api_backtest_trades(strategy):
    """Get trade logs for the specified strategy from CSV."""
    if strategy not in ['price', 'pe', 'roe']:
        return jsonify({"error": "Invalid strategy"}), 400
        
    csv_path = os.path.join(REPORTS_DIR, f'backtest_{strategy}_trades.csv')
    if not os.path.exists(csv_path):
        return jsonify({"error": "Backtest trade file not found"}), 404
        
    try:
        df = pd.read_csv(csv_path, encoding='utf-8-sig')
        # Fill NaN values to prevent JSON errors
        df = df.fillna("")
        trades = df.to_dict(orient='records')
        return jsonify(trades)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    print(f"Starting server on http://127.0.0.1:5000")
    app.run(host='127.0.0.1', port=5000, debug=True)
