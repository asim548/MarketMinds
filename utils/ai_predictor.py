import pandas as pd
import numpy as np
import joblib
import os
from datetime import datetime, timedelta
import warnings

warnings.filterwarnings('ignore')

# --- FULL SYMBOL LIST FOR PREDICTION (EXPANDED) ---
PREDICTION_SYMBOLS = {
    'Stocks': [
        {'symbol': 'AAPL', 'name': 'Apple Inc.'}, 
        {'symbol': 'TSLA', 'name': 'Tesla Inc.'}, 
        {'symbol': 'MSFT', 'name': 'Microsoft Corp.'},
        {'symbol': 'NVDA', 'name': 'NVIDIA Corp.'},
        {'symbol': 'SPY', 'name': 'S&P 500 ETF'},
        {'symbol': 'GOOGL', 'name': 'Alphabet Inc. (Google)'}, 
        {'symbol': 'AMZN', 'name': 'Amazon.com Inc.'},          
        {'symbol': 'NFLX', 'name': 'Netflix Inc.'},             
        {'symbol': 'AMD', 'name': 'Adv Micro Devices'},          
        {'symbol': 'JPM', 'name': 'JPMorgan Chase & Co.'},       
    ],
    'Crypto': [
        {'symbol': 'BTC-USD', 'name': 'Bitcoin'}, 
        {'symbol': 'ETH-USD', 'name': 'Ethereum'}, 
        {'symbol': 'SOL-USD', 'name': 'Solana'},
        {'symbol': 'DOGE-USD', 'name': 'Dogecoin'},
        {'symbol': 'ADA-USD', 'name': 'Cardano'},                
        {'symbol': 'XRP-USD', 'name': 'Ripple'},                 
        {'symbol': 'LINK-USD', 'name': 'Chainlink'},             
        {'symbol': 'DOT-USD', 'name': 'Polkadot'},               
        {'symbol': 'LTC-USD', 'name': 'Litecoin'},               
        {'symbol': 'BNB-USD', 'name': 'Binance Coin'},           
    ],
    'Forex': [
        {'symbol': 'EURUSD=X', 'name': 'Euro/USD'}, 
        {'symbol': 'GBPUSD=X', 'name': 'GBP/USD'},
        {'symbol': 'USDJPY=X', 'name': 'USD/JPY'},               
        {'symbol': 'AUDUSD=X', 'name': 'AUD/USD'},               
        {'symbol': 'USDCAD=X', 'name': 'USD/CAD'},               
    ],
    'Commodities': [
        {'symbol': 'GC=F', 'name': 'Gold Futures'}, 
        {'symbol': 'CL=F', 'name': 'Crude Oil'}
    ]
}

class AdvancedFeatureEngineer:
    """
    Complete Feature Engineer implementation to generate ALL 34 features 
    required by the trained model (from notebook Cell 2).
    """
    def __init__(self): 
        pass

    def calculate_technical_indicators(self, df_group):
        """
        Calculate ALL technical indicators for a single instrument group.
        """
        
        # FIX: Reset index to ensure 'symbol' is not ambiguous, then set 'index' as time index
        df_group = df_group.reset_index().set_index('index')
        
        # Price features & MAs
        df_group['returns'] = df_group['close'].pct_change().fillna(0)
        # Use log base e for log_returns
        df_group['log_returns'] = np.log(df_group['close'] / (df_group['close'].shift(1).fillna(df_group['close']) + 1e-10)).fillna(0)
        
        for window in [5, 10, 20]:
            df_group[f'sma_{window}'] = df_group['close'].rolling(window, min_periods=1).mean()
            df_group[f'ema_{window}'] = df_group['close'].ewm(span=window, adjust=False).mean()
        
        # Volatility
        for window in [5, 10, 20]:
            df_group[f'volatility_{window}'] = df_group['returns'].rolling(window, min_periods=1).std().fillna(0)
        
        # Volume Ratio
        vol_sma = df_group['volume'].rolling(20, min_periods=1).mean().fillna(1e-10)
        df_group['volume_ratio'] = df_group['volume'] / (vol_sma + 1e-10)

        # RSI (14 period)
        delta = df_group['close'].diff()
        gain = delta.where(delta > 0, 0).fillna(0)
        loss = -delta.where(delta < 0, 0).fillna(0)
        avg_gain = gain.ewm(span=14, adjust=False).mean().fillna(0)
        avg_loss = loss.ewm(span=14, adjust=False).mean().fillna(0)
        rs = avg_gain / (avg_loss + 1e-10)
        df_group['rsi'] = 100 - (100 / (1 + rs)).fillna(50)

        # MACD
        ema12 = df_group['close'].ewm(span=12, adjust=False).mean()
        ema26 = df_group['close'].ewm(span=26, adjust=False).mean()
        df_group['macd'] = ema12 - ema26
        df_group['macd_signal'] = ema26.ewm(span=9, adjust=False).mean()
        df_group['macd_diff'] = df_group['macd'] - df_group['macd_signal']
        
        # Bollinger Bands (20 period)
        bb_mid = df_group['close'].rolling(20, min_periods=1).mean()
        bb_std = df_group['close'].rolling(20, min_periods=1).std().fillna(0)
        bb_high = bb_mid + (bb_std * 2)
        bb_low = bb_mid - (bb_std * 2)
        df_group['bb_width'] = ((bb_high - bb_low) / (bb_mid + 1e-10)).fillna(0)
        df_group['bb_position'] = ((df_group['close'] - bb_low) / ((bb_high - bb_low) + 1e-10)).fillna(0.5)
        
        # ATR (14 period)
        high_low = df_group['high'] - df_group['low']
        high_close = np.abs(df_group['high'] - df_group['close'].shift(1)).fillna(high_low)
        low_close = np.abs(df_group['low'] - df_group['close'].shift(1)).fillna(high_low)
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df_group['atr'] = tr.rolling(14, min_periods=1).mean().fillna(0)
        
        # Pre-event features
        df_group['pre_event_volatility'] = df_group['volatility_5'].shift(1).fillna(0)
        df_group['pre_event_return'] = ((df_group['close'].shift(1) - df_group['close'].shift(6)) / (df_group['close'].shift(6) + 1e-10)).fillna(0)

        return df_group.reset_index()


    def create_event_features(self, df, event_data):
        """
        Generates live economic event features for the prediction.
        """
        # Assign event data to all rows 
        df['event_actual'] = event_data.get('event_actual', 0.0)
        df['event_expected'] = event_data.get('event_expected', 0.0)
        df['event_previous'] = event_data.get('event_previous', 0.0)
        df['event_type'] = event_data.get('event_type', 'NONE')
        
        # 1. Core Event Surprise/Momentum
        raw_surprise = event_data.get('raw_surprise', 0.0)
        
        df['surprise'] = raw_surprise * 100
        df['momentum'] = (df['event_actual'] - df['event_previous']) / (df['event_previous'] + 1e-10) * 100

        # 2. Directional Features (FIXED: Uses scalar integer values for consistency)
        surprise_dir_val = int(np.sign(raw_surprise))
        momentum_dir_val = int(np.sign(df['momentum'].iloc[-1])) 
        
        df['surprise_dir'] = surprise_dir_val
        df['momentum_dir'] = momentum_dir_val
        
        df['abs_surprise'] = df['surprise'].abs()
        
        # 3. One-Hot Encoding (FIXED: Assigns scalar integer 1 or 0 directly to the DataFrame Series)
        event_type = event_data.get('event_type', 'NONE').upper()
        
        df['is_cpi'] = int(event_type == 'CPI')
        df['is_ppi'] = int(event_type == 'PPI')
        df['is_nfp'] = int(event_type == 'NFP')
        df['is_fomc'] = int(event_type == 'FOMC')

        # 4. Interaction Features 
        df['surprise_x_rsi_high'] = df['surprise'] * (df['rsi'] > 70).astype(int).fillna(0)
        df['surprise_x_rsi_low'] = df['surprise'] * (df['rsi'] < 30).astype(int).fillna(0)
        df['surprise_x_bb_low'] = df['surprise'] * (df['bb_position'] < 0.1).astype(int).fillna(0)
        
        if not df['pre_event_volatility'].any():
             volatility_threshold = 0.0
        else:
             volatility_threshold = df['pre_event_volatility'].quantile(0.2)
             
        df['surprise_x_low_vol'] = df['surprise'] * (df['pre_event_volatility'] < volatility_threshold).astype(int).fillna(0)
        
        # 5. Time to Event 
        df['hours_from_event'] = 0.0
        df['hours_to_event'] = 0.0
        
        return df

    def process_complete_dataset_for_prediction(self, df, event_data, last_surprises):
        """Full pipeline to process the raw market data into the required feature matrix."""
        
        # 1. Calculate Technicals
        df = df.groupby('symbol', group_keys=False).apply(self.calculate_technical_indicators).reset_index(drop=True) 

        # 2. Create Event Features (uses current event data)
        df = self.create_event_features(df, event_data)
        
        # 3. Apply stored Lagged Surprise (Memory Logic)
        for event_type_mem in ['CPI', 'PPI', 'NFP', 'FOMC']:
            # Apply the stored historical lag surprises (from live_economic_event)
            df[f'lag_surprise_{event_type_mem}'] = last_surprises.get(f'{event_type_mem}_SURPRISE', 0.0)
        
        # 4. Asset Encoding
        asset_map = {'stocks': 0, 'forex': 1, 'crypto': 2, 'commodity': 3}
        
        def determine_instrument_type(symbol):
            if '-USD' in symbol: return 'crypto'
            if '=' in symbol: 
                return 'forex' if symbol.endswith('=X') else 'commodity' 
            return 'stocks'

        df['instrument_type'] = df['symbol'].apply(determine_instrument_type)
        df['asset_type_encoded'] = df['instrument_type'].map(asset_map).fillna(0)
        
        return df


class AIPredictor:
    def __init__(self, model_path='models_unified/', feature_cols_file='feature_cols.txt'):
        self.direction_model = None
        self.ranking_model = None
        self.feature_cols = None
        self.last_surprises = {'CPI_SURPRISE': 0.00, 'PPI_SURPRISE': 0.00, 'NFP_SURPRISE': 0.00, 'FOMC_SURPRISE': 0.0} 
        self.fe = AdvancedFeatureEngineer()
        self.is_ready = False
        self.full_symbol_list = PREDICTION_SYMBOLS
        
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.model_full_path = os.path.join(script_dir, model_path)
        
        self._load_feature_cols(feature_cols_file)
        
        if self.feature_cols:
            self._load_models(self.model_full_path)
        
        if self.direction_model and self.ranking_model and self.feature_cols:
            self.is_ready = True
            print("AI Predictor: Models loaded and ready for production use.")
        else:
             print("AI Predictor: Failed to initialize models. Check file paths and logs.")


    def _load_models(self, path):
        try:
            self.direction_model = joblib.load(os.path.join(path, 'direction_model.pkl'))
            self.ranking_model = joblib.load(os.path.join(path, 'ranking_model.pkl'))
        except FileNotFoundError:
            print(f"ERROR: AI Model files not found. Tried path: {path}")
            print("Please ensure 'models_unified' folder exists in your app root and contains the PKL files.")
        except Exception as e:
            print(f"ERROR loading models: {e}. Prediction disabled.")

    def _load_feature_cols(self, file_path):
        try:
            full_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), file_path)
            with open(full_path, 'r') as f:
                self.feature_cols = [col.strip() for col in f.read().split(',')]
        except FileNotFoundError:
            print(f"ERROR: Feature column list not found at {full_path}. Prediction disabled.")
        except Exception as e:
            print(f"ERROR loading feature columns: {e}. Prediction disabled.")

    def _calculate_hybrid_score(self, X):
        if not self.direction_model or not self.ranking_model:
            return None, None
        
        dir_prob = self.direction_model.predict_proba(X)[:, 1] 
        ranking_score = self.ranking_model.predict(X)
        
        directions = (dir_prob > 0.5).astype(int)
        confidence = np.abs(dir_prob - 0.5) * 2 
        final_score = ranking_score * confidence
        
        return directions, final_score

    def predict_signals(self, market_data, live_economic_event, debug=False, features_only=False):
        """
        Generates signals for all defined symbols using market data and live economic context.
        If features_only=True, skips GBM readiness and only runs feature engineering (for RL, etc.).
        """
        if not market_data:
            return []
        if not features_only and not self.is_ready:
            return []
        if features_only and not self.feature_cols:
            return []

        # --- 1. Generate Historical DataFrame Mock (CRITICAL: Needs 100 periods) ---
        mock_history_len = 100 
        history_list = []
        now = datetime.now()
        
        live_price_map = {item['symbol']: item for item in market_data}
        symbols_to_predict = live_price_map.keys()
        
        for symbol in symbols_to_predict:
            live_data = live_price_map[symbol]
            current_price = live_data['price'] 
            
            np.random.seed(sum(ord(c) for c in symbol) % 1000 + now.day)
            
            prices = [current_price]
            for i in range(mock_history_len - 1, 0, -1):
                 prev_price = prices[-1]
                 change = np.random.normal(loc=1.000, scale=0.001) 
                 prices.append(prev_price * change)
                 
            prices.reverse()

            default_volume = np.random.randint(50000, 200000)
            
            for i in range(mock_history_len):
                price = prices[i]
                
                history_list.append({
                    'index': now - timedelta(hours=mock_history_len - i),
                    'symbol': symbol,
                    'close': price,
                    'open': price * np.random.uniform(0.999, 1.001),
                    'high': price * np.random.uniform(1.001, 1.002),
                    'low': price * np.random.uniform(0.998, 0.999),
                    'volume': live_data.get('volume', default_volume), 
                })

        raw_df = pd.DataFrame(history_list)
        
        if raw_df.empty:
            return []

        # --- 2. Feature Engineering & Prediction (INJECT LIVE ECONOMIC DATA HERE) ---
        
        # Use only the lagged surprises for the memory features
        lagged_surprises = {
            'CPI_SURPRISE': live_economic_event.get('CPI_SURPRISE', 0.0),
            'NFP_SURPRISE': live_economic_event.get('NFP_SURPRISE', 0.0),
            'PPI_SURPRISE': live_economic_event.get('PPI_SURPRISE', 0.0),
            'FOMC_SURPRISE': live_economic_event.get('FOMC_SURPRISE', 0.0),
        }
        
        df_processed = self.fe.process_complete_dataset_for_prediction(raw_df, live_economic_event, lagged_surprises)
        
        # Get the latest row for each symbol (CRITICAL: This is the feature vector for prediction)
        X_latest = df_processed.groupby('symbol').last().reset_index()
        
        # Extract features and handle potential missing columns (should be 0)
        X = X_latest[self.feature_cols].copy()
        X = X.fillna(0)

        # --- 2b. DEBUG RETURN ---
        if debug:
            # Include symbol for downstream consumers (e.g. RL API); feature columns unchanged
            return X_latest[["symbol"] + self.feature_cols].copy()

        if features_only:
            return []

        # --- 3. Run Models ---
        directions, hybrid_scores = self._calculate_hybrid_score(X)

        if directions is None or X.empty:
             return []

        # --- 4. Compile Results ---
        X_latest['current_price'] = X_latest['symbol'].apply(lambda s: live_price_map.get(s, {}).get('price', 0))
        X_latest['name'] = X_latest['symbol'].apply(lambda s: live_price_map.get(s, {}).get('name', s))
        X_latest['category'] = X_latest['symbol'].apply(lambda s: live_price_map.get(s, {}).get('category', 'Unknown'))
        
        results = X_latest[['symbol', 'name', 'current_price', 'category']].copy()
        
        results['Signal'] = ['BUY' if d == 1 else 'SELL' for d in directions]
        results['Hybrid_Score'] = hybrid_scores
        
        # --- 5. Final Formatting & Filtering ---
        results['confidence'] = np.clip(55 + np.abs(results['Hybrid_Score']) * 20, 55, 95)

        return results.to_dict('records')