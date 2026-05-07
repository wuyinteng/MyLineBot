from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage, QuickReply, QuickReplyButton, MessageAction, ImageMessage
import yfinance as yf
from FinMind.data import DataLoader
import datetime
from apscheduler.schedulers.background import BackgroundScheduler
import time
import pandas as pd
import os
import matplotlib
matplotlib.use('Agg')
import mplfinance as mpf
import requests
import base64
import io
import cv2
import numpy as np

app = Flask(__name__)

# --- 1. 您的庫存設定 (請自行修改成本) ---
my_holdings = {
    '2330': [600.0, 1000],  # 格式：'代號': [買進價格, 股數]
    '2317': [100.5, 2000],
    'NVDA': [120.0, 10]
}

# --- 基礎設定 ---
IMGBB_API_KEY = "4bc61e9d363f21433c906beb7440dd92"
dl = DataLoader()
dl.login_by_token(api_token='eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoid3V5aW50ZW5nIiwiZW1haWwiOiJ3dXlpbnRlbmcxMjA2QGdtYWlsLmNvbSJ9.3-HFSvEh15UnzB4Nt_TZUYLCF7OSjrDuB31fwZ1foJA')
line_bot_api = LineBotApi('0PkQu4ePT9fMFke5+i/e6A1cxm7dD4Nt04K47Uq7Pxy5vIUxKnIzaYUCBcNGJ1Y/RWscQlvRknxmtdioggR+rI LSsd28GBtd1lbDcvPgv1UkrIcrrDEOgHZNgQl1b6HH8mRpvvDLUBzPH4FVOnOGwAdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('6394456d4596cc6aadb9c92dda96b296')
MY_USER_ID = 'U288dc1f88aabee28ca0342d542b8040f'

# --- 建立台股名稱字典 ---
tw_stock_dict = {}
try:
    df_info = dl.taiwan_stock_info()
    tw_stock_dict = dict(zip(df_info['stock_id'], df_info['stock_name']))
except: pass

# --- 損益計算報告 ---
def get_portfolio_status():
    report = " 【即時損益報告】\n------------------\n"
    total_cost, total_mkt = 0, 0
    for sid, info in my_holdings.items():
        buy_p, qty = info
        ticker = f"{sid}.TW" if sid.isdigit() else sid
        try:
            s = yf.Ticker(ticker)
            curr_p = s.fast_info['last_price']
            profit_pct = (curr_p - buy_p) / buy_p * 100
            total_cost += buy_p * qty
            total_mkt += curr_p * qty
            icon = "🔺" if curr_p >= buy_p else "🔻"
            name = tw_stock_dict.get(sid, sid)
            report += f"{icon} {name}\n現價:{curr_p:.2f} ({profit_pct:+.2f}%)\n\n"
        except: report += f" {sid} 抓取失敗\n"
    
    if total_cost > 0:
        total_ret = ((total_mkt - total_cost) / total_cost) * 100
        report += f"------------------\n 總回報率：{total_ret:+.2f}%"
    return report

# --- [關鍵] 照片手勢處理 (放在裡面，用完就丟，省記憶體防當機) ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    try:
        # 下載並轉換圖片
        msg_content = line_bot_api.get_message_content(event.message.id)
        img_bytes = b"".join([chunk for chunk in msg_content.iter_content()])
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        
        # 在這裡才「臨時」呼叫 AI 模組
        import mediapipe as mp
        mp_hands = mp.solutions.hands
        
        with mp_hands.Hands(static_image_mode=True, max_num_hands=1, min_detection_confidence=0.5) as hands_engine:
            results = hands_engine.process(img_rgb)
            
            if results.multi_hand_landmarks:
                landmarks = results.multi_hand_landmarks[0].landmark
                fingers = []
                if landmarks[4].x < landmarks[3].x: fingers.append(1) # 拇指
                for tip in [8, 12, 16, 20]: # 其他四指
                    if landmarks[tip].y < landmarks[tip - 2].y: fingers.append(1)
                
                count = len(fingers)
                
                if count == 1:
                    res_text = get_portfolio_status()
                elif 2 <= count <= 5:
                    res_text = f"偵測到數字 {count}：此功能目前尚未設定。"
                else:
                    res_text = "看到手掌了，但沒比出清楚的數字。"
            else:
                res_text = "沒抓到手勢，請對準鏡頭拍照。"

        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=res_text))
        
    except Exception as e:
        print(f"辨識出錯: {e}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"系統過載或發生錯誤，請稍後再拍！"))

# --- 核心畫圖函式 ---
def generate_chart(stock_id, chart_type="K"):
    try:
        df = pd.DataFrame()
        ticker = f"{stock_id}.TW" if stock_id.isdigit() else stock_id
        if stock_id.isdigit() and chart_type == "K":
            start_date = (datetime.datetime.now() - datetime.timedelta(days=100)).strftime('%Y-%m-%d')
            df = dl.taiwan_stock_daily(stock_id=stock_id, start_date=start_date)
            if not df.empty:
                df = df.rename(columns={'date': 'Date', 'open': 'Open', 'max': 'High', 'min': 'Low', 'close': 'Close', 'Trading_Volume': 'Volume'})
                df['Date'] = pd.to_datetime(df['Date'])
                df.set_index('Date', inplace=True)
            plot_type = 'candle'
            title_suffix = "3-Month Chart"
            dt_format = "%m/%d"
        else:
            stock = yf.Ticker(ticker)
            if chart_type == "K":
                df = stock.history(period="3mo")
                plot_type = 'candle'
                title_suffix = "3-Month Chart"
                dt_format = "%m/%d"
            else:
                req_interval = "5m" if stock_id.isdigit() else "1m"
                df = stock.history(period="5d", interval=req_interval)
                if not df.empty:
                    df = df.dropna()
                if not df.empty and len(df) >= 2:
                    df.index = df.index.tz_localize(None)
                    last_day = df.index[-1].date()
                    df = df[df.index.date == last_day]
                plot_type = 'line'
                title_suffix = "Intraday Trend"
                dt_format = "%H:%M"
        
        if df.empty or len(df) < 2: return None
        title_text = f"[{stock_id}] {title_suffix}"
        buf = io.BytesIO()
        mc = mpf.make_marketcolors(up='r', down='g', inherit=True)
        s = mpf.make_mpf_style(marketcolors=mc)
        mpf.plot(df, type=plot_type, volume=(chart_type=="K"), style=s, title=title_text, ylabel="Price", ylabel_lower="Volume", datetime_format=dt_format, savefig=buf, show_nontrading=False)
        buf.seek(0)
        img_base64 = base64.b64encode(buf.read()).decode('utf-8')
        res = requests.post("https://api.imgbb.com/1/upload", data={"key": IMGBB_API_KEY, "image": img_base64})
        if res.status_code == 200: return res.json()["data"]["url"]
        return None
    except: return None

# --- 報價取得函式 ---
def get_quote(msg):
    msg = msg.upper().strip()
    if msg.isdigit() and len(msg) >= 4:
        try:
            stock_name = tw_stock_dict.get(msg, "")
            name_display = f"{stock_name} ({msg})" if stock_name else f"代碼：{msg}"
            start_date = (datetime.datetime.now() - datetime.timedelta(days=10)).strftime('%Y-%m-%d')
            df = dl.taiwan_stock_daily(stock_id=msg, start_date=start_date)
            if df.empty: return f"找不到台股【{name_display}】資料"
            if len(df) >= 2:
                tc, to, pc = df['close'].iloc[-1], df['open'].iloc[-1], df['close'].iloc[-2]
                dp, pp = tc - pc, (tc - pc) / pc * 100
                do, po = tc - to, (tc - to) / to * 100
                sp = "🔺" if dp > 0 else ("🔻" if dp < 0 else "➖")
                so = "🔺" if do > 0 else ("🔻" if do < 0 else "➖")
                return (f"【台股】{name_display}\n目前價格：{tc:.2f} TWD\n---\n"
                        f"前日收盤：{pc:.2f} TWD\n總漲跌幅：{sp}{dp:+.2f} ({pp:+.2f}%)\n---\n"
                        f"今日開盤：{to:.2f} TWD\n盤中走勢：{so}{do:+.2f} ({po:+.2f}%)")
            else: return f"【{name_display}】歷史資料筆數不足。"
        except Exception as e: return f"查詢錯誤：{str(e)}"
    elif msg.isalpha() and 1 <= len(msg) <= 5:
        try:
            stock = yf.Ticker(msg)
            df = stock.history(period='1mo')
            if df.empty: return f"找不到美股【{msg}】資料"
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
            else: return f"【{msg}】歷史資料筆數不足。"
        except Exception as e: return f"查詢錯誤：{str(e)}"
    return None

# --- 定時報告 ---
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

scheduler = BackgroundScheduler(timezone="Asia/Taipei")
scheduler.add_job(us_market_closing_report, 'cron', day_of_week='mon-sat', hour=8, minute=0)
scheduler.add_job(daily_report, 'cron', day_of_week='mon-fri', hour=9, minute=1)
scheduler.add_job(us_night_report, 'cron', day_of_week='mon-fri', hour=21, minute=31)
scheduler.start()

@app.route("/", methods=['GET'])
def index(): return "Stock Bot is Alive!"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event): 
    user_msg = event.message.text.strip().upper()
    user_id = event.source.user_id
    if user_msg.startswith("K"):
        sid = user_msg.replace("K", "")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"正在繪製 {sid} K線圖..."))
        url = generate_chart(sid, "K")
        if url: line_bot_api.push_message(user_id, ImageSendMessage(original_content_url=url, preview_image_url=url))
        else: line_bot_api.push_message(user_id, TextSendMessage(text="圖片產生失敗。"))
        return
    if user_msg.startswith("走"):
        sid = user_msg.replace("走", "")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"正在抓取 {sid} 即時走勢..."))
        url = generate_chart(sid, "走")
        if url: line_bot_api.push_message(user_id, ImageSendMessage(original_content_url=url, preview_image_url=url))
        else: line_bot_api.push_message(user_id, TextSendMessage(text="圖片產生失敗。"))
        return
    
    result = get_quote(user_msg)
    if result and "找不到" not in result and "錯誤" not in result:
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="K 線圖", text=f"K{user_msg}")),
            QuickReplyButton(action=MessageAction(label="當日走勢", text=f"走{user_msg}"))
        ])
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=result, quick_reply=quick_reply))
    else:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=result if result else "請輸入正確代號"))

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)