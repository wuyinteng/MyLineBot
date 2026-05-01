from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import yfinance as yf
from FinMind.data import DataLoader
import datetime
import os  # 新增：讀取雲端環境設定
from apscheduler.schedulers.background import BackgroundScheduler 

app = Flask(__name__)
dl = DataLoader()

# --- 你的金鑰與 ID ---
line_bot_api = LineBotApi('0PkQu4ePT9fMFke5+i/e6A1cxm7dD4Nt04K47Uq7Pxy5vIUxKnIzaYUCBcNGJ1Y/RWscQlvRknxmtdioggR+rI LSsd28GBtd1lbDcvPgv1UkrIcrrDEOgHZNgQl1b6HH8mRpvvDLUBzPH4FVOnOGwAdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('6394456d4596cc6aadb9c92dda96b296')
MY_USER_ID = 'U288dc1f88aabee28ca0342d542b8040f'

def get_quote(msg):
    msg = msg.upper().strip()
    if msg.isdigit() and len(msg) >= 4:
        try:
            start = (datetime.datetime.now() - datetime.timedelta(days=5)).strftime('%Y-%m-%d')
            df = dl.taiwan_stock_daily(stock_id=msg, start_date=start)
            if not df.empty:
                data = df.iloc[-1]
                return f"【台股】{data.get('stock_name', '股票')} ({msg})\n價格：{data['close']} TWD"
        except: return "台股連線異常"
    elif msg.isalpha() and 1 <= len(msg) <= 5:
        try:
            stock = yf.Ticker(msg)
            df = stock.history(period='1d')
            if not df.empty:
                price = round(df['Close'].iloc[-1], 2)
                return f"【美股】代碼：{msg}\n目前價格：${price} USD"
        except: return "美股連線異常"
    return None

def daily_report():
    targets = ["2330", "2308", "2454", "3711", "2408"]
    results = [get_quote(t) for t in targets if get_quote(t)]
    if results:
        report = "📢 台股開盤報價：\n\n" + "\n---\n".join(results)
        line_bot_api.broadcast(TextSendMessage(text=report)) 

def us_night_report():
    targets = ["AAPL", "TSLA", "NVDA", "MSFT" ,"AMD" , "AMZN" ,"MU"]
    results = [get_quote(t) for t in targets if get_quote(t)]
    if results:
        report = "📢 美股開盤報價：\n\n" + "\n---\n".join(results)
        line_bot_api.broadcast(TextSendMessage(text=report))

# 【啟動鬧鐘】
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
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請輸入代號查詢"))

if __name__ == "__main__":
    # Render 會透過環境變數 PORT 告訴程式要聽哪個孔
    # 如果在本地電腦執行，則預設使用 5000 埠
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)