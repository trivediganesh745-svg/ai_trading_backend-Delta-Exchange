# app.py - Pandas-free version for better Render compatibility
import os
import json
import time
import hmac
import hashlib
import logging
import threading
import websocket
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any
from collections import defaultdict, deque
import sqlite3
from contextlib import contextmanager
import statistics
import math

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

# Configuration
class Config:
    # API Configuration
    BASE_URL = os.getenv('DELTA_BASE_URL', 'https://api.india.delta.exchange')
    WS_URL = os.getenv('DELTA_WS_URL', 'wss://socket.india.delta.exchange')
    API_KEY = os.getenv('DELTA_API_KEY', 'your_api_key_here')
    API_SECRET = os.getenv('DELTA_API_SECRET', 'your_api_secret_here')
    
    # Trading Configuration
    MAX_POSITION_SIZE = int(os.getenv('MAX_POSITION_SIZE', '10'))
    MAX_DAILY_LOSS = float(os.getenv('MAX_DAILY_LOSS', '1000.0'))
    RISK_MANAGEMENT_ENABLED = os.getenv('RISK_MANAGEMENT_ENABLED', 'true').lower() == 'true'
    
    # Database
    DATABASE_PATH = os.getenv('DATABASE_PATH', 'trading_system.db')

# Data Models
@dataclass
class Order:
    id: Optional[int] = None
    product_id: int = 0
    product_symbol: str = ""
    size: int = 0
    side: str = ""  # buy/sell
    order_type: str = "limit_order"  # limit_order/market_order
    limit_price: Optional[str] = None
    state: str = "pending"
    client_order_id: Optional[str] = None
    created_at: Optional[str] = None
    unfilled_size: int = 0

@dataclass
class Position:
    product_id: int
    product_symbol: str
    size: int
    entry_price: str
    margin: str
    liquidation_price: str
    unrealized_pnl: float = 0.0

@dataclass
class Trade:
    id: str
    symbol: str
    side: str
    size: int
    price: str
    timestamp: str
    commission: str

# Lightweight data processing utilities (replacing pandas)
class DataProcessor:
    @staticmethod
    def calculate_sma(prices: List[float], window: int) -> Optional[float]:
        """Calculate Simple Moving Average"""
        if len(prices) < window:
            return None
        return sum(prices[-window:]) / window
    
    @staticmethod
    def calculate_returns(prices: List[float]) -> List[float]:
        """Calculate price returns"""
        if len(prices) < 2:
            return []
        
        returns = []
        for i in range(1, len(prices)):
            ret = (prices[i] - prices[i-1]) / prices[i-1]
            returns.append(ret)
        return returns
    
    @staticmethod
    def calculate_sharpe_ratio(returns: List[float], periods_per_year: int = 252) -> float:
        """Calculate Sharpe ratio"""
        if len(returns) < 2:
            return 0.0
        
        mean_return = statistics.mean(returns)
        std_return = statistics.stdev(returns) if len(returns) > 1 else 0
        
        if std_return == 0:
            return 0.0
        
        return (mean_return * periods_per_year) / (std_return * math.sqrt(periods_per_year))
    
    @staticmethod
    def calculate_max_drawdown(equity_curve: List[float]) -> float:
        """Calculate maximum drawdown"""
        if len(equity_curve) < 2:
            return 0.0
        
        peak = equity_curve[0]
        max_dd = 0.0
        
        for value in equity_curve:
            if value > peak:
                peak = value
            
            drawdown = (peak - value) / peak
            if drawdown > max_dd:
                max_dd = drawdown
        
        return max_dd

# Database Manager
class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
    
    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def init_database(self):
        with self.get_connection() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY,
                    product_id INTEGER,
                    product_symbol TEXT,
                    size INTEGER,
                    side TEXT,
                    order_type TEXT,
                    limit_price TEXT,
                    state TEXT,
                    client_order_id TEXT,
                    created_at TEXT,
                    unfilled_size INTEGER,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS positions (
                    product_id INTEGER PRIMARY KEY,
                    product_symbol TEXT,
                    size INTEGER,
                    entry_price TEXT,
                    margin TEXT,
                    liquidation_price TEXT,
                    unrealized_pnl REAL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    symbol TEXT,
                    side TEXT,
                    size INTEGER,
                    price TEXT,
                    commission TEXT,
                    timestamp TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS market_data (
                    symbol TEXT,
                    price REAL,
                    volume REAL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                
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
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            ''')
            conn.commit()

# Delta Exchange API Client
class DeltaExchangeAPI:
    def __init__(self, api_key: str, api_secret: str, base_url: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.session = requests.Session()
        
    def generate_signature(self, message: str) -> str:
        """Generate HMAC SHA256 signature for API authentication"""
        message_bytes = bytes(message, 'utf-8')
        secret_bytes = bytes(self.api_secret, 'utf-8')
        hash_obj = hmac.new(secret_bytes, message_bytes, hashlib.sha256)
        return hash_obj.hexdigest()
    
    def get_headers(self, method: str, path: str, query_string: str = "", payload: str = "") -> Dict[str, str]:
        """Generate authentication headers for API requests"""
        timestamp = str(int(time.time()))
        signature_data = method + timestamp + path + query_string + payload
        signature = self.generate_signature(signature_data)
        
        return {
            'api-key': self.api_key,
            'timestamp': timestamp,
            'signature': signature,
            'User-Agent': 'python-trading-bot',
            'Content-Type': 'application/json'
        }
    
    def make_request(self, method: str, endpoint: str, params: Dict = None, data: Dict = None) -> Dict:
        """Make authenticated API request"""
        url = f"{self.base_url}{endpoint}"
        query_string = ""
        payload = ""
        
        if params:
            query_string = "?" + "&".join([f"{k}={v}" for k, v in params.items()])
        
        if data:
            payload = json.dumps(data)
        
        headers = self.get_headers(method, endpoint, query_string, payload)
        
        try:
            if method == 'GET':
                response = self.session.get(url, headers=headers, params=params, timeout=30)
            elif method == 'POST':
                response = self.session.post(url, headers=headers, data=payload, timeout=30)
            elif method == 'PUT':
                response = self.session.put(url, headers=headers, data=payload, timeout=30)
            elif method == 'DELETE':
                response = self.session.delete(url, headers=headers, data=payload, timeout=30)
            
            response.raise_for_status()
            return response.json()
        
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            return {"success": False, "error": str(e)}
    
    # Order Management Methods
    def place_order(self, order: Order) -> Dict:
        """Place a new order"""
        data = {
            "product_symbol": order.product_symbol,
            "size": order.size,
            "side": order.side,
            "order_type": order.order_type
        }
        
        if order.limit_price:
            data["limit_price"] = order.limit_price
        
        if order.client_order_id:
            data["client_order_id"] = order.client_order_id
        
        return self.make_request('POST', '/v2/orders', data=data)
    
    def cancel_order(self, order_id: int) -> Dict:
        """Cancel an existing order"""
        data = {"id": order_id}
        return self.make_request('DELETE', '/v2/orders', data=data)
    
    def get_orders(self, product_id: int = None, states: str = "open") -> Dict:
        """Get orders"""
        params = {"states": states}
        if product_id:
            params["product_ids"] = str(product_id)
        
        return self.make_request('GET', '/v2/orders', params=params)
    
    def get_positions(self, product_id: int = None) -> Dict:
        """Get positions"""
        params = {}
        if product_id:
            params["product_id"] = product_id
        
        return self.make_request('GET', '/v2/positions', params=params)
    
    def get_wallet_balances(self) -> Dict:
        """Get wallet balances"""
        return self.make_request('GET', '/v2/wallet/balances')

# WebSocket Manager (simplified)
class WebSocketManager:
    def __init__(self, api_key: str, api_secret: str, ws_url: str, callback_handler):
        self.api_key = api_key
        self.api_secret = api_secret
        self.ws_url = ws_url
        self.callback_handler = callback_handler
        self.ws = None
        self.is_connected = False
        self.is_authenticated = False
        
    def generate_signature(self, message: str) -> str:
        """Generate signature for WebSocket authentication"""
        message_bytes = bytes(message, 'utf-8')
        secret_bytes = bytes(self.api_secret, 'utf-8')
        hash_obj = hmac.new(secret_bytes, message_bytes, hashlib.sha256)
        return hash_obj.hexdigest()
    
    def on_open(self, ws):
        """WebSocket connection opened"""
        logger.info("WebSocket connection opened")
        self.is_connected = True
        self.authenticate()
    
    def on_message(self, ws, message):
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(message)
            
            if data.get('type') == 'success' and data.get('message') == 'Authenticated':
                logger.info("WebSocket authenticated successfully")
                self.is_authenticated = True
                self.subscribe_to_channels()
            else:
                self.callback_handler.handle_message(data)
                
        except Exception as e:
            logger.error(f"Error processing WebSocket message: {e}")
    
    def on_error(self, ws, error):
        """Handle WebSocket errors"""
        logger.error(f"WebSocket error: {error}")
        self.is_connected = False
        self.is_authenticated = False
    
    def on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket connection close"""
        logger.info(f"WebSocket connection closed: {close_status_code} - {close_msg}")
        self.is_connected = False
        self.is_authenticated = False
    
    def authenticate(self):
        """Authenticate WebSocket connection"""
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
        
        self.ws.send(json.dumps(auth_message))
    
    def subscribe_to_channels(self):
        """Subscribe to required channels after authentication"""
        # Subscribe to orders channel
        orders_subscription = {
            "type": "subscribe",
            "payload": {
                "channels": [
                    {
                        "name": "orders",
                        "symbols": ["all"]
                    }
                ]
            }
        }
        self.ws.send(json.dumps(orders_subscription))
        
        # Subscribe to positions channel
        positions_subscription = {
            "type": "subscribe",
            "payload": {
                "channels": [
                    {
                        "name": "positions",
                        "symbols": ["all"]
                    }
                ]
            }
        }
        self.ws.send(json.dumps(positions_subscription))
    
    def connect(self):
        """Establish WebSocket connection"""
        try:
            self.ws = websocket.WebSocketApp(
                self.ws_url,
                on_open=self.on_open,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close
            )
            
            # Run WebSocket in a separate thread
            ws_thread = threading.Thread(target=self.ws.run_forever)
            ws_thread.daemon = True
            ws_thread.start()
        except Exception as e:
            logger.error(f"Failed to connect WebSocket: {e}")

# Trading Strategy Base Class
class TradingStrategy:
    def __init__(self, name: str):
        self.name = name
        self.positions = {}
        self.orders = {}
        
    def should_buy(self, symbol: str, price: float, data: Dict) -> bool:
        """Override this method to implement buy logic"""
        return False
    
    def should_sell(self, symbol: str, price: float, data: Dict) -> bool:
        """Override this method to implement sell logic"""
        return False
    
    def calculate_position_size(self, symbol: str, price: float) -> int:
        """Calculate position size based on risk management"""
        return 1

# Simple Moving Average Strategy (pandas-free)
class SMAStrategy(TradingStrategy):
    def __init__(self, short_window: int = 10, long_window: int = 30):
        super().__init__("SMA_Strategy")
        self.short_window = short_window
        self.long_window = long_window
        self.price_history = defaultdict(lambda: deque(maxlen=long_window))
    
    def update_price(self, symbol: str, price: float):
        """Update price history for the symbol"""
        self.price_history[symbol].append(price)
    
    def get_sma(self, symbol: str, window: int) -> Optional[float]:
        """Calculate Simple Moving Average"""
        prices = list(self.price_history[symbol])
        return DataProcessor.calculate_sma(prices, window)
    
    def should_buy(self, symbol: str, price: float, data: Dict) -> bool:
        """Buy when short SMA crosses above long SMA"""
        short_sma = self.get_sma(symbol, self.short_window)
        long_sma = self.get_sma(symbol, self.long_window)
        
        if short_sma is None or long_sma is None:
            return False
        
        return short_sma > long_sma and symbol not in self.positions
    
    def should_sell(self, symbol: str, price: float, data: Dict) -> bool:
        """Sell when short SMA crosses below long SMA"""
        short_sma = self.get_sma(symbol, self.short_window)
        long_sma = self.get_sma(symbol, self.long_window)
        
        if short_sma is None or long_sma is None:
            return False
        
        return short_sma < long_sma and symbol in self.positions

# Risk Management System
class RiskManager:
    def __init__(self, max_position_size: int, max_daily_loss: float):
        self.max_position_size = max_position_size
        self.max_daily_loss = max_daily_loss
        self.daily_pnl = 0.0
        self.last_reset_date = datetime.now().date()
    
    def reset_daily_pnl_if_needed(self):
        """Reset daily PnL if it's a new day"""
        current_date = datetime.now().date()
        if current_date > self.last_reset_date:
            self.daily_pnl = 0.0
            self.last_reset_date = current_date
    
    def can_place_order(self, order: Order, current_positions: Dict) -> bool:
        """Check if order can be placed based on risk limits"""
        self.reset_daily_pnl_if_needed()
        
        # Check daily loss limit
        if self.daily_pnl <= -self.max_daily_loss:
            logger.warning(f"Daily loss limit reached: {self.daily_pnl}")
            return False
        
        # Check position size limit
        current_position = current_positions.get(order.product_symbol, 0)
        new_position = current_position + (order.size if order.side == 'buy' else -order.size)
        
        if abs(new_position) > self.max_position_size:
            logger.warning(f"Position size limit exceeded for {order.product_symbol}")
            return False
        
        return True
    
    def update_pnl(self, pnl_change: float):
        """Update daily PnL"""
        self.daily_pnl += pnl_change

# Simplified Backtesting Engine (pandas-free)
class BacktestEngine:
    def __init__(self, strategy: TradingStrategy, initial_capital: float = 10000.0):
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.positions = {}
        self.trades = []
        self.equity_curve = []
    
    def run_backtest(self, price_data: List[Dict], symbol: str) -> Dict:
        """Run backtest on historical price data"""
        logger.info(f"Starting backtest for {symbol}")
        
        for data_point in price_data:
            price = data_point['price']
            timestamp = data_point.get('timestamp', time.time())
            
            # Update strategy with new price
            self.strategy.update_price(symbol, price)
            
            # Check for buy signals
            if self.strategy.should_buy(symbol, price, data_point):
                self.execute_buy(symbol, price, timestamp)
            
            # Check for sell signals
            elif self.strategy.should_sell(symbol, price, data_point):
                self.execute_sell(symbol, price, timestamp)
            
            # Update equity curve
            self.update_equity(price)
        
        return self.calculate_performance_metrics()
    
    def execute_buy(self, symbol: str, price: float, timestamp):
        """Execute buy order in backtest"""
        if symbol not in self.positions:
            size = self.strategy.calculate_position_size(symbol, price)
            cost = size * price
            
            if cost <= self.capital:
                self.positions[symbol] = {'size': size, 'entry_price': price}
                self.capital -= cost
                self.trades.append({
                    'symbol': symbol,
                    'side': 'buy',
                    'size': size,
                    'price': price,
                    'timestamp': timestamp
                })
                logger.info(f"Backtest BUY: {symbol} @ {price}, Size: {size}")
    
    def execute_sell(self, symbol: str, price: float, timestamp):
        """Execute sell order in backtest"""
        if symbol in self.positions:
            position = self.positions[symbol]
            proceeds = position['size'] * price
            pnl = proceeds - (position['size'] * position['entry_price'])
            
            self.capital += proceeds
            del self.positions[symbol]
            
            self.trades.append({
                'symbol': symbol,
                'side': 'sell',
                'size': position['size'],
                'price': price,
                'timestamp': timestamp,
                'pnl': pnl
            })
            logger.info(f"Backtest SELL: {symbol} @ {price}, PnL: {pnl}")
    
    def update_equity(self, current_price: float):
        """Update equity curve"""
        total_equity = self.capital
        for symbol, position in self.positions.items():
            total_equity += position['size'] * current_price
        
        self.equity_curve.append(total_equity)
    
    def calculate_performance_metrics(self) -> Dict:
        """Calculate backtest performance metrics"""
        if not self.equity_curve:
            return {}
        
        returns = DataProcessor.calculate_returns(self.equity_curve)
        total_return = (self.equity_curve[-1] - self.initial_capital) / self.initial_capital
        sharpe_ratio = DataProcessor.calculate_sharpe_ratio(returns)
        max_drawdown = DataProcessor.calculate_max_drawdown(self.equity_curve)
        
        # Calculate win rate
        profitable_trades = [t for t in self.trades if t.get('pnl', 0) > 0]
        win_rate = len(profitable_trades) / len(self.trades) if self.trades else 0
        
        return {
            'total_return': total_return,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown,
            'total_trades': len(self.trades),
            'win_rate': win_rate,
            'final_capital': self.equity_curve[-1] if self.equity_curve else self.initial_capital
        }

# Main Trading System (simplified for better compatibility)
class TradingSystem:
    def __init__(self):
        self.config = Config()
        self.db = DatabaseManager(self.config.DATABASE_PATH)
        self.api = DeltaExchangeAPI(
            self.config.API_KEY,
            self.config.API_SECRET,
            self.config.BASE_URL
        )
        self.risk_manager = RiskManager(
            self.config.MAX_POSITION_SIZE,
            self.config.MAX_DAILY_LOSS
        )
        self.strategy = SMAStrategy()
        
        # Initialize WebSocket manager with error handling
        try:
            self.ws_manager = WebSocketManager(
                self.config.API_KEY,
                self.config.API_SECRET,
                self.config.WS_URL,
                self
            )
        except Exception as e:
            logger.error(f"Failed to initialize WebSocket manager: {e}")
            self.ws_manager = None
        
        self.current_positions = {}
        self.current_orders = {}
        self.is_trading_enabled = True
        
    def start(self):
        """Start the trading system"""
        logger.info("Starting Delta Exchange Trading System")
        
        # Connect to WebSocket if available
        if self.ws_manager:
            try:
                self.ws_manager.connect()
            except Exception as e:
                logger.error(f"Failed to connect WebSocket: {e}")
        
        # Load initial positions and orders
        self.load_initial_state()
        
        logger.info("Trading system started successfully")
    
    def load_initial_state(self):
        """Load current positions and orders from API"""
        try:
            # Load positions
            positions_response = self.api.get_positions()
            if positions_response.get('success'):
                result = positions_response.get('result')
                if isinstance(result, list):
                    for pos_data in result:
                        position = Position(
                            product_id=pos_data.get('product_id', 0),
                            product_symbol=pos_data.get('product_symbol', ''),
                            size=pos_data.get('size', 0),
                            entry_price=pos_data.get('entry_price', '0'),
                            margin=pos_data.get('margin', '0'),
                            liquidation_price=pos_data.get('liquidation_price', '0')
                        )
                        self.current_positions[position.product_symbol] = position
                elif isinstance(result, dict):
                    # Single position response
                    position = Position(
                        product_id=result.get('product_id', 0),
                        product_symbol=result.get('product_symbol', ''),
                        size=result.get('size', 0),
                        entry_price=result.get('entry_price', '0'),
                        margin=result.get('margin', '0'),
                        liquidation_price=result.get('liquidation_price', '0')
                    )
                    self.current_positions[position.product_symbol] = position
            
            # Load open orders
            orders_response = self.api.get_orders(states="open")
            if orders_response.get('success'):
                for order_data in orders_response.get('result', []):
                    order = Order(
                        id=order_data.get('id'),
                        product_id=order_data.get('product_id', 0),
                        product_symbol=order_data.get('product_symbol', ''),
                        size=order_data.get('size', 0),
                        side=order_data.get('side', ''),
                        order_type=order_data.get('order_type', 'limit_order'),
                        limit_price=order_data.get('limit_price'),
                        state=order_data.get('state', 'pending'),
                        client_order_id=order_data.get('client_order_id'),
                        created_at=order_data.get('created_at'),
                        unfilled_size=order_data.get('unfilled_size', 0)
                    )
                    if order.id:
                        self.current_orders[order.id] = order
            
            logger.info(f"Loaded {len(self.current_positions)} positions and {len(self.current_orders)} orders")
            
        except Exception as e:
            logger.error(f"Error loading initial state: {e}")
    
    def handle_message(self, message: Dict):
        """Handle WebSocket messages"""
        try:
            msg_type = message.get('type')
            
            if msg_type == 'orders':
                self.handle_order_update(message)
            elif msg_type == 'positions':
                self.handle_position_update(message)
            elif msg_type == 'l2_orderbook':
                self.handle_market_data(message)
            
        except Exception as e:
            logger.error(f"Error handling WebSocket message: {e}")
    
    def handle_order_update(self, message: Dict):
        """Handle order updates from WebSocket"""
        try:
            order_id = message.get('order_id')
            action = message.get('action')
            
            if not order_id:
                return
            
            if action == 'create':
                order = Order(
                    id=order_id,
                    product_id=message.get('product_id', 0),
                    product_symbol=message.get('symbol', ''),
                    size=message.get('size', 0),
                    side=message.get('side', ''),
                    order_type=message.get('order_type', 'limit_order'),
                    limit_price=message.get('limit_price'),
                    state=message.get('state', 'pending'),
                    client_order_id=message.get('client_order_id'),
                    unfilled_size=message.get('unfilled_size', 0)
                )
                self.current_orders[order_id] = order
                self.save_order_to_db(order)
                
            elif action == 'update':
                if order_id in self.current_orders:
                    order = self.current_orders[order_id]
                    order.state = message.get('state', order.state)
                    order.unfilled_size = message.get('unfilled_size', order.unfilled_size)
                    self.save_order_to_db(order)
                    
            elif action == 'delete':
                if order_id in self.current_orders:
                    del self.current_orders[order_id]
            
            logger.info(f"Order {action}: {order_id}")
            
        except Exception as e:
            logger.error(f"Error handling order update: {e}")
    
    def handle_position_update(self, message: Dict):
        """Handle position updates from WebSocket"""
        try:
            symbol = message.get('symbol')
            action = message.get('action')
            
            if not symbol:
                return
            
            if action in ['create', 'update']:
                position = Position(
                    product_id=message.get('product_id', 0),
                    product_symbol=symbol,
                    size=message.get('size', 0),
                    entry_price=message.get('entry_price', '0'),
                    margin=message.get('margin', '0'),
                    liquidation_price=message.get('liquidation_price', '0')                )
                self.current_positions[symbol] = position
                self.save_position_to_db(position)
                
            elif action == 'delete':
                if symbol in self.current_positions:
                    del self.current_positions[symbol]
            
            logger.info(f"Position {action}: {symbol}")
            
        except Exception as e:
            logger.error(f"Error handling position update: {e}")
    
    def handle_market_data(self, message: Dict):
        """Handle market data updates"""
        try:
            symbol = message.get('symbol')
            if not symbol:
                return
            
            # Extract price from orderbook
            asks = message.get('asks', [])
            bids = message.get('bids', [])
            
            if asks and bids:
                best_ask = float(asks[0][0])
                best_bid = float(bids[0][0])
                mid_price = (best_ask + best_bid) / 2
                
                # Update strategy with new price
                self.strategy.update_price(symbol, mid_price)
                
                # Check for trading signals
                if self.is_trading_enabled:
                    self.check_trading_signals(symbol, mid_price, message)
                
                # Save market data
                self.save_market_data(symbol, mid_price, message.get('timestamp'))
            
        except Exception as e:
            logger.error(f"Error handling market data: {e}")
    
    def check_trading_signals(self, symbol: str, price: float, data: Dict):
        """Check for trading signals and execute orders"""
        try:
            current_position_size = 0
            if symbol in self.current_positions:
                current_position_size = self.current_positions[symbol].size
            
            # Check buy signal
            if self.strategy.should_buy(symbol, price, data) and current_position_size <= 0:
                size = self.strategy.calculate_position_size(symbol, price)
                order = Order(
                    product_symbol=symbol,
                    size=size,
                    side='buy',
                    order_type='market_order',
                    client_order_id=f"auto_buy_{int(time.time())}"
                )
                
                if self.config.RISK_MANAGEMENT_ENABLED:
                    position_sizes = {s: p.size for s, p in self.current_positions.items()}
                    if not self.risk_manager.can_place_order(order, position_sizes):
                        logger.warning(f"Risk manager blocked buy order for {symbol}")
                        return
                
                self.place_order(order)
            
            # Check sell signal
            elif self.strategy.should_sell(symbol, price, data) and current_position_size > 0:
                size = abs(current_position_size)
                order = Order(
                    product_symbol=symbol,
                    size=size,
                    side='sell',
                    order_type='market_order',
                    client_order_id=f"auto_sell_{int(time.time())}"
                )
                
                if self.config.RISK_MANAGEMENT_ENABLED:
                    position_sizes = {s: p.size for s, p in self.current_positions.items()}
                    if not self.risk_manager.can_place_order(order, position_sizes):
                        logger.warning(f"Risk manager blocked sell order for {symbol}")
                        return
                
                self.place_order(order)
            
        except Exception as e:
            logger.error(f"Error checking trading signals for {symbol}: {e}")
    
    def place_order(self, order: Order):
        """Place an order through the API"""
        try:
            logger.info(f"Placing {order.side} order for {order.product_symbol}: {order.size} @ {order.limit_price or 'market'}")
            
            response = self.api.place_order(order)
            
            if response.get('success'):
                order_data = response.get('result', {})
                order.id = order_data.get('id')
                order.state = order_data.get('state')
                order.created_at = order_data.get('created_at')
                
                if order.id:
                    self.current_orders[order.id] = order
                    self.save_order_to_db(order)
                
                logger.info(f"Order placed successfully: ID {order.id}")
            else:
                logger.error(f"Failed to place order: {response.get('error')}")
                
        except Exception as e:
            logger.error(f"Error placing order: {e}")
    
    def cancel_order(self, order_id: int):
        """Cancel an order"""
        try:
            response = self.api.cancel_order(order_id)
            
            if response.get('success'):
                logger.info(f"Order {order_id} cancelled successfully")
                if order_id in self.current_orders:
                    self.current_orders[order_id].state = 'cancelled'
            else:
                logger.error(f"Failed to cancel order {order_id}: {response.get('error')}")
                
        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
    
    def save_order_to_db(self, order: Order):
        """Save order to database"""
        try:
            with self.db.get_connection() as conn:
                conn.execute('''
                    INSERT OR REPLACE INTO orders 
                    (id, product_id, product_symbol, size, side, order_type, limit_price, 
                     state, client_order_id, created_at, unfilled_size)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    order.id, order.product_id, order.product_symbol, order.size,
                    order.side, order.order_type, order.limit_price, order.state,
                    order.client_order_id, order.created_at, order.unfilled_size
                ))
                conn.commit()
        except Exception as e:
            logger.error(f"Error saving order to database: {e}")
    
    def save_position_to_db(self, position: Position):
        """Save position to database"""
        try:
            with self.db.get_connection() as conn:
                conn.execute('''
                    INSERT OR REPLACE INTO positions 
                    (product_id, product_symbol, size, entry_price, margin, 
                     liquidation_price, unrealized_pnl)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    position.product_id, position.product_symbol, position.size,
                    position.entry_price, position.margin, position.liquidation_price,
                    position.unrealized_pnl
                ))
                conn.commit()
        except Exception as e:
            logger.error(f"Error saving position to database: {e}")
    
    def save_market_data(self, symbol: str, price: float, timestamp: str):
        """Save market data to database"""
        try:
            with self.db.get_connection() as conn:
                conn.execute('''
                    INSERT INTO market_data (symbol, price, volume, timestamp)
                    VALUES (?, ?, ?, ?)
                ''', (symbol, price, 0, timestamp or datetime.now().isoformat()))
                conn.commit()
        except Exception as e:
            logger.error(f"Error saving market data: {e}")
    
    def run_backtest(self, symbol: str, start_date: str, end_date: str) -> Dict:
        """Run backtest for a symbol"""
        try:
            # Generate sample price data (in real implementation, fetch from API or database)
            import random
            random.seed(42)  # For reproducible results
            
            # Generate realistic price data with trend and volatility
            base_price = 50000  # Starting price
            num_points = 1000
            price_data = []
            
            current_price = base_price
            for i in range(num_points):
                # Add some random walk with slight upward bias
                change = random.gauss(0.0001, 0.02)  # Small positive drift with volatility
                current_price = current_price * (1 + change)
                
                price_data.append({
                    'price': current_price,
                    'timestamp': time.time() + i * 3600,  # Hourly data
                    'volume': random.randint(100, 1000)
                })
            
            # Run backtest
            backtest_engine = BacktestEngine(SMAStrategy(10, 30))
            results = backtest_engine.run_backtest(price_data, symbol)
            
            # Save results to database
            with self.db.get_connection() as conn:
                conn.execute('''
                    INSERT INTO backtest_results 
                    (strategy_name, symbol, start_date, end_date, total_return, 
                     sharpe_ratio, max_drawdown, total_trades, win_rate)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    'SMA_Strategy', symbol, start_date, end_date,
                    results.get('total_return', 0),
                    results.get('sharpe_ratio', 0),
                    results.get('max_drawdown', 0),
                    results.get('total_trades', 0),
                    results.get('win_rate', 0)
                ))
                conn.commit()
            
            logger.info(f"Backtest completed for {symbol}: {results}")
            return results
            
        except Exception as e:
            logger.error(f"Error running backtest: {e}")
            return {}
    
    def get_system_status(self) -> Dict:
        """Get current system status"""
        return {
            'is_trading_enabled': self.is_trading_enabled,
            'websocket_connected': self.ws_manager.is_connected if self.ws_manager else False,
            'websocket_authenticated': self.ws_manager.is_authenticated if self.ws_manager else False,
            'active_positions': len(self.current_positions),
            'open_orders': len(self.current_orders),
            'daily_pnl': self.risk_manager.daily_pnl,
            'max_daily_loss': self.risk_manager.max_daily_loss,
            'max_position_size': self.risk_manager.max_position_size
        }

# Flask Application
app = Flask(__name__)

# Initialize trading system with error handling
try:
    trading_system = TradingSystem()
except Exception as e:
    logger.error(f"Failed to initialize trading system: {e}")
    trading_system = None

# HTML Template for Status Dashboard (simplified for better compatibility)
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>Delta Exchange Trading System</title>
    <meta http-equiv="refresh" content="30">
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; }
        .card { background: white; padding: 20px; margin: 10px 0; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; }
        .status-item { text-align: center; }
        .status-value { font-size: 2em; font-weight: bold; margin: 10px 0; }
        .status-label { color: #666; }
        .green { color: #28a745; }
        .red { color: #dc3545; }
        .yellow { color: #ffc107; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
        th { background-color: #f8f9fa; }
        .btn { padding: 10px 20px; margin: 5px; border: none; border-radius: 4px; cursor: pointer; }
        .btn-success { background-color: #28a745; color: white; }
        .btn-danger { background-color: #dc3545; color: white; }
        .btn-primary { background-color: #007bff; color: white; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Delta Exchange Automated Trading System</h1>
        
        <div class="card">
            <h2>System Status</h2>
            <div class="status-grid">
                <div class="status-item">
                    <div class="status-value {% if status.websocket_connected %}green{% else %}red{% endif %}">
                        {% if status.websocket_connected %}CONNECTED{% else %}DISCONNECTED{% endif %}
                    </div>
                    <div class="status-label">WebSocket</div>
                </div>
                <div class="status-item">
                    <div class="status-value {% if status.is_trading_enabled %}green{% else %}red{% endif %}">
                        {% if status.is_trading_enabled %}ENABLED{% else %}DISABLED{% endif %}
                    </div>
                    <div class="status-label">Trading</div>
                </div>
                <div class="status-item">
                    <div class="status-value">{{ status.active_positions }}</div>
                    <div class="status-label">Active Positions</div>
                </div>
                <div class="status-item">
                    <div class="status-value">{{ status.open_orders }}</div>
                    <div class="status-label">Open Orders</div>
                </div>
                <div class="status-item">
                    <div class="status-value {% if status.daily_pnl >= 0 %}green{% else %}red{% endif %}">
                        ${{ "%.2f"|format(status.daily_pnl) }}
                    </div>
                    <div class="status-label">Daily PnL</div>
                </div>
            </div>
        </div>
        
        <div class="card">
            <h2>Current Positions</h2>
            <table>
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Size</th>
                        <th>Entry Price</th>
                        <th>Liquidation Price</th>
                        <th>Margin</th>
                    </tr>
                </thead>
                <tbody>
                    {% for position in positions %}
                    <tr>
                        <td>{{ position.product_symbol }}</td>
                        <td class="{% if position.size > 0 %}green{% else %}red{% endif %}">{{ position.size }}</td>
                        <td>${{ position.entry_price }}</td>
                        <td>${{ position.liquidation_price }}</td>
                        <td>${{ position.margin }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        
        <div class="card">
            <h2>Recent Orders</h2>
            <table>
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Symbol</th>
                        <th>Side</th>
                        <th>Size</th>
                        <th>Price</th>
                        <th>State</th>
                        <th>Created</th>
                    </tr>
                </thead>
                <tbody>
                    {% for order in orders %}
                    <tr>
                        <td>{{ order.id }}</td>
                        <td>{{ order.product_symbol }}</td>
                        <td class="{% if order.side == 'buy' %}green{% else %}red{% endif %}">{{ order.side.upper() }}</td>
                        <td>{{ order.size }}</td>
                        <td>${{ order.limit_price or 'MARKET' }}</td>
                        <td>{{ order.state.upper() }}</td>
                        <td>{{ order.created_at }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        
        <div class="card">
            <h2>Controls</h2>
            <button class="btn btn-success" onclick="toggleTrading()">
                {% if status.is_trading_enabled %}Disable Trading{% else %}Enable Trading{% endif %}
            </button>
            <button class="btn btn-danger" onclick="cancelAllOrders()">Cancel All Orders</button>
            <button class="btn btn-primary" onclick="runBacktest()">Run Backtest</button>
        </div>
    </div>
    
    <script>
        function toggleTrading() {
            fetch('/api/toggle-trading', {method: 'POST'})
                .then(response => response.json())
                .then(data => {
                    alert(data.message);
                    location.reload();
                })
                .catch(error => {
                    alert('Error: ' + error);
                });
        }
        
        function cancelAllOrders() {
            if (confirm('Are you sure you want to cancel all orders?')) {
                fetch('/api/cancel-all-orders', {method: 'POST'})
                    .then(response => response.json())
                    .then(data => {
                        alert(data.message);
                        location.reload();
                    })
                    .catch(error => {
                        alert('Error: ' + error);
                    });
            }
        }
        
        function runBacktest() {
            const symbol = prompt('Enter symbol for backtest (e.g., BTCUSD):');
            if (symbol) {
                fetch('/api/backtest', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        symbol: symbol,
                        start_date: '2024-01-01',
                        end_date: '2024-12-31'
                    })
                })
                .then(response => response.json())
                .then(data => {
                    alert('Backtest completed. Check logs for results.');
                })
                .catch(error => {
                    alert('Error: ' + error);
                });
            }
        }
    </script>
</body>
</html>
'''

# API Routes with error handling
@app.route('/')
def dashboard():
    """Main dashboard"""
    try:
        if not trading_system:
            return "Trading system not initialized", 500
            
        status = trading_system.get_system_status()
        positions = list(trading_system.current_positions.values())
        orders = list(trading_system.current_orders.values())[-10:]  # Last 10 orders
        
        return render_template_string(DASHBOARD_HTML, 
                                    status=status, 
                                    positions=positions, 
                                    orders=orders)
    except Exception as e:
        logger.error(f"Error rendering dashboard: {e}")
        return f"Error: {e}", 500

@app.route('/api/status')
def api_status():
    """Get system status"""
    try:
        if not trading_system:
            return jsonify({"error": "Trading system not initialized"}), 500
            
        return jsonify(trading_system.get_system_status())
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/positions')
def api_positions():
    """Get current positions"""
    try:
        if not trading_system:
            return jsonify({"error": "Trading system not initialized"}), 500
            
        positions = [asdict(pos) for pos in trading_system.current_positions.values()]
        return jsonify({"positions": positions})
    except Exception as e:
        logger.error(f"Error getting positions: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/orders')
def api_orders():
    """Get current orders"""
    try:
        if not trading_system:
            return jsonify({"error": "Trading system not initialized"}), 500
            
        orders = [asdict(order) for order in trading_system.current_orders.values()]
        return jsonify({"orders": orders})
    except Exception as e:
        logger.error(f"Error getting orders: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/place-order', methods=['POST'])
def api_place_order():
    """Place a new order"""
    try:
        if not trading_system:
            return jsonify({"error": "Trading system not initialized"}), 500
            
        data = request.get_json()
        
        order = Order(
            product_symbol=data.get('symbol'),
            size=int(data.get('size')),
            side=data.get('side'),
            order_type=data.get('order_type', 'market_order'),
            limit_price=data.get('limit_price'),
            client_order_id=f"manual_{int(time.time())}"
        )
        
        trading_system.place_order(order)
        
        return jsonify({"success": True, "message": "Order placed successfully"})
    except Exception as e:
        logger.error(f"Error placing order: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/cancel-order', methods=['POST'])
def api_cancel_order():
    """Cancel an order"""
    try:
        if not trading_system:
            return jsonify({"error": "Trading system not initialized"}), 500
            
        data = request.get_json()
        order_id = int(data.get('order_id'))
        
        trading_system.cancel_order(order_id)
        
        return jsonify({"success": True, "message": f"Order {order_id} cancelled"})
    except Exception as e:
        logger.error(f"Error cancelling order: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/cancel-all-orders', methods=['POST'])
def api_cancel_all_orders():
    """Cancel all open orders"""
    try:
        if not trading_system:
            return jsonify({"error": "Trading system not initialized"}), 500
            
        cancelled_count = 0
        for order_id in list(trading_system.current_orders.keys()):
            trading_system.cancel_order(order_id)
            cancelled_count += 1
        
        return jsonify({"success": True, "message": f"Cancelled {cancelled_count} orders"})
    except Exception as e:
        logger.error(f"Error cancelling all orders: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/toggle-trading', methods=['POST'])
def api_toggle_trading():
    """Toggle trading on/off"""
    try:
        if not trading_system:
            return jsonify({"error": "Trading system not initialized"}), 500
            
        trading_system.is_trading_enabled = not trading_system.is_trading_enabled
        status = "enabled" if trading_system.is_trading_enabled else "disabled"
        
        return jsonify({"success": True, "message": f"Trading {status}"})
    except Exception as e:
        logger.error(f"Error toggling trading: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/backtest', methods=['POST'])
def api_backtest():
    """Run backtest"""
    try:
        if not trading_system:
            return jsonify({"error": "Trading system not initialized"}), 500
            
        data = request.get_json()
        symbol = data.get('symbol', 'BTCUSD')
        start_date = data.get('start_date', '2024-01-01')
        end_date = data.get('end_date', '2024-12-31')
        
        results = trading_system.run_backtest(symbol, start_date, end_date)
        
        return jsonify({"success": True, "results": results})
    except Exception as e:
        logger.error(f"Error running backtest: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/wallet')
def api_wallet():
    """Get wallet balances"""
    try:
        if not trading_system:
            return jsonify({"error": "Trading system not initialized"}), 500
            
        response = trading_system.api.get_wallet_balances()
        return jsonify(response)
    except Exception as e:
        logger.error(f"Error getting wallet: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health')
def health_check():
    """Health check endpoint for monitoring"""
    try:
        if not trading_system:
            return jsonify({"status": "unhealthy", "reason": "Trading system not initialized"}), 503
            
        status = trading_system.get_system_status()
        
        return jsonify({
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "system_status": status
        })
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({"status": "unhealthy", "error": str(e)}), 503

@app.route('/api/logs')
def api_logs():
    """Get recent log entries"""
    try:
        try:
            with open('trading_system.log', 'r') as f:
                lines = f.readlines()
                recent_logs = lines[-100:]  # Last 100 lines
        except FileNotFoundError:
            recent_logs = ["Log file not found"]
        
        return jsonify({"logs": recent_logs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({"error": "Internal server error"}), 500

# Initialize and start the system
if __name__ == '__main__':
    try:
        # Start the trading system if initialized
        if trading_system:
            trading_system.start()
        
        # Start Flask app
        port = int(os.getenv('PORT', 5000))
        app.run(host='0.0.0.0', port=port, debug=False)
        
    except Exception as e:
        logger.error(f"Failed to start application: {e}")
        # Don't raise the exception, let the app start anyway for debugging
        port = int(os.getenv('PORT', 5000))
        app.run(host='0.0.0.0', port=port, debug=False)

