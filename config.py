import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key'
    
    # API Keys (replace with your actual keys)
    ALPHA_VANTAGE_API_KEY = 'Q7UKFOXKT4K0JKXT'
    COINGECKO_API_KEY = 'CG-uwAfEaQYJHKQTjeC4EVDbGLH'
    FINNHUB_API_KEY = 'd3du7nhr01qrd38s35cgd3du7nhr01qrd38s35d0'
    
    # API Endpoints
    ALPHA_VANTAGE_URL = 'https://www.alphavantage.co/query'
    COINGECKO_URL = 'https://api.coingecko.com/api/v3'
    FINNHUB_URL = 'https://finnhub.io/api/v1'
    
    # TradingView configuration
    TRADINGVIEW_WIDGET_URL = 'https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js'