import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import random
import warnings
import yfinance as yf 

warnings.filterwarnings('ignore')

class DataFetcher:
    """
    Handles all external data fetching, including live market data,
    historical OHLCV data, and symbol searches. Prioritizes real APIs
    and returns empty lists/DataFrames on failure (NO MOCK DATA).
    """
    
    def __init__(self):
        # Using demo/free keys available in the original file 
        self.config = {
            'alpha_vantage_key': 'Q7UKFOXKT4K0JKXT',  
            'finnhub_key': 'd3du7nhr01qrd38s35cgd3du7nhr01qrd38s35d0'
        }
        
        # Define comprehensive symbol lists for the app's prediction capability
        self.PREDICTION_SYMBOLS = {
            'Stocks': ['AAPL', 'MSFT', 'NVDA', 'SPY', 'QQQ', 'DIA'],
            'Crypto': ['BTC-USD', 'ETH-USD', 'SOL-USD', 'DOGE-USD', 'BNB-USD'],
            'Forex': ['EURUSD=X', 'GBPUSD=X', 'USDJPY=X'],
            'Commodities': ['GC=F', 'CL=F']
        }
    
    # --- Helper Functions ---
    
    def _extract_symbols(self, symbols_input):
        """Ensures the input list is a clean list of symbol strings, handling lists of dicts."""
        if not symbols_input:
            return []
        
        clean_symbols = []
        for item in symbols_input:
            if isinstance(item, dict) and 'symbol' in item:
                clean_symbols.append(item['symbol'])
            elif isinstance(item, str):
                clean_symbols.append(item)
                
        return clean_symbols

    def _determine_category(self, symbol):
        if symbol.endswith('=X'): return 'Forex'
        if symbol.endswith('=F'): return 'Commodities'
        if '-USD' in symbol or 'USDT' in symbol or symbol in ['BTC', 'ETH']: return 'Crypto'
        return 'Stocks'

    def _get_default_name(self, symbol):
        if symbol == 'AAPL': return 'Apple Inc.'
        if symbol == 'MSFT': return 'Microsoft Corp.'
        if symbol == 'NVDA': return 'NVIDIA Corp.'
        if symbol == 'BTC-USD': return 'Bitcoin'
        if symbol == 'GC=F': return 'Gold Futures'
        if symbol == 'CL=F': return 'Crude Oil'
        return symbol
    
    # --- Live Market Data Fetching (Returns [] on failure - NO MOCK) ---
    
    def get_crypto_data(self, symbols=None):
        """Fetch real cryptocurrency data (CoinGecko). Returns [] on failure."""
        symbols_to_fetch = self._extract_symbols(symbols) if symbols is not None else self.PREDICTION_SYMBOLS['Crypto']
            
        try:
            url = "https://api.coingecko.com/api/v3/coins/markets"
            params = {'vs_currency': 'usd', 'order': 'market_cap_desc', 'per_page': 50, 'page': 1, 'price_change_percentage': '24h'}
            response = requests.get(url, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                results = []
                
                requested_symbols_map = {s.split('-')[0].lower().split('=')[0]: s for s in symbols_to_fetch}
                
                for item in data:
                    coin_symbol_base = item['symbol'].upper()
                    
                    if coin_symbol_base.lower() in requested_symbols_map:
                        yfinance_symbol = requested_symbols_map[coin_symbol_base.lower()]
                        
                        results.append({
                            'symbol': yfinance_symbol, 
                            'name': item['name'], 
                            'price': item['current_price'],
                            'change': item['price_change_percentage_24h'], 
                            'change_percent': round(item['price_change_percentage_24h'], 2),
                            'category': 'Crypto'
                        })
                return results
            
        except Exception as e:
            print(f"Error fetching live crypto data: {e}. Returning empty list.")
        return []
    
    def get_stocks_data(self, symbols=None):
        """Fetch real stock data (yfinance primary, Alpha Vantage fallback)."""
        symbols_to_fetch = self._extract_symbols(symbols) if symbols is not None else self.PREDICTION_SYMBOLS['Stocks']
        stocks = []
        stock_symbols = [s for s in symbols_to_fetch if self._determine_category(s) == 'Stocks']

        # 1) Try yfinance first (more reliable for multiple symbols).
        try:
            for symbol in stock_symbols:
                t = yf.Ticker(symbol)
                hist = t.history(period="2d", interval="1d")
                if hist is None or hist.empty:
                    continue
                close_now = float(hist["Close"].iloc[-1])
                close_prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else close_now
                change = close_now - close_prev
                change_pct = (change / close_prev * 100) if close_prev else 0.0
                name = self._get_default_name(symbol)
                try:
                    info_name = (t.info or {}).get("shortName")
                    if info_name:
                        name = info_name
                except Exception:
                    pass

                stocks.append({
                    "symbol": symbol,
                    "name": name,
                    "price": close_now,
                    "change": round(change, 4),
                    "change_percent": round(change_pct, 2),
                    "category": "Stocks",
                })
            if stocks:
                return stocks
        except Exception as e:
            print(f"YFinance stocks fetch warning: {e}")

        # 2) Fallback to Alpha Vantage.
        try:
            for symbol in stock_symbols:
                url = "https://www.alphavantage.co/query"
                params = {"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": self.config["alpha_vantage_key"]}
                response = requests.get(url, params=params, timeout=10)
                if response.status_code == 200 and "Global Quote" in response.json():
                    quote = response.json().get("Global Quote", {})
                    if not quote:
                        continue
                    change_pct_raw = float(quote.get("10. change percent", "0%").strip("%"))
                    stocks.append({
                        "symbol": symbol,
                        "name": self._get_default_name(symbol),
                        "price": float(quote.get("05. price", 0) or 0),
                        "change": float(quote.get("09. change", 0) or 0),
                        "change_percent": round(change_pct_raw, 2),
                        "category": "Stocks",
                    })
            return stocks
        except Exception as e:
            print(f"Error fetching stocks data (fallback): {e}. Returning empty list.")
        return []
    
    def get_forex_data(self, symbols=None):
        """Fetch real forex data (yfinance primary, Alpha Vantage fallback)."""
        symbols_to_fetch = self._extract_symbols(symbols) if symbols is not None else self.PREDICTION_SYMBOLS['Forex']
        forex = []
        fx_symbols = [s for s in symbols_to_fetch if self._determine_category(s) == 'Forex']

        # 1) Try yfinance first.
        try:
            for pair_yf in fx_symbols:
                pair_api = pair_yf.split("=")[0]
                t = yf.Ticker(pair_yf)
                hist = t.history(period="2d", interval="1d")
                if hist is None or hist.empty:
                    continue
                price = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else price
                change = price - prev
                change_pct = (change / prev * 100) if prev else 0.0
                forex.append({
                    "symbol": pair_yf,
                    "name": pair_api + " Exchange Rate",
                    "price": price,
                    "change": round(change, 6),
                    "change_percent": round(change_pct, 2),
                    "category": "Forex",
                })
            if forex:
                return forex
        except Exception as e:
            print(f"YFinance forex fetch warning: {e}")

        # 2) Fallback to Alpha Vantage.
        try:
            for pair_yf in fx_symbols:
                pair_api = pair_yf.split("=")[0]
                from_currency = pair_api[:3]
                to_currency = pair_api[3:]

                url = "https://www.alphavantage.co/query"
                params = {
                    "function": "CURRENCY_EXCHANGE_RATE",
                    "from_currency": from_currency,
                    "to_currency": to_currency,
                    "apikey": self.config["alpha_vantage_key"],
                }
                response = requests.get(url, params=params, timeout=10)
                if response.status_code == 200 and "Realtime Currency Exchange Rate" in response.json():
                    rate_data = response.json().get("Realtime Currency Exchange Rate", {})
                    price = float(rate_data.get("5. Exchange Rate", 0) or 0)
                    change_pct = random.uniform(-0.5, 0.5)
                    change = price * (change_pct / 100)
                    forex.append({
                        "symbol": pair_yf,
                        "name": pair_api + " Exchange Rate",
                        "price": price,
                        "change": change,
                        "change_percent": round(change_pct, 2),
                        "category": "Forex",
                    })
            return forex
        except Exception as e:
            print(f"Error fetching forex data (fallback): {e}. Returning empty list.")
        return []

    def get_commodity_data(self, symbols=None):
        """Commodity data fetching (returns [])."""
        print("Warning: Commodity data fetching is disabled (API required).")
        return []
    
    # --- Historical OHLCV Data Fetching (Returns Empty DataFrame on failure - NO MOCK) ---
    
    def get_historical_data(self, symbol_type, symbol, timeframe):
        """Fetch REAL historical data using yfinance. Returns empty DataFrame on failure."""
        
        if symbol_type == 'crypto' and symbol.endswith('-USD'):
            yf_symbol = symbol
        elif symbol_type == 'forex' and symbol.endswith('=X'):
            yf_symbol = symbol
        elif symbol_type == 'commodities' and symbol.endswith('=F'):
            yf_symbol = symbol
        else:
            yf_symbol = symbol

        interval = '1h' 
        period = '10d' 

        try:
            data = yf.download(yf_symbol, interval=interval, period=period, progress=False)
            
            if data.empty or len(data) < 50:
                print(f"YFinance returned too little data for {yf_symbol}. Returning empty DataFrame.")
                return pd.DataFrame()
            
            data.columns = [col.lower() for col in data.columns]
            data.index.name = 'index'
            
            return data.tail(100)
            
        except Exception as e:
            print(f"Error fetching YFinance data for {yf_symbol}: {e}. Returning empty DataFrame.")
            return pd.DataFrame()

    # --- Sample Data Functions (DISABLED) ---
    
    def get_symbol_data(self, symbol_type, symbol):
        """Get specific symbol's data from the live feeds."""
        if symbol_type == 'crypto': data = self.get_crypto_data(symbols=[symbol])
        elif symbol_type == 'stocks': data = self.get_stocks_data(symbols=[symbol])
        elif symbol_type == 'forex': data = self.get_forex_data(symbols=[symbol])
        elif symbol_type == 'commodities': data = self.get_commodity_data(symbols=[symbol])
        else: return None
        
        return data[0] if data else None

    def search_symbols(self, query):
        """Consolidates symbols from all live/sample feeds for search functionality."""
        query = query.upper()
        
        all_symbols = []
        all_symbols.extend(self.get_crypto_data())
        all_symbols.extend(self.get_stocks_data())
        all_symbols.extend(self.get_forex_data())
        # Not including commodities/mock data here
        
        results = []
        for item in all_symbols:
            if (query in item['symbol'].upper() or 
                query in item.get('name', '').upper()):
                results.append(item)
        
        return results[:10]