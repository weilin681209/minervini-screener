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

def normalize_df(df):
    """將 DataFrame 欄位強制轉為小寫與正確的欄位名稱"""
    if df is None or df.empty:
        return df
    df.columns = [c.lower() for c in df.columns]
    return df

def check_vcp(df):
    """偵測 VCP (波動收縮型態)，欄位已標準化"""
    try:
        if df is None or len(df) < 60:
            return False
            
        recent_df = df.tail(60)
        # 欄位一律小寫
        vol_5 = df['volume'].tail(5).mean()
        vol_50 = df['volume'].tail(50).mean()
        if vol_5 >= vol_50:
            return False
            
        first_half = recent_df.head(30)
        second_half = recent_df.tail(30)
        if first_half['close'].max() == 0 or second_half['close'].max() == 0:
            return False
            
        volatility_1 = (first_half['close'].max() - first_half['close'].min()) / first_half['close'].max()
        volatility_2 = (second_half['close'].max() - second_half['close'].min()) / second_half['close'].max()
        if not (volatility_2 < volatility_1):
            return False
            
        current_tightness = (df['close'].tail(10).max() - df['close'].tail(10).min()) / df['close'].tail(10).max()
        if current_tightness >= 0.12:
            return False
        return True
    except:
        return False

def get_sp500_tickers():
    """優先從 GitHub 動態抓取最新 S&P 500 名單"""
    try:
        csv_url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
        response = requests.get(csv_url, timeout=10)
        df = pd.read_csv(io.StringIO(response.text))
        if not df.empty and 'Symbol' in df.columns:
            return [str(s).replace('.', '-') for s in df['Symbol'].tolist()]
    except Exception:
        pass
    return ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA", "BRK-B", "UNH", "V", "JPM", "JNJ", "MA", "AVGO", "HD", "PG", "XOM", "LLY", "CVX", "ASML", "PEP", "ABBV", "KO", "MRK", "PFE", "COST", "TMO", "AVGO", "ORCL", "AZN", "CSCO", "ABT", "NKE", "DHR", "ACN", "CMCSA", "LIN", "MCD", "ADBE", "TXN", "UPS", "NEE", "PM", "MS", "VZ", "RTX", "BMY", "HON", "AMGN", "LOW", "COP", "IBM", "DE", "UNP", "CAT", "LMT", "GE", "INTC", "GS", "QCOM", "PLD", "SBUX", "ELV", "T", "SPGI", "BLK", "NOW", "AXP", "INTU", "ADP", "SYK", "AMT", "ISRG", "MDLZ", "TJX", "C", "AMAT", "EL", "GILD", "CB", "ADI", "LRCX", "VLO", "CI", "MO", "PGR", "HCA", "REGN", "ZTS", "MMC", "CVS", "EW", "FISV", "BDX", "BSX", "HUM", "DUK", "SO", "EOG", "CSX", "EQIX", "WM"]

def evaluate_stock_data(ticker, df, spy_return, market_cap_val, avg_vol, is_mag7=False):
    """根據抓取的資料進行評估，增加欄位容錯"""
    try:
        df = normalize_df(df)
        if df is None or df.empty or len(df) < 150: # 放寬天數門檻以供初步顯示
            return None
            
        df['sma_50'] = df['close'].rolling(window=50).mean()
        df['sma_150'] = df['close'].rolling(window=150).mean()
        df['sma_200'] = df['close'].rolling(window=200).mean()
        
        current_close = df['close'].iloc[-1]
        current_50 = df['sma_50'].iloc[-1]
        current_150 = df['sma_150'].iloc[-1]
        current_200 = df['sma_200'].iloc[-1]
        
        low_of_52week = df['close'].tail(252).min()
        high_of_52week = df['close'].tail(252).max()
        sma_200_20_days_ago = df['sma_200'].iloc[-21] if len(df) >= 200 else current_200
        
        # 相對強度計算
        first_price = df['close'].iloc[0]
        stock_1y_return = (current_close / first_price) - 1
        
        cond_1 = current_close > current_150 and current_close > current_200
        cond_2 = current_150 > current_200
        cond_3 = current_200 > (sma_200_20_days_ago * 0.99) # 容許 1% 內的微幅下滑
        cond_4 = current_50 > current_150 and current_50 > current_200
        cond_5 = current_close > current_50
        cond_6 = current_close >= (1.25 * low_of_52week) # 微幅調低門檻 (1.3 -> 1.25)
        cond_7 = current_close >= (0.75 * high_of_52week)
        cond_8 = stock_1y_return > spy_return
        
        score = int(sum([cond_1, cond_2, cond_3, cond_4, cond_5, cond_6, cond_7, cond_8]))
        
        # 七巨頭不管幾分都顯現以供觀察
        if not is_mag7 and score < 7: # 放寬顯示門檻從 8 -> 7
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
    """終極雲端引擎：增加訊息反饋與欄位標準化"""
    try:
        tasks_store[task_id] = {"status": "running", "progress": 0, "total": 0, "results": [], "benchmark": 0, "message": "正在初始化系統..."}
        
        is_mag7 = (criteria.target_list == "mag7")
        orig_tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA"] if is_mag7 else get_sp500_tickers()
        tasks_store[task_id]["total"] = len(orig_tickers)
        
        # 階段一：過濾市值
        tasks_store[task_id]["message"] = "正在篩選合適市值與成交量的股票 (Phase 1/3)..."
        valid_meta = {}
        for i in range(0, len(orig_tickers), 250):
            chunk = orig_tickers[i:i+250]
            yq = YQTicker(chunk)
            p_dict = yq.price or {}
            s_dict = yq.summary_detail or {}
            for t in chunk:
                try:
                    p = p_dict.get(t, {}) if isinstance(p_dict, dict) else {}
                    s = s_dict.get(t, {}) if isinstance(s_dict, dict) else {}
                    mcap = p.get('marketCap', 0) if isinstance(p, dict) else 0
                    vol = s.get('averageDailyVolume10Day') or s.get('averageVolume') or 0 if isinstance(s, dict) else 0
                    if mcap >= criteria.min_market_cap and vol >= criteria.min_volume:
                        valid_meta[t] = {"mcap": mcap, "vol": vol}
                except: continue
            tasks_store[task_id]["progress"] = min(tasks_store[task_id]["total"] // 4, tasks_store[task_id]["progress"] + len(chunk))

        # 階段二：獲取大盤數據
        tasks_store[task_id]["message"] = "正在獲取 S&P 500 大盤基準資料 (Phase 2/3)..."
        spy_1y_return = 0.1
        try:
            spy_yq = YQTicker("SPY")
            spy_hist = spy_yq.history(period="1y")
            if not spy_hist.empty:
                spy_hist = normalize_df(spy_hist)
                spy_pr = spy_hist['close'].tolist()
                if len(spy_pr) >= 200:
                    spy_1y_return = (spy_pr[-1] / spy_pr[0]) - 1
            tasks_store[task_id]["message"] = f"大盤基準獲取成功 (年度基準: {spy_1y_return*100:.1f}%)"
        except:
            tasks_store[task_id]["message"] = "大盤抓取失敗，改用預設基準 (10%)"

        tasks_store[task_id]["benchmark"] = round(float(spy_1y_return) * 100, 2)
        filtered_tickers = list(valid_meta.keys())
        passed_stocks = []
        
        # 階段三：獲取歷史資料
        tasks_store[task_id]["message"] = f"正在下載 {len(filtered_tickers)} 檔股票的歷史價格並計算條件 (Phase 3/3)..."
        chunk_size = 30
        for i in range(0, len(filtered_tickers), chunk_size):
            batch = filtered_tickers[i:i+chunk_size]
            try:
                batch_yq = YQTicker(batch)
                hist_data = batch_yq.history(period="1y")
                if hist_data is None or hist_data.empty: continue
                
                for t in batch:
                    try:
                        # 處理 MultiIndex 解析
                        if isinstance(hist_data.index, pd.MultiIndex):
                            if t in hist_data.index.get_level_values(0):
                                t_df = hist_data.xs(t, level=0)
                            else: continue
                        else:
                            t_df = hist_data if len(batch) == 1 else pd.DataFrame()
                        
                        res = evaluate_stock_data(t, t_df, spy_1y_return, valid_meta[t]['mcap'], valid_meta[t]['vol'], is_mag7)
                        if res: passed_stocks.append(res)
                    except: continue
            except: continue
            
            tasks_store[task_id]["progress"] = min(tasks_store[task_id]["total"] - 1, tasks_store[task_id]["progress"] + len(batch))

        tasks_store[task_id]["results"] = sorted(passed_stocks, key=lambda x: (x['score'], x['return_1y']), reverse=True)
        tasks_store[task_id]["message"] = f"掃描完成！共發現 {len(passed_stocks)} 檔符合條件的標的。"
        tasks_store[task_id]["progress"] = tasks_store[task_id]["total"]
        tasks_store[task_id]["status"] = "completed"
        
    except Exception as e:
        tasks_store[task_id].update({"status": "error", "message": f"發生異常: {str(e)}", "detail": traceback.format_exc()})

@app.post("/api/screen/start")
def start_screener(criteria: FilterCriteria, background_tasks: BackgroundTasks):
    task_id = str(uuid.uuid4())
    background_tasks.add_task(process_screener, task_id, criteria)
    return {"task_id": task_id}
    
@app.get("/api/screen/status/{task_id}")
def get_task_status(task_id: str):
    if task_id not in tasks_store:
        return {"status": "error", "message": "找不到該任務編號！"}
    return tasks_store[task_id]
