from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage, QuickReply, QuickReplyButton, MessageAction
import yfinance as yf
from FinMind.data import DataLoader
import datetime
from datetime import timedelta
import pandas as pd
import os
import matplotlib
matplotlib.use('Agg')
import mplfinance as mpf
import requests
import base64
import io

app = Flask(__name__)

# ==========================================
# 🔑 1. 金鑰與初始化設定
# ==========================================
FINMIND_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoid3V5aW50ZW5nIiwiZW1haWwiOiJ3dXlpbnRlbmcxMjA2QGdtYWlsLmNvbSIsInRva2VuX3ZlcnNpb24iOjB9.4roGRm1h6ihqAubqa5aqEDOweoFoXCkKuU408HXyt90" 
IMGBB_API_KEY = "4bc61e9d363f21433c906beb7440dd92"
LINE_CHANNEL_ACCESS_TOKEN = '8g/5K/9WQ7EiuEm16BBJ/aOjy7beli9UQS1oKoX3Jswq1iGuYxvlvT+OLpWO4ZTjRWscQlvRknxmtdioggR+rILSsd28GBtd1lbDcvPgv1VEE6yzdGScPxD/Evstgxtd6+lFTohe+R5lBjVi/+fqpQdB04t89/1O/w1cDnyilFU='

dl = DataLoader()
if FINMIND_TOKEN: dl.login_by_token(api_token=FINMIND_TOKEN)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler('6394456d4596cc6aadb9c92dda96b296')

# --- 建立台股名稱字典 ---
tw_stock_dict = {}
try:
    df_info = dl.taiwan_stock_info()
    tw_stock_dict = dict(zip(df_info['stock_id'], df_info['stock_name']))
except: pass

# ==========================================
# 🌟 LINE 讀取中動畫
# ==========================================
def show_loading_animation(chat_id, loading_seconds=10):
    url = "https://api.line.me/v2/bot/chat/loading/start"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    data = {"chatId": chat_id, "loadingSeconds": loading_seconds}
    try: requests.post(url, headers=headers, json=data, timeout=3)
    except: pass

# ==========================================
# 📈 2. 繪圖與報價函式 (含均線位置判斷)
# ==========================================
def generate_chart(stock_id, chart_type="K"):
    try:
        df = pd.DataFrame()
        ma_status_text = ""
        
        # 轉換常見的四大指數中文關鍵字到對應代號
        index_mapping = {
            "道瓊": "^DJI", "標普": "^GSPC", "那斯達克": "^IXIC", "費城半導體": "^SOX", "費半": "^SOX"
        }
        for k, v in index_mapping.items():
            if k in stock_id:
                stock_id = v
                break

        # 1. 處理台股 (全數字)
        if stock_id.isdigit():
            if chart_type == "K":
                # K線圖多抓一點歷史數據（120天）以利計算 20日均線
                start_date = (datetime.datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d')
                df = dl.taiwan_stock_daily(stock_id=stock_id, start_date=start_date)
                if not df.empty:
                    df = df.rename(columns={'date': 'Date', 'open': 'Open', 'max': 'High', 'min': 'Low', 'close': 'Close', 'Trading_Volume': 'Volume'})
                    df['Date'] = pd.to_datetime(df['Date'])
                    df.set_index('Date', inplace=True)
                plot_type, title_suffix, dt_format = 'candle', "3-Month Chart (MA 5/10/20)", "%m/%d"
            else:
                req_interval = "5m"
                stock = yf.Ticker(f"{stock_id}.TW")
                df = stock.history(period="5d", interval=req_interval)
                if df.empty:
                    stock = yf.Ticker(f"{stock_id}.TWO")
                    df = stock.history(period="5d", interval=req_interval)
                if not df.empty: df = df.dropna()
                if not df.empty and len(df) >= 2:
                    df.index = df.index.tz_localize(None)
                    last_day = df.index[-1].date()
                    df = df[df.index.date == last_day]
                plot_type, title_suffix, dt_format = 'line', "Intraday Trend", "%H:%M"

        # 2. 處理美股與指數
        else:
            stock = yf.Ticker(stock_id)
            if chart_type == "K":
                df = stock.history(period="6mo") # 多抓歷史數據確保均線完整
                if not df.empty: df.index = df.index.tz_localize(None)
                plot_type, title_suffix, dt_format = 'candle', "3-Month Chart (MA 5/10/20)", "%m/%d"
            else:
                req_interval = "5m"
                df = stock.history(period="5d", interval=req_interval)
                if not df.empty: df = df.dropna()
                if not df.empty and len(df) >= 2:
                    df.index = df.index.tz_localize(None)
                    last_day = df.index[-1].date()
                    df = df[df.index.date == last_day]
                plot_type, title_suffix, dt_format = 'line', "Intraday Trend", "%H:%M"
        
        if df.empty or len(df) < 2: return None, ""

        # 🌟 核心修改：判斷目前股價與均線的相對位置
        if chart_type == "K" and len(df) >= 20:
            df['MA5'] = df['Close'].rolling(window=5).mean()
            df['MA10'] = df['Close'].rolling(window=10).mean()
            df['MA20'] = df['Close'].rolling(window=20).mean()
            
            latest = df.iloc[-1]
            c_price = latest['Close']
            
            # 判斷位置：如果大於等於均線就是「站上(上)」，小於就是「跌破(下)」
            pos5 = "🔼 站上" if c_price >= latest['MA5'] else "🔽 跌破"
            pos10 = "🔼 站上" if c_price >= latest['MA10'] else "🔽 跌破"
            pos20 = "🔼 站上" if c_price >= latest['MA20'] else "🔽 跌破"
            
            ma_status_text = (
                f"\n\n📈 【當前股價與均線位置】\n"
                f"最新收盤價：{c_price:.2f}\n"
                f"--------------------\n"
                f"▪️ 5日均線 ({latest['MA5']:.2f}): {pos5}均線\n"
                f"▪️ 10日均線 ({latest['MA10']:.2f}): {pos10}均線\n"
                f"▪️ 20日均線 ({latest['MA20']:.2f}): {pos20}均線"
            )
            
            # 畫圖時只留近 60 筆資料，畫面比較乾淨
            df = df.tail(60)

        buf = io.BytesIO()
        mc = mpf.make_marketcolors(up='r', down='g', inherit=True)
        s = mpf.make_mpf_style(marketcolors=mc)
        
        # 在 K 線圖模式下傳入 mav=(5, 10, 20) 參數畫出三條線
        if chart_type == "K":
            mpf.plot(df, type=plot_type, volume=(chart_type=="K" and "^" not in stock_id), style=s, title=f"[{stock_id}] {title_suffix}", ylabel="Price", datetime_format=dt_format, savefig=buf, show_nontrading=False, mav=(5, 10, 20))
        else:
            mpf.plot(df, type=plot_type, volume=False, style=s, title=f"[{stock_id}] {title_suffix}", ylabel="Price", datetime_format=dt_format, savefig=buf, show_nontrading=False)
            
        buf.seek(0)
        res = requests.post("https://api.imgbb.com/1/upload", data={"key": IMGBB_API_KEY, "image": base64.b64encode(buf.read()).decode('utf-8')})
        if res.status_code == 200: 
            return res.json()["data"]["url"], ma_status_text
        return None, ""
    except Exception as e: 
        print(f"繪圖出錯: {str(e)}")
        return None, ""

def get_quote(msg):
    msg = msg.upper().strip()
    
    if msg in ["四大指數", "美股指數", "美股四大指數", "INDEX"]:
        indices = {
            "🇺🇸 道瓊工業 (^DJI)": "^DJI",
            "🇺🇸 標普 500 (^GSPC)": "^GSPC",
            "🇺🇸 那斯達克 (^IXIC)": "^IXIC",
            "🇺🇸 費城半導體 (^SOX)": "^SOX"
        }
        output = "📊 【美股四大指數最新報價】\n====================\n"
        for name, ticker_symbol in indices.items():
            try:
                df = yf.Ticker(ticker_symbol).history(period="2d")
                if len(df) >= 2:
                    tc, pc = df['Close'].iloc[-1], df['Close'].iloc[-2]
                    dp, pp = tc - pc, (tc - pc) / pc * 100
                    sp = "🔺" if dp > 0 else ("🔻" if dp < 0 else "➖")
                    output += f"{name}\n指數：{tc:,.2f}\n漲跌：{sp}{dp:+.2f} ({pp:+.2f}%)\n--------------------\n"
            except:
                output += f"{name} 獲取失敗\n--------------------\n"
        return output.strip("\n--------------------")

    if msg.isdigit() and len(msg) >= 4:
        try:
            stock_name = tw_stock_dict.get(msg, "")
            name_display = f"{stock_name} ({msg})" if stock_name else f"代碼：{msg}"
            start_date = (datetime.datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')
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
        except Exception as e: return f"查詢錯誤：{str(e)}"
        
    else:
        index_mapping = {"道瓊": "^DJI", "標普": "^GSPC", "那斯達克": "^IXIC", "費城半導體": "^SOX", "費半": "^SOX"}
        if msg in index_mapping:
            msg = index_mapping[msg]

        try:
            stock = yf.Ticker(msg)
            df = stock.history(period="5d")
            
            if df.empty:
                return f"找不到代號【{msg}】的資料，請確認輸入是否正確。"
            
            if len(df) >= 2:
                tc, to, pc = df['Close'].iloc[-1], df['Open'].iloc[-1], df['Close'].iloc[-2]
                dp, pp = tc - pc, (tc - pc) / pc * 100
                do, po = tc - to, (tc - to) / to * 100
                sp = "🔺" if dp > 0 else ("🔻" if dp < 0 else "➖")
                so = "🔺" if do > 0 else ("🔻" if do < 0 else "➖")
                
                currency = "USD" if "^" in msg or msg.isalpha() else "TWD"
                title_label = "【美股 / 指數】" if "^" in msg or msg.isalpha() else "【市場標的】"
                
                return (f"{title_label} {msg}\n目前價格：{tc:.2f} {currency}\n---\n"
                        f"前日收盤：{pc:.2f} {currency}\n總漲跌幅：{sp}{dp:+.2f} ({pp:+.2f}%)\n---\n"
                        f"今日開盤：{to:.2f} {currency}\n盤中走勢：{so}{do:+.2f} ({po:+.2f}%)")
        except Exception as e:
            return f"查詢錯誤：{str(e)}"
            
    return None

# ==========================================
# 🌐 3. 伺服器與 LINE 路由處理
# ==========================================
@app.route("/", methods=['GET'])
def index(): return "Stock Bot is Alive!"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'

# ==========================================
# 💬 4. 接收訊息與邏輯分流
# ==========================================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event): 
    user_msg = event.message.text.strip().upper()
    
    chat_id = event.source.user_id
    if event.source.type == 'group': chat_id = event.source.group_id
    elif event.source.type == 'room': chat_id = event.source.room_id
    
    if user_msg.startswith("K"):
        sid = user_msg.replace("K", "")
        show_loading_animation(chat_id)
        # 🌟 接收圖片網址與均線位置判斷文字
        url, ma_status_text = generate_chart(sid, "K")
        stock_name = tw_stock_dict.get(sid, f"代號 {sid}")
        if url: 
            line_bot_api.reply_message(event.reply_token, [
                TextSendMessage(text=f"✅ 已經為您繪製【{stock_name}】的 5/10/20 均線圖囉！{ma_status_text}"),
                ImageSendMessage(original_content_url=url, preview_image_url=url)
            ])
        else: 
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="圖片產生失敗，請確認代號是否正確或稍後再試。"))
        return

    if user_msg.startswith("走"):
        sid = user_msg.replace("走", "")
        show_loading_animation(chat_id)
        url, _ = generate_chart(sid, "走")
        stock_name = tw_stock_dict.get(sid, f"代號 {sid}")
        if url: 
            line_bot_api.reply_message(event.reply_token, [
                TextSendMessage(text=f"✅ 已經為您繪製【{stock_name}】的今日盤中走勢圖囉！"),
                ImageSendMessage(original_content_url=url, preview_image_url=url)
            ])
        else: 
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="圖片產生失敗，請確認代號是否正確或稍後再試。"))
        return
        
    result = get_quote(user_msg)
    if result and "找不到" not in result and "錯誤" not in result:
        
        if user_msg in ["四大指數", "美股指數", "美股四大指數", "INDEX"]:
            buttons = [
                QuickReplyButton(action=MessageAction(label="📈 道瓊走勢", text="走^DJI")),
                QuickReplyButton(action=MessageAction(label="📊 道瓊 K 線", text="K^DJI")),
                QuickReplyButton(action=MessageAction(label="📈 那指走勢", text="走^IXIC"))
            ]
        else:
            buttons = [
                QuickReplyButton(action=MessageAction(label="📈 當日走勢", text=f"走{user_msg}")),
                QuickReplyButton(action=MessageAction(label="📊 K 線圖", text=f"K{user_msg}"))
            ]

        quick_reply = QuickReply(items=buttons)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=result, quick_reply=quick_reply))
    else:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=result if result else "請輸入正確代號（台股如 2330，美股如 AAPL，或輸入 '四大指數'）"))

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)