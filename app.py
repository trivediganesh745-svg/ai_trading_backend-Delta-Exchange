import os
import logging
import hashlib
import hmac
import json
import time
import threading
import websocket
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any, Callable
from collections import defaultdict
import sqlite3
from contextlib import contextmanager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('trading_system.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Flask app initialization
app = Flask(__name__)

# Environment variables - FIX: Remove extra quotes and add proper defaults
DELTA_API_KEY = os.getenv('DELTA_API_KEY', 'your_delta_api_key_here')
DELTA_API_SECRET = os.getenv('DELTA_API_SECRET', 'your_delta_api_secret_here')
AI_STUDIO_SECRET = os.getenv('AI_STUDIO_SECRET', 'your_ai_studio_secret_here_for_testing')
ENVIRONMENT = os.getenv('ENVIRONMENT', 'testnet').strip().strip("'\"")  # FIX: Strip quotes

# API Configuration - FIX: Use correct URLs from documentation
if ENVIRONMENT == 'production':
    BASE_URL = 'https://api.india.delta.exchange'
    WS_URL = 'wss://socket.india.delta.exchange'
else:
    BASE_URL = 'https://cdn-ind.testnet.deltaex.org'
    WS_URL = 'wss://socket-ind.testnet.deltaex.org'

logger.info(f"Environment: {ENVIRONMENT}")
logger.info(f"Base URL: {BASE_URL}")
logger.info(f"WebSocket URL: {WS_URL}")

# --- Data Models ---
@dataclass
class Order:
    id: Optional[int] = None
    product_id: int = 0
    product_symbol: str = ""
    size: int = 0
    side: str = ""  # buy/sell
    order_type: str = "market_order"  # limit_order/market_order
    limit_price: Optional[str] = None
    stop_price: Optional[str] = None
    state: str = "pending"
    client_order_id: Optional[str] = None
    created_at: Optional[str] = None
    unfilled_size: int = 0
    average_fill_price: Optional[str] = None

@dataclass
class Position:
    product_id: int
    product_symbol: str
    size: int
    entry_price: str
    mark_price: str
    unrealized_pnl: str
    realized_pnl: str

@dataclass
class Trade:
    id: str
    product_symbol: str
    side: str
    size: int
    price: str
    timestamp: int
    commission: str

@dataclass
class MarketData:
    symbol: str
    price: str
    timestamp: int
    volume: Optional[str] = None
    bid: Optional[str] = None
    ask: Optional[str] = None

# --- Database Manager ---
class DatabaseManager:
    def __init__(self, db_path: str = "trading_system.db"):
        self.db_path = db_path
        self.init_database()
    
    @contextmanager
    def get_connection(self):
        """Provides a database connection that is automatically closed."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def init_database(self):
        """Initializes database schema if tables do not exist."""
        with self.get_connection() as conn:
            # Orders table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY,
                    product_id INTEGER,
                    product_symbol TEXT,
                    size INTEGER,
                    side TEXT,
                    order_type TEXT,
                    limit_price TEXT,
                    stop_price TEXT,
                    state TEXT,
                    client_order_id TEXT,
                    created_at TEXT,
                    unfilled_size INTEGER,
                    average_fill_price TEXT
                )
            ''')
            
            # Trades table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    product_symbol TEXT,
                    side TEXT,
                    size INTEGER,
                    price TEXT,
                    timestamp INTEGER,
                    commission TEXT
                )
            ''')
            
            # Market data table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS market_data (
                    symbol TEXT,
                    price TEXT,
                    timestamp INTEGER,
                    volume TEXT,
                    bid TEXT,
                    ask TEXT,
                    PRIMARY KEY (symbol, timestamp)
                )
            ''')
            
            # Backtest results table
            conn.execute('''
                CREATE TABLE IF NOT EXISTS backtest_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_name TEXT,
                    symbol TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    total_return REAL,
                    sharpe_ratio REAL,
                    max_drawdown REAL,
                    total_trades INTEGER,
                    win_rate REAL,
                    created_at TEXT
                )
            ''')
            
            conn.commit()

# --- Delta Exchange API Client ---
class DeltaExchangeAPI:
    def __init__(self, api_key: str, api_secret: str, base_url: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.session = requests.Session()
    
    def generate_signature(self, message: str) -> str:
        """Generate HMAC signature for API authentication"""
        message_bytes = bytes(message, 'utf-8')
        secret_bytes = bytes(self.api_secret, 'utf-8')
        hash_obj = hmac.new(secret_bytes, message_bytes, hashlib.sha256)
        return hash_obj.hexdigest()
    
    def make_request(self, method: str, path: str, params: Optional[Dict] = None, data: Optional[Dict] = None) -> Dict:
        """Make authenticated API request"""
        timestamp = str(int(time.time()))
        query_string = ""
        
        if params:
            sorted_params = sorted(params.items())
            query_string = "?" + "&".join([f"{k}={v}" for k, v in sorted_params])
        
        payload = json.dumps(data) if data else ""
        signature_data = method + timestamp + path + query_string + payload
        signature = self.generate_signature(signature_data)
        
        headers = {
            'api-key': self.api_key,
            'timestamp': timestamp,
            'signature': signature,
            'User-Agent': 'python-trading-bot',
            'Content-Type': 'application/json'
        }
        
        url = f"{self.base_url}{path}"
        
        try:
            response = self.session.request(
                method, url, 
                params=params, 
                data=payload if data else None, 
                headers=headers, 
                timeout=(5, 30)
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"API request to {url} failed: {e}")
            raise
    
    def place_order(self, order: Order) -> Dict:
        """Place a new order"""
        data = {
            "product_symbol": order.product_symbol,
            "size": order.size,
            "side": order.side,
            "order_type": order.order_type
        }
        
        if order.limit_price is not None:
            data["limit_price"] = order.limit_price
        if order.stop_price is not None:
            data["stop_price"] = order.stop_price
        if order.client_order_id is not None:
            data["client_order_id"] = order.client_order_id
        
        return self.make_request("POST", "/v2/orders", data=data)
    
    def cancel_order(self, order_id: int, product_id: int) -> Dict:
        """Cancel an existing order"""
        data = {
            "id": order_id,
            "product_id": product_id
        }
        return self.make_request("DELETE", "/v2/orders", data=data)
    
    def get_orders(self, product_ids: Optional[str] = None, states: str = "open,pending") -> Dict:
        """Get orders"""
        params = {"states": states}
        if product_ids:
            params["product_ids"] = product_ids
        return self.make_request("GET", "/v2/orders", params=params)
    
    def get_positions(self) -> Dict:
        """Get current positions"""
        return self.make_request("GET", "/v2/positions")
    
    def get_products(self) -> Dict:
        """Get available products"""
        return self.make_request("GET", "/v2/products")

# --- WebSocket Manager - FIX: Add better error handling and connection management ---
class WebSocketManager:
    def __init__(self, api_key: str, api_secret: str, ws_url: str, trading_system: 'TradingSystem'):
        self.api_key = api_key
        self.api_secret = api_secret
        self.ws_url = ws_url
        self.trading_system = trading_system
        self.ws: Optional[websocket.WebSocketApp] = None
        self.is_connected = False
        self.is_authenticated = False
        self.subscribed_channels: set[str] = set()
        self._reconnect_lock = threading.Lock()
        self._connection_attempts = 0
        self._max_reconnect_attempts = 5
        
    def generate_signature(self, message: str) -> str:
        """Generate HMAC signature for WebSocket authentication"""
        message_bytes = bytes(message, 'utf-8')
        secret_bytes = bytes(self.api_secret, 'utf-8')
        hash_obj = hmac.new(secret_bytes, message_bytes, hashlib.sha256)
        return hash_obj.hexdigest()
    
    def on_open(self, ws: websocket.WebSocketApp):
        """WebSocket connection opened"""
        logger.info("WebSocket connection opened")
        self.is_connected = True
        self._connection_attempts = 0  # Reset on successful connection
        self.send_authentication()
    
    def on_close(self, ws: websocket.WebSocketApp, close_status_code: int, close_msg: str):
        """WebSocket connection closed"""
        logger.warning(f"WebSocket closed: {close_status_code} - {close_msg}")
        self.is_connected = False
        self.is_authenticated = False
        
        # FIX: Better reconnection logic with exponential backoff
        with self._reconnect_lock:
            if not self.trading_system.is_stopping and self._connection_attempts < self._max_reconnect_attempts:
                self._connection_attempts += 1
                backoff_time = min(5 * (2 ** (self._connection_attempts - 1)), 60)  # Exponential backoff, max 60s
                logger.info(f"Attempting to reconnect WebSocket in {backoff_time} seconds... (attempt {self._connection_attempts}/{self._max_reconnect_attempts})")
                threading.Timer(backoff_time, self.connect).start()
            elif self._connection_attempts >= self._max_reconnect_attempts:
                logger.error("Max reconnection attempts reached. WebSocket will not reconnect automatically.")
    
    def on_error(self, ws: websocket.WebSocketApp, error: Exception):
        """WebSocket error occurred"""
        logger.error(f"WebSocket error: {error}")
    
    def on_message(self, ws: websocket.WebSocketApp, message: str):
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(message)
            self.handle_message(data)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse WebSocket message: {e}")
        except Exception as e:
            logger.error(f"Error processing WebSocket message: {e}")
    
    def handle_message(self, data: Dict):
        """Process WebSocket messages"""
        message_type = data.get('type')
        
        if message_type == 'success' and data.get('message') == 'Authenticated':
            logger.info("WebSocket authenticated successfully")
            self.is_authenticated = True
            self.subscribe_to_channels()
        
        elif message_type == 'orders':
            self.handle_order_update(data)
        
        elif message_type == 'positions':
            self.handle_position_update(data)
        
        elif message_type == 'v2/ticker':
            self.handle_ticker_update(data)
        
        elif message_type == 'all_trades':
            self.handle_trade_update(data)
        
        else:
            logger.debug(f"Unhandled message type: {message_type}")
    
    def handle_order_update(self, data: Dict):
        """Handle order updates"""
        self.trading_system.process_order_update(data)
    
    def handle_position_update(self, data: Dict):
        """Handle position updates"""
        self.trading_system.process_position_update(data)
    
    def handle_ticker_update(self, data: Dict):
        """Handle ticker updates"""
        market_data = MarketData(
            symbol=data.get('symbol', ''),
            price=str(data.get('close', '0')),
            timestamp=data.get('timestamp', int(time.time() * 1000000)),
            volume=str(data.get('volume')) if data.get('volume') is not None else None,
            bid=str(data.get('best_bid')) if data.get('best_bid') is not None else None,
            ask=str(data.get('best_ask')) if data.get('best_ask') is not None else None
        )
        self.trading_system.process_market_data(market_data)
    
    def handle_trade_update(self, data: Dict):
        """Handle trade updates"""
        pass 
    
    def send_authentication(self):
        """Send authentication message"""
        method = 'GET'
        timestamp = str(int(time.time()))
        path = '/live'
        signature_data = method + timestamp + path
        signature = self.generate_signature(signature_data)
        
        auth_message = {
            "type": "auth",
            "payload": {
                "api-key": self.api_key,
                "signature": signature,
                "timestamp": timestamp
            }
        }
        
        if self.ws:
            self.ws.send(json.dumps(auth_message))
            logger.info("Authentication message sent to WebSocket.")
    
    def subscribe_to_channels(self):
        """Subscribe to required channels after authentication."""
        if not self.is_authenticated:
            logger.warning("Not authenticated, skipping channel subscriptions.")
            return

        # FIX: Use valid symbols for your environment
        subscriptions = [
            {"name": "orders", "symbols": ["all"]},
            {"name": "positions", "symbols": ["all"]},
            {"name": "v2/ticker", "symbols": ["BTCUSD", "ETHUSD"]},
            {"name": "all_trades", "symbols": ["BTCUSD", "ETHUSD"]}
        ]

        for sub in subscriptions:
            self.subscribe(sub["name"], sub["symbols"])
    
    def subscribe(self, channel: str, symbols: List[str]):
        """Subscribe to a WebSocket channel"""
        payload = {
            "type": "subscribe",
            "payload": {
                "channels": [
                    {
                        "name": channel,
                        "symbols": symbols
                    }
                ]
            }
        }
        
        if self.ws and self.is_authenticated:
            self.ws.send(json.dumps(payload))
            self.subscribed_channels.add(channel)
            logger.info(f"Subscribed to {channel} for symbols: {symbols}")
        else:
            logger.warning(f"Cannot subscribe to {channel}. WS not connected or not authenticated.")
    
    def connect(self):
        """Connect to WebSocket in a separate thread."""
        if self.ws and self.ws.sock and self.ws.sock.connected:
            logger.info("WebSocket is already connected.")
            return

        try:
            # FIX: Add connection validation
            if not self.api_key or self.api_key == 'your_delta_api_key_here':
                logger.error("Cannot connect WebSocket: API key not configured")
                return
                
            if not self.api_secret or self.api_secret == 'your_delta_api_secret_here':
                logger.error("Cannot connect WebSocket: API secret not configured")
                return

            logger.info(f"Connecting to WebSocket: {self.ws_url}")
            self.ws = websocket.WebSocketApp(
                self.ws_url,
                on_open=self.on_open,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close
            )
            
            ws_thread = threading.Thread(target=self.ws.run_forever, daemon=True)
            ws_thread.start()
            logger.info("WebSocket connection thread started.")
            
        except Exception as e:
            logger.error(f"Failed to connect to WebSocket: {e}")

# --- Trading Strategy Base Class ---
class TradingStrategy:
    def __init__(self, name: str):
        self.name = name
        self.trading_system: Optional['TradingSystem'] = None
        self.positions: Dict[str, Any] = {}
        self.orders: Dict[str, Any] = {}
        self.market_data: Dict[str, MarketData] = {}
        
    def set_trading_system(self, system: 'TradingSystem'):
        self.trading_system = system

    def on_market_data(self, data: MarketData):
        """Called when new market data is received."""
        self.market_data[data.symbol] = data
        self.generate_signals(data)
    
    def generate_signals(self, data: MarketData):
        """Override this method to implement trading logic."""
        pass
    
    def get_current_position(self, symbol: str) -> int:
        """Helper to get current position size for a symbol."""
        current_pos_size = 0
        for pos in self.trading_system.positions.values():
            if pos.get('product_symbol') == symbol:
                current_pos_size = pos.get('size', 0)
                break
        return current_pos_size

    def get_available_balance(self) -> float:
        """Placeholder to get available balance from the system."""
        logger.warning("get_available_balance is a placeholder and returns dummy value (1000). Implement real balance fetch.")
        return 1000.0

# --- Simple Moving Average Strategy ---
class SMAStrategy(TradingStrategy):
    def __init__(self, short_window: int = 10, long_window: int = 30, trade_size_percentage: float = 0.05):
        super().__init__("SMA_Strategy")
        self.short_window = short_window
        self.long_window = long_window
        self.trade_size_percentage = trade_size_percentage
        
        self.price_history: Dict[str, pd.Series] = defaultdict(lambda: pd.Series(dtype='float64'))
        self.active_orders: Dict[str, Order] = {}
    
    def generate_signals(self, data: MarketData):
        """Generate trading signals based on SMA crossover using Pandas Series."""
        symbol = data.symbol
        try:
            price = float(data.price)
        except ValueError:
            logger.warning(f"Invalid price data received for {symbol}: {data.price}")
            return
        
        new_price_series = pd.Series([price], index=[pd.to_datetime(data.timestamp, unit='us')])
        self.price_history[symbol] = pd.concat([self.price_history[symbol], new_price_series])
        
        max_history_needed = self.long_window + 1
        if len(self.price_history[symbol]) > max_history_needed:
            self.price_history[symbol] = self.price_history[symbol].iloc[-max_history_needed:]
        
        if len(self.price_history[symbol]) >= self.long_window + 1:
            current_short_ma = self.price_history[symbol].iloc[-self.short_window:].mean()
            current_long_ma = self.price_history[symbol].iloc[-self.long_window:].mean()
            
            prev_short_ma = self.price_history[symbol].iloc[-(self.short_window + 1):-1].mean()
            prev_long_ma = self.price_history[symbol].iloc[-(self.long_window + 1):-1].mean()
            
            current_position_size = self.get_current_position(symbol)
            
            # Bullish crossover: Short MA crosses above Long MA
            if current_short_ma > current_long_ma and prev_short_ma <= prev_long_ma:
                logger.info(f"[{self.name}] BUY signal for {symbol}: Short MA ({current_short_ma:.2f}) crossed above Long MA ({current_long_ma:.2f})")
                
                if current_position_size <= 0:
                    balance = self.get_available_balance()
                    trade_amount = balance * self.trade_size_percentage
                    size_to_buy = int(trade_amount / price)
                    
                    if size_to_buy > 0 and self.trading_system:
                        if current_position_size < 0:
                            logger.info(f"[{self.name}] Closing existing SHORT position ({current_position_size}) for {symbol} before BUY.")
                            close_order = Order(product_symbol=symbol, size=abs(current_position_size), side="buy", order_type="market_order")
                            self.trading_system.place_order(close_order)
                            time.sleep(1)
                            current_position_size = self.get_current_position(symbol)

                        if current_position_size == 0:
                            order = Order(product_symbol=symbol, size=size_to_buy, side="buy", order_type="market_order")
                            result = self.trading_system.place_order(order)
                            if result and 'id' in result:
                                logger.info(f"[{self.name}] Placed BUY market order for {size_to_buy} {symbol}. Order ID: {result['id']}")
                            else:
                                logger.error(f"[{self.name}] Failed to place BUY order for {symbol}: {result.get('error', 'Unknown error')}")
                        else:
                             logger.info(f"[{self.name}] Already have position or still short for {symbol} after close attempt. Not placing new BUY order.")
                    else:
                        logger.warning(f"[{self.name}] Not enough capital or invalid size to place BUY order for {symbol}.")
                else:
                    logger.debug(f"[{self.name}] Already long {symbol} ({current_position_size}). No new BUY order.")

            # Bearish crossover: Short MA crosses below Long MA
            elif current_short_ma < current_long_ma and prev_short_ma >= prev_long_ma:
                logger.info(f"[{self.name}] SELL signal for {symbol}: Short MA ({current_short_ma:.2f}) crossed below Long MA ({current_long_ma:.2f})")
                
                if current_position_size >= 0:
                    balance = self.get_available_balance()
                    trade_amount = balance * self.trade_size_percentage
                    size_to_sell = int(trade_amount / price)
                    
                    if size_to_sell > 0 and self.trading_system:
                        if current_position_size > 0:
                            logger.info(f"[{self.name}] Closing existing LONG position ({current_position_size}) for {symbol} before SELL.")
                            close_order = Order(product_symbol=symbol, size=current_position_size, side="sell", order_type="market_order")
                            self.trading_system.place_order(close_order)
                            time.sleep(1)
                            current_position_size = self.get_current_position(symbol)

                        if current_position_size == 0:
                            order = Order(product_symbol=symbol, size=size_to_sell, side="sell", order_type="market_order")
                            result = self.trading_system.place_order(order)
                            if result and 'id' in result:
                                logger.info(f"[{self.name}] Placed SELL market order for {size_to_sell} {symbol} (going short). Order ID: {result['id']}")
                            else:
                                logger.error(f"[{self.name}] Failed to place SELL order for {symbol}: {result.get('error', 'Unknown error')}")
                        else:
                            logger.info(f"[{self.name}] Already have position or still long for {symbol} after close attempt. Not placing new SELL order.")
                    else:
                        logger.warning(f"[{self.name}] Not enough capital or invalid size to place SELL order for {symbol}.")
                else:
                    logger.debug(f"[{self.name}] Already short {symbol} ({current_position_size}). No new SELL order.")
            else:
                logger.debug(f"[{self.name}] No new crossover signal for {symbol}.")
        else:
            logger.debug(f"[{self.name}] Insufficient data for MA calculation for {symbol}. Current length: {len(self.price_history[symbol])}")

# --- Backtesting Engine ---
class BacktestEngine:
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
    
    def run_backtest(self, strategy: TradingStrategy, symbol: str, 
                    start_date: str, end_date: str, initial_capital: float = 10000) -> Dict:
        """Run backtest for a strategy"""
        logger.info(f"Running backtest for {strategy.name} on {symbol} from {start_date} to {end_date}")
        
        historical_data_raw = self.get_historical_data(symbol, start_date, end_date)
        
        if not historical_data_raw:
            logger.warning(f"No historical data available for {symbol} between {start_date} and {end_date}.")
            return {"error": "No historical data available"}
        
        df = pd.DataFrame(historical_data_raw)
        df['price'] = pd.to_numeric(df['price'])
        df = df.sort_values(by='timestamp').reset_index(drop=True)

        capital = initial_capital
        position_size = 0.0
        trades = []
        equity_curve = []
        
        if isinstance(strategy, SMAStrategy):
            df['short_ma'] = df['price'].rolling(window=strategy.short_window, min_periods=1).mean()
            df['long_ma'] = df['price'].rolling(window=strategy.long_window, min_periods=1).mean()
            
            df['buy_signal'] = (df['short_ma'] > df['long_ma']) & (df['short_ma'].shift(1) <= df['long_ma'].shift(1))
            df['sell_signal'] = (df['short_ma'] < df['long_ma']) & (df['short_ma'].shift(1) >= df['long_ma'].shift(1))
            
            df[['buy_signal', 'sell_signal']] = df[['buy_signal', 'sell_signal']].fillna(False)

            for i, row in df.iterrows():
                current_price = row['price']
                timestamp = row['timestamp']
                
                if row['buy_signal'] and position_size <= 0:
                    if position_size < 0:
                        pnl = abs(position_size) * (row['price'] - trades[-1]['entry_price'])
                        capital += pnl
                        trades.append({
                            'type': 'close_short',
                            'price': current_price,
                            'timestamp': timestamp,
                            'pnl': pnl,
                            'capital_after_trade': capital
                        })
                        position_size = 0

                    if capital > 0:
                        units_to_buy = capital / current_price
                        position_size += units_to_buy
                        trades.append({
                            'type': 'buy',
                            'entry_price': current_price,
                            'timestamp': timestamp,
                            'position_size': position_size,
                            'capital_after_trade': capital
                        })
                        logger.debug(f"Backtest BUY at {current_price:.2f} for {symbol}. New position: {position_size:.2f}")

                elif row['sell_signal'] and position_size >= 0:
                    if position_size > 0:
                        pnl = position_size * (row['price'] - trades[-1]['entry_price'])
                        capital += pnl
                        trades.append({
                            'type': 'close_long',
                            'price': current_price,
                            'timestamp': timestamp,
                            'pnl': pnl,
                            'capital_after_trade': capital
                        })
                        position_size = 0

                    if capital > 0:
                        units_to_sell = capital / current_price
                        position_size -= units_to_sell
                        trades.append({
                            'type': 'sell',
                            'entry_price': current_price,
                            'timestamp': timestamp,
                            'position_size': position_size,
                            'capital_after_trade': capital
                        })
                        logger.debug(f"Backtest SELL at {current_price:.2f} for {symbol}. New position: {position_size:.2f}")

                current_equity = capital
                if position_size != 0:
                    current_equity += position_size * current_price
                
                equity_curve.append({
                    'timestamp': timestamp,
                    'equity': current_equity
                })
        else:
            logger.error(f"Strategy {strategy.name} does not implement specific backtesting logic.")

        results = self.calculate_performance_metrics(
            equity_curve, trades, initial_capital, strategy.name, symbol, start_date, end_date
        )
        
        self.save_backtest_results(results)
        
        return results
 def get_historical_data(self, symbol: str, start_date_str: str, end_date_str: str) -> List[Dict]:
        """Get historical market data from database within a timestamp range."""
        try:
            start_ts_us = int(datetime.strptime(start_date_str, '%Y-%m-%d').timestamp() * 1_000_000)
            end_ts_us = int((datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1, microseconds=-1)).timestamp() * 1_000_000)

            with self.db_manager.get_connection() as conn:
                cursor = conn.execute('''
                    SELECT symbol, price, timestamp, volume, bid, ask FROM market_data 
                    WHERE symbol = ? AND timestamp BETWEEN ? AND ?
                    ORDER BY timestamp ASC
                ''', (symbol, start_ts_us, end_ts_us))
                
                return [dict(row) for row in cursor.fetchall()]
        except ValueError as e:
            logger.error(f"Error parsing date strings for historical data: {e}")
            return []
        except Exception as e:
            logger.error(f"Error retrieving historical data from database: {e}")
            return []
    
    def calculate_performance_metrics(self, equity_curve: List[Dict], trades: List[Dict], 
                                    initial_capital: float, strategy_name: str, 
                                    symbol: str, start_date: str, end_date: str) -> Dict:
        """Calculate backtest performance metrics."""
        if not equity_curve:
            logger.warning(f"No equity curve data for {strategy_name} on {symbol}.")
            return {}
        
        final_equity = equity_curve[-1]['equity']
        total_return = (final_equity - initial_capital) / initial_capital
        
        equity_series = pd.Series([d['equity'] for d in equity_curve])
        
        returns = equity_series.pct_change().dropna()
        
        sharpe_ratio = 0
        if not returns.empty and returns.std() > 0:
            num_days = (datetime.strptime(end_date, '%Y-%m-%d') - datetime.strptime(start_date, '%Y-%m-%d')).days + 1
            if num_days > 0 and len(equity_curve) > 0:
                annualization_factor = np.sqrt(len(equity_curve) / num_days * 252)
                sharpe_ratio = returns.mean() / returns.std() * annualization_factor
            else:
                sharpe_ratio = 0
            if np.isinf(sharpe_ratio) or np.isnan(sharpe_ratio):
                sharpe_ratio = 0

        cum_returns = (equity_series / initial_capital)
        peak = cum_returns.expanding(min_periods=1).max()
        drawdown = (peak - cum_returns) / peak
        max_drawdown = drawdown.max()
        if np.isnan(max_drawdown):
            max_drawdown = 0.0

        pnl_trades = [t for t in trades if 'pnl' in t]
        winning_trades = sum(1 for trade in pnl_trades if trade.get('pnl', 0) > 0)
        total_trades = len(pnl_trades)
        win_rate = winning_trades / total_trades if total_trades > 0 else 0
        
        return {
            'strategy_name': strategy_name,
            'symbol': symbol,
            'start_date': start_date,
            'end_date': end_date,
            'initial_capital': initial_capital,
            'final_equity': final_equity,
            'total_return': total_return,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown,
            'total_trades': total_trades,
            'win_rate': win_rate,
            'trades': trades,
            'equity_curve': equity_curve
        }
    
    def save_backtest_results(self, results: Dict):
        """Save backtest results to database."""
        if not results or "error" in results:
            logger.warning("Attempted to save empty or erroneous backtest results.")
            return

        with self.db_manager.get_connection() as conn:
            conn.execute('''
                INSERT INTO backtest_results 
                (strategy_name, symbol, start_date, end_date, total_return, 
                 sharpe_ratio, max_drawdown, total_trades, win_rate, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                results.get('strategy_name', ''),
                results.get('symbol', ''),
                results.get('start_date', ''),
                results.get('end_date', ''),
                results.get('total_return', 0.0),
                results.get('sharpe_ratio', 0.0),
                results.get('max_drawdown', 0.0),
                results.get('total_trades', 0),
                results.get('win_rate', 0.0),
                datetime.now().isoformat()
            ))
            conn.commit()
            logger.info(f"Backtest results for {results.get('strategy_name')} on {results.get('symbol')} saved.")

# --- Main Trading System - FIX: Auto-start system ---
class TradingSystem:
    def __init__(self):
        self.db_manager = DatabaseManager()
        self.api_client = DeltaExchangeAPI(DELTA_API_KEY, DELTA_API_SECRET, BASE_URL)
        self.ws_manager = WebSocketManager(DELTA_API_KEY, DELTA_API_SECRET, WS_URL, self)
        self.backtest_engine = BacktestEngine(self.db_manager)
        
        self.orders: Dict[int, Dict] = {}
        self.positions: Dict[int, Dict] = {}
        self.market_data: Dict[str, MarketData] = {}
        self.strategies: Dict[str, TradingStrategy] = {}
        
        self.is_running = False
        self.is_stopping = False
        
        # Initialize default strategy
        self.strategies['sma'] = SMAStrategy()
        self.strategies['sma'].set_trading_system(self)
        logger.info(f"Default SMA Strategy initialized with short_window={self.strategies['sma'].short_window}, long_window={self.strategies['sma'].long_window}")
    
    def start(self):
        """Start the trading system."""
        if self.is_running:
            logger.info("Trading system is already running.")
            return

        logger.info("Starting trading system...")
        self.is_running = True
        self.is_stopping = False
        
        # FIX: Only connect WebSocket if API credentials are configured
        if (DELTA_API_KEY and DELTA_API_KEY != 'your_delta_api_key_here' and 
            DELTA_API_SECRET and DELTA_API_SECRET != 'your_delta_api_secret_here'):
            self.ws_manager.connect()
            logger.info("WebSocket connection initiated.")
        else:
            logger.warning("API credentials not configured. WebSocket connection skipped.")
        
        logger.info("Trading system startup completed.")
    
    def stop(self):
        """Stop the trading system."""
        if not self.is_running:
            logger.info("Trading system is already stopped.")
            return

        logger.info("Stopping trading system...")
        self.is_stopping = True
        self.is_running = False
        
        if self.ws_manager.ws:
            logger.info("Closing WebSocket connection...")
            self.ws_manager.ws.close()
            time.sleep(1) 
        
        logger.info("Trading system stopped successfully.")
    
    def process_order_update(self, data: Dict):
        """Process order updates from WebSocket."""
        order_id = data.get('id') or data.get('order_id')
        if order_id:
            self.orders[order_id] = data
            logger.debug(f"Order {order_id} updated to state: {data.get('state')}")
            
            with self.db_manager.get_connection() as conn:
                conn.execute('''
                    INSERT OR REPLACE INTO orders 
                    (id, product_id, product_symbol, size, side, order_type, 
                     limit_price, stop_price, state, client_order_id, 
                     created_at, unfilled_size, average_fill_price)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    order_id,
                    data.get('product_id'),
                    data.get('symbol'),
                    data.get('size'),
                    data.get('side'),
                    data.get('order_type', 'market_order'),
                    str(data.get('limit_price')) if data.get('limit_price') is not None else None,
                    str(data.get('stop_price')) if data.get('stop_price') is not None else None,
                    data.get('state'),
                    data.get('client_order_id'),
                    data.get('created_at'),
                    data.get('unfilled_size'),
                    str(data.get('average_fill_price')) if data.get('average_fill_price') is not None else None
                ))
                conn.commit()
    
    def process_position_update(self, data: Dict):
        """Process position updates from WebSocket."""
        product_id = data.get('product_id')
        if product_id:
            self.positions[product_id] = data
            logger.debug(f"Position for product {product_id} updated. Size: {data.get('size')}")
    
    def process_market_data(self, data: MarketData):
        """Process market data updates."""
        self.market_data[data.symbol] = data
        
        with self.db_manager.get_connection() as conn:
            conn.execute('''
                INSERT OR REPLACE INTO market_data 
                (symbol, price, timestamp, volume, bid, ask)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                data.symbol,
                data.price,
                data.timestamp,
                data.volume,
                data.bid,
                data.ask
            ))
            conn.commit()
        
        for strategy in self.strategies.values():
            strategy.on_market_data(data)
    
    def place_order(self, order: Order) -> Dict:
        """Place a new order via API."""
        try:
            result = self.api_client.place_order(order)
            logger.info(f"API: Order placed successfully: {result.get('id')} for {order.product_symbol}")
            return result
        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return {"error": str(e)}
    
    def cancel_order(self, order_id: int, product_id: int) -> Dict:
        """Cancel an order via API."""
        try:
            result = self.api_client.cancel_order(order_id, product_id)
            logger.info(f"API: Order {order_id} cancelled successfully.")
            return result
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return {"error": str(e)}
    
    def get_system_status(self) -> Dict:
        """Get system status."""
        return {
            "is_running": self.is_running,
            "websocket_connected": self.ws_manager.is_connected,
            "websocket_authenticated": self.ws_manager.is_authenticated,
            "subscribed_channels": list(self.ws_manager.subscribed_channels),
            "active_orders_in_memory": len(self.orders),
            "active_positions_in_memory": len(self.positions),
            "strategies_loaded": list(self.strategies.keys()),
            "environment": ENVIRONMENT,
            "current_market_data_symbols": list(self.market_data.keys())
        }

# --- Initialize trading system ---
trading_system = TradingSystem()

# --- Authentication decorator ---
def ai_studio_auth_required(f: Callable) -> Callable:
    """Decorator to require X-AI-Studio-Secret header for API access."""
    @wraps(f)
    def decorated_function(*args: Any, **kwargs: Any) -> Any:
        if not AI_STUDIO_SECRET or AI_STUDIO_SECRET == 'your_ai_studio_secret_here_for_testing':
            logger.error("AI_STUDIO_SECRET environment variable not properly set. API endpoints are unprotected or using default.")
            pass

        provided_secret = request.headers.get('X-AI-Studio-Secret')
        if provided_secret == AI_STUDIO_SECRET:
            return f(*args, **kwargs)
        else:
            logger.warning(f"Unauthorized access attempt to {request.path} from {request.remote_addr}. Provided secret: {provided_secret}")
            return jsonify({"error": "Unauthorized"}), 401
    return decorated_function

# --- API Routes ---
@app.route('/api/status', methods=['GET'])
@ai_studio_auth_required
def api_status():
    """Get system status."""
    try:
        return jsonify(trading_system.get_system_status())
    except Exception as e:
        logger.exception("Error getting system status:")
        return jsonify({"error": f"Error getting status: {str(e)}"}), 500

@app.route('/api/start', methods=['POST'])
@ai_studio_auth_required
def start_system():
    """Start the trading system."""
    try:
        trading_system.start()
        return jsonify({"message": "Trading system startup initiated."})
    except Exception as e:
        logger.exception("Error starting system:")
        return jsonify({"error": f"Error starting system: {str(e)}"}), 500

@app.route('/api/stop', methods=['POST'])
@ai_studio_auth_required
def stop_system():
    """Stop the trading system."""
    try:
        trading_system.stop()
        return jsonify({"message": "Trading system stopped successfully."})
    except Exception as e:
        logger.exception("Error stopping system:")
        return jsonify({"error": f"Error stopping system: {str(e)}"}), 500

@app.route('/api/orders', methods=['GET'])
@ai_studio_auth_required
def get_orders_api():
    """Get orders from Delta Exchange API."""
    try:
        product_ids = request.args.get('product_ids')
        states = request.args.get('states', 'open,pending')
        
        result = trading_system.api_client.get_orders(product_ids, states)
        return jsonify(result)
    except Exception as e:
        logger.exception("Error getting orders from API:")
        return jsonify({"error": f"Error getting orders: {str(e)}"}), 500

@app.route('/api/orders', methods=['POST'])
@ai_studio_auth_required
def place_order_api():
    """Place a new order."""
    try:
        data = request.get_json()
        
        required_fields = ['product_symbol', 'size', 'side']
        if not all(field in data for field in required_fields):
            return jsonify({"error": f"Missing required fields. Needs: {', '.join(required_fields)}"}), 400

        order = Order(
            product_symbol=data.get('product_symbol'),
            size=data.get('size'),
            side=data.get('side'),
            order_type=data.get('order_type', 'market_order'),
            limit_price=str(data.get('limit_price')) if data.get('limit_price') is not None else None,
            stop_price=str(data.get('stop_price')) if data.get('stop_price') is not None else None,
            client_order_id=data.get('client_order_id')
        )
        
        result = trading_system.place_order(order)
        return jsonify(result)
    except Exception as e:
        logger.exception("Error placing order:")
        return jsonify({"error": f"Error placing order: {str(e)}"}), 500

@app.route('/api/orders/<int:order_id>', methods=['DELETE'])
@ai_studio_auth_required
def cancel_order_api(order_id: int):
    """Cancel an order."""
    try:
        data = request.get_json()
        product_id = data.get('product_id')
        
        if not product_id:
            return jsonify({"error": "product_id is required in the request body"}), 400
        
        result = trading_system.cancel_order(order_id, product_id)
        return jsonify(result)
    except Exception as e:
        logger.exception(f"Error cancelling order {order_id}:")
        return jsonify({"error": f"Error cancelling order: {str(e)}"}), 500

@app.route('/api/positions', methods=['GET'])
@ai_studio_auth_required
def get_positions():
    """Get current positions from Delta Exchange API."""
    try:
        result = trading_system.api_client.get_positions()
        return jsonify(result)
    except Exception as e:
        logger.exception("Error getting positions from API:")
        return jsonify({"error": f"Error getting positions: {str(e)}"}), 500

@app.route('/api/products', methods=['GET'])
@ai_studio_auth_required
def get_products():
    """Get available products from Delta Exchange API."""
    try:
        result = trading_system.api_client.get_products()
        return jsonify(result)
    except Exception as e:
        logger.exception("Error getting products from API:")
        return jsonify({"error": f"Error getting products: {str(e)}"}), 500

@app.route('/api/market-data/<symbol>', methods=['GET'])
@ai_studio_auth_required
def get_market_data(symbol: str):
    """Get latest market data for a symbol from in-memory cache."""
    try:
        if symbol in trading_system.market_data:
            data = trading_system.market_data[symbol]
            return jsonify(asdict(data))
        else:
            return jsonify({"error": f"No market data available for symbol '{symbol}'"}), 404
    except Exception as e:
        logger.exception(f"Error getting market data for {symbol}:")
        return jsonify({"error": f"Error getting market data: {str(e)}"}), 500

@app.route('/api/backtest', methods=['POST'])
@ai_studio_auth_required
def run_backtest():
    """Run a backtest."""
    try:
        data = request.get_json()
        
        strategy_name = data.get('strategy', 'sma')
        symbol = data.get('symbol', 'BTCUSD')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        initial_capital = data.get('initial_capital', 10000.0)
        
        if not start_date or not end_date:
            return jsonify({"error": "start_date and end_date are required in YYYY-MM-DD format"}), 400
        
        if strategy_name not in trading_system.strategies:
            return jsonify({"error": f"Strategy '{strategy_name}' not found"}), 404
        
        strategy = trading_system.strategies[strategy_name]
        result = trading_system.backtest_engine.run_backtest(
            strategy, symbol, start_date, end_date, initial_capital
        )
        
        return jsonify(result)
    except Exception as e:
        logger.exception("Error running backtest:")
        return jsonify({"error": f"Error running backtest: {str(e)}"}), 500

@app.route('/api/backtest/results', methods=['GET'])
@ai_studio_auth_required
def get_backtest_results():
    """Get historical backtest results."""
    try:
        limit = request.args.get('limit', 50, type=int)
        
        with trading_system.db_manager.get_connection() as conn:
            cursor = conn.execute(f'''
                SELECT 
                    id, strategy_name, symbol, start_date, end_date, 
                    total_return, sharpe_ratio, max_drawdown, total_trades, 
                    win_rate, created_at 
                FROM backtest_results 
                ORDER BY created_at DESC 
                LIMIT ?
            ''', (limit,))
            results = [dict(row) for row in cursor.fetchall()]
        
        return jsonify({"results": results})
    except Exception as e:
        logger.exception("Error getting backtest results:")
        return jsonify({"error": f"Error getting backtest results: {str(e)}"}), 500

@app.route('/api/strategies', methods=['GET'])
@ai_studio_auth_required
def get_strategies():
    """Get available strategies and their basic info."""
    try:
        strategies_info = {}
        for name, strategy in trading_system.strategies.items():
            strategies_info[name] = {
                "name": strategy.name,
                "type": type(strategy).__name__,
                "config": {
                    "short_window": strategy.short_window if isinstance(strategy, SMAStrategy) else None,
                    "long_window": strategy.long_window if isinstance(strategy, SMAStrategy) else None,
                    "trade_size_percentage": strategy.trade_size_percentage if isinstance(strategy, SMAStrategy) else None,
                },
                "active_market_data_symbols": list(strategy.market_data.keys())
            }
        
        return jsonify({"strategies": strategies_info})
    except Exception as e:
        logger.exception("Error getting strategies:")
        return jsonify({"error": f"Error getting strategies: {str(e)}"}), 500

@app.route('/api/strategies', methods=['POST'])
@ai_studio_auth_required
def create_strategy():
    """Create a new strategy."""
    try:
        data = request.get_json()
        strategy_type = data.get('type', 'sma').lower()
        strategy_name = data.get('name')
        
        if not strategy_name:
            return jsonify({"error": "Strategy name is required"}), 400
        
        if strategy_name in trading_system.strategies:
            return jsonify({"error": f"Strategy '{strategy_name}' already exists"}), 400
        
        strategy = None
        if strategy_type == 'sma':
            short_window = data.get('short_window', 10)
            long_window = data.get('long_window', 30)
            trade_size_percentage = data.get('trade_size_percentage', 0.05)

            if not isinstance(short_window, int) or not isinstance(long_window, int) or short_window <= 0 or long_window <= 0 or short_window >= long_window:
                return jsonify({"error": "short_window and long_window must be positive integers, and short_window < long_window"}), 400
            if not isinstance(trade_size_percentage, (float, int)) or not (0 < trade_size_percentage <= 1.0):
                 return jsonify({"error": "trade_size_percentage must be a float between 0 and 1 (exclusive of 0)"}), 400
            
            strategy = SMAStrategy(short_window, long_window, trade_size_percentage)
            strategy.name = strategy_name
        else:
            return jsonify({"error": f"Unknown strategy type: '{strategy_type}'"}), 400
        
        if strategy:
            trading_system.strategies[strategy_name] = strategy
            strategy.set_trading_system(trading_system)
            logger.info(f"Strategy '{strategy_name}' of type '{strategy_type}' created successfully.")
            return jsonify({"message": f"Strategy '{strategy_name}' created successfully", "config": data})
        else:
            return jsonify({"error": "Failed to create strategy due to internal error."}), 500

    except Exception as e:
        logger.exception("Error creating strategy:")
        return jsonify({"error": f"Error creating strategy: {str(e)}"}), 500

@app.route('/api/strategies/<strategy_name>', methods=['DELETE'])
@ai_studio_auth_required
def delete_strategy(strategy_name: str):
    """Delete a strategy."""
    try:
        if strategy_name not in trading_system.strategies:
            return jsonify({"error": f"Strategy '{strategy_name}' not found"}), 404
        
        if strategy_name == 'sma' and len(trading_system.strategies) == 1:
            return jsonify({"error": "Cannot delete default 'sma' strategy if it's the only one active. Create another strategy first."}), 400
        
        del trading_system.strategies[strategy_name]
        logger.info(f"Strategy '{strategy_name}' deleted successfully.")
        return jsonify({"message": f"Strategy '{strategy_name}' deleted successfully"})
    except Exception as e:
        logger.exception(f"Error deleting strategy '{strategy_name}':")
        return jsonify({"error": f"Error deleting strategy: {str(e)}"}), 500

@app.route('/api/logs', methods=['GET'])
@ai_studio_auth_required
def get_logs():
    """Get recent log entries from the log file."""
    try:
        lines = request.args.get('lines', 100, type=int)
        
        try:
            with open('trading_system.log', 'r') as f:
                log_lines = f.readlines()
                recent_logs = log_lines[-lines:] if len(log_lines) > lines else log_lines
                
            return jsonify({"logs": recent_logs})
        except FileNotFoundError:
            return jsonify({"logs": ["Log file 'trading_system.log' not found. Ensure logging is configured correctly."]})
    except Exception as e:
        logger.exception("Error getting logs:")
        return jsonify({"error": f"Error getting logs: {str(e)}"}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint (no auth required)."""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "environment": ENVIRONMENT,
        "api_key_configured": DELTA_API_KEY != 'your_delta_api_key_here',
        "ai_studio_secret_configured": AI_STUDIO_SECRET != 'your_ai_studio_secret_here_for_testing',
        "websocket_status": trading_system.ws_manager.is_connected,
        "system_running": trading_system.is_running
    })

@app.route('/api/websocket/reconnect', methods=['POST'])
@ai_studio_auth_required
def reconnect_websocket():
    """Manually reconnect WebSocket."""
    try:
        logger.info("Manual WebSocket reconnection requested.")
        if trading_system.ws_manager.ws:
            trading_system.ws_manager.ws.close()
        else:
            trading_system.ws_manager.connect()
        
        return jsonify({"message": "WebSocket reconnection initiated. Check /api/status for status."})
    except Exception as e:
        logger.exception("Error reconnecting WebSocket:")
        return jsonify({"error": f"Error reconnecting WebSocket: {str(e)}"}), 500

@app.route('/api/database/cleanup', methods=['POST'])
@ai_studio_auth_required
def cleanup_database():
    """Clean up old database records."""
    try:
        days = request.args.get('days', 30, type=int)
        if days < 1:
            return jsonify({"error": "Days must be a positive integer"}), 400
        
        cutoff_datetime = datetime.now() - timedelta(days=days)
        cutoff_timestamp = int(cutoff_datetime.timestamp() * 1_000_000)
        
        market_data_deleted = 0
        trades_deleted = 0
        
        with trading_system.db_manager.get_connection() as conn:
            cursor = conn.execute(
                'DELETE FROM market_data WHERE timestamp < ?', 
                (cutoff_timestamp,)
            )
            market_data_deleted = cursor.rowcount
            
            cursor = conn.execute(
                'DELETE FROM trades WHERE timestamp < ?', 
                (cutoff_timestamp,)
            )
            trades_deleted = cursor.rowcount
            
            conn.commit()
        
        logger.info(f"Database cleanup completed: {market_data_deleted} market data records, {trades_deleted} trade records deleted older than {days} days.")
        return jsonify({
            "message": "Database cleanup completed",
            "market_data_deleted": market_data_deleted,
            "trades_deleted": trades_deleted,
            "cutoff_date": cutoff_datetime.isoformat()
        })
    except Exception as e:
        logger.exception("Error cleaning database:")
        return jsonify({"error": f"Error cleaning database: {str(e)}"}), 500

# --- Error handlers ---
@app.errorhandler(400)
def bad_request_error(error):
    logger.error(f"Bad Request: {error.description}")
    return jsonify({"error": error.description or "Bad request"}), 400

@app.errorhandler(404)
def not_found_error(error):
    logger.warning(f"Endpoint not found: {request.path}")
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(405)
def method_not_allowed_error(error):
    logger.warning(f"Method not allowed: {request.method} on {request.path}")
    return jsonify({"error": "Method not allowed"}), 405

@app.errorhandler(500)
def internal_error(error):
    logger.exception("Internal server error caught by handler:")
    return jsonify({"error": "Internal server error"}), 500

@app.errorhandler(Exception)
def handle_exception(e: Exception):
    """A catch-all for unhandled exceptions."""
    if isinstance(e, requests.exceptions.HTTPError):
        return jsonify({"error": f"HTTP Error: {e.response.status_code} - {e.response.text}"}), e.response.status_code
    if isinstance(e, requests.exceptions.ConnectionError):
        logger.error(f"Connection Error: {e}. Check API connectivity.")
        return jsonify({"error": "Connection error to external API"}), 503

    logger.exception("An unhandled exception occurred:")
    return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500

# --- Startup function - FIX: Auto-start system ---
def initialize_system():
    """Initialize the trading system on startup."""
    try:
        logger.info("Initializing trading system...")
        
        if DELTA_API_KEY == 'your_delta_api_key_here' or not DELTA_API_KEY:
            logger.error("CRITICAL: DELTA_API_KEY is not set or using default placeholder. API functionality will fail.")
        
        if DELTA_API_SECRET == 'your_delta_api_secret_here' or not DELTA_API_SECRET:
            logger.error("CRITICAL: DELTA_API_SECRET is not set or using default placeholder. API functionality will fail.")
        
        if AI_STUDIO_SECRET == 'your_ai_studio_secret_here_for_testing' or not AI_STUDIO_SECRET:
            logger.warning("WARNING: AI_STUDIO_SECRET is not set or using default placeholder. API endpoints are unprotected or using default for testing.")
        
        # FIX: Auto-start the trading system
        trading_system.start()
        
        logger.info("Trading system initialization complete. Check /api/status for live status.")
        
    except Exception as e:
        logger.exception("CRITICAL: Failed to initialize trading system:")
        raise
    # --- Main execution ---
if __name__ == '__main__':
    initialize_system()
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)), debug=False)
