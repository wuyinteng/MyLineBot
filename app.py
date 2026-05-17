from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageSendMessage, QuickReply, QuickReplyButton, MessageAction
import yfinance as yf
from FinMind.data import DataLoader
import datetime
from datetime import timedelta
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
import traceback
import google.generativeai as genai
import threading

app = Flask(__name__)

# ==========================================
# 🔑 1. 金鑰與初始化設定
# ==========================================
GEMINI_API_KEY = "AIzaSyCiQU1PjlYDyk3onLYytPv2ldrVJwD2s8o"
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash')

FINMIND_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoid3V5aW50ZW5nIiwiZW1haWwiOiJ3dXlpbnRlbmcxMjA2QGdtYWlsLmNvbSIsInRva2VuX3ZlcnNpb24iOjB9.4roGRm1h6ihqAubqa5aqEDOweoFoXCkKuU408HXyt90" 
IMGBB_API_KEY = "4bc61e9d363f21433c906beb7440dd92"

dl = DataLoader()
if FINMIND_TOKEN: dl.login_by_token(api_token=FINMIND_TOKEN)

# 為了讓發送動畫也可以使用，將 Token 獨立成變數
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
# 🌟 [新增] LINE 隱藏版讀取中動畫 (...) 函式
# ==========================================
def show_loading_animation(chat_id, loading_seconds=20):
    """
    呼叫 LINE 官方 API，在聊天室顯示「...」的打字動畫。
    這是完全免費的，能讓使用者知道機器人正在處理，沒有當機！
    """
    url = "https://api.line.me/v2/bot/chat/loading/start"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    data = {
        "chatId": chat_id,
        "loadingSeconds": loading_seconds # 預設顯示 20 秒，當訊息送出時動畫會自動消失
    }
    try:
        requests.post(url, headers=headers, json=data, timeout=3)
    except:
        pass

# ==========================================
# 📊 2. 技術指標量化引擎 (保留原樣)
# ==========================================
def get_technical_indicators(stock_id):
    try:
        ticker_id = f"{stock_id}.TW"
        df = yf.Ticker(ticker_id).history(period="6mo")
        if df.empty:
            ticker_id = f"{stock_id}.TWO"
            df = yf.Ticker(ticker_id).history(period="6mo")
            
        if df.empty or len(df) < 30:
            return "\n📈 技術指標: 數據不足\n"

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

        macd_trend = "🔴紅柱增長(動能強)" if latest['MACD_Histogram'] > 0 and latest['MACD_Histogram'] > prev['MACD_Histogram'] else \
                     "🔴紅柱縮減(動能弱)" if latest['MACD_Histogram'] > 0 else \
                     "🟢綠柱縮減(跌勢緩)" if latest['MACD_Histogram'] < 0 and latest['MACD_Histogram'] > prev['MACD_Histogram'] else \
                     "🟢綠柱增長(跌勢強)"
                     
        kd_cross = "⭐黃金交叉" if prev['K'] < prev['D'] and latest['K'] > latest['D'] else \
                   "⚠️死亡交叉" if prev['K'] > prev['D'] and latest['K'] < latest['D'] else \
                   "偏多" if latest['K'] > latest['D'] else "偏空"

        return (f"\n📈 【最新技術與量價動能】:\n"
                f"- 量價結構: {vp_status} (今日成交量: {int(latest['Volume']/1000)}張, 5日均量: {int(latest['Vol_5MA']/1000)}張)\n"
                f"- RSI (14日): {round(latest['RSI'], 1)}\n"
                f"- MACD 柱狀圖: {macd_trend} (數值: {round(latest['MACD_Histogram'], 2)})\n"
                f"- KD 指標 (9,3,3): K={round(latest['K'], 1)}, D={round(latest['D'], 1)} [{kd_cross}]\n")
    except Exception as e:
        return "\n📈 技術指標: 暫時無法獲取\n"

# ==========================================
# 💰 3. 基礎面與籌碼直連引擎 (保留原樣)
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
    return stock_name, data_summary, latest_price, historical_avg_pe

# ==========================================
# 🤖 4. AI 報告生成核心 (保留原樣)
# ==========================================
def get_ai_report_for_line(stock_id):
    try:
        stock_name, real_data_context, latest_price, historical_avg_pe = get_finmind_data(stock_id)
        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        
        prompt = f"""
        你現在是一位頂級的外資券商首席分析師。請為台股代號 【{stock_id} {stock_name}】 撰寫一份深度的個股研究報告。
        【系統已為你抓取最新的量化、籌碼與技術面真實數據如下，請務必參考】：
        {real_data_context}
        
        請嚴格遵守以下「三頁式」的排版與內容要求，使用 Markdown 格式輸出：
        # 【{stock_id} {stock_name}】深度投資評估報告
        **日期時間：** {now_str}
        
        【核心規則：目標價計算公式】
        你必須絕對依賴系統提供的數據，並遵守以下公式計算目標價：
        1. 算出「近四季 EPS 總和」(TTM EPS)。
        2. 觀察 EPS 成長率，客觀推估「未來 1 年預估 EPS」。
        3. 合理目標價 = (未來 1 年預估 EPS) × (近三年歷史平均 PE {historical_avg_pe} 倍)。
        4. 潛在漲幅 = [(合理目標價 / 目前股價) - 1] × 100%。
        ---
        ## 🎯 核心評價與目標價
        * **現在市價：** {latest_price} 元
        * **估值算式：** (預估未來一年 EPS _____ 元) × (歷史平均 PE {historical_avg_pe} 倍)
        * **AI 目標價：** (填入算出的目標價) 元
        * **潛在空間：** (填寫漲跌幅，例如：+15% 或 -5%)
        * **買賣評價：** (根據潛在空間給出：強勢買進 / 逢低布局 / 中立觀望 / 避開減碼)
        ---
        ## 🏢 公司基本資訊與估值評估
        | 項目 | 數據評估 | 項目 | 數據評估 |
        | :--- | :--- | :--- | :--- |
        | **所屬產業** | (依據上方數據填寫) | **目前市值** | (依據上方數據填寫) 億元 (請評估為大型/中小型) |
        | **目前股本** | (依據上方數據填寫) 億元 | **本益比位階** | (對比歷史PE，評估偏高/合理/偏低) |
        ---
        ## 🌟 五角雷達綜合評分 (總分：__/100)
        1. **題材面 ( /20分)：** 所屬族群為(填寫)，目前資金熱度(填寫)。
        2. **基本面 ( /20分)：** (依據營收YoY與EPS成長率評估)。
        3. **技術面 ( /25分)：** (請綜合評估均線位置與量價狀態給予建議)。
        4. **籌碼面 ( /25分)：** (依據三大法人買賣超評估)。
        5. **新聞面 ( /10分)：** (近期市場催化劑評估)。
        ---
        ## 📄 第二頁：公司基本面與財務對比分析
        1. 公司基本介紹與核心業務
        2. 主力成長動能與業務關鍵分析
        3. 同業競爭力評比表格
        4. 分析師綜合評估總結 (300字以內)
        ---
        ## 📊 第三頁：三大法人籌碼動向與股價解析
        **1. 近 10 個交易日法人籌碼流向表**
        **2. 籌碼與技術指標連動之專業分析 (300~400字)**
        """
        response = model.generate_content(prompt)
        full_report = response.text
        
        paragraphs = full_report.split('---')
        pages = []
        current_page = ""
        
        for p in paragraphs:
            clean_p = p.strip()
            if not clean_p: continue
            
            if len(current_page) + len(clean_p) < 800:
                current_page += clean_p + "\n\n---\n\n"
            else:
                if current_page: pages.append(current_page.strip('- \n'))
                current_page = clean_p + "\n\n---\n\n"
                
        if current_page:
            pages.append(current_page.strip('- \n'))
            
        return stock_name, pages[:5]

    except Exception as e:
        return stock_id, [f"AI 分析失敗: {str(e)}"]

# ==========================================
# 繪圖與報價函式
# ==========================================
def generate_chart(stock_id, chart_type="K"):
    try:
        df = pd.DataFrame()
        ticker = f"{stock_id}.TW" if stock_id.isdigit() else stock_id
        if stock_id.isdigit() and chart_type == "K":
            start_date = (datetime.datetime.now() - timedelta(days=100)).strftime('%Y-%m-%d')
            df = dl.taiwan_stock_daily(stock_id=stock_id, start_date=start_date)
            if not df.empty:
                df = df.rename(columns={'date': 'Date', 'open': 'Open', 'max': 'High', 'min': 'Low', 'close': 'Close', 'Trading_Volume': 'Volume'})
                df['Date'] = pd.to_datetime(df['Date'])
                df.set_index('Date', inplace=True)
            plot_type, title_suffix, dt_format = 'candle', "3-Month Chart", "%m/%d"
        else:
            stock = yf.Ticker(ticker)
            if chart_type == "K":
                df = stock.history(period="3mo")
                plot_type, title_suffix, dt_format = 'candle', "3-Month Chart", "%m/%d"
            else:
                req_interval = "5m" if stock_id.isdigit() else "1m"
                df = stock.history(period="5d", interval=req_interval)
                if not df.empty: df = df.dropna()
                if not df.empty and len(df) >= 2:
                    df.index = df.index.tz_localize(None)
                    last_day = df.index[-1].date()
                    df = df[df.index.date == last_day]
                plot_type, title_suffix, dt_format = 'line', "Intraday Trend", "%H:%M"
        
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
    return None

# ==========================================
# 🌐 5. 伺服器與 LINE 路由處理
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
# ⚡ 專屬處理 AI 報告的背景線程
# ==========================================
def async_ai_reply_task(reply_token, sid):
    try:
        stock_name, report_pages = get_ai_report_for_line(sid)
        
        messages = []
        # 第一個氣泡先給一句溫馨提示
        messages.append(TextSendMessage(text=f"🎯 【{stock_name}】 的深度特戰報告已經為您生成完畢！請過目："))
        
        for i, page_text in enumerate(report_pages):
            if i < 4: 
                messages.append(TextSendMessage(text=page_text.strip()))
        
        if len(messages) > 1:
            line_bot_api.reply_message(reply_token, messages)
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="報告生成失敗，請稍後再試。"))
            
    except Exception as e:
        print(f"背景 AI 任務發生錯誤: {e}")
        try:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="抱歉，AI 報告生成超時或發生錯誤，請稍後再試。"))
        except:
            pass 

# ==========================================
# 💬 6. 接收訊息與邏輯分流
# ==========================================
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event): 
    user_msg = event.message.text.strip().upper()
    
    # 取得聊天室 ID（用於觸發免費讀取動畫）
    chat_id = event.source.user_id
    if event.source.type == 'group': chat_id = event.source.group_id
    elif event.source.type == 'room': chat_id = event.source.room_id
    
    # --- 1. K 線圖 (加上動畫與提示詞) ---
    if user_msg.startswith("K"):
        sid = user_msg.replace("K", "")
        # 顯示 LINE 打字動畫「...」
        show_loading_animation(chat_id)
        
        url = generate_chart(sid, "K")
        stock_name = tw_stock_dict.get(sid, f"代號 {sid}")
        
        if url: 
            # 把「文字」跟「圖片」打包成一個 list 一起回傳
            line_bot_api.reply_message(event.reply_token, [
                TextSendMessage(text=f"✅ 已經為您繪製【{stock_name}】的近期 K 線圖囉！"),
                ImageSendMessage(original_content_url=url, preview_image_url=url)
            ])
        else: 
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="圖片產生失敗，請確認代號是否正確或稍後再試。"))
        return

    # --- 2. 走勢圖 (加上動畫與提示詞) ---
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

    # --- 3. AI 深度報告 (交給背景執行 + 動畫) ---
    if user_msg.startswith("AI"):
        sid = user_msg.replace("AI", "")
        
        # 顯示 LINE 打字動畫「...」 (最長可顯示 20 秒，算完就會提早消失)
        show_loading_animation(chat_id, loading_seconds=30)
        
        # 開啟背景線程去慢慢算
        thread = threading.Thread(target=async_ai_reply_task, args=(event.reply_token, sid))
        thread.start()
        return
        
    # --- 4. 一般股價查詢與選單 ---
    result = get_quote(user_msg)
    if result and "找不到" not in result and "錯誤" not in result:
        quick_reply = QuickReply(items=[
            QuickReplyButton(action=MessageAction(label="📈 當日走勢", text=f"走{user_msg}")),
            QuickReplyButton(action=MessageAction(label="📊 K 線圖", text=f"K{user_msg}")),
            QuickReplyButton(action=MessageAction(label="🤖 AI 深度報告", text=f"AI{user_msg}"))
        ])
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=result, quick_reply=quick_reply))
    else:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=result if result else "請輸入正確代號"))

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)