from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage, QuickReply, QuickReplyButton, MessageAction, FlexSendMessage
import yfinance as yf
from FinMind.data import DataLoader
import datetime
from datetime import timedelta
import time
import pandas as pd
import os
import matplotlib
matplotlib.use('Agg')
import mplfinance as mpf
import requests
import base64
import io
import traceback
import google.generativeai as genai
import threading
import json
import uuid  # 🌟 新增：用來產生唯一的網頁報告不重複 ID

app = Flask(__name__)

# ==========================================
# 🔑 1. 金鑰與初始化設定
# ==========================================
GEMINI_API_KEY = "AIzaSyCiQU1PjlYDyk3onLYytPv2ldrVJwD2s8o"
genai.configure(api_key=GEMINI_API_KEY)

# 🌟 升級：改用 gemini-2.5-pro，生成 HTML 的排版與美感最強
model = genai.GenerativeModel('gemini-2.5-pro')

# 🌟 升級：建立全域字典，用來暫存 AI 寫好的網頁報告
report_storage = {}

FINMIND_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoid3V5aW50ZW5nIiwiZW1haWwiOiJ3dXlpbnRlbmcxMjA2QGdtYWlsLmNvbSIsInRva2VuX3ZlcnNpb24iOjB9.4roGRm1h6ihqAubqa5aqEDOweoFoXCkKuU408HXyt90" 
IMGBB_API_KEY = "4bc61e9d363f21433c906beb7440dd92"

dl = DataLoader()
if FINMIND_TOKEN: dl.login_by_token(api_token=FINMIND_TOKEN)

LINE_CHANNEL_ACCESS_TOKEN = '8g/5K/9WQ7EiuEm16BBJ/aOjy7beli9UQS1oKoX3Jswq1iGuYxvlvT+OLpWO4ZTjRWscQlvRknxmtdioggR+rILSsd28GBtd1lbDcvPgv1VEE6yzdGScPxD/Evstgxtd6+lFTohe+R5lBjVi/+fqpQdB04t89/1O/w1cDnyilFU='
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
def show_loading_animation(chat_id, loading_seconds=20):
    url = "https://api.line.me/v2/bot/chat/loading/start"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    data = {"chatId": chat_id, "loadingSeconds": loading_seconds}
    try: requests.post(url, headers=headers, json=data, timeout=3)
    except: pass

# ==========================================
# 📊 2. 技術指標量化引擎
# ==========================================
def get_technical_indicators(stock_id):
    try:
        ticker_id = f"{stock_id}.TW"
        df = yf.Ticker(ticker_id).history(period="6mo")
        if df.empty:
            ticker_id = f"{stock_id}.TWO"
            df = yf.Ticker(ticker_id).history(period="6mo")
            
        if df.empty or len(df) < 30: return "\n📈 技術指標: 數據不足\n"

        delta = df['Close'].diff()
        up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
        rs = up.ewm(com=13, adjust=False).mean() / down.ewm(com=13, adjust=False).mean()
        df['RSI'] = 100 - (100 / (1 + rs))

        exp1 = df['Close'].ewm(span=12, adjust=False).mean()
        exp2 = df['Close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = exp1 - exp2
        df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Histogram'] = df['MACD'] - df['Signal']

        low_min = df['Low'].rolling(window=9).min()
        high_max = df['High'].rolling(window=9).max()
        df['RSV'] = 100 * ((df['Close'] - low_min) / (high_max - low_min))
        df['K'] = df['RSV'].ewm(com=2, adjust=False).mean()
        df['D'] = df['K'].ewm(com=2, adjust=False).mean()
        df['Vol_5MA'] = df['Volume'].rolling(window=5).mean()
        
        latest, prev = df.iloc[-1], df.iloc[-2]
        price_up = latest['Close'] > prev['Close'] 
        vol_breakout = latest['Volume'] > latest['Vol_5MA'] * 1.2 
        vol_shrink = latest['Volume'] < latest['Vol_5MA'] * 0.8 

        if price_up and vol_breakout: vp_status = "🔥 量價齊揚 (主力積極點火，放量上攻)"
        elif price_up and vol_shrink: vp_status = "⚠️ 量縮上漲 (追價意願不足或籌碼已被鎖定)"
        elif not price_up and vol_breakout: vp_status = "🩸 爆量收黑 (警戒！可能有主力高檔出貨，或低檔爆量換手)"
        elif not price_up and vol_shrink: vp_status = "🟢 量縮價跌 (健康回檔洗盤，賣壓不重)"
        elif price_up: vp_status = "📈 溫和上漲 (量能持平)"
        else: vp_status = "📉 溫和下跌 (量能持平)"

        macd_trend = "🔴紅柱增長" if latest['MACD_Histogram'] > 0 and latest['MACD_Histogram'] > prev['MACD_Histogram'] else \
                     "🔴紅柱縮減" if latest['MACD_Histogram'] > 0 else \
                     "🟢綠柱縮減" if latest['MACD_Histogram'] < 0 and latest['MACD_Histogram'] > prev['MACD_Histogram'] else \
                     "🟢綠柱增長"
                     
        kd_cross = "⭐黃金交叉" if prev['K'] < prev['D'] and latest['K'] > latest['D'] else \
                   "⚠️死亡交叉" if prev['K'] > prev['D'] and latest['K'] < latest['D'] else \
                   "偏多" if latest['K'] > latest['D'] else "偏空"

        return (f"\n📈 【最新技術與量價動能】:\n"
                f"- 量價結構: {vp_status} (成交量: {int(latest['Volume']/1000)}張)\n"
                f"- RSI (14日): {round(latest['RSI'], 1)}\n"
                f"- MACD 柱狀圖: {macd_trend} ({round(latest['MACD_Histogram'], 2)})\n"
                f"- KD 指標: K={round(latest['K'], 1)}, D={round(latest['D'], 1)} [{kd_cross}]\n")
    except Exception:
        return "\n📈 技術指標: 暫時無法獲取\n"

# ==========================================
# 💰 3. 基礎面與籌碼直連引擎
# ==========================================
def get_finmind_data(stock_id):
    today = datetime.datetime.now()
    start_date_short = (today - timedelta(days=20)).strftime('%Y-%m-%d') 
    start_date_eps = (today - timedelta(days=730)).strftime('%Y-%m-%d')
    start_date_pe = (today - timedelta(days=1095)).strftime('%Y-%m-%d')
    
    stock_name = tw_stock_dict.get(stock_id, f"台股 {stock_id}")
    latest_price, historical_avg_pe, ttm_eps = 0.0, 15.0, 0.0
    data_summary = f"【首席操盤手核心量化資料庫回傳】\n========================\n"

    def fetch_api_direct(dataset, s_date):
        try:
            res = requests.get("https://api.finmindtrade.com/api/v4/data", 
                               params={"dataset": dataset, "data_id": str(stock_id), "start_date": s_date, "token": FINMIND_TOKEN}, timeout=5)
            data = res.json()
            if data.get('status') == 200 and data.get('data'): return pd.DataFrame(data['data'])
        except: pass
        return pd.DataFrame()

    try:
        ticker = yf.Ticker(f"{stock_id}.TW" if stock_id.isdigit() else stock_id)
        price_df = ticker.history(period="5d")
        if not price_df.empty: latest_price = round(price_df['Close'].iloc[-1], 2)
    except: pass
    data_summary += f"🏢 標的: {stock_id} {stock_name}\n- 市價: {latest_price} 元\n"

    chips_df = fetch_api_direct("TaiwanStockInstitutionalInvestorsBuySell", start_date_short)
    if not chips_df.empty:
        data_summary += "\n📊 近期三大法人買賣超變動明細:\n" + chips_df.tail(10).to_string(index=False) + "\n"

    fs_df = fetch_api_direct("TaiwanStockFinancialStatements", start_date_eps)
    if not fs_df.empty:
        eps_data = fs_df[fs_df['type'] == 'EPS'].copy()
        if not eps_data.empty:
            eps_data['value'] = pd.to_numeric(eps_data['value'], errors='coerce')
            eps_data = eps_data.drop_duplicates(subset=['date']).sort_values('date', ascending=False)
            if len(eps_data) >= 4:
                ttm_eps = round(eps_data.head(4)['value'].sum(), 2)
                data_summary += f"💰 近四季累積 EPS (TTM): {ttm_eps} 元\n"

    pe_df = fetch_api_direct("TaiwanStockPER", start_date_pe)
    if not pe_df.empty:
        pe_df['PER'] = pd.to_numeric(pe_df['PER'], errors='coerce')
        valid_pe = pe_df[(pe_df['PER'] > 5) & (pe_df['PER'] < 35)]['PER']
        if not valid_pe.empty: historical_avg_pe = round(valid_pe.median(), 2)

    data_summary += get_technical_indicators(stock_id)
    return stock_name, data_summary, latest_price, historical_avg_pe, ttm_eps

# ==========================================
# 🤖 4. AI 網頁報告生成核心 (解鎖 HTML+CSS)
# ==========================================
def get_ai_html_report(stock_id):
    try:
        stock_name, real_data_context, latest_price, historical_avg_pe, ttm_eps = get_finmind_data(stock_id)
        
        # 🌟 升級：直接要求 Gemini 輸出精美的 HTML 網頁代碼（自帶深色科技感樣式）
        prompt = f"""
        你是一位頂級的外資券商首席分析師與前端設計大師。請為台股代號 【{stock_id} {stock_name}】 撰寫一份極致精美、高質感的個股研究報告網頁。
        
        【系統量化、籌碼與技術面真實數據】：
        {real_data_context}
        
        【技術參數補充】：
        * 歷史平均本益比：{historical_avg_pe} 倍
        * 近四季累積 EPS：{ttm_eps} 元
        * 最新收盤價：{latest_price} 元

        【格式與設計嚴格要求】：
        1. 必須輸出一個完整的 HTML 網頁原始碼，包含 <!DOCTYPE html> <html> <head> 與 <body>。
        2. 不要使用任何 Markdown 標記（如 ```html），直接以 HTML 字串輸出。
        3. 視覺風格必須是高質感的「深色科技風」：
           - 背景色使用深沉科技黑 (#0d1117 或 #121212)
           - 主要卡片背景使用 #161b22，並帶有細微邊框 (#30363d) 與圓角 (12px)
           - 主要文字使用白色與科技亮灰 (#e6edf2, #8b949e)
           - 強調重點與「上漲/看多」一律使用高亮霓虹紅 (#ff4560 或 #ff5252)
           - 「下跌/看空」一律使用高亮霓虹綠 (#00e396 或 #4caf50)
        4. 版面配置需採用響應式設計 (Responsive)，確保在手機或電腦點開都能完美閱讀。數據請用非常乾淨漂亮的網頁表格 (Table) 呈現。

        【網頁內容結構】：
        - 頂部大標題：【{stock_id} {stock_name}】AI 特戰深度個股研究報告
        - 第一區塊（核心評價）：包含估值算式、AI 推估目標價、潛在獲利空間、強勢買進/逢低布局等核心戰術評級。
        - 第二區塊（五角雷達戰力）：題材面、基本面、技術面、籌碼面、新聞面的分數（各20分，總分100）及短評。
        - 第三區塊（基本面與競爭力）：核心業務、產品線與主要同業（代號與名稱）的競爭利基對比表。
        - 第四區塊（策略與籌碼戰術）：主力籌碼狀態（土洋對作或主力集中）、未來一週的操作守備範圍（支撐價與壓力防守價）。
        """
        
        response = model.generate_content(prompt)
        html_code = response.text.strip()
        
        # 防止模型不聽話加上了 

        if html_code.startswith("```html"):
            html_code = html_code[7:]
        if html_code.endswith("
```"):
            html_code = html_code[:-3]
            
        return stock_name, html_code.strip(), latest_price, historical_avg_pe, ttm_eps

    except Exception as e:
        error_html = f"<html><body style='background:#121212;color:white;padding:20px;'><h2>❌ 報告生成失敗</h2><p>{str(e)}</p></body></html>"
        return stock_id, error_html, 0, 0, 0

# ==========================================
# 🎨 5. 創建 Flex Message 卡片
# ==========================================
def create_report_flex_card(stock_id, stock_name, latest_price, ttm_eps, pe_ratio, report_url):
    flex_json = {
      "type": "bubble",
      "size": "mega",
      "header": {
        "type": "box",
        "layout": "vertical",
        "contents": [
          {
            "type": "text",
            "text": "🤖 AI 首席特戰報告",
            "color": "#aaaaaa",
            "size": "sm",
            "weight": "bold"
          },
          {
            "type": "text",
            "text": f"{stock_name} ({stock_id})",
            "color": "#1DB446",
            "size": "xl",
            "weight": "bold",
            "margin": "sm"
          }
        ],
        "paddingAll": "20px",
        "paddingBottom": "0px"
      },
      "body": {
        "type": "box",
        "layout": "vertical",
        "contents": [
          {
            "type": "text",
            "text": f"{latest_price} 元",
            "size": "3xl",
            "weight": "bold",
            "color": "#333333"
          },
          {
            "type": "text",
            "text": "最新市價",
            "color": "#aaaaaa",
            "size": "sm",
            "margin": "xs"
          },
          {
            "type": "separator",
            "margin": "lg"
          },
          {
            "type": "box",
            "layout": "vertical",
            "margin": "lg",
            "spacing": "sm",
            "contents": [
              {
                "type": "box",
                "layout": "horizontal",
                "contents": [
                  {"type": "text", "text": "近四季 EPS", "color": "#aaaaaa", "size": "sm", "flex": 1},
                  {"type": "text", "text": f"{ttm_eps} 元", "color": "#666666", "size": "sm", "flex": 2, "align": "end", "weight": "bold"}
                ]
              },
              {
                "type": "box",
                "layout": "horizontal",
                "contents": [
                  {"type": "text", "text": "歷史本益比", "color": "#aaaaaa", "size": "sm", "flex": 1},
                  {"type": "text", "text": f"{pe_ratio} 倍", "color": "#666666", "size": "sm", "flex": 2, "align": "end", "weight": "bold"}
                ]
              }
            ]
          }
        ],
        "paddingAll": "20px"
      },
      "footer": {
        "type": "box",
        "layout": "vertical",
        "spacing": "sm",
        "contents": [
          {
            "type": "button",
            "style": "primary",
            "color": "#1DB446",
            "action": {
              "type": "uri",
              "label": "🌐 點擊閱讀精美全幅報告",
              "uri": report_url
            }
          }
        ]
      }
    }
    return FlexSendMessage(alt_text=f"【{stock_name}】深度分析報告出爐", contents=flex_json)

# ==========================================
# 📈 6. 繪圖與報價函式 (含美股與上櫃修復)
# ==========================================
def generate_chart(stock_id, chart_type="K"):
    try:
        df = pd.DataFrame()
        
        # 1. 處理台股 (全數字)
        if stock_id.isdigit():
            if chart_type == "K":
                start_date = (datetime.datetime.now() - timedelta(days=100)).strftime('%Y-%m-%d')
                df = dl.taiwan_stock_daily(stock_id=stock_id, start_date=start_date)
                if not df.empty:
                    df = df.rename(columns={'date': 'Date', 'open': 'Open', 'max': 'High', 'min': 'Low', 'close': 'Close', 'Trading_Volume': 'Volume'})
                    df['Date'] = pd.to_datetime(df['Date'])
                    df.set_index('Date', inplace=True)
                plot_type, title_suffix, dt_format = 'candle', "3-Month Chart", "%m/%d"
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

        # 2. 處理美股 (包含英文字母)
        else:
            stock = yf.Ticker(stock_id)
            if chart_type == "K":
                df = stock.history(period="3mo")
                if not df.empty: df.index = df.index.tz_localize(None)
                plot_type, title_suffix, dt_format = 'candle', "3-Month Chart", "%m/%d"
            else:
                req_interval = "5m"
                df = stock.history(period="5d", interval=req_interval)
                if not df.empty: df = df.dropna()
                if not df.empty and len(df) >= 2:
                    df.index = df.index.tz_localize(None)
                    last_day = df.index[-1].date()
                    df = df[df.index.date == last_day]
                plot_type, title_suffix, dt_format = 'line', "Intraday Trend", "%H:%M"
        
        # 3. 開始繪圖
        if df.empty or len(df) < 2: return None
        buf = io.BytesIO()
        mc = mpf.make_marketcolors(up='r', down='g', inherit=True)
        s = mpf.make_mpf_style(marketcolors=mc)
        mpf.plot(df, type=plot_type, volume=(chart_type=="K"), style=s, title=f"[{stock_id}] {title_suffix}", ylabel="Price", ylabel_lower="Volume", datetime_format=dt_format, savefig=buf, show_nontrading=False)
        buf.seek(0)
        res = requests.post("https://api.imgbb.com/1/upload", data={"key": IMGBB_API_KEY, "image": base64.b64encode(buf.read()).decode('utf-8')})
        if res.status_code == 200: return res.json()["data"]["url"]
        return None
    except: return None

def get_quote(msg):
    msg = msg.upper().strip()
    
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
        try:
            stock = yf.Ticker(msg)
            df = stock.history(period="5d")
            if df.empty: return f"找不到代號【{msg}】的資料，請確認輸入是否正確。"
            if len(df) >= 2:
                tc, to, pc = df['Close'].iloc[-1], df['Open'].iloc[-1], df['Close'].iloc[-2]
                dp, pp = tc - pc, (tc - pc) / pc * 100
                do, po = tc - to, (tc - to) / to * 100
                sp = "🔺" if dp > 0 else ("🔻" if dp < 0 else "➖")
                so = "🔺" if do > 0 else ("🔻" if do < 0 else "➖")
                return (f"【美股 / ETF】{msg}\n目前價格：{tc:.2f} USD\n---\n"
                        f"前日收盤：{pc:.2f} USD\n總漲跌幅：{sp}{dp:+.2f} ({pp:+.2f}%)\n---\n"
                        f"今日開盤：{to:.2f} USD\n盤中走勢：{so}{do:+.2f} ({po:+.2f}%)")
        except Exception as e: return f"查詢錯誤：{str(e)}"
    return None

# ==========================================
# 🌐 7. 伺服器與 LINE / 網頁報告 路由處理
# ==========================================
@app.route("/", methods=['GET'])
def index(): return "Stock Bot is Alive!"

# 🌟 新增：專屬動態網頁報告的入口。當您在 LINE 點擊網址時，會觸發這裡直接吐出精美 HTML
@app.route("/report/<report_id>", methods=['GET'])
def show_report(report_id):
    if report_id in report_storage:
        return report_storage[report_id]  # 瀏覽器收到後會直接渲染出滿版高質感的分析網頁
    else:
        return "<html><body style='background:#121212;color:white;text-align:center;padding-top:100px;'><h2>❌ 報告已過期</h2><p>因為雲端主機重啟或超出暫存時間，請回到 LINE 重新輸入指令生成報告。</p></body></html>", 404

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'

# ==========================================
# ⚡ 專屬處理 AI 報告的背景線程 (已改為網頁生成版)
# ==========================================
def async_ai_reply_task(reply_token, sid):
    try:
        # 1. 呼叫新寫好的網頁生成函式
        stock_name, ai_html_content, latest_price, historical_avg_pe, ttm_eps = get_ai_html_report(sid)
        
        # 2. 隨機產生這份報告的唯一識別碼 (UUID)
        report_id = str(uuid.uuid4())
        
        # 3. 把 HTML 原始碼存進全域字典中
        report_storage[report_id] = ai_html_content
        
        # 4. 建立您專屬的 Render 網頁連結
        # ⚠️ 請把 'YOUR_RENDER_URL' 換成您真實的 Render 專案網址（例如 'my-stock-bot.onrender.com'）
        YOUR_RENDER_URL = "mylinebot-wda9" 
        report_url = f"https://{YOUR_RENDER_URL}.onrender.com/report/{report_id}"
        
        # 5. 生成帶有「點擊閱讀」按鈕的 Flex Message 卡片並回傳給 LINE
        flex_card = create_report_flex_card(sid, stock_name, latest_price, ttm_eps, historical_avg_pe, report_url)
        line_bot_api.reply_message(reply_token, flex_card)
            
    except Exception as e:
        print(f"背景 AI 任務發生錯誤: {e}")
        try: line_bot_api.reply_message(reply_token, TextSendMessage(text="抱歉，AI 報告生成發生錯誤，請稍後再試。"))
        except: pass 

# ==========================================
# 💬 8. 接收訊息與邏輯分流
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
        url = generate_chart(sid, "K")
        stock_name = tw_stock_dict.get(sid, f"代號 {sid}")
        if url: 
            line_bot_api.reply_message(event.reply_token, [
                TextSendMessage(text=f"✅ 已經為您繪製【{stock_name}】的近期 K 線圖囉！"),
                ImageSendMessage(original_content_url=url, preview_image_url=url)
            ])
        else: 
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="圖片產生失敗，請確認代號是否正確或稍後再試。"))
        return

    if user_msg.startswith("走"):
        sid = user_msg.replace("走", "")
        show_loading_animation(chat_id)
        url = generate_chart(sid, "走")
        stock_name = tw_stock_dict.get(sid, f"代號 {sid}")
        if url: 
            line_bot_api.reply_message(event.reply_token, [
                TextSendMessage(text=f"✅ 已經為您繪製【{stock_name}】的今日盤中走勢圖囉！"),
                ImageSendMessage(original_content_url=url, preview_image_url=url)
            ])
        else: 
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="圖片產生失敗，請確認代號是否正確或稍後再試。"))
        return

    if user_msg.startswith("AI"):
        sid = user_msg.replace("AI", "")
        show_loading_animation(chat_id, loading_seconds=30)
        thread = threading.Thread(target=async_ai_reply_task, args=(event.reply_token, sid))
        thread.start()
        return
        
    result = get_quote(user_msg)
    if result and "找不到" not in result and "錯誤" not in result:
        buttons = [
            QuickReplyButton(action=MessageAction(label="📈 當日走勢", text=f"走{user_msg}")),
            QuickReplyButton(action=MessageAction(label="📊 K 線圖", text=f"K{user_msg}"))
        ]
        
        if user_msg.isdigit():
            buttons.append(QuickReplyButton(action=MessageAction(label="🤖 AI 深度報告", text=f"AI{user_msg}")))

        quick_reply = QuickReply(items=buttons)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=result, quick_reply=quick_reply))
    else:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=result if result else "請輸入正確代號（台股如 2330，美股如 AAPL）"))

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)