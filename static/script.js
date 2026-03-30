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
    
    // Modal Elements
    const chartModal = document.getElementById('chartModal');
    const closeModalBtn = document.getElementById('closeModalBtn');
    const modalTickerTitle = document.getElementById('modalTickerTitle');

    // 關閉 Modal 面板
    closeModalBtn.addEventListener('click', () => {
        chartModal.classList.add('hidden');
        document.getElementById('tradingview_container').innerHTML = '';
    });

    runBtn.addEventListener('click', () => performScan(runBtn, 'sp500'));
    runMag7Btn.addEventListener('click', () => performScan(runMag7Btn, 'mag7'));

    async function performScan(btnEl, targetList) {
        // Validation
        const volumeVal = parseInt(minVolume.value);
        const capVal = parseInt(minMarketCap.value);
        
        if (isNaN(volumeVal) || isNaN(capVal)) {
            alert('請輸入有效的數字');
            return;
        }

        // Set Loading State
        btnEl.classList.add('loading');
        runBtn.disabled = true;
        runMag7Btn.disabled = true;
        
        resultsGrid.innerHTML = '';
        stats.classList.add('hidden');

        try {
            const response = await fetch('/api/screen', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    min_volume: volumeVal,
                    min_market_cap: capVal,
                    target_list: targetList
                })
            });

            if (!response.ok) {
                throw new Error('伺服器發生錯誤，請檢查後端是否正常運作。');
            }

            const data = await response.json();
            
            // Update stats
            scanRange.textContent = targetList === 'mag7' ? '美股七巨頭 (Mag 7)' : 'S&P 500';
            scannedCount.textContent = data.total_scanned;
            benchmarkReturn.textContent = data.benchmark_return;
            stats.classList.remove('hidden');

            renderResults(data.passed);
            
        } catch (error) {
            console.error('Error:', error);
            resultsGrid.innerHTML = `<div class="empty-state">掃描失敗或逾時：${error.message} <br/><br/>第一次掃描可能會比較久，請確認後端服務運行中。</div>`;
        } finally {
            btnEl.classList.remove('loading');
            runBtn.disabled = false;
            runMag7Btn.disabled = false;
        }
    }

    function renderResults(stocks) {
        if (!stocks || stocks.length === 0) {
            resultsGrid.innerHTML = `<div class="empty-state">目前沒有股票符合所有極其嚴格的 Mark Minervini 條件。</div>`;
            return;
        }

        // Add stocks with staggered animation delay
        stocks.forEach((stock, index) => {
            const card = document.createElement('div');
            card.className = 'result-card';
            card.style.animationDelay = `${index * 0.05}s`;
            
            // Format volume with commas
            const formattedVol = stock.volume.toLocaleString('en-US');
            
            // Format return color
            const returnClass = stock.return_1y > 0 ? 'return-positive' : 'return-negative';
            const returnSign = stock.return_1y > 0 ? '+' : '';

            // VCP Badge rendering
            const vcpBadgeHtml = stock.is_vcp 
                ? '<div class="vcp-badge"><span class="vcp-icon">🔥</span> VCP 潛力股</div>' 
                : '';

            // Score Badge rendering
            const scoreHtml = (stock.score !== undefined)
                ? `<div class="score-badge ${stock.score === 8 ? 'score-perfect' : ''}">🎯 ${stock.score}/8 條件</div>` 
                : '';

            // 賦予卡片點擊看線圖功能
            card.style.cursor = 'pointer';
            card.addEventListener('click', () => {
                openTradingViewChart(stock.ticker);
            });

            card.innerHTML = `
                <div class="card-header">
                    <div class="ticker-box">
                        <div class="ticker">${stock.ticker}</div>
                        ${scoreHtml}
                        ${vcpBadgeHtml}
                    </div>
                    <div class="price">$${stock.price}</div>
                </div>
                <div class="card-body">
                    <div class="data-row">
                        <span class="data-label">日均量</span>
                        <span class="data-value">${formattedVol}</span>
                    </div>
                    <div class="data-row">
                        <span class="data-label">市值</span>
                        <span class="data-value">${stock.market_cap.includes('M') || stock.market_cap.includes('B') ? '$' : ''}${stock.market_cap}</span>
                    </div>
                    <div class="data-row">
                        <span class="data-label">近 1Y 報酬</span>
                        <span class="data-value ${returnClass}">${returnSign}${stock.return_1y}%</span>
                    </div>
                </div>
            `;
            resultsGrid.appendChild(card);
        });
    }

    // 啟動 TradingView Widget 的函式
    function openTradingViewChart(ticker) {
        chartModal.classList.remove('hidden');
        modalTickerTitle.textContent = `${ticker} - 專業技術分析線圖`;
        
        // TradingView 掛載
        new TradingView.widget({
            "autosize": true,
            "symbol": ticker,
            "interval": "D",    // 日線圖
            "timezone": "exchange",
            "theme": "dark",    // 深色主題完美融合
            "style": "1",       // 蠟燭線
            "locale": "zh_TW",
            "enable_publishing": false,
            "backgroundColor": "#030014", // 吻合我們的最高級深色背景色
            "gridColor": "rgba(255, 255, 255, 0.05)",
            "hide_legend": false,
            "save_image": false,
            "container_id": "tradingview_container", // 我們 Modal 裡的 div
            "studies": [
                "Volume@tv-basicstudies",      // 交易量
                "MASimple@tv-basicstudies",    // 簡單移動平均線
                "RSI@tv-basicstudies"          // 相對強弱指標
            ]
        });
    }
});
