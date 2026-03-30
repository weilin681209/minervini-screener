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
    
    const progressContainer = document.getElementById('progressContainer');
    const progressBarFill = document.getElementById('progressBarFill');
    const progressText = document.getElementById('progressText');
    const progressPercent = document.getElementById('progressPercent');
    
    const chartModal = document.getElementById('chartModal');
    const closeModalBtn = document.getElementById('closeModalBtn');
    const modalTickerTitle = document.getElementById('modalTickerTitle');
    
    let currentTaskId = null;
    let taskInterval = null;

    closeModalBtn.addEventListener('click', () => {
        chartModal.classList.add('hidden');
        document.getElementById('tradingview_container').innerHTML = '';
    });

    runBtn.addEventListener('click', () => performScan(runBtn, 'sp500'));
    runMag7Btn.addEventListener('click', () => performScan(runMag7Btn, 'mag7'));

    // 當手機螢幕熄滅再打開時，自動喚醒輪詢
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden && currentTaskId && !taskInterval) {
            console.log("喚醒背景輪詢...");
            startPolling(currentTaskId, document.getElementById('runBtn')); // 預設用 runBtn
        }
    });

    async function performScan(btnEl, targetList) {
        // 第一步：清理手機輸入法可能帶入的非數字字元 (逗號、空格等)
        const volStr = minVolume.value.toString().replace(/[^0-9]/g, '');
        const capStr = minMarketCap.value.toString().replace(/[^0-9]/g, '');
        const volumeVal = parseInt(volStr);
        const capVal = parseInt(capStr);
        
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
        progressText.textContent = `伺服器連線中...`;

        try {
            const startResponse = await fetch('/api/screen/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ min_volume: volumeVal, min_market_cap: capVal, target_list: targetList })
            });

            if (!startResponse.ok) throw new Error('伺服器目前忙碌，請稍後再試。');

            const { task_id } = await startResponse.json();
            currentTaskId = task_id;
            startPolling(task_id, btnEl, targetList);

        } catch (error) {
            showError(`啟動失敗：${error.message}`, btnEl);
        }
    }

    function startPolling(task_id, btnEl, targetList) {
        if (taskInterval) clearInterval(taskInterval);
        
        taskInterval = setInterval(async () => {
            try {
                const statusRes = await fetch(`/api/screen/status/${task_id}`);
                if (!statusRes.ok) throw new Error("通訊中斷");
                
                const data = await statusRes.json();
                
                if (data.status === "error") {
                    throw new Error(data.message || data.detail || "處理機房異常");
                }
                
                if (data.status === "running" || data.status === "completed") {
                    const percent = data.total > 0 ? Math.round((data.progress / data.total) * 100) : 0;
                    progressBarFill.style.width = `${percent}%`;
                    progressPercent.textContent = `${percent}%`;
                    progressText.textContent = data.message || `正在掃描個股趨勢 (${data.progress}/${data.total})...`;
                }
                
                if (data.status === "completed") {
                    stopPolling(btnEl);
                    scanRange.textContent = targetList === 'mag7' ? '美股七巨頭' : 'S&P 500';
                    scannedCount.textContent = data.total;
                    benchmarkReturn.textContent = data.benchmark;
                    stats.classList.remove('hidden');
                    renderResults(data.results);
                }
            } catch (err) {
                // 手機不穩定時不立即自殺，先嘗試靜默重連一次
                console.warn('Polling Warning:', err);
            }
        }, 3000); // 手機端拉長到 3 秒一次減少壓力
    }

    function stopPolling(btnEl) {
        clearInterval(taskInterval);
        taskInterval = null;
        currentTaskId = null;
        btnEl.classList.remove('loading');
        runBtn.disabled = false;
        runMag7Btn.disabled = false;
        setTimeout(() => progressContainer.classList.add('hidden'), 1000);
    }

    function showError(msg, btnEl) {
        stopPolling(btnEl);
        resultsGrid.innerHTML = `<div class="error-state">⚠️ ${msg}</div>`;
        progressContainer.classList.add('hidden');
    }

    function renderResults(stocks) {
        if (!stocks || stocks.length === 0) {
            resultsGrid.innerHTML = `<div class="empty-state">✅ 掃描完成。目前市場暫無符合所有嚴苛條件的標的。</div>`;
            return;
        }

        stocks.forEach((stock, index) => {
            const card = document.createElement('div');
            card.className = 'result-card';
            card.style.animationDelay = `${index * 0.05}s`;
            
            const formattedVol = stock.volume.toLocaleString('en-US');
            const returnClass = stock.return_1y > 0 ? 'return-positive' : 'return-negative';
            const returnSign = stock.return_1y > 0 ? '+' : '';
            const vcpBadgeHtml = stock.is_vcp ? '<div class="vcp-badge">🔥 VCP 潛力</div>' : '';
            const scoreHtml = `<div class="score-badge ${stock.score === 8 ? 'score-perfect' : ''}">🎯 ${stock.score}/8 條件</div>`;

            card.style.cursor = 'pointer';
            card.addEventListener('click', () => openTradingViewChart(stock.ticker));

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
                    <div class="data-row"><span>日均量</span><span>${formattedVol}</span></div>
                    <div class="data-row"><span>市值</span><span>$${stock.market_cap}</span></div>
                    <div class="data-row"><span>近 1Y 報酬</span><span class="${returnClass}">${returnSign}${stock.return_1y}%</span></div>
                </div>
            `;
            resultsGrid.appendChild(card);
        });
    }

    function openTradingViewChart(ticker) {
        chartModal.classList.remove('hidden');
        modalTickerTitle.textContent = `${ticker} - 實時分析`;
        new TradingView.widget({
            "autosize": true,
            "symbol": ticker,
            "interval": "D",
            "timezone": "exchange",
            "theme": "dark",
            "style": "1",
            "locale": "zh_TW",
            "container_id": "tradingview_container",
            "studies": ["Volume@tv-basicstudies", "MASimple@tv-basicstudies"]
        });
    }
});
