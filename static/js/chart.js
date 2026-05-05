// Chart-specific JavaScript functionality

class ChartManager {
    constructor() {
        this.currentSymbol = '';
        this.currentTimeframe = '1D';
        this.activeIndicators = new Set();
        this.chartWidget = null;
    }
    
    initializeChart(symbol, containerId) {
        this.currentSymbol = symbol;
        
        // Initialize TradingView widget
        this.chartWidget = new TradingView.widget({
            container_id: containerId,
            autosize: true,
            symbol: symbol,
            interval: this.currentTimeframe,
            timezone: "Etc/UTC",
            theme: "dark",
            style: "1",
            locale: "en",
            toolbar_bg: "#0a0a0a",
            enable_publishing: false,
            allow_symbol_change: true,
            studies: [],
            details: true,
            hotlist: true,
            calendar: true,
            studies_overrides: {},
            overrides: {
                "paneProperties.background": "#0a0a0a",
                "paneProperties.vertGridProperties.color": "#1a1a1a",
                "paneProperties.horzGridProperties.color": "#1a1a1a",
                "mainSeriesProperties.candleStyle.upColor": "#00ff88",
                "mainSeriesProperties.candleStyle.downColor": "#ff4444",
                "mainSeriesProperties.candleStyle.borderUpColor": "#00ff88",
                "mainSeriesProperties.candleStyle.borderDownColor": "#ff4444",
            }
        });
    }
    
    changeTimeframe(timeframe) {
        this.currentTimeframe = timeframe;
        if (this.chartWidget) {
            this.chartWidget.setChartType(this.getChartType(timeframe));
        }
    }
    
    getChartType(timeframe) {
        const minuteTimeframes = ['1', '5', '15', '30', '60', '240'];
        return minuteTimeframes.includes(timeframe) ? 'candles' : 'line';
    }
    
    addIndicator(indicator) {
        if (this.activeIndicators.has(indicator)) {
            this.removeIndicator(indicator);
            return;
        }
        
        this.activeIndicators.add(indicator);
        
        // Add indicator to TradingView chart
        if (this.chartWidget) {
            const studyId = this.getStudyId(indicator);
            this.chartWidget.chart().createStudy(studyId, false, false);
        }
        
        // Perform backend analysis
        this.performTechnicalAnalysis(indicator);
    }
    
    removeIndicator(indicator) {
        this.activeIndicators.delete(indicator);
        // Implementation to remove indicator from chart
    }
    
    getStudyId(indicator) {
        const studyMap = {
            'sma': 'Moving Average Exponential',
            'ema': 'Moving Average Exponential',
            'rsi': 'RSI',
            'macd': 'MACD',
            'bollinger': 'Bollinger Bands'
        };
        return studyMap[indicator] || indicator;
    }
    
    async performTechnicalAnalysis(indicator) {
        try {
            const response = await fetch('/api/technical_analysis', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    symbol_type: this.getSymbolType(this.currentSymbol),
                    symbol: this.currentSymbol,
                    analysis_type: indicator,
                    timeframe: this.currentTimeframe
                })
            });
            
            const result = await response.json();
            this.displayAnalysisResult(result);
            
        } catch (error) {
            console.error('Technical analysis error:', error);
            showNotification('Failed to perform technical analysis', 'error');
        }
    }
    
    getSymbolType(symbol) {
        if (symbol.includes('/')) return 'forex';
        if (symbol.length <= 4) return 'stocks';
        return 'crypto';
    }
    
    displayAnalysisResult(result) {
        // Display analysis results in a dedicated panel
        console.log('Analysis result:', result);
        
        // This would update a dedicated analysis panel in the UI
        const analysisPanel = document.getElementById('analysis-panel') || 
                            this.createAnalysisPanel();
        
        const analysisItem = document.createElement('div');
        analysisItem.className = 'analysis-item';
        analysisItem.innerHTML = `
            <h4>${result.analysis_type.toUpperCase()} Analysis</h4>
            <p>Symbol: ${result.symbol}</p>
            <p>Timeframe: ${result.timeframe}</p>
            <p>Timestamp: ${new Date(result.timestamp).toLocaleString()}</p>
        `;
        
        analysisPanel.appendChild(analysisItem);
    }
    
    createAnalysisPanel() {
        const panel = document.createElement('div');
        panel.id = 'analysis-panel';
        panel.style.cssText = `
            position: fixed;
            right: 20px;
            top: 100px;
            width: 300px;
            max-height: 400px;
            overflow-y: auto;
            background: var(--secondary-bg);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 1rem;
            z-index: 1000;
        `;
        
        document.body.appendChild(panel);
        return panel;
    }
}

// Global chart manager instance
window.chartManager = new ChartManager();