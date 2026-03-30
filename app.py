import yfinance as yf
import pandas as pd
from fastapi import FastAPI, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from concurrent.futures import ThreadPoolExecutor
import os
import math
import requests
import uuid

app = FastAPI(title="Mark Minervini Screener")

# 全域字典儲存背景任務進度
tasks_store = {}

# 確保 static 資料夾存在
os.makedirs("static", exist_ok=True)

# 掛載網頁靜態資料夾
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def read_root():
    """造訪根目錄時回傳 index.html"""
    return FileResponse("static/index.html")

class FilterCriteria(BaseModel):
    min_volume: int = 1000000
    min_market_cap: int = 2000000000
    target_list: str = "sp500"

def check_vcp(df):
    """
    初步偵測 VCP (波動收縮型態) 的輔助條件：
    1. 計算過去 60 個交易日 (約3個月) 中，後半場的波動是否小於前半場
    2. 最近 10 日價格區間必須小於 12% (緊密收斂)
    3. 最近 5 日平均成交量低於 50 日平均成交量 (量縮 Volume Dry-up)
    """
    if len(df) < 60:
        return False
        
    recent_df = df.tail(60)
    
    # 判斷一：量縮 (Volume Dry-up)
    vol_5 = df['Volume'].tail(5).mean()
    vol_50 = df['Volume'].tail(50).mean()
    if vol_5 >= vol_50:
        return False
        
    # 判斷二：波動收斂 (Volatility Contraction)
    # 將 60 天切成兩半，測量高低點的距離百分比
    first_half = recent_df.head(30)
    second_half = recent_df.tail(30)
    
    if first_half['Close'].max() == 0 or second_half['Close'].max() == 0:
        return False
        
    volatility_1 = (first_half['Close'].max() - first_half['Close'].min()) / first_half['Close'].max()
    volatility_2 = (second_half['Close'].max() - second_half['Close'].min()) / second_half['Close'].max()
    
    # 前半場必須具有一定波動，且後半場波動比前半場小
    if not (volatility_2 < volatility_1):
        return False
        
    # 判斷三：近期 (10日) 的緊密收盤 (Tightness)
    current_tightness = (df['Close'].tail(10).max() - df['Close'].tail(10).min()) / df['Close'].tail(10).max()
    if current_tightness >= 0.12: # 區間過大，未收斂
        return False
        
    return True

def get_sp500_tickers():
    """從維基百科取得 S&P 500 最新成份股"""
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    html = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}).text
    tables = pd.read_html(html)
    df = tables[0]
    return [t.replace('.', '-') for t in df['Symbol'].tolist()]

def evaluate_stock(ticker, spy_return, min_volume, min_market_cap, is_mag7=False):
    try:
        stock = yf.Ticker(ticker)
        
        # 安全取用市值
        try:
            market_cap_val = stock.fast_info['marketCap']
            if market_cap_val < min_market_cap:
                return None
        except Exception:
            market_cap_val = 0
            
        df = stock.history(period="2y")
        if df.empty or len(df) < 250:
            return None
            
        avg_vol = df['Volume'].tail(20).mean()
        if avg_vol < min_volume:
            return None
            
        # 計算移動均線
        df['SMA_50'] = df['Close'].rolling(window=50).mean()
        df['SMA_150'] = df['Close'].rolling(window=150).mean()
        df['SMA_200'] = df['Close'].rolling(window=200).mean()
        
        current_close = df['Close'].iloc[-1]
        current_50 = df['SMA_50'].iloc[-1]
        current_150 = df['SMA_150'].iloc[-1]
        current_200 = df['SMA_200'].iloc[-1]
        
        low_of_52week = df['Close'].tail(252).min()
        high_of_52week = df['Close'].tail(252).max()
        sma_200_20_days_ago = df['SMA_200'].iloc[-21]
        stock_1y_return = (current_close / df['Close'].iloc[-252]) - 1 if len(df) >= 252 else 0
        
        # 八大條件
        cond_1 = current_close > current_150 and current_close > current_200
        cond_2 = current_150 > current_200
        cond_3 = current_200 > sma_200_20_days_ago 
        cond_4 = current_50 > current_150 and current_50 > current_200
        cond_5 = current_close > current_50
        cond_6 = current_close >= (1.3 * low_of_52week)
        cond_7 = current_close >= (0.75 * high_of_52week)
        cond_8 = stock_1y_return > spy_return
        
        # 計算符合條件的分數 (0-8)，並且強制轉換為純 Python int
        score = int(sum([cond_1, cond_2, cond_3, cond_4, cond_5, cond_6, cond_7, cond_8]))
        
        # S&P500 必須滿分才展示；若是七巨頭(Mag7)則無論幾分都展示以供排名
        if not is_mag7 and score < 8:
            return None
        
        # 格式化市值顯示
        if market_cap_val >= 1e9:
            mc_str = f"{market_cap_val/1e9:.2f}B"
        else:
            mc_str = f"{market_cap_val/1e6:.2f}M"

        # 偵測是否具備 VCP 型態，強制轉純 Python bool
        is_vcp_pattern = bool(check_vcp(df))

        return {
            "ticker": ticker,
            "price": round(float(current_close), 2),
            "volume": int(avg_vol),
            "market_cap": mc_str,
            "return_1y": round(float(stock_1y_return) * 100, 2),
            "is_vcp": is_vcp_pattern,
            "score": score
        }
        
    except Exception as e:
        return None

def process_screener(task_id: str, criteria: FilterCriteria):
    """真正在背景幫老闆跑腿的過濾長工"""
    try:
        tasks_store[task_id] = {"status": "running", "progress": 0, "total": 0, "results": [], "benchmark": 0}
        
        is_mag7 = (criteria.target_list == "mag7")
        if is_mag7:
            tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA"]
        else:
            tickers = get_sp500_tickers()
            
        tasks_store[task_id]["total"] = len(tickers)
        
        # 爬取 SPY として基準
        spy = yf.Ticker("SPY").history(period="2y")
        if spy.empty or len(spy) < 252:
            spy_1y_return = 0.1 # Fallback
        else:
            spy_1y_return = (spy['Close'].iloc[-1] / spy['Close'].iloc[-252]) - 1
            
        tasks_store[task_id]["benchmark"] = round(float(spy_1y_return) * 100, 2)

        passed_stocks = []
        
        # 建立進度追蹤包裝器
        def _eval_with_progress(t):
            res = evaluate_stock(t, spy_1y_return, criteria.min_volume, criteria.min_market_cap, is_mag7)
            tasks_store[task_id]["progress"] += 1
            return res

        with ThreadPoolExecutor(max_workers=8) as executor:
            # 將包裝器而不是直接把 evaluate_stock 丟進去
            results = list(executor.map(_eval_with_progress, tickers))
            
        for res in results:
            if res:
                passed_stocks.append(res)
                
        # 按強弱排序：得分高在前，再比一年期報酬率
        tasks_store[task_id]["results"] = sorted(passed_stocks, key=lambda x: (x.get('score', 0), x['return_1y']), reverse=True)
        tasks_store[task_id]["status"] = "completed"
        
    except Exception as e:
        tasks_store[task_id]["status"] = "error"
        tasks_store[task_id]["detail"] = str(e)

@app.post("/api/screen/start")
def start_screener(criteria: FilterCriteria, background_tasks: BackgroundTasks):
    """前端敲門的櫃台：發放號碼牌並交代後台去跑"""
    task_id = str(uuid.uuid4())
    background_tasks.add_task(process_screener, task_id, criteria)
    return {"task_id": task_id}
    
@app.get("/api/screen/status/{task_id}")
def get_task_status(task_id: str):
    """前端無時無刻來確認進度的廣播台"""
    if task_id not in tasks_store:
        return {"status": "error", "detail": "找不到該任務編號！"}
    return tasks_store[task_id]
