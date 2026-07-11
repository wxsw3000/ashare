"""
测试 GitHub Actions 定时触发精度的脚本
只记录时间，不做任何实际数据操作
"""
import datetime
import time
import os

def main():
    # 获取当前 UTC 时间
    utc_now = datetime.datetime.utcnow()
    # 获取北京时间 (UTC+8)
    beijing_now = utc_now + datetime.timedelta(hours=8)
    
    # 获取 GitHub Actions 预置的环境变量（如果有的话）
    github_event = os.getenv('GITHUB_EVENT_NAME', 'unknown')
    github_run_id = os.getenv('GITHUB_RUN_ID', 'unknown')
    github_run_number = os.getenv('GITHUB_RUN_NUMBER', 'unknown')
    
    print("=" * 60)
    print("🧪 GitHub Actions 定时触发精度测试")
    print("=" * 60)
    print(f"触发事件类型 (GITHUB_EVENT_NAME): {github_event}")
    print(f"运行ID (GITHUB_RUN_ID): {github_run_id}")
    print(f"运行编号 (GITHUB_RUN_NUMBER): {github_run_number}")
    print("-" * 60)
    print(f"当前 UTC 时间:   {utc_now.strftime('%Y-%m-%d %H:%M:%S')} (UTC)")
    print(f"当前北京时间:    {beijing_now.strftime('%Y-%m-%d %H:%M:%S')} (UTC+8)")
    print("-" * 60)
    print("✅ 如果这个时间与你设定的定时时间相差很大，")
    print("   说明 GitHub Actions 的 schedule 存在明显延迟。")
    print("=" * 60)
    
    # 记录到文件，方便以后查看
    with open('test_time_result.txt', 'w', encoding='utf-8') as f:
        f.write(f"触发时间(UTC): {utc_now.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"触发时间(北京): {beijing_now.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"事件类型: {github_event}\n")
        f.write(f"运行ID: {github_run_id}\n")
        f.write(f"运行编号: {github_run_number}\n")

if __name__ == "__main__":
    main()