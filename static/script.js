document.addEventListener('DOMContentLoaded', () => {
    const runBtn = document.getElementById('runBtn');
    const runMag7Btn = document.getElementById('runMag7Btn');
    const minVolume = document.getElementById('minVolume');
    const minMarketCap = document.getElementById('minMarketCap');
    const resultsGrid = document.getElementById('resultsGrid');
    const stats = document.getElementById('stats');
    const scannedCount = document.getElementById('scannedCount');
    const benchmarkReturn = document.getElementById('benchmarkReturn');
    const scanRange = document.getElementById('scanRange');
    
    // Progress Bar Elements
    const progressContainer = document.getElementById('progressContainer');
    const progressBarFill = document.getElementById('progressBarFill');
    const progressText = document.getElementById('progressText');
    const progressPercent = document.getElementById('progressPercent');
    
    // Modal Elements
    const chartModal = document.getElementById('chartModal');
    const closeModalBtn = document.getElementById('closeModalBtn');
    const modalTickerTitle = document.getElementById('modalTickerTitle');
    
    closeModalBtn.addEventListener('click', () => {
        chartModal.classList.add('hidden');
        document.getElementById('tradingview_container').innerHTML = '';
    });

    runBtn.addEventListener('click', () => performScan(runBtn, 'sp500'));
    runMag7Btn.addEventListener('click', () => performScan(runMag7Btn, 'mag7'));

    async function performScan(btnEl, targetList) {
        const volumeVal = parseInt(minVolume.value);
        const capVal = parseInt(minMarketCap.value);
        
        if (isNaN(volumeVal) || isNaN(capVal)) {
            alert('請輸入有效的數字');
            return;
        }

        btnEl.classList.add('loading');
        runBtn.disabled = true;
        runMag7Btn.disabled = true;
        resultsGrid.innerHTML = '';
        stats.classList.add('hidden');
        progressContainer.classList.remove('hidden');
        progressBarFill.style.width = '0%';
        progressPercent.textContent = '0%';
        progressText.textContent = `準備執行專業 RS 百分比排名掃描 (${targetList === 'mag7' ? '美股七巨頭' : 'S&P 500'})...`;

        try {
            const startResponse = await fetch('/api/screen/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ min_volume: volumeVal, min_market_cap: capVal, target_list: targetList })
            });

            const { task_id } = await startResponse.json();
            
            const taskInterval = setInterval(async () => {
                const statusRes = await fetch(`/api/screen/status/${task_id}`);
                const data = await statusRes.json();
                
                if (data.status === "running" || data.status === "completed") {
                    const percent = data.total > 0 ? Math.round((data.progress / data.total) * 100) : 0;
                    progressBarFill.style.width = `${percent}%`;
                    progressPercent.textContent = `${percent}%`;
                    progressText.textContent = data.message || `正在抓取全市場資料以進行強度排名...`;
                }
                
                if (data.status === "completed") {
                    clearInterval(taskInterval);
                    setTimeout(() => progressContainer.classList.add('hidden'), 500);
                    btnEl.classList.remove('loading');
                    runBtn.disabled = false;
                    runMag7Btn.disabled = false;
                    scanRange.textContent = targetList === 'mag7' ? '美股七巨頭 (Mag 7)' : 'S&P 500';
                    scannedCount.textContent = data.total;
                    benchmarkReturn.textContent = data.benchmark;
                    stats.classList.remove('hidden');
                    renderResults(data.results);
                }
            }, 2000);
        } catch (error) {
            console.error('Error:', error);
            resultsGrid.innerHTML = `<div class="empty-state">掃描中斷：伺服器解析錯誤。</div>`;
            progressContainer.classList.add('hidden');
            btnEl.classList.remove('loading');
            runBtn.disabled = false;
            runMag7Btn.disabled = false;
        }
    }

    function renderResults(stocks) {
        resultsGrid.innerHTML = '';
        if (!stocks || stocks.length === 0) {
            resultsGrid.innerHTML = `<div class="empty-state">目前沒有股票完全符合 8/8 門檻與 RS Rating > 70 的嚴苛標準。</div>`;
            return;
        }

        stocks.forEach((stock, index) => {
            const card = document.createElement('div');
            card.className = 'result-card';
            card.style.animationDelay = `${index * 0.05}s`;
            
            const formattedVol = stock.volume.toLocaleString('en-US');
            const returnClass = stock.return_1y > 0 ? 'return-positive' : 'return-negative';
            const returnSign = stock.return_1y > 0 ? '+' : '';
            const vcpBadgeHtml = stock.is_vcp ? '<div class="vcp-badge">🔥 VCP</div>' : '';
            const scoreHtml = `<div class="score-badge ${stock.score === 8 ? 'score-perfect' : ''}">🎯 ${stock.score}/8</div>`;
            
            // 🔴 專業級 RS 標籤：高分 (90+) 時加入特別樣式
            const rsBadgeHtml = `<div class="rs-badge ${stock.rs_rating >= 90 ? 'rs-high' : ''}">⚡ RS: ${stock.rs_rating}</div>`;

            card.addEventListener('click', () => openTradingViewChart(stock.ticker));

            card.innerHTML = `
                <div class="card-header">
                    <div class="ticker-box">
                        <div class="ticker">${stock.ticker}</div>
                        ${scoreHtml}
                        ${rsBadgeHtml}
                        ${vcpBadgeHtml}
                    </div>
                    <div class="price">$${stock.price}</div>
                </div>
                <div class="card-body">
                    <div class="data-row"><span class="data-label">日均量</span><span class="data-value">${formattedVol}</span></div>
                    <div class="data-row"><span class="data-label">RS 排名</span><span class="data-value ${stock.rs_rating >= 90 ? 'return-positive' : ''}">市場評分 ${stock.rs_rating}</span></div>
                    <div class="data-row"><span class="data-label">1Y 報酬</span><span class="data-value ${returnClass}">${returnSign}${stock.return_1y}%</span></div>
                </div>
            `;
            resultsGrid.appendChild(card);
        });
    }

    function openTradingViewChart(ticker) {
        chartModal.classList.remove('hidden');
        modalTickerTitle.textContent = `${ticker} - 技術分析圖`;
        new TradingView.widget({
            "autosize": true,
            "symbol": ticker,
            "interval": "D",
            "timezone": "exchange",
            "theme": "dark",
            "style": "1",
            "locale": "zh_TW",
            "container_id": "tradingview_container",
            "studies": ["Volume@tv-basicstudies", "MASimple@tv-basicstudies", "RSI@tv-basicstudies", "MAExp@tv-basicstudies"]
        });
    }
});
