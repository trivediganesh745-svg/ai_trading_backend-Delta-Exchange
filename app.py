# app.py

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
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Any, Callable
from collections import defaultdict, deque
import sqlite3
from contextlib import contextmanager

# --- RECOMMENDATION ---
# While your custom API client is well-written, for production, it's highly recommended
# to use the official or a well-maintained community library like `delta_rest_client`.
# This reduces maintenance overhead and benefits from community testing and updates.
# This corrected script retains your client but adds robustness.

# --- Configuration ---

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

# Environment variables
DELTA_API_KEY = os.getenv('DELTA_API_KEY')
DELTA_API_SECRET = os.getenv('DELTA_API_SECRET')
AI_STUDIO_SECRET = os.getenv('AI_STUDIO_SECRET')
ENVIRONMENT = os.getenv('ENVIRONMENT', 'testnet').strip().strip("'\"")

# API Configuration (Using standard, non-regional URLs for better reliability)
if ENVIRONMENT == 'production':
    BASE_URL = 'https://api.delta.exchange'
    WS_URL = 'wss://socket.delta.exchange'
else:
    BASE_URL = 'https://testnet-api.delta.exchange'
    WS_URL = 'wss://testnet-socket.delta.exchange'

logger.info(f"ENVIRONMENT: {ENVIRONMENT}")
logger.info(f"BASE_URL: {BASE_URL}")
logger.info(f"WS_URL: {WS_URL}")

# --- Data Models ---
@dataclass
class Order:
    product_symbol: str
    size: int
    side: str  # buy/sell
    order_type: str = "market_order"  # limit_order/market_order
    id: Optional[int] = None
    product_id: Optional[int] = None
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
class MarketData:
    symbol: str
    price: str
    timestamp: int  # Microseconds
    volume: Optional[str] = None
    bid: Optional[str] = None
    ask: Optional[str] = None

# --- Database Manager ---
class DatabaseManager:
    def __init__(self, db_path: str = "trading_system.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self.init_database()

    @contextmanager
    def get_connection(self):
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
            finally:
                conn.close()

    def init_database(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY, product_id INTEGER, product_symbol TEXT, size INTEGER, side TEXT,
                    order_type TEXT, limit_price TEXT, stop_price TEXT, state TEXT, client_order_id TEXT,
                    created_at TEXT, unfilled_size INTEGER, average_fill_price TEXT
                )
            ''')
            # Indexes for faster lookups in a production environment
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_orders_symbol_state ON orders (product_symbol, state)')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS market_data (
                    symbol TEXT, price TEXT, timestamp INTEGER, volume TEXT, bid TEXT, ask TEXT,
                    PRIMARY KEY (symbol, timestamp)
                )
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_market_data_symbol_timestamp ON market_data (symbol, timestamp)')
            conn.commit()

# --- Delta Exchange API Client ---
class DeltaExchangeAPI:
    def __init__(self, api_key: str, api_secret: str, base_url: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.session = requests.Session()

    def _generate_signature(self, message: str) -> str:
        return hmac.new(self.api_secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()

    def make_request(self, method: str, path: str, params: Optional[Dict] = None, data: Optional[Dict] = None) -> Dict:
        timestamp = str(int(time.time()))
        query_string = ""
        if params:
            sorted_params = sorted(params.items())
            query_string = "&".join([f"{k}={v}" for k, v in sorted_params])

        path_with_query = f"{path}?{query_string}" if query_string else path
        payload = json.dumps(data) if data else ""
        signature_data = method.upper() + timestamp + path_with_query + payload
        signature = self._generate_signature(signature_data)

        headers = {
            'api-key': self.api_key, 'timestamp': timestamp, 'signature': signature,
            'User-Agent': 'python-trading-system-v1', 'Content-Type': 'application/json'
        }
        url = f"{self.base_url}{path}"

        try:
            response = self.session.request(
                method, url, params=params, data=payload if data else None, headers=headers, timeout=(5, 30)
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            error_content = e.response.text if e.response else "No response"
            logger.error(f"API request to {url} failed: {e}. Response: {error_content}")
            raise

    def place_order(self, order: Order) -> Dict:
        data = {
            "product_symbol": order.product_symbol, "size": order.size, "side": order.side, "order_type": order.order_type
        }
        if order.limit_price is not None: data["limit_price"] = order.limit_price
        if order.stop_price is not None: data["stop_price"] = order.stop_price
        if order.client_order_id is not None: data["client_order_id"] = order.client_order_id
        return self.make_request("POST", "/v2/orders", data=data)

    def cancel_order(self, order_id: int, product_id: int) -> Dict:
        return self.make_request("DELETE", f"/v2/orders/{order_id}?product_id={product_id}")

    def get_orders(self, product_ids: Optional[str] = None, states: str = "open,pending") -> Dict:
        params = {"states": states}
        if product_ids: params["product_ids"] = product_ids
        return self.make_request("GET", "/v2/orders", params=params)

    def get_positions(self) -> Dict:
        return self.make_request("GET", "/v2/positions")
    
    def get_wallet_balances(self) -> Dict:
        return self.make_request("GET", "/v2/wallets/balances")


# --- WebSocket Manager ---
class WebSocketManager:
    def __init__(self, api_key: str, api_secret: str, ws_url: str, trading_system: 'TradingSystem'):
        self.api_key = api_key
        self.api_secret = api_secret
        self.ws_url = ws_url
        self.trading_system = trading_system
        self.ws: Optional[websocket.WebSocketApp] = None
        self.is_connected = False
        self.is_authenticated = False
        self._connect_lock = threading.Lock()
        self._reconnect_attempts = 0
        self.max_reconnect_attempts = 0  # 0 for infinite retries

    def _generate_signature(self, message: str) -> str:
        return hmac.new(self.api_secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()

    def on_open(self, ws: websocket.WebSocketApp):
        logger.info("WebSocket connection opened.")
        self.is_connected = True
        self._reconnect_attempts = 0
        self._send_authentication()

    def on_close(self, ws: websocket.WebSocketApp, close_status_code: int, close_msg: str):
        logger.warning(f"WebSocket closed: {close_status_code} - {close_msg}")
        self.is_connected = False
        self.is_authenticated = False
        if not self.trading_system.is_stopping:
            self._reconnect()

    def on_error(self, ws: websocket.WebSocketApp, error: Exception):
        logger.error(f"WebSocket error: {error}")
        # on_close will be called automatically after an error, triggering reconnect logic.

    def on_message(self, ws: websocket.WebSocketApp, message: str):
        try:
            data = json.loads(message)
            self._handle_message(data)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse WebSocket message: {message} | Error: {e}")
        except Exception as e:
            logger.exception(f"Error processing WebSocket message: {message}")

    def _handle_message(self, data: Dict):
        message_type = data.get('type')
        if message_type == 'success' and data.get('message') == 'Authenticated':
            logger.info("WebSocket authenticated successfully.")
            self.is_authenticated = True
            self._subscribe_to_channels()
        elif message_type == 'orders':
            self.trading_system.process_order_update(data)
        elif message_type == 'positions':
            self.trading_system.process_position_update(data)
        elif message_type == 'v2/ticker':
            self.trading_system.process_market_data(data)
        else:
            logger.debug(f"Received unhandled message type '{message_type}': {data}")

    def _send_authentication(self):
        timestamp = str(int(time.time()))
        signature = self._generate_signature(f"GET{timestamp}/live")
        auth_message = {"type": "auth", "payload": {"api-key": self.api_key, "signature": signature, "timestamp": timestamp}}
        self._send_message(auth_message)
        logger.info("Authentication message sent.")

    def _subscribe_to_channels(self):
        symbols_to_subscribe = self.trading_system.get_all_strategy_symbols()
        if not symbols_to_subscribe:
            logger.warning("No symbols to subscribe to from strategies. Subscribing to default BTCUSD/ETHUSD.")
            symbols_to_subscribe = ["BTCUSD", "ETHUSD"]
        
        subscriptions = [
            {"name": "orders", "symbols": ["all"]},
            {"name": "positions", "symbols": ["all"]},
            {"name": "v2/ticker", "symbols": list(symbols_to_subscribe)},
        ]
        for sub in subscriptions:
            self._subscribe(sub["name"], sub["symbols"])
    
    def _subscribe(self, channel: str, symbols: List[str]):
        payload = {"type": "subscribe", "payload": {"channels": [{"name": channel, "symbols": symbols}]}}
        self._send_message(payload)
        logger.info(f"Subscribed to {channel} for symbols: {symbols}")
    
    def _send_message(self, message: Dict):
        if self.ws and self.is_connected:
            try:
                self.ws.send(json.dumps(message))
            except websocket.WebSocketConnectionClosedException:
                logger.warning("Attempted to send message on a closed WebSocket.")
        else:
            logger.warning(f"Cannot send message, WebSocket not connected. Message: {message}")

    def connect(self):
        with self._connect_lock:
            if self.is_connected:
                logger.info("WebSocket is already connected.")
                return
            if not self.api_key or not self.api_secret:
                logger.error("Cannot connect WebSocket: API key or secret not configured.")
                return

            logger.info(f"Connecting to WebSocket: {self.ws_url}")
            self.ws = websocket.WebSocketApp(
                self.ws_url, on_open=self.on_open, on_message=self.on_message,
                on_error=self.on_error, on_close=self.on_close
            )
            threading.Thread(target=self.ws.run_forever, daemon=True).start()
    
    def _reconnect(self):
        if self.max_reconnect_attempts > 0 and self._reconnect_attempts >= self.max_reconnect_attempts:
            logger.error("Max reconnection attempts reached. WebSocket will not reconnect automatically.")
            return
        
        self._reconnect_attempts += 1
        backoff_time = min(5 * (2 ** (self._reconnect_attempts - 1)), 60)
        logger.info(f"Attempting to reconnect WebSocket in {backoff_time} seconds... (attempt {self._reconnect_attempts})")
        time.sleep(backoff_time)
        self.connect()

    def close(self):
        if self.ws:
            self.ws.close()

# --- Trading Strategy Base Class ---
@dataclass
class TradingStrategy:
    name: str
    trading_system: Optional['TradingSystem'] = field(init=False, default=None)
    
    def set_trading_system(self, system: 'TradingSystem'):
        self.trading_system = system
        
    def get_subscribed_symbols(self) -> List[str]:
        raise NotImplementedError

    def on_market_data(self, data: MarketData):
        raise NotImplementedError
    
    def get_current_position_size(self, symbol: str) -> int:
        if self.trading_system:
            return self.trading_system.get_position_size(symbol)
        return 0

# --- Simple Moving Average Strategy ---
class SMAStrategy(TradingStrategy):
    def __init__(self, name: str, symbol: str, short_window: int = 10, long_window: int = 30, trade_size: int = 1):
        super().__init__(name)
        self.symbol = symbol
        self.short_window = short_window
        self.long_window = long_window
        self.trade_size = trade_size
        self.prices = deque(maxlen=self.long_window + 1)
        self.short_ma = 0
        self.long_ma = 0
        self.prev_short_ma = 0
        self.prev_long_ma = 0
        self.is_warmed_up = False

    def get_subscribed_symbols(self) -> List[str]:
        return [self.symbol]

    def on_market_data(self, data: MarketData):
        if data.symbol != self.symbol:
            return
        try:
            price = float(data.price)
        except (ValueError, TypeError):
            logger.warning(f"[{self.name}] Invalid price data for {self.symbol}: {data.price}")
            return
        
        self.prices.append(price)
        if len(self.prices) < self.long_window:
            return
        
        self.is_warmed_up = True
        self.prev_short_ma, self.prev_long_ma = self.short_ma, self.long_ma
        
        # More efficient MA calculation with deque
        short_prices = list(self.prices)[-self.short_window:]
        self.short_ma = sum(short_prices) / len(short_prices)
        self.long_ma = sum(self.prices) / len(self.prices)
        
        self._generate_signals()
    
    def _generate_signals(self):
        if not self.is_warmed_up or self.prev_short_ma == 0:
            return # Avoid signals on first calculation
        
        current_position_size = self.get_current_position_size(self.symbol)
        
        # Bullish Crossover
        if self.short_ma > self.long_ma and self.prev_short_ma <= self.prev_long_ma:
            logger.info(f"[{self.name}] BUY signal for {self.symbol}. Short MA ({self.short_ma:.2f}) crossed above Long MA ({self.long_ma:.2f})")
            if current_position_size < 0:
                logger.info(f"[{self.name}] Closing existing SHORT position ({current_position_size}) for {self.symbol}.")
                order = Order(product_symbol=self.symbol, size=abs(current_position_size), side="buy")
                self.trading_system.place_order(order)
            elif current_position_size == 0:
                logger.info(f"[{self.name}] Entering LONG position for {self.symbol}.")
                order = Order(product_symbol=self.symbol, size=self.trade_size, side="buy")
                self.trading_system.place_order(order)

        # Bearish Crossover
        elif self.short_ma < self.long_ma and self.prev_short_ma >= self.prev_long_ma:
            logger.info(f"[{self.name}] SELL signal for {self.symbol}. Short MA ({self.short_ma:.2f}) crossed below Long MA ({self.long_ma:.2f})")
            if current_position_size > 0:
                logger.info(f"[{self.name}] Closing existing LONG position ({current_position_size}) for {self.symbol}.")
                order = Order(product_symbol=self.symbol, size=current_position_size, side="sell")
                self.trading_system.place_order(order)
            elif current_position_size == 0:
                logger.info(f"[{self.name}] Entering SHORT position for {self.symbol}.")
                order = Order(product_symbol=self.symbol, size=self.trade_size, side="sell")
                self.trading_system.place_order(order)

# --- Main Trading System ---
class TradingSystem:
    def __init__(self):
        self.db_manager = DatabaseManager()
        self.api_client = DeltaExchangeAPI(DELTA_API_KEY, DELTA_API_SECRET, BASE_URL)
        self.ws_manager = WebSocketManager(DELTA_API_KEY, DELTA_API_SECRET, WS_URL, self)
        self.orders: Dict[int, Dict] = {} # In-memory state
        self.positions: Dict[str, int] = defaultdict(int) # Symbol -> Size
        self.market_data: Dict[str, MarketData] = {}
        self.strategies: Dict[str, TradingStrategy] = {}
        self.is_running = False
        self.is_stopping = False
        self._state_lock = threading.Lock()
    
    def start(self):
        if self.is_running:
            logger.info("Trading system is already running.")
            return
        logger.info("Starting trading system...")
        self.is_running = True
        self.is_stopping = False
        self.ws_manager.connect()
        logger.info("Trading system startup process completed.")
    
    def stop(self):
        if not self.is_running:
            logger.info("Trading system is already stopped.")
            return
        logger.info("Stopping trading system...")
        self.is_stopping = True
        self.ws_manager.close()
        self.is_running = False
        logger.info("Trading system stopped.")
    
    def add_strategy(self, strategy: TradingStrategy):
        with self._state_lock:
            if strategy.name in self.strategies:
                logger.warning(f"Strategy '{strategy.name}' already exists. Overwriting.")
            self.strategies[strategy.name] = strategy
            strategy.set_trading_system(self)
            logger.info(f"Strategy '{strategy.name}' added.")
            # If system is running, resubscribe to include new symbols
            if self.is_running and self.ws_manager.is_authenticated:
                self.ws_manager._subscribe_to_channels()

    def get_all_strategy_symbols(self) -> set:
        symbols = set()
        with self._state_lock:
            for strategy in self.strategies.values():
                symbols.update(strategy.get_subscribed_symbols())
        return symbols

    def get_position_size(self, symbol: str) -> int:
        with self._state_lock:
            return self.positions.get(symbol, 0)
    
    def process_order_update(self, data: Dict):
        with self._state_lock:
            order_id = data.get('id')
            if order_id:
                self.orders[order_id] = data
                logger.debug(f"Order update for {order_id}, state: {data.get('state')}")
    
    def process_position_update(self, data: Dict):
        with self._state_lock:
            symbol = data.get('product_symbol')
            size = data.get('size', 0)
            if symbol:
                if size == 0 and symbol in self.positions:
                    del self.positions[symbol]
                    logger.info(f"Position for {symbol} is now flat.")
                else:
                    self.positions[symbol] = size
                    logger.info(f"Position update for {symbol}, size: {size}")
    
    def process_market_data(self, data: Dict):
        try:
            market_data = MarketData(
                symbol=data.get('symbol', ''),
                price=str(data.get('close', '0')),
                timestamp=data.get('timestamp', int(time.time() * 1_000_000)),
                volume=str(data.get('volume')) if data.get('volume') is not None else None,
                bid=str(data.get('best_bid')) if data.get('best_bid') is not None else None,
                ask=str(data.get('best_ask')) if data.get('best_ask') is not None else None
            )
            with self._state_lock:
                self.market_data[market_data.symbol] = market_data
            
            for strategy in list(self.strategies.values()): # Use list to avoid issues if strategies dict changes
                strategy.on_market_data(market_data)
        except Exception as e:
            logger.exception(f"Error processing market data: {data}")

    def place_order(self, order: Order) -> Dict:
        logger.info(f"Placing order: {order.side} {order.size} {order.product_symbol}")
        try:
            result = self.api_client.place_order(order)
            logger.info(f"API: Order placed response: {result}")
            return result
        except Exception as e:
            logger.error(f"Failed to place order {order.product_symbol}: {e}")
            return {"error": str(e)}

# --- Global Trading System Instance & Initialization ---
trading_system = TradingSystem()

def initialize_system():
    logger.info("Initializing trading system...")
    if not DELTA_API_KEY or not DELTA_API_SECRET:
        logger.critical("CRITICAL: DELTA_API_KEY or DELTA_API_SECRET is not set. API functionality will fail.")
    if not AI_STUDIO_SECRET:
        logger.warning("WARNING: AI_STUDIO_SECRET is not set. API endpoints are unprotected.")
    
    # Add a default strategy on startup
    default_strategy = SMAStrategy(name="default_sma_btc", symbol="BTCUSD", trade_size=1)
    trading_system.add_strategy(default_strategy)
    
    trading_system.start()
    logger.info("Trading system initialization complete.")

# --- API Routes & Auth ---

def ai_studio_auth_required(f: Callable) -> Callable:
    @wraps(f)
    def decorated_function(*args: Any, **kwargs: Any) -> Any:
        if not AI_STUDIO_SECRET:
            logger.error("AI_STUDIO_SECRET not configured, blocking request.")
            return jsonify({"error": "Service is misconfigured - no secret set."}), 503
        
        provided_secret = request.headers.get('X-AI-Studio-Secret')
        if provided_secret == AI_STUDIO_SECRET:
            return f(*args, **kwargs)
        else:
            logger.warning(f"Unauthorized access attempt to {request.path} from {request.remote_addr}.")
            return jsonify({"error": "Unauthorized"}), 401
    return decorated_function

# Simplified API routes, as control is now more strategy-centric
@app.route('/api/status', methods=['GET'])
@ai_studio_auth_required
def api_status():
    return jsonify({
        "is_running": trading_system.is_running,
        "websocket_connected": trading_system.ws_manager.is_connected,
        "websocket_authenticated": trading_system.ws_manager.is_authenticated,
        "positions": trading_system.positions,
        "strategies": list(trading_system.strategies.keys()),
        "environment": ENVIRONMENT
    })

# Add other necessary API endpoints (get orders, positions, create/delete strategies etc.)
# ... (for brevity, keeping the core system logic as the focus)

# --- Main Execution ---
if __name__ == '__main__':
    initialize_system()
    # For production, use a proper WSGI server like Gunicorn or uWSGI
    # e.g., gunicorn --workers 4 --bind 0.0.0.0:10000 app:app
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)), debug=False)
