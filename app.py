from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import yfinance as yf
from FinMind.data import DataLoader
import datetime
from apscheduler.schedulers.background import BackgroundScheduler
import time
import pandas as pd
import os  # <--- 💡 雲端必備：讀取環境變數

app = Flask(__name__)

# --- FinMind 登入設定 ---
dl = DataLoader()
dl.login_by_token(api_token='eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoid3V5aW50ZW5nIiwiZW1haWwiOiJ3dXlpbnRlbmcxMjA2QGdtYWlsLmNvbSJ9.3-HFSvEh15UnzB4Nt_TZUYLCF7OSjrDuB31fwZ1foJA')

# --- LINE Bot 金鑰設定 ---
line_bot_api = LineBotApi('0PkQu4ePT9fMFke5+i/e6A1cxm7dD4Nt04K47Uq7Pxy5vIUxKnIzaYUCBcNGJ1Y/RWscQlvRknxmtdioggR+rI LSsd28GBtd1lbDcvPgv1UkrIcrrDEOgHZNgQl1b6HH8mRpvvDLUBzPH4FVOnOGwAdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('6394456d4596cc6aadb9c92dda96b296')
MY_USER_ID = 'U288dc1f88aabee28ca0342d542b8040f'

# --- 💡 指標計算函式庫 (KD, MACD) ---
def calculate_kd(df):
    df['9H'] = df['High'].rolling(window=9).max()
    df['9L'] = df['Low'].rolling(window=9).min()
    df['RSV'] = (df['Close'] - df['9L']) / (df['9H'] - df['9L']) * 100
    df['RSV'] = df['RSV'].fillna(50)
    k_list, d_list = [50.0], [50.0]
    for rsv in df['RSV']:
        new_k = (2/3) * k_list[-1] + (1/3) * rsv
        new_d = (2/3) * d_list[-1] + (1/3) * new_k
        k_list.append(new_k)
        d_list.append(new_d)
    df['K'], df['D'] = k_list[1:], d_list[1:]
    return df

def calculate_macd(df):
    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['DIF'] = ema12 - ema26
    df['MACD_LINE'] = df['DIF'].ewm(span=9, adjust=False).mean()
    df['MACD_HIST'] = df['DIF'] - df['MACD_LINE']
    return df

# --- 報價核心邏輯 ---
def get_quote(msg):
    msg = msg.upper().strip()
    if msg.isdigit() and len(msg) >= 4:
        try:
            start = (datetime.datetime.now() - datetime.timedelta(days=60)).strftime('%Y-%m-%d')
            df = dl.taiwan_stock_daily(stock_id=msg, start_date=start)
            if len(df) >= 2:
                today, yesterday = df.iloc[-1], df.iloc[-2]
                price, open_p, prev_c = today['close'], today['open'], yesterday['close']
                diff_p, per_p = price - prev_c, ((price - prev_c) / prev_c) * 100
                diff_o, per_o = price - open_p, ((price - open_p) / open_p) * 100
                s_p = "🔺" if diff_p > 0 else "🔻"
                s_o = "🔺" if diff_o > 0 else "🔻"
                return (f"【台股】{today.get('stock_name', '股票')} ({msg})\n目前價格：{price} TWD\n---\n"
                        f"昨日收盤：{prev_c} TWD\n總漲跌幅：{s_p}{diff_p:+.2f} ({per_p:+.2f}%)\n---\n"
                        f"今日開盤：{open_p} TWD\n盤中走勢：{s_o}{diff_o:+.2f} ({per_o:+.2f}%)")
        except: pass
    elif msg.isalpha() and 1 <= len(msg) <= 5:
        try:
            stock = yf.Ticker(msg)
            df = stock.history(period='60d')
            if len(df) >= 2:
                tc, to, pc = df['Close'].iloc[-1], df['Open'].iloc[-1], df['Close'].iloc[-2]
                dp, pp = tc - pc, (tc - pc) / pc * 100
                do, po = tc - to, (tc - to) / to * 100
                sp, so = ("🔺" if dp > 0 else "🔻"), ("🔺" if do > 0 else "🔻")
                return (f"【美股】代碼：{msg}\n目前價格：${tc:.2f} USD\n---\n"
                        f"昨日收盤：${pc:.2f} USD\n總漲跌幅：{sp}{dp:+.2f} ({pp:+.2f}%)\n---\n"
                        f"今日開盤：${to:.2f} USD\n盤中走勢：{so}{do:+.2f} ({po:+.2f}%)")
        except: pass
    return None

# --- 🚀 全市場掃描 ---
def weekly_scan():
    print(f"[{datetime.datetime.now()}] 🚀 啟動全台股嚴選掃描...")
    try:
        df_info = dl.taiwan_stock_info()
        all_listed = df_info[(df_info['type'] == 'twse') & (df_info['stock_id'].str.len() == 4)].drop_duplicates(subset=['stock_id'])
    except Exception as e:
        print(f"❌ 取得清單失敗: {e}"); return

    drop_list = []
    for _, row in all_listed.iterrows():
        sid, sname = row['stock_id'], row['stock_name']
        try:
            df = yf.Ticker(f"{sid}.TW").history(period="8mo")
            if len(df) >= 65:
                df['60MA'] = df['Close'].rolling(window=60).mean()
                df = calculate_kd(df)
                df = calculate_macd(df)
                recent_40 = df.tail(40)
                max_h, cur_p = recent_40['High'].max(), df['Close'].iloc[-1]
                cur_60, pre_60 = df['60MA'].iloc[-1], df['60MA'].iloc[-2]
                cur_k, cur_d = df['K'].iloc[-1], df['D'].iloc[-1]
                cur_osc, pre_osc = df['MACD_HIST'].iloc[-1], df['MACD_HIST'].iloc[-2]
                drop_per = ((cur_p / max_h) - 1) * 100
                if (drop_per <= -15 and cur_p > cur_60 and cur_60 > pre_60 and 
                   (cur_k < 30 or cur_k > cur_d) and cur_osc > pre_osc):
                    macd_status = "🔴紅柱增長" if cur_osc > 0 else "🟢綠柱縮短"
                    drop_list.append(f"⚠️ {sname}({sid}): 跌 {drop_per:.1f}%\n(價:{cur_p:.2f} / K:{cur_k:.1f} / {macd_status})")
            time.sleep(0.1)
        except: continue

    if drop_list:
        header = "📢 【台股三位一體嚴選掃描】\n條件：回檔15%+季線保護+KD/MACD轉強\n---\n\n"
        for i in range(0, len(drop_list), 20):
            report = header + "\n\n".join(drop_list[i:i+20])
            line_bot_api.push_message(MY_USER_ID, TextSendMessage(text=report))
            time.sleep(3)
    else:
        line_bot_api.push_message(MY_USER_ID, TextSendMessage(text="✅ 掃描完畢：目前無符合嚴選標的。"))

# --- 任務定義 ---
def daily_report():
    targets = ["2330", "2308", "2454", "3711", "2408"] 
    results = [get_quote(t) for t in targets if get_quote(t)]
    if results:
        report = "📢 台股盤前報價：\n\n" + "\n---\n".join(results)
        line_bot_api.push_message(MY_USER_ID, TextSendMessage(text=report)) 

def us_night_report():
    targets = ["AAPL", "TSLA", "NVDA", "MSFT" ,"AMD" , "AMZN" ,"MU"] 
    results = [get_quote(t) for t in targets if get_quote(t)]
    if results:
        report = "📢 美股開盤報價：\n\n" + "\n---\n".join(results)
        line_bot_api.push_message(MY_USER_ID, TextSendMessage(text=report))

# 【排程器】
scheduler = BackgroundScheduler(timezone="Asia/Taipei")
scheduler.add_job(daily_report, 'cron', day_of_week='mon-fri', hour=9, minute=0)
scheduler.add_job(us_night_report, 'cron', day_of_week='mon-fri', hour=21, minute=30)
scheduler.add_job(weekly_scan, 'cron', day_of_week='sat', hour=22, minute=45)
scheduler.start()

# 💡 新增：防休眠首頁 (供 Cron-job.org 敲門使用)
@app.route("/", methods=['GET'])
def index():
    return "Bot is alive!", 200

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event): 
    result = get_quote(event.message.text)
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=result if result else "請輸入代號查詢"))

if __name__ == "__main__":
    # 💡 雲端必備：自動讀取系統分配的 PORT 
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)