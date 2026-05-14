import logging
import sqlite3
import json
from datetime import datetime, timedelta
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import re
import os
import io

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ========== 配置 ==========
TOKEN = "8764596414:AAGvTV2fU0MQiB6R87UxOPSiYjUXi1Ps2OY"
MASTER_USER_ID = 8782394486

TIMEZONES = {
    'china': 'Asia/Shanghai',
    'myanmar': 'Asia/Yangon',
    'thailand': 'Asia/Bangkok',
    'vietnam': 'Asia/Ho_Chi_Minh',
    'singapore': 'Asia/Singapore',
}
# =========================

def get_current_time(timezone_str):
    try:
        tz = pytz.timezone(timezone_str)
        now = datetime.now(tz)
        return now, now.strftime("%H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S")
    except:
        tz = pytz.timezone('Asia/Shanghai')
        now = datetime.now(tz)
        return now, now.strftime("%H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S")

def init_db():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS settings
                 (group_id INTEGER PRIMARY KEY,
                  operators TEXT DEFAULT '[]',
                  exchange_rate REAL DEFAULT 7.2,
                  fee_rate REAL DEFAULT 0,
                  is_active INTEGER DEFAULT 0,
                  language TEXT DEFAULT 'myanmar',
                  timezone TEXT DEFAULT 'Asia/Yangon',
                  show_usdt INTEGER DEFAULT 1)''')
    c.execute('''CREATE TABLE IF NOT EXISTS bills
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  group_id INTEGER,
                  user_id INTEGER,
                  username TEXT,
                  remark TEXT,
                  amount REAL,
                  usdt_amount REAL,
                  exchange_rate REAL,
                  bill_type TEXT,
                  timestamp TEXT)''')
    conn.commit()
    conn.close()
    print("✅ 数据库初始化完成")

def get_setting(group_id, key):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT * FROM settings WHERE group_id = ?", (group_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    cols = ['group_id', 'operators', 'exchange_rate', 'fee_rate', 'is_active', 'language', 'timezone', 'show_usdt']
    return dict(zip(cols, row)).get(key)

def update_setting(group_id, key, value):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT * FROM settings WHERE group_id = ?", (group_id,))
    if c.fetchone():
        c.execute(f"UPDATE settings SET {key} = ? WHERE group_id = ?", (value, group_id))
    else:
        c.execute("INSERT INTO settings (group_id, operators, exchange_rate, fee_rate, is_active, language, timezone, show_usdt) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (group_id, '[]', 7.2, 0, 0, 'myanmar', 'Asia/Yangon', 1))
        c.execute(f"UPDATE settings SET {key} = ? WHERE group_id = ?", (value, group_id))
    conn.commit()
    conn.close()

def is_master(user_id):
    return user_id == MASTER_USER_ID

def is_operator(group_id, user_id):
    ops = json.loads(get_setting(group_id, 'operators') or '[]')
    return user_id in ops

def can_use(group_id, user_id):
    return is_master(user_id) or is_operator(group_id, user_id)

def add_bill(group_id, user_id, username, remark, amount, bill_type, exchange_rate=None):
    if exchange_rate is None:
        exchange_rate = get_setting(group_id, 'exchange_rate') or 7.2
    if bill_type == 'income':
        usdt_amount = amount / exchange_rate
    else:
        usdt_amount = amount
    tz_str = get_setting(group_id, 'timezone') or 'Asia/Yangon'
    _, _, full_time = get_current_time(tz_str)
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('''INSERT INTO bills 
                 (group_id, user_id, username, remark, amount, usdt_amount, exchange_rate, bill_type, timestamp)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (group_id, user_id, username, remark, amount, usdt_amount, exchange_rate, bill_type, full_time))
    conn.commit()
    conn.close()
    return usdt_amount

def get_bills_by_date(group_id, date_str):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT remark, username, amount, usdt_amount, exchange_rate, bill_type, timestamp FROM bills WHERE group_id = ? AND date(timestamp) = ? ORDER BY timestamp DESC", 
              (group_id, date_str))
    bills = c.fetchall()
    c.execute("SELECT SUM(amount), SUM(usdt_amount) FROM bills WHERE group_id = ? AND bill_type = 'income' AND date(timestamp) = ?", 
              (group_id, date_str))
    total_income = c.fetchone()
    c.execute("SELECT SUM(usdt_amount) FROM bills WHERE group_id = ? AND bill_type = 'expense' AND date(timestamp) = ?", 
              (group_id, date_str))
    total_expense = c.fetchone()
    conn.close()
    return bills, total_income, total_expense

def get_all_dates(group_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT DISTINCT date(timestamp) FROM bills WHERE group_id = ? ORDER BY date(timestamp) DESC", (group_id,))
    dates = [row[0] for row in c.fetchall()]
    conn.close()
    return dates

def delete_today_bills(group_id):
    tz_str = get_setting(group_id, 'timezone') or 'Asia/Yangon'
    now, _, _ = get_current_time(tz_str)
    today_date = now.strftime("%Y-%m-%d")
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("DELETE FROM bills WHERE group_id = ? AND date(timestamp) = ?", (group_id, today_date))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted

def delete_last_bill(group_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT id FROM bills WHERE group_id = ? ORDER BY id DESC LIMIT 1", (group_id,))
    last = c.fetchone()
    if last:
        c.execute("DELETE FROM bills WHERE id = ?", (last[0],))
        deleted = 1
    else:
        deleted = 0
    conn.commit()
    conn.close()
    return deleted

def delete_all_bills(group_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("DELETE FROM bills WHERE group_id = ?", (group_id,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted

def delete_user_bills(group_id, name):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("DELETE FROM bills WHERE group_id = ? AND (LOWER(username) = ? OR LOWER(remark) = ?)", (group_id, name.lower(), name.lower()))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted

def get_today_bills(group_id):
    """获取今天的账单"""
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    tz_str = get_setting(group_id, 'timezone') or 'Asia/Yangon'
    now, _, _ = get_current_time(tz_str)
    today_date = now.strftime("%Y-%m-%d")
    
    # 入款
    c.execute("SELECT remark, username, amount, usdt_amount, exchange_rate, timestamp FROM bills WHERE group_id = ? AND bill_type = 'income' AND date(timestamp) = ? ORDER BY id DESC", (group_id, today_date))
    income = c.fetchall()
    # 下发
    c.execute("SELECT remark, username, usdt_amount, exchange_rate, timestamp FROM bills WHERE group_id = ? AND bill_type = 'expense' AND date(timestamp) = ? ORDER BY id DESC", (group_id, today_date))
    expense = c.fetchall()
    # 总计
    c.execute("SELECT SUM(amount), SUM(usdt_amount) FROM bills WHERE group_id = ? AND bill_type = 'income' AND date(timestamp) = ?", (group_id, today_date))
    total_income = c.fetchone()
    c.execute("SELECT SUM(usdt_amount) FROM bills WHERE group_id = ? AND bill_type = 'expense' AND date(timestamp) = ?", (group_id, today_date))
    total_expense = c.fetchone()
    conn.close()
    return income, expense, total_income, total_expense, today_date

# ========== 生成 HTML 账单 ==========

def generate_html_bill(gid, date_str):
    """生成指定日期的 HTML 账单"""
    bills, total_income, total_expense = get_bills_by_date(gid, date_str)
    rate = get_setting(gid, 'exchange_rate') or 7.2
    fee_rate = get_setting(gid, 'fee_rate') or 0
    show_usdt = get_setting(gid, 'show_usdt') or 1
    
    total_rmb = total_income[0] or 0
    total_usdt = total_income[1] or 0
    expense_usdt = total_expense[0] or 0
    
    income_bills = [b for b in bills if b[5] == 'income']
    expense_bills = [b for b in bills if b[5] == 'expense']
    
    # 按备注分类
    remark_stats = {}
    for bill in income_bills:
        remark = bill[0] if bill[0] else '无备注'
        if remark not in remark_stats:
            remark_stats[remark] = {'count': 0, 'amount': 0, 'usdt': 0}
        remark_stats[remark]['count'] += 1
        remark_stats[remark]['amount'] += bill[2]
        remark_stats[remark]['usdt'] += bill[3]
    
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    date_display = f"{date_str[5:7]}-{date_str[8:10]}" if len(date_str) > 8 else date_str
    
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>记账账单 {date_str}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: #f0f2f5;
            padding: 20px;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 16px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.1);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 24px 30px;
        }}
        .header h1 {{ font-size: 28px; margin-bottom: 8px; }}
        .header p {{ opacity: 0.9; font-size: 14px; }}
        .date-nav {{
            background: white;
            padding: 15px 20px;
            border-bottom: 1px solid #e0e0e0;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 10px;
        }}
        .date-nav button {{
            background: #667eea;
            color: white;
            border: none;
            padding: 8px 20px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            transition: background 0.3s;
        }}
        .date-nav button:hover {{ background: #5a67d8; }}
        .date-nav .date-display {{
            font-size: 20px;
            font-weight: 600;
            color: #333;
        }}
        .date-picker {{
            display: flex;
            gap: 10px;
            align-items: center;
        }}
        .date-picker input {{
            padding: 8px 12px;
            border: 1px solid #ddd;
            border-radius: 8px;
            font-size: 14px;
        }}
        .content {{ padding: 24px 30px; }}
        .section {{ margin-bottom: 32px; }}
        .section-title {{
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 16px;
            padding-bottom: 8px;
            border-bottom: 2px solid #667eea;
            color: #333;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }}
        th, td {{
            padding: 12px 10px;
            text-align: left;
            border-bottom: 1px solid #eef2f6;
        }}
        th {{
            background: #f8f9fc;
            font-weight: 600;
            color: #4a5568;
        }}
        tr:hover {{ background: #f7fafc; }}
        .stats-box {{
            background: linear-gradient(135deg, #f8f9fc 0%, #f0f2f5 100%);
            border-radius: 12px;
            padding: 24px;
            margin-top: 20px;
        }}
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
        }}
        .stat-card {{
            background: white;
            padding: 16px;
            border-radius: 12px;
            text-align: center;
            box-shadow: 0 2px 8px rgba(0,0,0,0.05);
        }}
        .stat-label {{ font-size: 12px; color: #888; margin-bottom: 8px; }}
        .stat-value {{ font-size: 24px; font-weight: 700; color: #333; }}
        .stat-unit {{ font-size: 12px; color: #888; margin-left: 4px; }}
        .category-list {{
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            margin-top: 12px;
        }}
        .category-item {{
            background: #e8f4f8;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 13px;
            color: #2c5282;
        }}
        .no-data {{
            text-align: center;
            padding: 40px;
            color: #888;
        }}
        .footer {{
            background: #f8f9fc;
            padding: 16px 30px;
            text-align: center;
            font-size: 12px;
            color: #888;
            border-top: 1px solid #e0e0e0;
        }}
        @media (max-width: 768px) {{
            body {{ padding: 10px; }}
            .content {{ padding: 16px; }}
            th, td {{ padding: 8px 4px; font-size: 11px; }}
            .stat-value {{ font-size: 16px; }}
            .date-nav {{ flex-direction: column; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📋 记账账单</h1>
            <p>📅 {date_str} | 时差对照：UTC+8 北京时间</p>
        </div>
        <div class="date-nav">
            <div class="date-picker">
                <label>📅 选择日期：</label>
                <input type="date" id="datePicker" value="{date_str}" onchange="goToDate()">
                <button onclick="goToDate()">跳转</button>
            </div>
            <div class="date-picker">
                <button onclick="prevDay()">◀ 前一天</button>
                <button onclick="nextDay()">后一天 ▶</button>
            </div>
        </div>
        <div class="content">'''
    
    if income_bills:
        html += f'''
            <div class="section">
                <div class="section-title">📥 入款记录 ({len(income_bills)} 笔)</div>
                <table>
                    <thead><tr><th>备注</th><th>时间</th><th>金额(元)</th><th>汇率</th><th>USDT</th><th>操作人</th></tr></thead>
                    <tbody>'''
        for bill in income_bills:
            remark, username, amount, usdt, ex_rate, _, ts = bill
            time_str = ts[11:16] if len(ts) > 11 else ts
            remark_text = remark if remark else '-'
            html += f'<tr><td>{remark_text}</td><td>{time_str}</td><td>{amount:.0f}</td><td>{ex_rate:.2f}</td><td>{usdt:.2f}</td><td>{username}</td></tr>'
        html += '</tbody></table></div>'
    else:
        html += '<div class="no-data">📭 今日暂无入款记录</div>'
    
    if expense_bills:
        html += f'''
            <div class="section">
                <div class="section-title">📤 下发记录 ({len(expense_bills)} 笔)</div>
                <table>
                    <thead><tr><th>备注</th><th>时间</th><th>USDT</th><th>操作人</th></tr></thead>
                    <tbody>'''
        for bill in expense_bills:
            remark, username, amount, usdt, ex_rate, _, ts = bill
            time_str = ts[11:16] if len(ts) > 11 else ts
            remark_text = remark if remark else '-'
            html += f'<tr><td>{remark_text}</td><td>{time_str}</td><td>{usdt:.2f}</td><td>{username}</td></tr>'
        html += '</tbody></table></div>'
    
    if remark_stats:
        html += '<div class="section"><div class="section-title">📊 入款备注分类</div><div class="category-list">'
        for remark, stats in remark_stats.items():
            html += f'<div class="category-item">📝 {remark}: {stats["count"]}笔, {stats["amount"]:.0f}元, {stats["usdt"]:.2f}U</div>'
        html += '</div></div>'
    
    html += f'''
            <div class="stats-box">
                <div class="stats-grid">
                    <div class="stat-card"><div class="stat-label">💰 费率</div><div class="stat-value">{fee_rate:.0f}<span class="stat-unit">%</span></div></div>
                    <div class="stat-card"><div class="stat-label">💱 汇率</div><div class="stat-value">{rate:.2f}<span class="stat-unit"></span></div></div>
                    <div class="stat-card"><div class="stat-label">📥 总入款(元)</div><div class="stat-value">{total_rmb:.0f}<span class="stat-unit"></span></div></div>
                    <div class="stat-card"><div class="stat-label">💵 总入款(USDT)</div><div class="stat-value">{total_usdt:.2f}<span class="stat-unit">U</span></div></div>
                    <div class="stat-card"><div class="stat-label">📤 已下发</div><div class="stat-value">{expense_usdt:.2f}<span class="stat-unit">U</span></div></div>
                    <div class="stat-card"><div class="stat-label">📊 未下发</div><div class="stat-value">{total_usdt - expense_usdt:.2f}<span class="stat-unit">U</span></div></div>
                </div>
            </div>
        </div>
        <div class="footer">
            <p>📊 账单生成时间：{today}</p>
            <p>💡 点击上方日历可以切换日期查看任意一天的账单</p>
        </div>
    </div>
    <script>
        const baseUrl = window.location.origin + window.location.pathname;
        function goToDate() {{
            const date = document.getElementById('datePicker').value;
            if (date) {{
                window.location.href = baseUrl + '?date=' + date;
            }}
        }}
        function prevDay() {{
            const date = document.getElementById('datePicker').value;
            const d = new Date(date);
            d.setDate(d.getDate() - 1);
            window.location.href = baseUrl + '?date=' + d.toISOString().split('T')[0];
        }}
        function nextDay() {{
            const date = document.getElementById('datePicker').value;
            const d = new Date(date);
            d.setDate(d.getDate() + 1);
            window.location.href = baseUrl + '?date=' + d.toISOString().split('T')[0];
        }}
    </script>
</body>
</html>'''
    
    return html

# ========== 生成 HTML 文件并上传 ==========

async def send_html_bill(update: Update, context: ContextTypes.DEFAULT_TYPE, date_str=None):
    """发送 HTML 账单文件"""
    gid = update.effective_chat.id
    if date_str is None:
        tz_str = get_setting(gid, 'timezone') or 'Asia/Yangon'
        now, _, _ = get_current_time(tz_str)
        date_str = now.strftime("%Y-%m-%d")
    
    html_content = generate_html_bill(gid, date_str)
    filename = f"bill_{gid}_{date_str}.html"
    filepath = os.path.join('/tmp', filename)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    with open(filepath, 'rb') as f:
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=f,
            filename=filename,
            caption=f"📊 账单 {date_str}\n点击文件即可在浏览器中查看"
        )
    
    os.remove(filepath)

# ========== 按钮回调 ==========

async def show_full_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看完整账单（生成HTML文件）"""
    query = update.callback_query
    await query.answer()
    await send_html_bill(update, context)

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    gid = update.effective_chat.id
    uid = update.effective_user.id
    lang = get_setting(gid, 'language') or 'myanmar'
    
    if not can_use(gid, uid):
        await query.edit_message_text("❌ 你没有权限查看帮助")
        return
    
    help_text = """
📖 *记账机器人帮助*

📌 *记账格式：*
`+1000` - 入款1000元
`အမည်+2000` - 带备注入款
`下发50` - 下发50 USDT
`+0` - 查看今日汇总

📌 *管理命令：*
`/mode` - 开启/关闭记账模式
`/setrate 7.2` - 设置汇率
`/setoperator` - 设置操作人
`/bill` - 查看今日账单
`/history 2026-05-13` - 查询指定日期账单
`/language` - 切换语言
`/timezone` - 设置时区
`/showusdt` - 显示USDT金额
`/hideusdt` - 隐藏USDT金额

📌 *删除命令：*
`/deltoday` - 删除今日所有账单
`/dellast` - 删除最后一笔账单
`/delall` - 删除所有账单
`/deluser 名字` - 删除某人的所有账单

📌 *查看账单：*
点击下方按钮即可生成完整的 HTML 账单文件
"""
    
    keyboard = [[InlineKeyboardButton("🔙 返回", callback_data='back_to_main')]]
    await query.edit_message_text(help_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    gid = update.effective_chat.id
    lang = get_setting(gid, 'language') or 'myanmar'
    
    rate = get_setting(gid, 'exchange_rate') or 7.2
    is_active = get_setting(gid, 'is_active') or 0
    tz_str = get_setting(gid, 'timezone') or 'Asia/Yangon'
    
    status = "🟢 开启" if is_active else "🔴 关闭"
    timezone_name = "缅甸" if tz_str == 'Asia/Yangon' else "中国" if tz_str == 'Asia/Shanghai' else "泰国"
    
    message = f"🤖 *记账机器人*\n\n📌 状态: {status}\n💰 汇率: 1 USDT = {rate:.2f} 元\n🌍 时区: {timezone_name}\n"
    message += "━━━━━━━━━━━━━━━━━━━━\n\n📝 *记账格式:*\n`+1000` - 入款1000元\n`အမည်+2000` - 带备注入款\n`下发50` - 下发50 USDT\n`+0` - 查看今日汇总\n\n"
    message += "📌 *管理命令:*\n`/mode` - 开关记账模式\n`/setrate` - 设置汇率\n`/setoperator` - 设置操作人\n`/bill` - 查看今日账单\n"
    message += "`/language` - 切换语言\n`/timezone` - 设置时区\n`/deltoday` - 删除今日账单\n`/dellast` - 删除最后一笔\n"
    message += "`/delall` - 删除所有账单\n`/deluser 名字` - 删除某人账单\n`/history 2026-05-13` - 查询历史账单"
    
    keyboard = [[
        InlineKeyboardButton("📊 查看完整账单", callback_data='show_full_report'),
        InlineKeyboardButton("📖 查看帮助", callback_data='show_help')
    ]]
    await query.edit_message_text(message, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

# ========== 显示账单函数 ==========

async def show_full_bill(update: Update, gid):
    income, expense, total_income, total_expense, today_date = get_today_bills(gid)
    rate = get_setting(gid, 'exchange_rate') or 7.2
    lang = get_setting(gid, 'language') or 'myanmar'
    show_usdt = get_setting(gid, 'show_usdt') or 1
    
    total_rmb = total_income[0] or 0
    total_usdt = total_income[1] or 0
    expense_usdt = total_expense[0] or 0
    
    message = f"📊 {'ယနေ့ငွေစာရင်း' if lang == 'myanmar' else '今日账单汇总'} {today_date}\n━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if income:
        message += f"📥 {'ဝင်ငွေ' if lang == 'myanmar' else '入款'}({len(income)} {'ခု' if lang == 'myanmar' else '笔'}):\n"
        for bill in income[:5]:
            remark, username, amount, usdt, ex_rate, ts = bill
            time_short = ts[11:16] if len(ts) > 11 else ts
            if remark:
                if show_usdt:
                    message += f"  {username}【{remark}】{time_short}  {amount:.0f} / {ex_rate:.0f} = {usdt:.2f} U\n"
                else:
                    message += f"  {username}【{remark}】{time_short}  {amount:.0f} {'ကျပ်' if lang == 'myanmar' else '元'}\n"
            else:
                if show_usdt:
                    message += f"  {username} {time_short}  {amount:.0f} / {ex_rate:.0f} = {usdt:.2f} U\n"
                else:
                    message += f"  {username} {time_short}  {amount:.0f} {'ကျပ်' if lang == 'myanmar' else '元'}\n"
        if len(income) > 5:
            message += f"  ... {len(income)-5} {'ခု' if lang == 'myanmar' else '笔'}\n"
        message += "\n"
    else:
        message += f"📥 {'ဝင်ငွေ' if lang == 'myanmar' else '入款'}(0 {'ခု' if lang == 'myanmar' else '笔'}):\n\n"
    
    if expense:
        message += f"📤 {'ထုတ်ငွေ' if lang == 'myanmar' else '下发'}({len(expense)} {'ခု' if lang == 'myanmar' else '笔'}):\n"
        for bill in expense[:5]:
            remark, username, usdt, ex_rate, ts = bill
            time_short = ts[11:16] if len(ts) > 11 else ts
            if show_usdt:
                message += f"  {username} {time_short}  {usdt:.2f} U\n"
            else:
                message += f"  {username} {time_short}  {usdt * ex_rate:.0f} {'ကျပ်' if lang == 'myanmar' else '元'}\n"
        if len(expense) > 5:
            message += f"  ... {len(expense)-5} {'ခု' if lang == 'myanmar' else '笔'}\n"
        message += "\n"
    else:
        message += f"📤 {'ထုတ်ငွေ' if lang == 'myanmar' else '下发'}(0 {'ခု' if lang == 'myanmar' else '笔'}):\n\n"
    
    message += f"💰 {'ငွေလဲနှုန်း' if lang == 'myanmar' else '汇率'}：{rate:.2f}\n"
    if show_usdt:
        message += f"📊 {'စုစုပေါင်းဝင်ငွေ' if lang == 'myanmar' else '总入款'}：{total_rmb:.0f} | {total_usdt:.2f} U\n"
        message += f"📊 {'ထုတ်ပြီး' if lang == 'myanmar' else '已下发'}：{expense_usdt:.2f} U\n"
        message += f"📊 {'ကျန်ငွေ' if lang == 'myanmar' else '未下发'}：{total_usdt - expense_usdt:.2f} U"
    else:
        message += f"📊 {'စုစုပေါင်းဝင်ငွေ' if lang == 'myanmar' else '总入款'}：{total_rmb:.0f} {'ကျပ်' if lang == 'myanmar' else '元'}\n"
        message += f"📊 {'ထုတ်ပြီး' if lang == 'myanmar' else '已下发'}：{expense_usdt * rate:.0f} {'ကျပ်' if lang == 'myanmar' else '元'}\n"
        message += f"📊 {'ကျန်ငွေ' if lang == 'myanmar' else '未下发'}：{(total_usdt - expense_usdt) * rate:.0f} {'ကျပ်' if lang == 'myanmar' else '元'}"
    
    keyboard = [[
        InlineKeyboardButton("📊 查看完整账单", callback_data='show_full_report'),
        InlineKeyboardButton("📖 查看帮助", callback_data='show_help')
    ]]
    await update.message.reply_text(message, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def show_today_summary(update: Update, gid):
    income, expense, total_income, total_expense, today_date = get_today_bills(gid)
    rate = get_setting(gid, 'exchange_rate') or 7.2
    lang = get_setting(gid, 'language') or 'myanmar'
    show_usdt = get_setting(gid, 'show_usdt') or 1
    
    total_rmb = total_income[0] or 0
    total_usdt = total_income[1] or 0
    expense_usdt = total_expense[0] or 0
    
    message = f"📊 {'ယနေ့ငွေစာရင်း' if lang == 'myanmar' else '今日账单汇总'}\n📅 {today_date}\n━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if income:
        message += f"📥 {'ဝင်ငွေ' if lang == 'myanmar' else '入款'}({len(income)} {'ခု' if lang == 'myanmar' else '笔'}):\n"
        for bill in income[:5]:
            remark, username, amount, usdt, ex_rate, ts = bill
            time_short = ts[11:16] if len(ts) > 11 else ts
            if remark:
                if show_usdt:
                    message += f"  {username}【{remark}】{time_short}  +{amount:.0f}元 = {usdt:.2f}U\n"
                else:
                    message += f"  {username}【{remark}】{time_short}  +{amount:.0f}元\n"
            else:
                if show_usdt:
                    message += f"  {username} {time_short}  +{amount:.0f}元 = {usdt:.2f}U\n"
                else:
                    message += f"  {username} {time_short}  +{amount:.0f}元\n"
        if len(income) > 5:
            message += f"  ... {len(income)-5} {'ခု' if lang == 'myanmar' else '笔'}\n"
        message += "\n"
    else:
        message += f"📥 {'ဝင်ငွေ' if lang == 'myanmar' else '入款'}(0 {'ခု' if lang == 'myanmar' else '笔'}):\n\n"
    
    if expense:
        message += f"📤 {'ထုတ်ငွေ' if lang == 'myanmar' else '下发'}({len(expense)} {'ခု' if lang == 'myanmar' else '笔'}):\n"
        for bill in expense[:5]:
            remark, username, usdt, ex_rate, ts = bill
            time_short = ts[11:16] if len(ts) > 11 else ts
            if show_usdt:
                message += f"  {username} {time_short}  ထုတ် {usdt:.2f}U\n" if lang == 'myanmar' else f"  {username} {time_short}  下发 {usdt:.2f}U\n"
            else:
                message += f"  {username} {time_short}  ထုတ် {usdt * ex_rate:.0f}ကျပ်\n" if lang == 'myanmar' else f"  {username} {time_short}  下发 {usdt * ex_rate:.0f}元\n"
        if len(expense) > 5:
            message += f"  ... {len(expense)-5} {'ခု' if lang == 'myanmar' else '笔'}\n"
        message += "\n"
    else:
        message += f"📤 {'ထုတ်ငွေ' if lang == 'myanmar' else '下发'}(0 {'ခု' if lang == 'myanmar' else '笔'}):\n\n"
    
    message += f"💰 {'ငွေလဲနှုန်း' if lang == 'myanmar' else '汇率'}：{rate:.2f}\n"
    if show_usdt:
        message += f"📊 {'စုစုပေါင်းဝင်ငွေ' if lang == 'myanmar' else '总入款'}：{total_rmb:.0f} 元 = {total_usdt:.2f} U\n"
        message += f"📊 {'ထုတ်ပြီး' if lang == 'myanmar' else '已下发'}：{expense_usdt:.2f} U\n"
        message += f"📊 {'ကျန်ငွေ' if lang == 'myanmar' else '未下发'}：{total_usdt - expense_usdt:.2f} U"
    else:
        message += f"📊 {'စုစုပေါင်းဝင်ငွေ' if lang == 'myanmar' else '总入款'}：{total_rmb:.0f} {'ကျပ်' if lang == 'myanmar' else '元'}\n"
        message += f"📊 {'ထုတ်ပြီး' if lang == 'myanmar' else '已下发'}：{expense_usdt * rate:.0f} {'ကျပ်' if lang == 'myanmar' else '元'}\n"
        message += f"📊 {'ကျန်ငွေ' if lang == 'myanmar' else '未下发'}：{(total_usdt - expense_usdt) * rate:.0f} {'ကျပ်' if lang == 'myanmar' else '元'}"
    
    keyboard = [[
        InlineKeyboardButton("📊 查看完整账单", callback_data='show_full_report'),
        InlineKeyboardButton("📖 查看帮助", callback_data='show_help')
    ]]
    await update.message.reply_text(message, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

# ========== 命令处理 ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    rate = get_setting(gid, 'exchange_rate') or 7.2
    is_active = get_setting(gid, 'is_active') or 0
    lang = get_setting(gid, 'language') or 'myanmar'
    
    status = "🟢 开启" if is_active else "🔴 关闭"
    if lang == 'myanmar':
        message = f"🤖 *ငွေစာရင်းဘော့စတင်ပြီး*\n\n📌 အခြေအနေ: {status}\n💰 ငွေလဲနှုန်း: 1 USDT = {rate:.2f} ကျပ်\n\nအကူအညီရယူရန် /help ကိုနှိပ်ပါ"
    else:
        message = f"🤖 *记账机器人已启动*\n\n📌 状态: {status}\n💰 汇率: 1 USDT = {rate:.2f} 元\n\n发送 /help 查看帮助"
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    lang = get_setting(gid, 'language') or 'myanmar'
    
    if lang == 'myanmar':
        help_text = """
🤖 *ငွေစာရင်းဘော့အကူအညီ*

📌 *ငွေစာရင်းသွင်းနည်း：*
`+1000` - ၁၀၀၀ ကျပ်သွင်းရန်
`အမည်+2000` - မှတ်ချက်ထည့်သွင်းရန်
`ထုတ်50` - USDT 50 ထုတ်ရန်
`+0` - ယနေ့အကျဉ်းချုပ်ကြည့်ရန်
`/history 2026-05-13` - ရက်စွဲအလိုက်ရှာဖွေရန်

📌 *စီမံခန့်ခွဲမှု：*
`/mode` - ငွေစာရင်းမုဒ်ဖွင့်/ပိတ်
`/setrate 7.2` - ငွေလဲနှုန်းသတ်မှတ်
`/setoperator` - အသုံးပြုသူသတ်မှတ်
`/bill` - ယနေ့ငွေစာရင်းကြည့်ရန်
`/language` - ဘာသာစကားပြောင်းရန်
`/timezone` - အချိန်ဇုန်ပြောင်းရန်

📌 *ဖျက်ခြင်း：*
`/deltoday` - ယနေ့ငွေစာရင်းဖျက်
`/dellast` - နောက်ဆုံးငွေစာရင်းဖျက်
`/delall` - ငွေစာရင်းအားလုံးဖျက်
"""
    else:
        help_text = """
🤖 *记账机器人帮助*

📌 *记账格式：*
`+1000` - 入款1000元
`အမည်+2000` - 带备注入款
`下发50` - 下发50 USDT
`+0` - 查看今日汇总
`/history 2026-05-13` - 查询指定日期账单

📌 *管理命令：*
`/mode` - 开启/关闭记账模式
`/setrate 7.2` - 设置汇率
`/setoperator` - 设置操作人
`/bill` - 查看今日账单
`/language` - 切换语言
`/timezone` - 设置时区

📌 *删除命令：*
`/deltoday` - 删除今日所有账单
`/dellast` - 删除最后一笔账单
`/delall` - 删除所有账单
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    uid = update.effective_user.id
    lang = get_setting(gid, 'language') or 'myanmar'
    
    if not can_use(gid, uid):
        await update.message.reply_text("❌ သင့်တွင်အသုံးပြုခွင့်မရှိပါ" if lang == 'myanmar' else "❌ 你没有操作权限")
        return
    
    current = get_setting(gid, 'is_active') or 0
    if current == 0:
        update_setting(gid, 'is_active', 1)
        await update.message.reply_text("✅ စာရင်းသွင်းမုဒ် ဖွင့်ပြီး\n\nငွေစာရင်းသွင်းနိုင်ပါပြီ။" if lang == 'myanmar' else "✅ 记账模式已开启\n\n现在可以发送记账命令了！")
    else:
        update_setting(gid, 'is_active', 0)
        await update.message.reply_text("🔕 စာရင်းသွင်းမုဒ် ပိတ်ပြီး" if lang == 'myanmar' else "🔕 记账模式已关闭")

async def setrate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    uid = update.effective_user.id
    lang = get_setting(gid, 'language') or 'myanmar'
    
    if not can_use(gid, uid):
        await update.message.reply_text("❌ သင့်တွင်အသုံးပြုခွင့်မရှိပါ" if lang == 'myanmar' else "❌ 你没有操作权限")
        return
    
    if not context.args:
        await update.message.reply_text("用法: /setrate 7.2")
        return
    
    try:
        rate = float(context.args[0])
        update_setting(gid, 'exchange_rate', rate)
        await update.message.reply_text(f"✅ ငွေလဲနှုန်းသတ်မှတ်ပြီး {rate}" if lang == 'myanmar' else f"✅ 汇率已设为 {rate}")
    except:
        await update.message.reply_text("❌ ကျေးဇူးပြု၍ ငွေလဲနှုန်းမှန်ကန်စွာထည့်ပါ" if lang == 'myanmar' else "❌ 请输入正确的数字")

async def bill_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    await show_today_summary(update, gid)

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    uid = update.effective_user.id
    lang = get_setting(gid, 'language') or 'myanmar'
    
    if not can_use(gid, uid):
        await update.message.reply_text("❌ သင့်တွင်အသုံးပြုခွင့်မရှိပါ" if lang == 'myanmar' else "❌ 你没有操作权限")
        return
    
    if not context.args:
        await update.message.reply_text("📅 သုံးစွဲပုံ: /history 2026-05-13\n\nဥပမာ: /history 2026-05-13" if lang == 'myanmar' else "📅 用法: /history 2026-05-13\n\n例如: /history 2026-05-13")
        return
    
    date_str = context.args[0]
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except:
        await update.message.reply_text("❌ ရက်စွဲပုံစံမှားယွင်းနေပါသည်။\nမှန်ကန်သောပုံစံ: 2026-05-13" if lang == 'myanmar' else "❌ 日期格式错误！\n正确格式: 2026-05-13")
        return
    
    bills, total_income, total_expense = get_bills_by_date(gid, date_str)
    rate = get_setting(gid, 'exchange_rate') or 7.2
    show_usdt = get_setting(gid, 'show_usdt') or 1
    
    total_rmb = total_income[0] or 0
    total_usdt = total_income[1] or 0
    expense_usdt = total_expense[0] or 0
    
    if not bills:
        await update.message.reply_text(f"📭 {date_str} {'ငွေစာရင်းမရှိပါ' if lang == 'myanmar' else '没有账单记录'}")
        return
    
    message = f"📊 *{'သမိုင်းစာရင်း' if lang == 'myanmar' else '历史账单'}*\n📅 {date_str}\n━━━━━━━━━━━━━━━━━━━━\n\n"
    
    income_bills = [b for b in bills if b[5] == 'income']
    expense_bills = [b for b in bills if b[5] == 'expense']
    
    if income_bills:
        message += f"📥 {'ဝင်ငွေ' if lang == 'myanmar' else '入款'}({len(income_bills)} {'ခု' if lang == 'myanmar' else '笔'}):\n"
        for bill in income_bills[:10]:
            remark, username, amount, usdt, ex_rate, _, ts = bill
            time_short = ts[11:16] if len(ts) > 11 else ts
            if remark:
                if show_usdt:
                    message += f"  {username}【{remark}】{time_short}  {amount:.0f} / {ex_rate:.0f} = {usdt:.2f} U\n"
                else:
                    message += f"  {username}【{remark}】{time_short}  {amount:.0f} {'ကျပ်' if lang == 'myanmar' else '元'}\n"
            else:
                if show_usdt:
                    message += f"  {username} {time_short}  {amount:.0f} / {ex_rate:.0f} = {usdt:.2f} U\n"
                else:
                    message += f"  {username} {time_short}  {amount:.0f} {'ကျပ်' if lang == 'myanmar' else '元'}\n"
        if len(income_bills) > 10:
            message += f"  ... {len(income_bills)-10} {'ခု' if lang == 'myanmar' else '笔'}\n"
        message += "\n"
    
    if expense_bills:
        message += f"📤 {'ထုတ်ငွေ' if lang == 'myanmar' else '下发'}({len(expense_bills)} {'ခု' if lang == 'myanmar' else '笔'}):\n"
        for bill in expense_bills[:10]:
            remark, username, amount, usdt, ex_rate, _, ts = bill
            time_short = ts[11:16] if len(ts) > 11 else ts
            if show_usdt:
                message += f"  {username} {time_short}  {usdt:.2f} U\n"
            else:
                message += f"  {username} {time_short}  {usdt * ex_rate:.0f} {'ကျပ်' if lang == 'myanmar' else '元'}\n"
        if len(expense_bills) > 10:
            message += f"  ... {len(expense_bills)-10} {'ခု' if lang == 'myanmar' else '笔'}\n"
        message += "\n"
    
    message += "━━━━━━━━━━━━━━━━━━━━\n"
    message += f"💰 {'ငွေလဲနှုန်း' if lang == 'myanmar' else '汇率'}：{rate:.2f}\n"
    if show_usdt:
        message += f"📊 {'စုစုပေါင်းဝင်ငွေ' if lang == 'myanmar' else '总入款'}：{total_rmb:.0f} | {total_usdt:.2f} U\n"
        message += f"📊 {'ထုတ်ပြီး' if lang == 'myanmar' else '已下发'}：{expense_usdt:.2f} U\n"
        message += f"📊 {'ကျန်ငွေ' if lang == 'myanmar' else '未下发'}：{total_usdt - expense_usdt:.2f} U"
    else:
        message += f"📊 {'စုစုပေါင်းဝင်ငွေ' if lang == 'myanmar' else '总入款'}：{total_rmb:.0f} {'ကျပ်' if lang == 'myanmar' else '元'}\n"
        message += f"📊 {'ထုတ်ပြီး' if lang == 'myanmar' else '已下发'}：{expense_usdt * rate:.0f} {'ကျပ်' if lang == 'myanmar' else '元'}\n"
        message += f"📊 {'ကျန်ငွေ' if lang == 'myanmar' else '未下发'}：{(total_usdt - expense_usdt) * rate:.0f} {'ကျပ်' if lang == 'myanmar' else '元'}"
    
    await update.message.reply_text(message, parse_mode='Markdown')

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    rate = get_setting(gid, 'exchange_rate') or 7.2
    is_active = get_setting(gid, 'is_active') or 0
    lang = get_setting(gid, 'language') or 'myanmar'
    tz_str = get_setting(gid, 'timezone') or 'Asia/Yangon'
    show_usdt = get_setting(gid, 'show_usdt') or 1
    ops = json.loads(get_setting(gid, 'operators') or '[]')
    
    status = "ဖွင့်" if is_active else "ပိတ်" if lang == 'myanmar' else "开启" if is_active else "关闭"
    timezone_name = "မြန်မာ" if tz_str == 'Asia/Yangon' else "တရုတ်" if tz_str == 'Asia/Shanghai' else "ထိုင်း" if lang == 'myanmar' else "缅甸" if tz_str == 'Asia/Yangon' else "中国" if tz_str == 'Asia/Shanghai' else "泰国"
    language_name = "မြန်မာ" if lang == 'myanmar' else "中文"
    usdt_status = "ပြ" if show_usdt else "ဝှက်" if lang == 'myanmar' else "显示" if show_usdt else "隐藏"
    
    message = f"⚙️ *{'ပြင်ဆင်ချက်များ' if lang == 'myanmar' else '当前设置'}*\n"
    message += f"💰 {'ငွေလဲနှုန်း' if lang == 'myanmar' else '汇率'}: {rate}\n"
    message += f"🔘 {'မုဒ်' if lang == 'myanmar' else '模式'}: {status}\n"
    message += f"🌍 {'အချိန်ဇုန်' if lang == 'myanmar' else '时区'}: {timezone_name}\n"
    message += f"📖 {'ဘာသာစကား' if lang == 'myanmar' else '语言'}: {language_name}\n"
    message += f"💵 USDT{'ပြသမှု' if lang == 'myanmar' else '显示'}: {usdt_status}\n"
    message += f"👤 {'အသုံးပြုသူ' if lang == 'myanmar' else '操作人'}: {len(ops)} {'ဦး' if lang == 'myanmar' else '人'}"
    await update.message.reply_text(message, parse_mode='Markdown')

async def setoperator_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    gid = update.effective_chat.id
    lang = get_setting(gid, 'language') or 'myanmar'
    
    if not is_master(uid):
        await update.message.reply_text("❌ ဘော့ပိုင်ရှင်သာ အသုံးပြုသူသတ်မှတ်နိုင်သည်။" if lang == 'myanmar' else "❌ 只有机器人主人可以设置操作人")
        return
    
    if not update.message.reply_to_message:
        await update.message.reply_text("❌ အသုံးပြုသူသတ်မှတ်ရန် စာကိုပြန်ကြားပေးပါ။" if lang == 'myanmar' else "❌ 请回复要设置为操作人的消息")
        return
    
    target = update.message.reply_to_message.from_user
    ops = json.loads(get_setting(gid, 'operators') or '[]')
    if target.id not in ops:
        ops.append(target.id)
        update_setting(gid, 'operators', json.dumps(ops))
        await update.message.reply_text(f"✅ {target.first_name} ကို အသုံးပြုသူအဖြစ်သတ်မှတ်ပြီး" if lang == 'myanmar' else f"✅ 已设置 {target.first_name} 为操作人")
    else:
        await update.message.reply_text("ဤအသုံးပြုသူသည် အသုံးပြုသူဖြစ်နေပြီး" if lang == 'myanmar' else "该用户已经是操作人")

async def listops_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    ops = json.loads(get_setting(gid, 'operators') or '[]')
    lang = get_setting(gid, 'language') or 'myanmar'
    
    if not ops:
        await update.message.reply_text("📋 အသုံးပြုသူမရှိသေးပါ" if lang == 'myanmar' else "📋 暂无操作人")
        return
    
    message = "📋 အသုံးပြုသူစာရင်း:\n" if lang == 'myanmar' else "📋 操作人列表:\n"
    for oid in ops:
        try:
            member = await context.bot.get_chat_member(gid, oid)
            message += f"  • {member.user.first_name}\n"
        except:
            message += f"  • ID: {oid}\n"
    await update.message.reply_text(message)

async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    uid = update.effective_user.id
    
    if not can_use(gid, uid):
        await update.message.reply_text("❌ 你没有权限")
        return
    
    current = get_setting(gid, 'language') or 'myanmar'
    new_lang = 'chinese' if current == 'myanmar' else 'myanmar'
    update_setting(gid, 'language', new_lang)
    await update.message.reply_text("✅ ဘာသာစကားပြောင်းပြီး\n✅ 语言已切换")

async def timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    uid = update.effective_user.id
    lang = get_setting(gid, 'language') or 'myanmar'
    
    if not can_use(gid, uid):
        await update.message.reply_text("❌ သင့်တွင်အသုံးပြုခွင့်မရှိပါ" if lang == 'myanmar' else "❌ 你没有操作权限")
        return
    
    if not context.args:
        tz_list = "可用时区:\n  /timezone china - 中国北京时间\n  /timezone myanmar - 缅甸\n  /timezone thailand - 泰国"
        await update.message.reply_text(tz_list)
        return
    
    tz_name = context.args[0].lower()
    if tz_name in TIMEZONES:
        update_setting(gid, 'timezone', TIMEZONES[tz_name])
        await update.message.reply_text("✅ အချိန်ဇုန်ပြောင်းပြီး\n✅ 时区已切换" if lang == 'myanmar' else "✅ 时区已切换\n✅ အချိန်ဇုန်ပြောင်းပြီး")
    else:
        await update.message.reply_text("❌ 无效的时区\n可用: china, myanmar, thailand")

async def show_usdt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    uid = update.effective_user.id
    lang = get_setting(gid, 'language') or 'myanmar'
    
    if not can_use(gid, uid):
        await update.message.reply_text("❌ သင့်တွင်အသုံးပြုခွင့်မရှိပါ" if lang == 'myanmar' else "❌ 你没有操作权限")
        return
    
    update_setting(gid, 'show_usdt', 1)
    await update.message.reply_text("✅ USDT ပြသမှုဖွင့်ပြီး" if lang == 'myanmar' else "✅ 已开启USDT显示模式")

async def hide_usdt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    uid = update.effective_user.id
    lang = get_setting(gid, 'language') or 'myanmar'
    
    if not can_use(gid, uid):
        await update.message.reply_text("❌ သင့်တွင်အသုံးပြုခွင့်မရှိပါ" if lang == 'myanmar' else "❌ 你没有操作权限")
        return
    
    update_setting(gid, 'show_usdt', 0)
    await update.message.reply_text("🔕 USDT ပြသမှုပိတ်ပြီး" if lang == 'myanmar' else "🔕 已关闭USDT显示模式")

async def del_today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    uid = update.effective_user.id
    lang = get_setting(gid, 'language') or 'myanmar'
    
    if not can_use(gid, uid):
        await update.message.reply_text("❌ သင့်တွင်အသုံးပြုခွင့်မရှိပါ" if lang == 'myanmar' else "❌ 你没有操作权限")
        return
    
    deleted = delete_today_bills(gid)
    await update.message.reply_text(f"✅ ယနေ့ငွေစာရင်း {deleted} ခုကိုဖျက်ပြီး" if deleted > 0 else "📭 ယနေ့ငွေစာရင်းမရှိပါ" if lang == 'myanmar' else f"✅ 已删除今日所有账单，共 {deleted} 条记录" if deleted > 0 else "📭 今日暂无账单可删除")

async def del_last_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    uid = update.effective_user.id
    lang = get_setting(gid, 'language') or 'myanmar'
    
    if not can_use(gid, uid):
        await update.message.reply_text("❌ သင့်တွင်အသုံးပြုခွင့်မရှိပါ" if lang == 'myanmar' else "❌ 你没有操作权限")
        return
    
    deleted = delete_last_bill(gid)
    await update.message.reply_text("✅ နောက်ဆုံးငွေစာရင်းကိုဖျက်ပြီး" if deleted > 0 else "📭 ငွေစာရင်းမရှိပါ" if lang == 'myanmar' else "✅ 已删除最后一笔账单" if deleted > 0 else "📭 暂无账单可删除")

async def del_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    uid = update.effective_user.id
    lang = get_setting(gid, 'language') or 'myanmar'
    
    if not can_use(gid, uid):
        await update.message.reply_text("❌ သင့်တွင်အသုံးပြုခွင့်မရှိပါ" if lang == 'myanmar' else "❌ 你没有操作权限")
        return
    
    deleted = delete_all_bills(gid)
    await update.message.reply_text(f"✅ ငွေစာရင်းအားလုံး {deleted} ခုကိုဖျက်ပြီး" if deleted > 0 else "📭 ငွေစာရင်းမရှိပါ" if lang == 'myanmar' else f"✅ 已删除所有账单，共 {deleted} 条记录" if deleted > 0 else "📭 暂无账单可删除")

async def del_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.effective_chat.id
    uid = update.effective_user.id
    lang = get_setting(gid, 'language') or 'myanmar'
    
    if not can_use(gid, uid):
        await update.message.reply_text("❌ သင့်တွင်အသုံးပြုခွင့်မရှိပါ" if lang == 'myanmar' else "❌ 你没有操作权限")
        return
    
    if not context.args:
        await update.message.reply_text("အသုံးပြုပုံ: /deluser အမည်" if lang == 'myanmar' else "用法: /deluser 名字")
        return
    
    target_name = ' '.join(context.args)
    deleted = delete_user_bills(gid, target_name)
    await update.message.reply_text(f"✅ {target_name} ၏ငွေစာရင်း {deleted} ခုကိုဖျက်ပြီး" if lang == 'myanmar' else f"✅ 已删除 {target_name} 的账单，共 {deleted} 条记录")

async def accounting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    gid = update.effective_chat.id
    uid = update.effective_user.id
    username = update.effective_user.first_name
    
    is_active = get_setting(gid, 'is_active') or 0
    if is_active == 0:
        return
    
    if not can_use(gid, uid):
        return
    
    if text == '+0':
        await show_today_summary(update, gid)
        return
    
    m = re.match(r'^下发(\d+(?:\.\d+)?)$', text)
    if m:
        amount = float(m.group(1))
        add_bill(gid, uid, username, '', amount, 'expense')
        await show_full_bill(update, gid)
        return
    
    m = re.match(r'^([^+\d]+)?\+(\d+(?:\.\d+)?)(?:/(\d+(?:\.\d+)?))?$', text)
    if m:
        remark = m.group(1).strip() if m.group(1) else ''
        amount = float(m.group(2))
        custom_rate = float(m.group(3)) if m.group(3) else None
        exchange_rate = custom_rate if custom_rate else get_setting(gid, 'exchange_rate') or 7.2
        add_bill(gid, uid, username, remark, amount, 'income', exchange_rate)
        await show_full_bill(update, gid)
        return

def main():
    init_db()
    print("🤖 机器人启动中...")
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("mode", mode_command))
    app.add_handler(CommandHandler("setrate", setrate_command))
    app.add_handler(CommandHandler("bill", bill_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("setoperator", setoperator_command))
    app.add_handler(CommandHandler("listops", listops_command))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(CommandHandler("timezone", timezone_command))
    app.add_handler(CommandHandler("showusdt", show_usdt_command))
    app.add_handler(CommandHandler("hideusdt", hide_usdt_command))
    app.add_handler(CommandHandler("deltoday", del_today_command))
    app.add_handler(CommandHandler("dellast", del_last_command))
    app.add_handler(CommandHandler("delall", del_all_command))
    app.add_handler(CommandHandler("deluser", del_user_command))
    
    app.add_handler(CallbackQueryHandler(show_full_report, pattern='show_full_report'))
    app.add_handler(CallbackQueryHandler(show_help, pattern='show_help'))
    app.add_handler(CallbackQueryHandler(back_to_main, pattern='back_to_main'))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, accounting))
    
    print("✅ 机器人运行中...")
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()