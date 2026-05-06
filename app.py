from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage, QuickReply, QuickReplyButton, MessageAction
import yfinance as yf
from FinMind.data import DataLoader
import datetime
from apscheduler.schedulers.background import BackgroundScheduler
import time
import pandas as pd
import os  # 讀取雲端系統資訊必備

# --- 畫圖所需套件 ---
import matplotlib
matplotlib.use('Agg') # ⚠️非常重要：告訴 matplotlib 在無螢幕的環境背景畫圖
import mplfinance as mpf
import requests
import base64
import io

app = Flask(__name__)

# --- ImgBB API 金鑰 ---
IMGBB_API_KEY = "4bc61e9d363f21433c906beb7440dd92"

# --- FinMind 伺服器登入 ---
dl = DataLoader()
dl.login_by_token(api_token='eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoid3V5aW50ZW5nIiwiZW1haWwiOiJ3dXlpbnRlbmcxMjA2QGdtYWlsLmNvbSJ9.3-HFSvEh15UnzB4Nt_TZUYLCF7OSjrDuB31fwZ1foJA')

# --- LINE Bot 金鑰設定 ---
line_bot_api = LineBotApi('0PkQu4ePT9fMFke5+i/e6A1cxm7dD4Nt04K47Uq7Pxy5vIUxKnIzaYUCBcNGJ1Y/RWscQlvRknxmtdioggR+rI LSsd28GBtd1lbDcvPgv1UkrIcrrDEOgHZNgQl1b6HH8mRpvvDLUBzPH4FVOnOGwAdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('6394456d4596cc6aadb9c92dda96b296')

MY_USER_ID = 'U288dc1f88aabee28ca0342d542b8040f'

# --- 建立台股名稱字典 (啟動時執行一次，加快查詢速度) ---
tw_stock_dict = {}
try:
    print("正在從 FinMind 載入台股清單以優化名稱查詢...")
    df_info = dl.taiwan_stock_info()
    tw_stock_dict = dict(zip(df_info['stock_id'], df_info['stock_name']))
    print(f"成功載入 {len(tw_stock_dict)} 檔股票名稱！")
except Exception as e:
    print(f"台股清單載入失敗: {e}")

# --- [修改] 核心畫圖函式 (全英文專業版，避開方塊字與 Yahoo 阻擋) ---
def generate_chart(stock_id, chart_type="K"):
    try:
        df = pd.DataFrame()
        ticker = f"{stock_id}.TW" if stock_id.isdigit() else stock_id
        
        if stock_id.isdigit() and chart_type == "K":
            # 【台股 K 線】：使用 FinMind 繞過 Yahoo 阻擋
            start_date = (datetime.datetime.now() - datetime.timedelta(days=100)).strftime('%Y-%m-%d')
            df = dl.taiwan_stock_daily(stock_id=stock_id, start_date=start_date)
            if not df.empty:
                # 轉換欄位名稱，讓 mplfinance 看得懂
                df = df.rename(columns={'date': 'Date', 'open': 'Open', 'max': 'High', 'min': 'Low', 'close': 'Close', 'Trading_Volume': 'Volume'})
                df['Date'] = pd.to_datetime(df['Date'])
                df.set_index('Date', inplace=True)
            
            plot_type = 'candle'
            title_suffix = "3-Month Chart"
            dt_format = "%m/%d"
            
        else:
            # 【美股 或 當日走勢圖】：使用 yfinance 並加上「偽裝面具」防封鎖
            session = requests.Session()
            session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'})
            stock = yf.Ticker(ticker, session=session)
            
            if chart_type == "K":
                df = stock.history(period="3mo")
                plot_type = 'candle'
                title_suffix = "3-Month Chart"
                dt_format = "%m/%d"
            else:
                df = stock.history(period="1d", interval="1m")
                plot_type = 'line'
                title_suffix = "Intraday Trend"
                dt_format = "%H:%M"

        if df.empty: 
            print(f"[{stock_id}] 抓不到資料")
            return None

        # 🏆 設定全英文圖表標題 (不再依賴中文字型)
        title_text = f"[{stock_id}] {title_suffix}"

        # --- 開始繪圖 ---
        buf = io.BytesIO()
        mc = mpf.make_marketcolors(up='r', down='g', inherit=True)
        s = mpf.make_mpf_style(marketcolors=mc)

        # 畫圖 (標籤全部改為英文 Price 與 Volume，避免亂碼)
        mpf.plot(df, type=plot_type, volume=(chart_type=="K"), style=s, 
                 title=title_text, ylabel="Price", ylabel_lower="Volume",
                 datetime_format=dt_format, savefig=buf, show_nontrading=False)

        # --- 上傳至 ImgBB ---
        buf.seek(0)
        img_base64 = base64.b64encode(buf.read()).decode('utf-8')
        res = requests.post("https://api.imgbb.com/1/upload", data={"key": IMGBB_API_KEY, "image": img_base64})
        
        if res.status_code == 200:
            return res.json()["data"]["url"]
        return None
        
    except Exception as e:
        print(f"畫圖失敗: {e}")
        return None

# --- 報價取得函式 ---
def get_quote(msg):
    msg = msg.upper().strip()
    
    # 1. 台股報價邏輯 (改用 FinMind，繞過 Yahoo 阻擋)
    if msg.isdigit() and len(msg) >= 4:
        try:
            stock_name = tw_stock_dict.get(msg, "")
            name_display = f"{stock_name} ({msg})" if stock_name else f"代碼：{msg}"
            
            start_date = (datetime.datetime.now() - datetime.timedelta(days=10)).strftime('%Y-%m-%d')
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
scheduler.add_job(us_market_closing_report, 'cron', day_of_week='mon-sat', hour=8, minute=0)
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

# --- [修改] LINE 收到訊息的處理邏輯 (結合雙按鈕) ---
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event): 
    user_msg = event.message.text.strip().upper()
    user_id = event.source.user_id

    # 1. 判斷是否為「看 K 線圖」指令
    if user_msg.startswith("K"):
        sid = user_msg.replace("K", "")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🖌️ 正在為您繪製 {sid} 的 K 線圖..."))
        url = generate_chart(sid, "K")
        if url: 
            line_bot_api.push_message(user_id, ImageSendMessage(original_content_url=url, preview_image_url=url))
        else:
            line_bot_api.push_message(user_id, TextSendMessage(text="❌ 圖片產生失敗，可能是網路超載或查無此股票資料，請稍後再試。"))
        return
    
    # 2. 判斷是否為「看 走勢圖」指令
    if user_msg.startswith("走"):
        sid = user_msg.replace("走", "")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"📈 正在為您抓取 {sid} 即時走勢..."))
        url = generate_chart(sid, "走")
        if url: 
            line_bot_api.push_message(user_id, ImageSendMessage(original_content_url=url, preview_image_url=url))
        else:
            line_bot_api.push_message(user_id, TextSendMessage(text="❌ 圖片產生失敗，請稍後再試。"))
        return

    # 3. 處理一般的報價查詢
    result = get_quote(user_msg)
    
    if result and "找不到" not in result and "發生錯誤" not in result:
        # 報價成功，建立兩個魔法按鈕
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="📊 K 線圖", text=f"K{user_msg}")),
            QuickReplyButton(action=MessageAction(label="📈 當日走勢", text=f"走{user_msg}"))
        ])
        # 回傳報價文字 + 按鈕
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=result, quick_reply=quick_reply))
    else:
        # 報價失敗或查無代號，只回傳純文字
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=result if result else "請輸入正確的股票代號查詢 (例如: 2330 或 AAPL)"))

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)