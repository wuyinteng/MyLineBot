from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import yfinance as yf
from FinMind.data import DataLoader
import datetime
import os
from apscheduler.schedulers.background import BackgroundScheduler 

app = Flask(__name__)
dl = DataLoader()

# --- 改為從環境變數讀取，安全性提升 ---
# 在 Render 的 Environment 介面設定這些變數
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

def get_quote(msg):
    msg = msg.upper().strip()
    # 台股判斷 (4碼以上數字)
    if msg.isdigit() and len(msg) >= 4:
        try:
            # 考慮週末，取過去 5 天的資料確保能拿到最新收盤價
            start = (datetime.datetime.now() - datetime.timedelta(days=5)).strftime('%Y-%m-%d')
            df = dl.taiwan_stock_daily(stock_id=msg, start_date=start)
            if not df.empty:
                data = df.iloc[-1]
                return f"【台股】{data.get('stock_name', '股票')} ({msg})\n價格：{data['close']} TWD"
            return "查無此台股代碼資料"
        except Exception as e:
            print(f"FinMind Error: {e}")
            return "台股連線異常"
            
    # 美股判斷 (1-5碼英文字母)
    elif msg.isalpha() and 1 <= len(msg) <= 5:
        try:
            stock = yf.Ticker(msg)
            df = stock.history(period='1d')
            if not df.empty:
                price = round(df['Close'].iloc[-1], 2)
                return f"【美股】代碼：{msg}\n目前價格：${price} USD"
            return "查無此美股代碼"
        except Exception as e:
            print(f"yfinance Error: {e}")
            return "美股連線異常"
    return None

def daily_report():
    # 加入你感興趣的標的，例如台積電、台達電、聯發科等
    targets = ["2330", "2308", "2454", "3711", "2408"]
    results = [get_quote(t) for t in targets if get_quote(t)]
    if results:
        report = "台股開盤報價：\n\n" + "\n---\n".join(results)
        # 使用 broadcast 群發給所有加入好友的使用者
        line_bot_api.broadcast(TextSendMessage(text=report)) 

def us_night_report():
    targets = ["AAPL", "TSLA", "NVDA", "MSFT" ,"AMD" , "AMZN" ,"MU"]
    results = [get_quote(t) for t in targets if get_quote(t)]
    if results:
        report = "美股開盤報價：\n\n" + "\n---\n".join(results)
        line_bot_api.broadcast(TextSendMessage(text=report))

# 【定時任務】
# 注意：Render 免費版會休眠，休眠期間 Scheduler 不會執行
scheduler = BackgroundScheduler(timezone="Asia/Taipei")
scheduler.add_job(daily_report, 'cron', day_of_week='mon-fri', hour=9, minute=0)
scheduler.add_job(us_night_report, 'cron', day_of_week='mon-fri', hour=21, minute=30)
scheduler.start()

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event): 
    user_msg = event.message.text
    result = get_quote(user_msg)
    if result:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=result))
    else:
        # 如果不是股票代號，維持原樣提醒
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請輸入股票代號查詢（如：2330 或 TSLA）"))

if __name__ == "__main__":
    # 這裡保留你的 port 設定，適合 Render 環境
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)