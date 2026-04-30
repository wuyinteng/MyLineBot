import os  # 1. 必須新增這行，才能讀取雲端環境設定 
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import yfinance as yf
from FinMind.data import DataLoader  # 匯入台股工具
import datetime

app = Flask(__name__)
dl = DataLoader()

# --- 你的金鑰已保留在下方 ---
line_bot_api = LineBotApi('0PkQu4ePT9fMFke5+i/e6A1cxm7dD4Nt04K47Uq7Pxy5vIUxKnIzaYUCBcNGJ1Y/RWscQlvRknxmtdioggR+rI LSsd28GBtd1lbDcvPgv1UkrIcrrDEOgHZNgQl1b6HH8mRpvvDLUBzPH4FVOnOGwAdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('6394456d4596cc6aadb9c92dda96b296')

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
    user_msg = event.message.text.upper().strip()
    
    # --- 邏輯 A：台股報價 ---
    if user_msg.isdigit() and len(user_msg) >= 4:
        try:
            start_date = (datetime.datetime.now() - datetime.timedelta(days=5)).strftime('%Y-%m-%d')
            df = dl.taiwan_stock_daily(stock_id=user_msg, start_date=start_date)
            if not df.empty:
                latest_data = df.iloc[-1]
                price = latest_data['close']
                stock_name = latest_data.get('stock_name', '該股票')
                reply_text = f"【台股報價】\n{stock_name} ({user_msg})\n收盤價格：{price} TWD"
            else:
                reply_text = f"查不到台股代號「{user_msg}」。"
        except Exception as e:
            reply_text = f"台股連線出錯。"

    # --- 邏輯 B：美股報價 ---
    elif user_msg.isalpha() and 1 <= len(user_msg) <= 5:
        try:
            stock = yf.Ticker(user_msg)
            data = stock.history(period='1d')
            if not data.empty:
                price = round(data['Close'].iloc[-1], 2)
                reply_text = f"【美股報價】\n股票代碼：{user_msg}\n目前價格：${price} USD"
            else:
                reply_text = f"查不到美股代號「{user_msg}」。"
        except:
            reply_text = "美股連線出錯。"
            
    # --- 邏輯 C：其他訊息 ---
    else:
        reply_text = f"收到訊息：{user_msg}\n請輸入台股代號(如 2330) 或美股代號(如 TSLA)"

    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

# 2. 修改啟動邏輯，適應雲端伺服器的 Port 
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))  # 自動抓取雲端指定的 Port，抓不到就用 5000 
    app.run(host='0.0.0.0', port=port)  # host 設定為 0.0.0.0 才能接收外部連線