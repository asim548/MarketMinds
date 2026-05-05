import pandas as pd
import numpy as np
from datetime import datetime

class TradingAPI:
    def __init__(self, data_fetcher):
        # CRITICAL: Now requires a DataFetcher instance to fetch real OHLCV data
        self.data_fetcher = data_fetcher 
        self.technical_indicators = {
            'sma': self._calculate_sma,
            'ema': self._calculate_ema,
            'rsi': self._calculate_rsi,
            'macd': self._calculate_macd,
            'bollinger_bands': self._calculate_bollinger_bands
        }
    
    def perform_technical_analysis(self, symbol_type, symbol, analysis_type, timeframe):
        """
        Fetches REAL historical data using DataFetcher and performs technical analysis.
        """
        if analysis_type in self.technical_indicators:
            
            # 1. Fetch REAL data using the injected DataFetcher
            # The AI model was trained on 1h data, so we enforce it here for compatibility
            data = self.data_fetcher.get_historical_data(symbol_type, symbol, '1h') 
            
            if data is None or data.empty:
                 # Raise ValueError to be caught in app.py
                 raise ValueError(f"Failed to retrieve REAL historical data for {symbol}.")
            
            # 2. Perform calculation
            result = self.technical_indicators[analysis_type](data)
            
            # 3. Extract the last value
            # last_value is a single number for RSI, or a dict for MACD/BB
            last_value = result[-1] if isinstance(result, list) else {k: v[-1] for k, v in result.items()}

            return {
                'analysis_type': analysis_type,
                'symbol': symbol,
                'timeframe': timeframe,
                'result_series': result,
                'last_value': last_value, 
                'current_price': data['close'].iloc[-1],
                'timestamp': datetime.now().isoformat()
            }
        
        return {'error': 'Analysis type not supported'}
    
    # --- Technical Indicator Calculation Methods (Need to return lists, not Series) ---
    
    def _calculate_sma(self, data, window=20):
        # We drop NA values to get a clean list of calculated points
        return data['close'].rolling(window=window).mean().dropna().tolist()
    
    def _calculate_ema(self, data, span=20):
        return data['close'].ewm(span=span).mean().tolist()
    
    def _calculate_rsi(self, data, window=14):
        delta = data['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
        # Handle division by zero
        rs = np.where(loss == 0, np.inf, gain / loss)
        rsi = 100 - (100 / (1 + rs))
        
        # We drop the initial 14-day NA values
        return rsi.dropna().tolist()
    
    def _calculate_macd(self, data):
        ema_12 = data['close'].ewm(span=12, adjust=False).mean()
        ema_26 = data['close'].ewm(span=26, adjust=False).mean()
        macd = ema_12 - ema_26
        signal = macd.ewm(span=9, adjust=False).mean()
        
        # We drop the NA values from the beginning (related to span 26)
        data_macd = pd.DataFrame({'macd': macd, 'signal': signal}).dropna()
        
        return {
            'macd': data_macd['macd'].tolist(),
            'signal': data_macd['signal'].tolist()
        }
    
    def _calculate_bollinger_bands(self, data, window=20):
        sma = data['close'].rolling(window=window).mean()
        std = data['close'].rolling(window=window).std()
        upper_band = sma + (std * 2)
        lower_band = sma - (std * 2)
        
        data_bb = pd.DataFrame({
            'sma': sma,
            'upper_band': upper_band,
            'lower_band': lower_band
        }).dropna()
        
        return {
            'sma': data_bb['sma'].tolist(),
            'upper_band': data_bb['upper_band'].tolist(),
            'lower_band': data_bb['lower_band'].tolist()
        }