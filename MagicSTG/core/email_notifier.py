# -*- coding: utf-8 -*-
"""
MagicSTG Email Notifier Module
Sends rich HTML daily quantitative stock & bond recommendation reports via SMTP.
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Dict, List, Any, Optional

from MagicSTG.core.db import get_connection, ensure_connection_alive


def send_daily_summary_email(target_date: Optional[str] = None) -> bool:
    """
    发送每日盘后量化选股推荐 HTML 日报邮件
    从环境变量读取 SMTP 配置：
      - SMTP_SERVER (如 smtp.qq.com)
      - SMTP_PORT (如 465 或 587)
      - SMTP_USER (发件人邮箱)
      - SMTP_PASSWORD (授权码/密码)
      - NOTIFY_EMAIL (收件人邮箱，默认同 SMTP_USER)
    """
    smtp_server = os.getenv("SMTP_SERVER", "").strip()
    smtp_port = int(os.getenv("SMTP_PORT", "465").strip())
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    receiver_email = os.getenv("NOTIFY_EMAIL", smtp_user).strip()

    if not smtp_server or not smtp_user or not smtp_password:
        print("[EmailNotifier] ℹ️ 未配置完整 SMTP 环境变量 (SMTP_SERVER/SMTP_USER/SMTP_PASSWORD)，跳过邮件发送。", flush=True)
        return False

    if not target_date:
        target_date = datetime.now().strftime("%Y-%m-%d")

    # 从 TiDB 查询当天各策略发出的最新推荐记录
    conn = None
    records = []
    stg_names = {}
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute("SELECT strategy_id, name FROM custom_strategies")
            for r in cursor.fetchall():
                stg_names[r[0]] = r[1]

            cursor.execute("""
                SELECT strategy, stock_code, action, price, reason, factor_data, created_at
                FROM recommendations
                WHERE signal_date = %s
                ORDER BY strategy ASC, id ASC
            """, (target_date,))
            rows = cursor.fetchall()

            import json
            for r in rows:
                f_data = r[5]
                if isinstance(f_data, str):
                    try:
                        f_data = json.loads(f_data)
                    except Exception:
                        f_data = {}

                records.append({
                    'strategy': r[0],
                    'strategy_name': stg_names.get(r[0], r[0]),
                    'stock_code': r[1],
                    'action': r[2],
                    'price': float(r[3]) if r[3] is not None else 0.0,
                    'reason': r[4] or '',
                    'factor_data': f_data or {}
                })
    except Exception as e:
        print(f"[EmailNotifier] ⚠️ 从数据库读取推荐结果失败: {e}", flush=True)
    finally:
        if conn:
            conn.close()

    # 组装 HTML 邮件模板
    msg = MIMEMultipart("alternative")
    total_count = len(records)
    msg["Subject"] = f"📈 [MagicSTG] {target_date} 盘后选股推荐报告 (共 {total_count} 条信号)"
    msg["From"] = f"MagicSTG Quant <{smtp_user}>"
    msg["To"] = receiver_email

    # 策略分组
    stg_grouped: Dict[str, List[Dict[str, Any]]] = {}
    for rec in records:
        stg_name = rec['strategy_name']
        if stg_name not in stg_grouped:
            stg_grouped[stg_name] = []
        stg_grouped[stg_name].append(rec)

    # 构建 HTML 表格
    html_sections = ""
    if not stg_grouped:
        html_sections = """
        <div style="background: #f8d7da; color: #721c24; padding: 15px; border-radius: 8px; margin: 20px 0;">
            ⚠️ 目标行情日无策略推荐输出或数据未达买入阈值。
        </div>
        """
    else:
        for s_name, rec_list in stg_grouped.items():
            rows_html = ""
            for idx, item in enumerate(rec_list, 1):
                action_bg = "#28a745" if item['action'] == 'BUY' else "#dc3545"
                action_text = "买入" if item['action'] == 'BUY' else "卖出"
                
                rows_html += f"""
                <tr style="border-bottom: 1px solid #eef2f7;">
                    <td style="padding: 10px; font-weight: bold; color: #1e293b;">{idx}</td>
                    <td style="padding: 10px; font-family: monospace; color: #0f766e; font-weight: bold;">{item['stock_code']}</td>
                    <td style="padding: 10px;"><span style="background: {action_bg}; color: #ffffff; padding: 3px 8px; border-radius: 4px; font-size: 12px;">{action_text}</span></td>
                    <td style="padding: 10px; font-weight: bold; color: #0284c7;">¥{item['price']:.2f}</td>
                    <td style="padding: 10px; font-size: 13px; color: #475569;">{item['reason']}</td>
                </tr>
                """

            html_sections += f"""
            <div style="margin-bottom: 25px; border: 1px solid #e2e8f0; border-radius: 10px; overflow: hidden; background: #ffffff; box-shadow: 0 2px 4px rgba(0,0,0,0.03);">
                <div style="background: #0f172a; color: #38bdf8; padding: 12px 18px; font-weight: bold; font-size: 15px; display: flex; justify-content: space-between;">
                    <span>📌 {s_name}</span>
                    <span style="font-size: 13px; color: #94a3b8;">({len(rec_list)} 只标的)</span>
                </div>
                <table style="width: 100%; border-collapse: collapse; text-align: left; font-size: 14px;">
                    <thead>
                        <tr style="background: #f1f5f9; color: #64748b; font-size: 12px; text-transform: uppercase;">
                            <th style="padding: 10px;">#</th>
                            <th style="padding: 10px;">标的代码</th>
                            <th style="padding: 10px;">动作</th>
                            <th style="padding: 10px;">参考价格</th>
                            <th style="padding: 10px;">推荐原因 / 因子指标</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows_html}
                    </tbody>
                </table>
            </div>
            """

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 20px; color: #334155;">
        <div style="max-width: 680px; margin: 0 auto; background: #ffffff; border-radius: 12px; padding: 25px; border: 1px solid #e2e8f0; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);">
            <div style="border-bottom: 2px solid #3b82f6; padding-bottom: 15px; margin-bottom: 20px;">
                <h2 style="margin: 0; color: #1e3a8a; font-size: 22px;">🚀 MagicSTG 量化盘后推荐日报</h2>
                <p style="margin: 5px 0 0 0; color: #64748b; font-size: 13px;">行情目标日: <strong>{target_date}</strong> &nbsp;|&nbsp; 自动推演生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            </div>

            <div style="background: #eff6ff; border-left: 4px solid #3b82f6; padding: 12px 15px; border-radius: 4px; margin-bottom: 20px; font-size: 14px; color: #1e40af;">
                📊 <strong>每日盘后概览：</strong> 本次工作流共自动运行 <strong>{len(stg_grouped)}</strong> 个激活因子策略，累计甄选出 <strong>{total_count}</strong> 条优质买卖候选推荐。
            </div>

            {html_sections}

            <div style="border-top: 1px solid #e2e8f0; margin-top: 30px; padding-top: 15px; text-align: center; font-size: 12px; color: #94a3b8;">
                MagicSTG Stock & Convertible Bond Quantitative System · 自动选股报告
            </div>
        </div>
    </body>
    </html>
    """

    msg.attach(MIMEText(html_content, "html", "utf-8"))

    try:
        print(f"[EmailNotifier] 📧 正在发送选股推荐日报邮件至 {receiver_email}...", flush=True)
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=15)
        else:
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=15)
            server.starttls()
        
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, [receiver_email], msg.as_string())
        server.quit()
        print("[EmailNotifier] 🎉 邮件发送成功！", flush=True)
        return True
    except Exception as e:
        print(f"[EmailNotifier] ❌ 邮件发送失败: {e}", flush=True)
        return False
