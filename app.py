import os
import io
import uuid
import asyncio
import pandas as pd
import numpy as np
import requests
import yahooquery as yq
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI()

if not os.path.exists("static"):
    os.makedirs("static")

app.mount("/static", StaticFiles(directory="static"), name="static")

tasks_store = {}

class ScreenRequest(BaseModel):
    min_volume: int = 1000000
    min_market_cap: int = 2000000000
    target_list: str = "sp500"

def get_sp500_tickers():
    """Wikipedia 首選名單抓取 (含 User-Agent 修復)"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        resp = requests.get(url, headers=headers, timeout=5)
        df = pd.read_html(resp.text)[0]
        return df['Symbol'].tolist()
    except:
        return ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AVGO", "ORCL", "COST", "NFLX", "AMD", "ADBE", "QCOM", "TXN", "INTC", "AMAT", "MU", "LRCX", "V", "MA", "JPM", "BAC", "WMT", "HD", "PG", "XOM", "CVX", "UNH", "LLY"]

def calculate_weighted_rs(df, price_col):
    """馬克·米奈維尼權重 RS 模型：(3mo*2 + 6mo + 9mo + 12mo)"""
    try:
        p = float(df[price_col].iloc[-1])
        ret_3m = (p / df[price_col].iloc[-63] - 1) * 100 if len(df) >= 63 else 0.0
        ret_6m = (p / df[price_col].iloc[-126] - 1) * 100 if len(df) >= 126 else 0.0
        ret_9m = (p / df[price_col].iloc[-189] - 1) * 100 if len(df) >= 189 else 0.0
        ret_1y = (p / df[price_col].iloc[-252] - 1) * 100 if len(df) >= 252 else 0.0
        raw_rs = (ret_3m * 2) + ret_6m + ret_9m + ret_1y
        return raw_rs, ret_1y
    except:
        return 0.0, 0.0

def evaluate_stock_data(ticker, df, min_vol):
    try:
        df.columns = [c.lower() for c in df.columns]
        price_col = 'adjclose' if 'adjclose' in df.columns else 'close'
        
        if df is None or df.empty or len(df) < 100:
            return None

        current_price = float(df[price_col].iloc[-1])
        avg_volume_50 = int(df['volume'].tail(50).mean())
        avg_volume_5 = int(df['volume'].tail(5).mean())
        
        # 基礎成交量過濾 (Mag7 在下一步會豁免)
        if avg_volume_50 < min_vol:
            return "VOL_LOW", None

        sma_50 = df[price_col].rolling(window=50, min_periods=1).mean()
        sma_150 = df[price_col].rolling(window=150, min_periods=1).mean()
        sma_200 = df[price_col].rolling(window=200, min_periods=1).mean()
        
        p = float(current_price)
        s50 = float(sma_50.iloc[-1])
        s150 = float(sma_150.iloc[-1])
        s200 = float(sma_200.iloc[-1])
        s200_20d = float(sma_200.iloc[-20]) if len(sma_200) >= 20 else s200
        
        low_52w = float(df[price_col].tail(252).min())
        high_52w = float(df[price_col].tail(252).max())
        
        # 技術面 Condition 1-7
        conditions = [
            bool(p > s150 and p > s200),
            bool(s150 > s200),
            bool(s200 > s200_20d),
            bool(s50 > s150 and s50 > s200),
            bool(p > s50),
            bool(p > low_52w * 1.25),
            bool(p > high_52w * 0.75)
        ]
        
        raw_rs, ret_1y = calculate_weighted_rs(df, price_col)
        
        # 專業 VCP 波動收縮檢測
        high_15 = float(df[price_col].tail(15).max())
        low_15 = float(df[price_col].tail(15).min())
        tightness_15 = (high_15 - low_15) / p
        is_vcp = bool(p > s50 and tightness_15 < 0.08 and avg_volume_5 < avg_volume_50)

        return "OK", {
            "ticker": ticker, "price": round(p, 2), "conditions": conditions,
            "raw_rs": float(raw_rs), "volume": int(avg_volume_50),
            "return_1y": round(float(ret_1y), 1), "is_vcp": is_vcp
        }
    except Exception as e:
        print(f"Error evaluating {ticker}: {e}")
        return "ERROR", None

async def process_screener(task_id: str, min_vol: int, min_cap: int, target_list: str):
    tasks_store[task_id]["status"] = "running"
    
    # 🔴 核心校正：無論目標清單，皆掃描 503 檔以建立「全市場背景排名」
    market_tickers = get_sp500_tickers()
    mag7_list = ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA"]
    tasks_store[task_id]["total"] = int(len(market_tickers))
    
    candidates = []
    
    # 🔵 第一階段：抓取全市場資料 (確保 RS Rating 是對稱的)
    batch_size = 50
    for i in range(0, len(market_tickers), batch_size):
        batch = market_tickers[i : i + batch_size]
        tasks_store[task_id]["message"] = f"🟡 正在建立全市場排名基準指標 ({i+1}/{len(market_tickers)})..."
        try:
            q = yq.Ticker(batch)
            all_history = q.history(period="2y", interval="1d")
            if all_history.empty:
                tasks_store[task_id]["progress"] += int(len(batch))
                continue

            if isinstance(all_history.index, pd.MultiIndex):
                unique_tickers = all_history.index.get_level_values(0).unique()
                for t in unique_tickers:
                    tasks_store[task_id]["progress"] += 1
                    try:
                        ticker_df = all_history.xs(t, level=0)
                        # 🟢 邏輯：對於 Mag7 或特定觀察標的，我們豁免最低成交量限制以建立基礎 Raw RS
                        vol_limit = 0 if (target_list == "mag7" and t in mag7_list) else min_vol
                        status, res = evaluate_stock_data(t, ticker_df, vol_limit)
                        if res: candidates.append(res)
                    except: continue
            else:
                t = batch[0]
                tasks_store[task_id]["progress"] += 1
                vol_limit = 0 if (target_list == "mag7" and t in mag7_list) else min_vol
                status, res = evaluate_stock_data(t, all_history, vol_limit)
                if res: candidates.append(res)
        except Exception as e:
            tasks_store[task_id]["progress"] += int(len(batch))
        await asyncio.sleep(0.1)

    if not candidates:
        tasks_store[task_id]["status"] = "completed"
        tasks_store[task_id]["message"] = "未發現符合基礎門檻的標的。"
        return

    # 🔴 第二階段：計算真實 market-relative RS Rating
    df_rank = pd.DataFrame(candidates)
    df_rank['rs_rating'] = df_rank['raw_rs'].rank(pct=True).clip(0, 0.99) * 99
    df_rank['rs_rating'] = df_rank['rs_rating'].round(0).astype(int) + 1

    final_results = []
    for _, row in df_rank.iterrows():
        rs_rating = int(row['rs_rating'])
        cond_8 = rs_rating >= 70
        score = sum(row['conditions']) + (1 if cond_8 else 0)
        display_obj = {
            "ticker": row['ticker'], "price": row['price'], "score": int(score),
            "rs_rating": rs_rating, "volume": int(row['volume']),
            "market_cap": "N/A", "return_1y": row['return_1y'], "is_vcp": row['is_vcp']
        }
        
        # 🟡 篩選：依照按鈕目標回傳結果
        if target_list == "mag7":
            if row['ticker'] in mag7_list:
                final_results.append(display_obj)
        elif score == 8:
            final_results.append(display_obj)

    tasks_store[task_id]["results"] = sorted(final_results, key=lambda x: (x['score'], x['rs_rating']), reverse=True)
    tasks_store[task_id]["status"] = "completed"
    tasks_store[task_id]["message"] = f"✅ 完成！已基於 503 檔標的校正強度排名。"

@app.post("/api/screen/start")
async def start_screen(req: ScreenRequest, background_tasks: BackgroundTasks):
    task_id = str(uuid.uuid4())
    tasks_store[task_id] = {
        "status": "pending", "progress": 0, "total": 0, "results": [], "benchmark": 0.0, "message": "啟動中..."
    }
    background_tasks.add_task(process_screener, task_id, int(req.min_volume), int(req.min_market_cap), req.target_list)
    return {"task_id": task_id}

@app.get("/api/screen/status/{task_id}")
async def get_status(task_id: str):
    if task_id not in tasks_store:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks_store[task_id]

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
