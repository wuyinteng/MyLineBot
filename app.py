from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
import yfinance as yf
from FinMind.data import DataLoader
import datetime
from apscheduler.schedulers.background import BackgroundScheduler
import time
import os # 👉【新增】負責讀取雲端環境變數

app = Flask(__name__)

# --- FinMind 登入設定 ---
dl = DataLoader()
# 載入你專屬的 Token
dl.login_by_token(api_token='eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoid3V5aW50ZW5nIiwiZW1haWwiOiJ3dXlpbnRlbmcxMjA2QGdtYWlsLmNvbSJ9.3-HFSvEh15UnzB4Nt_TZUYLCF7OSjrDuB31fwZ1foJA')

# --- LINE Bot 金鑰設定 ---
line_bot_api = LineBotApi('0PkQu4ePT9fMFke5+i/e6A1cxm7dD4Nt04K47Uq7Pxy5vIUxKnIzaYUCBcNGJ1Y/RWscQlvRknxmtdioggR+rI LSsd28GBtd1lbDcvPgv1UkrIcrrDEOgHZNgQl1b6HH8mRpvvDLUBzPH4FVOnOGwAdB04t89/1O/w1cDnyilFU=')
handler = WebhookHandler('6394456d4596cc6aadb9c92dda96b296')
MY_USER_ID = 'U288dc1f88aabee28ca0342d542b8040f'

# 【報價核心邏輯】抽離出來供手動與定時共用
def get_quote(msg):
    msg = msg.upper().strip()
    
    # 1. 台股判斷 (單支查詢，使用 FinMind)
    if msg.isdigit() and len(msg) >= 4:
        try:
            start = (datetime.datetime.now() - datetime.timedelta(days=60)).strftime('%Y-%m-%d')
            df = dl.taiwan_stock_daily(stock_id=msg, start_date=start)
            
            if len(df) >= 2:
                today = df.iloc[-1]
                yesterday = df.iloc[-2]
                
                price = today['close']
                open_price = today['open']
                prev_close = yesterday['close']
                
                diff_prev = price - prev_close
                per_prev = (diff_prev / prev_close) * 100
                diff_open = price - open_price
                per_open = (diff_open / open_price) * 100
                
                s_prev = "🔺" if diff_prev > 0 else "🔻"
                s_open = "🔺" if diff_open > 0 else "🔻"

                return (f"【台股】{today.get('stock_name', '股票')} ({msg})\n"
                        f"目前價格：{price} TWD\n"
                        f"---\n"
                        f"昨日收盤：{prev_close} TWD\n"
                        f"總漲跌幅：{s_prev}{diff_prev:+.2f} ({per_prev:+.2f}%)\n"
                        f"---\n"
                        f"今日開盤：{open_price} TWD\n"
                        f"盤中走勢：{s_open}{diff_open:+.2f} ({per_open:+.2f}%)")
            return "台股資料不足"
        except Exception as e:
            return f"台股連線異常: {e}"

    # 2. 美股判斷 (單支查詢，使用 yfinance)
    elif msg.isalpha() and 1 <= len(msg) <= 5:
        try:
            stock = yf.Ticker(msg)
            df = stock.history(period='60d')
            
            if len(df) >= 2:
                today_c = df['Close'].iloc[-1]
                today_o = df['Open'].iloc[-1]
                prev_c = df['Close'].iloc[-2]
                
                diff_p = today_c - prev_c
                per_p = (diff_p / prev_c) * 100
                diff_o = today_c - today_o
                per_o = (diff_o / today_o) * 100
                
                s_p = "🔺" if diff_p > 0 else "🔻"
                s_o = "🔺" if diff_o > 0 else "🔻"

                return (f"【美股】代碼：{msg}\n"
                        f"目前價格：${today_c:.2f} USD\n"
                        f"---\n"
                        f"昨日收盤：${prev_c:.2f} USD\n"
                        f"總漲跌幅：{s_p}{diff_p:+.2f} ({per_p:+.2f}%)\n"
                        f"---\n"
                        f"今日開盤：${today_o:.2f} USD\n"
                        f"盤中走勢：{s_o}{diff_o:+.2f} ({per_o:+.2f}%)")
            return "美股資料不足"
        except Exception as e:
            return f"美股連線異常: {e}"
            
    return None

# 【全市場掃描】(FinMind 拿名單 + Yahoo 算價格)
def weekly_scan():
    print(f"[{datetime.datetime.now()}] 🚀 開始全台股上市公司大掃描 (Yahoo引擎啟動)...")
    
    try:
        df_info = dl.taiwan_stock_info()
        all_listed = df_info[(df_info['type'] == 'twse') & (df_info['stock_id'].str.len() == 4)]
        all_listed = all_listed.drop_duplicates(subset=['stock_id'])
        print(f"✅ 共計發現 {len(all_listed)} 支無重複上市公司標的，準備開始分析...")
    except Exception as e:
        print(f"❌ 無法取得股票清單: {e}")
        return

    drop_list = []
    count = 0
    
    for index, row in all_listed.iterrows():
        stock_id = row['stock_id']
        stock_name = row['stock_name']
        
        try:
            count += 1
            if count % 100 == 0: 
                print(f"⏳ 已掃描 {count} 支...")
            
            yf_symbol = f"{stock_id}.TW"
            ticker = yf.Ticker(yf_symbol)
            df = ticker.history(period="3mo")
            
            if len(df) >= 40:
                recent_40 = df.tail(40)
                max_high = recent_40['High'].max()
                current_price = recent_40['Close'].iloc[-1]
                
                drop_per = ((current_price / max_high) - 1) * 100
                
                if drop_per <= -15:
                    drop_list.append(f"⚠️ {stock_name}({stock_id}): 跌 {drop_per:.1f}% (現價:{current_price:.2f})")
            
            time.sleep(0.1) 

        except Exception as e:
            continue

    if drop_list:
        print(f"💡 掃描完成！共找到 {len(drop_list)} 支超跌股，準備發送...") 
        try:
            line_bot_api.push_message(MY_USER_ID, TextSendMessage(text="測試：即將發送超跌報告"))
            header = "📢 【台股全市場超跌掃描】\n條件：較 40 交易日內最高點跌幅超過 15%\n---\n\n"
            for i in range(0, len(drop_list), 20):
                chunk = drop_list[i:i+20]
                report = header + "\n\n".join(chunk) if i == 0 else "\n\n".join(chunk)
                line_bot_api.push_message(MY_USER_ID, TextSendMessage(text=report))
                time.sleep(3)
            print("✅ 專屬推播已全數發出給老闆！")
        except Exception as e:
            print(f"❌ LINE 推播失敗，原因：{e}") 
    else:
        try:
            line_bot_api.push_message(MY_USER_ID, TextSendMessage(text="✅ 本週掃描完畢：目前全市場無回檔超過 15% 的標的。"))
        except:
            pass
        print("✅ 掃描完畢，無超跌標的，已發送平安推播")

# 【定時任務】定義每天要執行的動作
def daily_report():
    targets = ["2330", "2308", "2454", "3711", "2408"] 
    results = [get_quote(t) for t in targets if get_quote(t)]
    if results:
        report = "台股開盤報價：\n\n" + "\n---\n".join(results)
        line_bot_api.push_message(MY_USER_ID, TextSendMessage(text=report)) 

def us_night_report():
    targets = ["AAPL", "TSLA", "NVDA", "MSFT" ,"AMD" , "AMZN" ,"MU"] 
    results = [get_quote(t) for t in targets if get_quote(t)]
    if results:
        report = "美股開盤報價：\n\n" + "\n---\n".join(results)
        line_bot_api.push_message(MY_USER_ID, TextSendMessage(text=report))

# 【啟動鬧鐘】設定時間
scheduler = BackgroundScheduler(timezone="Asia/Taipei")
scheduler.add_job(daily_report, 'cron', day_of_week='mon-fri', hour=9, minute=0)
scheduler.add_job(us_night_report, 'cron', day_of_week='mon-fri', hour=21, minute=30)
scheduler.add_job(weekly_scan, 'cron', day_of_week='sat', hour=2, minute=19)
scheduler.start()


# 👉【新增】防休眠的門鈴：供 cron-job.org 每 10 分鐘敲門使用
@app.route("/", methods=['GET'])
def ping():
    return "LINE Bot is alive and running on Cloud!", 200

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

# 👉【修改】雲端執行的網路與 Port 設定
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)