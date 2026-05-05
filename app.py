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
import os  # [新增] 讀取雲端系統資訊必備

app = Flask(__name__)

# --- FinMind 伺服器登入 ---
dl = DataLoader()
dl.login_by_token(api_token='eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoid3V5aW50ZW5nIiwiZW1haWwiOiJ3dXlpbnRlbmcxMjA2QGdtYWlsLmNvbSJ9.3-HFSvEh15UnzB4Nt_TZUYLCF7OSjrDuB31fwZ1foJA')

# --- LINE Bot 金鑰設定 ---
line_bot_api = LineBotApi('0PkQu4ePT9fMFke5+i/e6A1cxm7dD4Nt04K47Uq7Pxy5vIUxKnIzaYUCBcNGJ1Y/RWscQlvRknxmtdioggR+rI LSsd28GBtd1lbDcvPgv1UkrIcrrDEOgHZNgQl1b6HH8mRpvvDLUBzPH4FVOnOGwAdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('6394456d4596cc6aadb9c92dda96b296')

MY_USER_ID = 'U288dc1f88aabee28ca0342d542b8040f'

# --- [優化] 建立台股名稱字典 (啟動時執行一次，加快查詢速度) ---
tw_stock_dict = {}
try:
    print("正在從 FinMind 載入台股清單以優化名稱查詢...")
    df_info = dl.taiwan_stock_info()
    tw_stock_dict = dict(zip(df_info['stock_id'], df_info['stock_name']))
    print(f"成功載入 {len(tw_stock_dict)} 檔股票名稱！")
except Exception as e:
    print(f"台股清單載入失敗: {e}")

# --- 報價取得函式 (已加上除錯回報機制與拉長抓取時間) ---
def get_quote(msg):
    msg = msg.upper().strip()
    
    # 1. 台股報價邏輯 (改用 FinMind，繞過 Yahoo 阻擋)
    if msg.isdigit() and len(msg) >= 4:
        try:
            stock_name = tw_stock_dict.get(msg, "")
            name_display = f"{stock_name} ({msg})" if stock_name else f"代碼：{msg}"
            
            # 設定抓取過去 10 天的資料，確保有足夠的 K 棒
            start_date = (datetime.datetime.now() - datetime.timedelta(days=10)).strftime('%Y-%m-%d')
            
            # 使用 FinMind 抓取台股資料
            df = dl.taiwan_stock_daily(stock_id=msg, start_date=start_date)
            
            if df.empty:
                return f"找不到台股【{name_display}】資料"
                
            if len(df) >= 2:
                tc, to, pc = df['close'].iloc[-1], df['open'].iloc[-1], df['close'].iloc[-2]
                dp, pp = tc - pc, (tc - pc) / pc * 100
                do, po = tc - to, (tc - to) / to * 100
                sp = "🔺" if dp > 0 else ("🔻" if dp < 0 else "➖")
                so = "🔺" if do > 0 else ("🔻" if do < 0 else "➖")
                
                return (f"【台股】{name_display}\n目前價格：{tc:.2f} TWD\n---\n"
                        f"前日收盤：{pc:.2f} TWD\n總漲跌幅：{sp}{dp:+.2f} ({pp:+.2f}%)\n---\n"
                        f"今日開盤：{to:.2f} TWD\n盤中走勢：{so}{do:+.2f} ({po:+.2f}%)")
            else:
                return f"【{name_display}】歷史資料筆數不足，無法計算。"
        except Exception as e:
            return f"查詢台股 {msg} 發生錯誤：{str(e)}"

    # 2. 美股報價邏輯 (維持使用 yfinance)
    elif msg.isalpha() and 1 <= len(msg) <= 5:
        try:
            stock = yf.Ticker(msg)
            df = stock.history(period='1mo') 
            
            if df.empty:
                return f"找不到美股【{msg}】資料"
                
            if len(df) >= 2:
                try: comp_name = stock.info.get('shortName', msg)
                except: comp_name = msg
                
                tc, to, pc = df['Close'].iloc[-1], df['Open'].iloc[-1], df['Close'].iloc[-2]
                dp, pp = tc - pc, (tc - pc) / pc * 100
                do, po = tc - to, (tc - to) / to * 100
                sp = "🔺" if dp > 0 else ("🔻" if dp < 0 else "➖")
                so = "🔺" if do > 0 else ("🔻" if do < 0 else "➖")
                
                return (f"【美股】{comp_name} ({msg})\n目前價格：${tc:.2f} USD\n---\n"
                        f"前日收盤：${pc:.2f} USD\n總漲跌幅：{sp}{dp:+.2f} ({pp:+.2f}%)\n---\n"
                        f"今日開盤：${to:.2f} USD\n盤中走勢：{so}{do:+.2f} ({po:+.2f}%)")
            else:
                return f"【{msg}】歷史資料筆數不足，無法計算。"
        except Exception as e:
            return f"查詢美股 {msg} 發生錯誤：{str(e)}"
            
    return None

# --- 美股四大指數報價 (早上 08:00) ---
def us_market_closing_report():
    indices = {"^DJI": "道瓊工業", "^GSPC": "標普 500", "^IXIC": "那斯達克", "^SOX": "費城半導體"}
    results = []
    for ticker, name in indices.items():
        try:
            stock = yf.Ticker(ticker)
            df = stock.history(period='2d')
            if len(df) >= 2:
                tc, pc = df['Close'].iloc[-1], df['Close'].iloc[-2]
                dp, pp = tc - pc, (tc - pc) / pc * 100
                sp = "🔺" if dp > 0 else "🔻"
                results.append(f"【{name}】\n收盤：{tc:.2f}\n漲跌：{sp}{dp:+.2f} ({pp:+.2f}%)")
        except: continue
    if results:
        report = "🇺🇸 美股收盤綜合報價：\n\n" + "\n---\n".join(results)
        line_bot_api.push_message(MY_USER_ID, TextSendMessage(text=report))

# --- 其他定時任務 ---
def daily_report():
    targets = ["2330", "2308", "2454", "3711", "2408"] 
    results = [get_quote(t) for t in targets if get_quote(t)]
    if results:
        report = "台股盤前即時報價：\n\n" + "\n---\n".join(results)
        line_bot_api.push_message(MY_USER_ID, TextSendMessage(text=report)) 

def us_night_report():
    targets = ["AAPL", "TSLA", "NVDA", "MSFT" ,"AMD" , "AMZN" ,"MU"] 
    results = [get_quote(t) for t in targets if get_quote(t)]
    if results:
        report = "美股開盤即時報價：\n\n" + "\n---\n".join(results)
        line_bot_api.push_message(MY_USER_ID, TextSendMessage(text=report))

# --- 排程器設定 (Asia/Taipei) ---
scheduler = BackgroundScheduler(timezone="Asia/Taipei")
# 早上 08:00 美股收盤報價 (美股交易日對應台時間週二至週六)
scheduler.add_job(us_market_closing_report, 'cron', day_of_week='mon-sat', hour=8, minute=0)

# 台股盤前與美股開盤報價
scheduler.add_job(daily_report, 'cron', day_of_week='mon-fri', hour=9, minute=1)
scheduler.add_job(us_night_report, 'cron', day_of_week='mon-fri', hour=21, minute=31)
scheduler.start()

# --- 基礎路由 (防休眠敲門用) ---
@app.route("/", methods=['GET'])
def index():
    return "Bot is running!"

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
    # [修改] 針對 Render 環境自動取得 Port 號，並監聽 0.0.0.0
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)