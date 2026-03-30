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
import io
import traceback
import time
import random
from yahooquery import Ticker as YQTicker

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
    first_half = recent_df.head(30)
    second_half = recent_df.tail(30)
    
    if first_half['Close'].max() == 0 or second_half['Close'].max() == 0:
        return False
        
    volatility_1 = (first_half['Close'].max() - first_half['Close'].min()) / first_half['Close'].max()
    volatility_2 = (second_half['Close'].max() - second_half['Close'].min()) / second_half['Close'].max()
    
    if not (volatility_2 < volatility_1):
        return False
        
    current_tightness = (df['Close'].tail(10).max() - df['Close'].tail(10).min()) / df['Close'].tail(10).max()
    if current_tightness >= 0.12:
        return False
        
    return True

def get_sp500_tickers():
    """優先從 GitHub 動態抓取最新 S&P 500 名單"""
    try:
        csv_url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
        response = requests.get(csv_url, timeout=10)
        df = pd.read_csv(io.StringIO(response.text))
        if not df.empty and 'Symbol' in df.columns:
            return [str(s).replace('.', '-') for s in df['Symbol'].tolist()]
    except Exception as e:
        print(f"動態抓取失敗: {e}")
        
    # 安全底線
    return ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA", "BRK-B", "UNH", "V", "JPM", "JNJ", "MA", "AVGO", "HD", "PG", "XOM", "LLY", "CVX", "ASML", "PEP", "ABBV", "KO", "MRK", "PFE", "COST", "TMO", "AVGO", "ORCL", "AZN", "CSCO", "ABT", "NKE", "DHR", "ACN", "CMCSA", "LIN", "MCD", "ADBE", "TXN", "UPS", "NEE", "PM", "MS", "VZ", "RTX", "BMY", "HON", "AMGN", "LOW", "COP", "IBM", "DE", "UNP", "CAT", "LMT", "GE", "INTC", "GS", "QCOM", "PLD", "SBUX", "ELV", "T", "SPGI", "BLK", "NOW", "AXP", "INTU", "ADP", "SYK", "AMT", "ISRG", "MDLZ", "TJX", "C", "AMAT", "EL", "GILD", "CB", "ADI", "LRCX", "VLO", "CI", "MO", "PGR", "HCA", "REGN", "ZTS", "MMC", "CVS", "EW", "FISV", "BDX", "BSX", "HUM", "DUK", "SO", "EOG", "CSX", "EQIX", "WM"]

def evaluate_stock_data(ticker, df, spy_return, market_cap_val, avg_vol, is_mag7=False):
    """根據已下載的數據進行條件評估"""
    try:
        if df is None or df.empty or len(df) < 250:
            return None
            
        df = df.copy() # 避免 SettingWithCopyWarning
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
        
        cond_1 = current_close > current_150 and current_close > current_200
        cond_2 = current_150 > current_200
        cond_3 = current_200 > sma_200_20_days_ago 
        cond_4 = current_50 > current_150 and current_50 > current_200
        cond_5 = current_close > current_50
        cond_6 = current_close >= (1.3 * low_of_52week)
        cond_7 = current_close >= (0.75 * high_of_52week)
        cond_8 = stock_1y_return > spy_return
        
        score = int(sum([cond_1, cond_2, cond_3, cond_4, cond_5, cond_6, cond_7, cond_8]))
        
        if not is_mag7 and score < 8:
            return None
        
        mc_str = f"{market_cap_val/1e9:.2f}B" if market_cap_val >= 1e9 else f"{market_cap_val/1e6:.2f}M"
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
    except Exception:
        return None

def process_screener(task_id: str, criteria: FilterCriteria):
    """Mega-Batch 批次處理邏輯"""
    try:
        tasks_store[task_id] = {"status": "running", "progress": 0, "total": 0, "results": [], "benchmark": 0}
        
        is_mag7 = (criteria.target_list == "mag7")
        orig_tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA"] if is_mag7 else get_sp500_tickers()
        tasks_store[task_id]["total"] = len(orig_tickers)
        
        # 階段一：批次過濾市值與量
        valid_meta = {}
        for i in range(0, len(orig_tickers), 250):
            chunk = orig_tickers[i:i+250]
            yq = YQTicker(chunk)
            p_dict = yq.price
            s_dict = yq.summary_detail
            for t in chunk:
                try:
                    p = p_dict.get(t, {})
                    s = s_dict.get(t, {})
                    if isinstance(p, str): continue
                    mcap = p.get('marketCap', 0)
                    vol = s.get('averageDailyVolume10Day') or s.get('averageVolume') or 0
                    if mcap >= criteria.min_market_cap and vol >= criteria.min_volume:
                        valid_meta[t] = {"mcap": mcap, "vol": vol}
                except: continue
            tasks_store[task_id]["progress"] = min(tasks_store[task_id]["total"] // 4, tasks_store[task_id]["progress"] + len(chunk))

        filtered_tickers = list(valid_meta.keys())
        spy = yf.Ticker("SPY").history(period="2y")
        spy_1y_return = (spy['Close'].iloc[-1] / spy['Close'].iloc[-252]) - 1 if len(spy) >= 252 else 0.1
        tasks_store[task_id]["benchmark"] = round(float(spy_1y_return) * 100, 2)

        passed_stocks = []
        chunk_size = 40
        for i in range(0, len(filtered_tickers), chunk_size):
            batch = filtered_tickers[i:i+chunk_size]
            data = yf.download(batch, period="2y", group_by='ticker', threads=False, progress=False)
            for t in batch:
                try:
                    t_df = data[t].dropna() if len(batch) > 1 else data.dropna()
                    res = evaluate_stock_data(t, t_df, spy_1y_return, valid_meta[t]['mcap'], valid_meta[t]['vol'], is_mag7)
                    if res: passed_stocks.append(res)
                except: continue
            tasks_store[task_id]["progress"] = min(tasks_store[task_id]["total"]-1, tasks_store[task_id]["progress"] + len(batch))

        tasks_store[task_id]["results"] = sorted(passed_stocks, key=lambda x: (x['score'], x['return_1y']), reverse=True)
        tasks_store[task_id]["progress"] = tasks_store[task_id]["total"]
        tasks_store[task_id]["status"] = "completed"
    except Exception as e:
        tasks_store[task_id].update({"status": "error", "detail": f"{str(e)}: {traceback.format_exc()}"})

@app.post("/api/screen/start")
def start_screener(criteria: FilterCriteria, background_tasks: BackgroundTasks):
    task_id = str(uuid.uuid4())
    background_tasks.add_task(process_screener, task_id, criteria)
    return {"task_id": task_id}
    
@app.get("/api/screen/status/{task_id}")
def get_task_status(task_id: str):
    if task_id not in tasks_store:
        return {"status": "error", "detail": "找不到該任務編號！"}
    return tasks_store[task_id]
