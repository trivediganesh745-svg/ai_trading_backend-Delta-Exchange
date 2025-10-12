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
from typing import Dict, List, Optional, Any
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

# Environment variables
DELTA_API_KEY = os.getenv('DELTA_API_KEY', 'your_api_key_here')
DELTA_API_SECRET = os.getenv('DELTA_API_SECRET', 'your_api_secret_here')
AI_STUDIO_SECRET = os.getenv('AI_STUDIO_SECRET')
ENVIRONMENT = os.getenv('ENVIRONMENT', 'testnet')  # 'production' or 'testnet'

# API Configuration
if ENVIRONMENT == 'production':
    BASE_URL = 'https://api.india.delta.exchange'
    WS_URL = 'wss://socket.india.delta.exchange'
else:
    BASE_URL = 'https://cdn-ind.testnet.deltaex.org'
    WS_URL = 'wss://socket-ind.testnet.deltaex.org'

# Data Models
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

# Database Manager
class DatabaseManager:
    def __init__(self, db_path: str = "trading_system.db"):
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

# Delta Exchange API Client
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
    
    def make_request(self, method: str, path: str, params: Dict = None, data: Dict = None) -> Dict:
        """Make authenticated API request"""
        timestamp = str(int(time.time()))
        query_string = ""
        
        if params:
            query_string = "?" + "&".join([f"{k}={v}" for k, v in params.items()])
        
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
                timeout=(3, 27)
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            raise
    
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
        if order.stop_price:
            data["stop_price"] = order.stop_price
        if order.client_order_id:
            data["client_order_id"] = order.client_order_id
        
        return self.make_request("POST", "/v2/orders", data=data)
    
    def cancel_order(self, order_id: int, product_id: int) -> Dict:
        """Cancel an existing order"""
        data = {
            "id": order_id,
            "product_id": product_id
        }
        return self.make_request("DELETE", "/v2/orders", data=data)
    
    def get_orders(self, product_ids: str = None, states: str = "open,pending") -> Dict:
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

# WebSocket Manager
class WebSocketManager:
    def __init__(self, api_key: str, api_secret: str, ws_url: str, trading_system):
        self.api_key = api_key
        self.api_secret = api_secret
        self.ws_url = ws_url
        self.trading_system = trading_system
        self.ws = None
        self.is_connected = False
        self.is_authenticated = False
        self.subscribed_channels = set()
        
    def generate_signature(self, message: str) -> str:
        """Generate HMAC signature for WebSocket authentication"""
        message_bytes = bytes(message, 'utf-8')
        secret_bytes = bytes(self.api_secret, 'utf-8')
        hash_obj = hmac.new(secret_bytes, message_bytes, hashlib.sha256)
        return hash_obj.hexdigest()
    
    def on_open(self, ws):
        """WebSocket connection opened"""
        logger.info("WebSocket connection opened")
        self.is_connected = True
        self.send_authentication()
    
    def on_close(self, ws, close_status_code, close_msg):
        """WebSocket connection closed"""
        logger.warning(f"WebSocket closed: {close_status_code} - {close_msg}")
        self.is_connected = False
        self.is_authenticated = False
        # Attempt to reconnect after 5 seconds
        threading.Timer(5.0, self.connect).start()
    
    def on_error(self, ws, error):
        """WebSocket error occurred"""
        logger.error(f"WebSocket error: {error}")
    
    def on_message(self, ws, message):
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(message)
            self.handle_message(data)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse WebSocket message: {e}")
    
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
        logger.info(f"Order update: {data}")
        self.trading_system.process_order_update(data)
    
    def handle_position_update(self, data: Dict):
        """Handle position updates"""
        logger.info(f"Position update: {data}")
        self.trading_system.process_position_update(data)
    
    def handle_ticker_update(self, data: Dict):
        """Handle ticker updates"""
        market_data = MarketData(
            symbol=data.get('symbol', ''),
            price=data.get('close', '0'),
            timestamp=data.get('timestamp', int(time.time() * 1000000)),
            volume=data.get('volume'),
            bid=data.get('best_bid'),
            ask=data.get('best_ask')
        )
        self.trading_system.process_market_data(market_data)
    
    def handle_trade_update(self, data: Dict):
        """Handle trade updates"""
        logger.info(f"Trade update: {data}")
    
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
        
        self.ws.send(json.dumps(auth_message))
    
    def subscribe_to_channels(self):
        """Subscribe to required channels"""
        # Subscribe to orders and positions for all contracts
        self.subscribe("orders", ["all"])
        self.subscribe("positions", ["all"])
        
        # Subscribe to ticker data for major symbols
        self.subscribe("v2/ticker", ["BTCUSD", "ETHUSD"])
        
        # Subscribe to trades
        self.subscribe("all_trades", ["BTCUSD", "ETHUSD"])
    
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
    
    def connect(self):
        """Connect to WebSocket"""
        try:
            self.ws = websocket.WebSocketApp(
                self.ws_url,
                on_open=self.on_open,
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close
            )
            
            # Run in a separate thread
            ws_thread = threading.Thread(target=self.ws.run_forever)
            ws_thread.daemon = True
            ws_thread.start()
            
        except Exception as e:
            logger.error(f"Failed to connect to WebSocket: {e}")

# Trading Strategy Base Class
class TradingStrategy:
    def __init__(self, name: str):
        self.name = name
        self.positions = {}
        self.orders = {}
        self.market_data = {}
    
    def on_market_data(self, data: MarketData):
        """Called when new market data is received"""
        self.market_data[data.symbol] = data
        self.generate_signals(data)
    
    def generate_signals(self, data: MarketData):
        """Override this method to implement trading logic"""
        pass
    
    def should_buy(self, symbol: str) -> bool:
        """Override this method to implement buy logic"""
        return False
    
    def should_sell(self, symbol: str) -> bool:
        """Override this method to implement sell logic"""
        return False

# Simple Moving Average Strategy
class SMAStrategy(TradingStrategy):
    def __init__(self, short_window: int = 10, long_window: int = 30):
        super().__init__("SMA_Strategy")
        self.short_window = short_window
        self.long_window = long_window
        self.price_history = defaultdict(list)
    
    def generate_signals(self, data: MarketData):
        """Generate trading signals based on SMA crossover"""
        symbol = data.symbol
        price = float(data.price)
        
        # Store price history
        self.price_history[symbol].append(price)
        
        # Keep only required history
        if len(self.price_history[symbol]) > self.long_window:
            self.price_history[symbol] = self.price_history[symbol][-self.long_window:]
        
        # Calculate moving averages
        if len(self.price_history[symbol]) >= self.long_window:
            short_ma = np.mean(self.price_history[symbol][-self.short_window:])
            long_ma = np.mean(self.price_history[symbol][-self.long_window:])
            
            # Previous values for crossover detection
            if len(self.price_history[symbol]) > self.long_window:
                prev_short_ma = np.mean(self.price_history[symbol][-self.short_window-1:-1])
                prev_long_ma = np.mean(self.price_history[symbol][-self.long_window-1:-1])
                
                # Bullish crossover
                if short_ma > long_ma and prev_short_ma <= prev_long_ma:
                    logger.info(f"BUY signal for {symbol}: Short MA crossed above Long MA")
                
                # Bearish crossover
                elif short_ma < long_ma and prev_short_ma >= prev_long_ma:
                    logger.info(f"SELL signal for {symbol}: Short MA crossed below Long MA")

# Backtesting Engine
class BacktestEngine:
    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
    
    def run_backtest(self, strategy: TradingStrategy, symbol: str, 
                    start_date: str, end_date: str, initial_capital: float = 10000) -> Dict:
        """Run backtest for a strategy"""
        logger.info(f"Running backtest for {strategy.name} on {symbol}")
        
        # Get historical data
        historical_data = self.get_historical_data(symbol, start_date, end_date)
        
        if not historical_data:
            return {"error": "No historical data available"}
        
        # Initialize backtest variables
        capital = initial_capital
        position = 0
        trades = []
        equity_curve = []
        
        for data_point in historical_data:
            market_data = MarketData(
                symbol=symbol,
                price=str(data_point['price']),
                timestamp=data_point['timestamp']
            )
            
            # Update strategy with market data
            strategy.on_market_data(market_data)
            
            # Check for signals
            if strategy.should_buy(symbol) and position <= 0:
                # Buy signal
                if position < 0:
                    # Close short position
                    pnl = position * (float(data_point['price']) - trades[-1]['entry_price'])
                    capital += pnl
                    trades.append({
                        'type': 'close_short',
                        'price': data_point['price'],
                        'timestamp': data_point['timestamp'],
                        'pnl': pnl
                    })
                
                # Open long position
                position = capital / float(data_point['price'])
                trades.append({
                    'type': 'buy',
                    'entry_price': data_point['price'],
                    'timestamp': data_point['timestamp'],
                    'position': position
                })
            
            elif strategy.should_sell(symbol) and position >= 0:
                # Sell signal
                if position > 0:
                    # Close long position
                    pnl = position * (float(data_point['price']) - trades[-1]['entry_price'])
                    capital += pnl
                    trades.append({
                        'type': 'close_long',
                        'price': data_point['price'],
                        'timestamp': data_point['timestamp'],
                        'pnl': pnl
                    })
                
                # Open short position
                position = -capital / float(data_point['price'])
                trades.append({
                    'type': 'sell',
                    'entry_price': data_point['price'],
                    'timestamp': data_point['timestamp'],
                    'position': position
                })
            
            # Calculate current equity
            if position != 0:
                current_value = abs(position) * float(data_point['price'])
                if position > 0:
                    equity = current_value
                else:
                    equity = capital - (current_value - capital)
            else:
                equity = capital
            
            equity_curve.append({
                'timestamp': data_point['timestamp'],
                'equity': equity
            })
        
        # Calculate performance metrics
        results = self.calculate_performance_metrics(
            equity_curve, trades, initial_capital, strategy.name, symbol, start_date, end_date
        )
        
        # Save results to database
        self.save_backtest_results(results)
        
        return results
    
    def get_historical_data(self, symbol: str, start_date: str, end_date: str) -> List[Dict]:
        """Get historical market data from database"""
        with self.db_manager.get_connection() as conn:
            cursor = conn.execute('''
                SELECT * FROM market_data 
                WHERE symbol = ? AND timestamp BETWEEN ? AND ?
                ORDER BY timestamp
            ''', (symbol, start_date, end_date))
            
            return [dict(row) for row in cursor.fetchall()]
    
    def calculate_performance_metrics(self, equity_curve: List[Dict], trades: List[Dict], 
                                    initial_capital: float, strategy_name: str, 
                                    symbol: str, start_date: str, end_date: str) -> Dict:
        """Calculate backtest performance metrics"""
        if not equity_curve:
            return {}
        
        final_equity = equity_curve[-1]['equity']
        total_return = (final_equity - initial_capital) / initial_capital
        
        # Calculate Sharpe ratio (simplified)
        returns = []
        for i in range(1, len(equity_curve)):
            ret = (equity_curve[i]['equity'] - equity_curve[i-1]['equity']) / equity_curve[i-1]['equity']
            returns.append(ret)
        
        if returns:
            sharpe_ratio = np.mean(returns) / np.std(returns) * np.sqrt(252) if np.std(returns) > 0 else 0
        else:
            sharpe_ratio = 0
        
        # Calculate maximum drawdown
        peak = initial_capital
        max_drawdown = 0
        for point in equity_curve:
            if point['equity'] > peak:
                peak = point['equity']
            drawdown = (peak - point['equity']) / peak
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        # Calculate win rate
        winning_trades = sum(1 for trade in trades if trade.get('pnl', 0) > 0)
        total_trades = len([trade for trade in trades if 'pnl' in trade])
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
        """Save backtest results to database"""
        with self.db_manager.get_connection() as conn:
            conn.execute('''
                INSERT INTO backtest_results 
                (strategy_name, symbol, start_date, end_date, total_return, 
                 sharpe_ratio, max_drawdown, total_trades, win_rate, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                results['strategy_name'],
                results['symbol'],
                results['start_date'],
                results['end_date'],
                results['total_return'],
                results['sharpe_ratio'],
                results['max_drawdown'],
                results['total_trades'],
                results['win_rate'],
                datetime.now().isoformat()
            ))
            conn.commit()

# Main Trading System
class TradingSystem:
    def __init__(self):
        self.db_manager = DatabaseManager()
        self.api_client = DeltaExchangeAPI(DELTA_API_KEY, DELTA_API_SECRET, BASE_URL)
        self.ws_manager = WebSocketManager(DELTA_API_KEY, DELTA_API_SECRET, WS_URL, self)
        self.backtest_engine = BacktestEngine(self.db_manager)
        
        self.orders = {}
        self.positions = {}
        self.market_data = {}
        self.strategies = {}
        
        # Initialize default strategy
        self.strategies['sma'] = SMAStrategy()
        
        self.is_running = False
    
    def start(self):
        """Start the trading system"""
        logger.info("Starting trading system...")
        self.is_running = True
        
        # Connect to WebSocket
        self.ws_manager.connect()
        
        logger.info("Trading system started successfully")
    
    def stop(self):
        """Stop the trading system"""
        logger.info("Stopping trading system...")
        self.is_running = False
        
        if self.ws_manager.ws:
            self.ws_manager.ws.close()
        
        logger.info("Trading system stopped")
    
    def process_order_update(self, data: Dict):
        """Process order updates from WebSocket"""
        order_id = data.get('order_id')
        if order_id:
            self.orders[order_id] = data
            
            # Save to database
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
                    'limit_order',  # Default
                    data.get('limit_price'),
                    data.get('stop_price'),
                    data.get('state'),
                    data.get('client_order_id'),
                    data.get('timestamp'),
                    data.get('unfilled_size'),
                    data.get('average_fill_price')
                ))
                conn.commit()
    
    def process_position_update(self, data: Dict):
        """Process position updates from WebSocket"""
        product_id = data.get('product_id')
        if product_id:
            self.positions[product_id] = data
    
    def process_market_data(self, data: MarketData):
        """Process market data updates"""
        self.market_data[data.symbol] = data
        
        # Save to database
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
        
        # Update strategies
        for strategy in self.strategies.values():
            strategy.on_market_data(data)
    
    def place_order(self, order: Order) -> Dict:
        """Place a new order"""
        try:
            result = self.api_client.place_order(order)
            logger.info(f"Order placed successfully: {result}")
            return result
        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return {"error": str(e)}
    
    def cancel_order(self, order_id: int, product_id: int) -> Dict:
        """Cancel an order"""
        try:
            result = self.api_client.cancel_order(order_id, product_id)
            logger.info(f"Order cancelled successfully: {result}")
            return result
        except Exception as e:
            logger.error(f"Failed to cancel order: {e}")
            return {"error": str(e)}
    
    def get_system_status(self) -> Dict:
        """Get system status"""
        return {
            "is_running": self.is_running,
            "websocket_connected": self.ws_manager.is_connected,
            "websocket_authenticated": self.ws_manager.is_authenticated,
            "subscribed_channels": list(self.ws_manager.subscribed_channels),
            "active_orders": len(self.orders),
            "active_positions": len(self.positions),
            "strategies": list(self.strategies.keys()),
            "environment": ENVIRONMENT
        }

# Initialize trading system
trading_system = TradingSystem()

# Authentication decorator
def ai_studio_auth_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not AI_STUDIO_SECRET:
            logger.error("AI_STUDIO_SECRET environment variable not set")
            return jsonify({"error": "Server authentication not configured"}), 500
        
        provided_secret = request.headers.get('X-AI-Studio-Secret')
        if provided_secret == AI_STUDIO_SECRET:
            return f(*args, **kwargs)
        else:
            logger.warning(f"Unauthorized access attempt to {request.path} from {request.remote_addr}")
            return jsonify({"error": "Unauthorized"}), 401
    return decorated_function

# API Routes
@app.route('/api/status')
@ai_studio_auth_required
def api_status():
    """Get system status"""
    try:
        return jsonify(trading_system.get_system_status())
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/start', methods=['POST'])
@ai_studio_auth_required
def start_system():
    """Start the trading system"""
    try:
        trading_system.start()
        return jsonify({"message": "Trading system started successfully"})
    except Exception as e:
        logger.error(f"Error starting system: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/stop', methods=['POST'])
@ai_studio_auth_required
def stop_system():
    """Stop the trading system"""
    try:
        trading_system.stop()
        return jsonify({"message": "Trading system stopped successfully"})
    except Exception as e:
        logger.error(f"Error stopping system: {e}")
        return jsonify({"error": str(e)}), 500
        @app.route('/api/orders', methods=['GET'])
        
@ai_studio_auth_required
def get_orders():
    """Get orders"""
    try:
        product_ids = request.args.get('product_ids')
        states = request.args.get('states', 'open,pending')
        
        result = trading_system.api_client.get_orders(product_ids, states)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error getting orders: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/orders', methods=['POST'])
@ai_studio_auth_required
def place_order():
    """Place a new order"""
    try:
        data = request.get_json()
        
        order = Order(
            product_symbol=data.get('product_symbol'),
            size=data.get('size'),
            side=data.get('side'),
            order_type=data.get('order_type', 'market_order'),
            limit_price=data.get('limit_price'),
            stop_price=data.get('stop_price'),
            client_order_id=data.get('client_order_id')
        )
        
        result = trading_system.place_order(order)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error placing order: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/orders/<int:order_id>', methods=['DELETE'])
@ai_studio_auth_required
def cancel_order(order_id):
    """Cancel an order"""
    try:
        data = request.get_json()
        product_id = data.get('product_id')
        
        if not product_id:
            return jsonify({"error": "product_id is required"}), 400
        
        result = trading_system.cancel_order(order_id, product_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error cancelling order: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/positions', methods=['GET'])
@ai_studio_auth_required
def get_positions():
    """Get current positions"""
    try:
        result = trading_system.api_client.get_positions()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error getting positions: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/products', methods=['GET'])
@ai_studio_auth_required
def get_products():
    """Get available products"""
    try:
        result = trading_system.api_client.get_products()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error getting products: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/market-data/<symbol>', methods=['GET'])
@ai_studio_auth_required
def get_market_data(symbol):
    """Get latest market data for a symbol"""
    try:
        if symbol in trading_system.market_data:
            data = trading_system.market_data[symbol]
            return jsonify(asdict(data))
        else:
            return jsonify({"error": "No market data available for symbol"}), 404
    except Exception as e:
        logger.error(f"Error getting market data: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/backtest', methods=['POST'])
@ai_studio_auth_required
def run_backtest():
    """Run a backtest"""
    try:
        data = request.get_json()
        
        strategy_name = data.get('strategy', 'sma')
        symbol = data.get('symbol', 'BTCUSD')
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        initial_capital = data.get('initial_capital', 10000)
        
        if not start_date or not end_date:
            return jsonify({"error": "start_date and end_date are required"}), 400
        
        if strategy_name not in trading_system.strategies:
            return jsonify({"error": f"Strategy {strategy_name} not found"}), 404
        
        strategy = trading_system.strategies[strategy_name]
        result = trading_system.backtest_engine.run_backtest(
            strategy, symbol, start_date, end_date, initial_capital
        )
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error running backtest: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/backtest/results', methods=['GET'])
@ai_studio_auth_required
def get_backtest_results():
    """Get historical backtest results"""
    try:
        with trading_system.db_manager.get_connection() as conn:
            cursor = conn.execute('''
                SELECT * FROM backtest_results 
                ORDER BY created_at DESC 
                LIMIT 50
            ''')
            results = [dict(row) for row in cursor.fetchall()]
        
        return jsonify({"results": results})
    except Exception as e:
        logger.error(f"Error getting backtest results: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/strategies', methods=['GET'])
@ai_studio_auth_required
def get_strategies():
    """Get available strategies"""
    try:
        strategies = {}
        for name, strategy in trading_system.strategies.items():
            strategies[name] = {
                "name": strategy.name,
                "type": type(strategy).__name__,
                "active_positions": len(strategy.positions),
                "market_data_symbols": list(strategy.market_data.keys())
            }
        
        return jsonify({"strategies": strategies})
    except Exception as e:
        logger.error(f"Error getting strategies: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/strategies', methods=['POST'])
@ai_studio_auth_required
def create_strategy():
    """Create a new strategy"""
    try:
        data = request.get_json()
        strategy_type = data.get('type', 'sma')
        strategy_name = data.get('name')
        
        if not strategy_name:
            return jsonify({"error": "Strategy name is required"}), 400
        
        if strategy_name in trading_system.strategies:
            return jsonify({"error": "Strategy already exists"}), 400
        
        if strategy_type == 'sma':
            short_window = data.get('short_window', 10)
            long_window = data.get('long_window', 30)
            strategy = SMAStrategy(short_window, long_window)
            strategy.name = strategy_name
        else:
            return jsonify({"error": f"Unknown strategy type: {strategy_type}"}), 400
        
        trading_system.strategies[strategy_name] = strategy
        
        return jsonify({"message": f"Strategy {strategy_name} created successfully"})
    except Exception as e:
        logger.error(f"Error creating strategy: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/strategies/<strategy_name>', methods=['DELETE'])
@ai_studio_auth_required
def delete_strategy(strategy_name):
    """Delete a strategy"""
    try:
        if strategy_name not in trading_system.strategies:
            return jsonify({"error": "Strategy not found"}), 404
        
        if strategy_name == 'sma':  # Protect default strategy
            return jsonify({"error": "Cannot delete default strategy"}), 400
        
        del trading_system.strategies[strategy_name]
        
        return jsonify({"message": f"Strategy {strategy_name} deleted successfully"})
    except Exception as e:
        logger.error(f"Error deleting strategy: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/logs', methods=['GET'])
@ai_studio_auth_required
def get_logs():
    """Get recent log entries"""
    try:
        lines = request.args.get('lines', 100, type=int)
        
        try:
            with open('trading_system.log', 'r') as f:
                log_lines = f.readlines()
                recent_logs = log_lines[-lines:] if len(log_lines) > lines else log_lines
                
            return jsonify({"logs": recent_logs})
        except FileNotFoundError:
            return jsonify({"logs": ["Log file not found"]})
    except Exception as e:
        logger.error(f"Error getting logs: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint (no auth required)"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "environment": ENVIRONMENT
    })

@app.route('/api/websocket/reconnect', methods=['POST'])
@ai_studio_auth_required
def reconnect_websocket():
    """Manually reconnect WebSocket"""
    try:
        if trading_system.ws_manager.ws:
            trading_system.ws_manager.ws.close()
        
        trading_system.ws_manager.connect()
        
        return jsonify({"message": "WebSocket reconnection initiated"})
    except Exception as e:
        logger.error(f"Error reconnecting WebSocket: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/database/cleanup', methods=['POST'])
@ai_studio_auth_required
def cleanup_database():
    """Clean up old database records"""
    try:
        days = request.args.get('days', 30, type=int)
        cutoff_timestamp = int((datetime.now() - timedelta(days=days)).timestamp() * 1000000)
        
        with trading_system.db_manager.get_connection() as conn:
            # Clean old market data
            cursor = conn.execute(
                'DELETE FROM market_data WHERE timestamp < ?', 
                (cutoff_timestamp,)
            )
            market_data_deleted = cursor.rowcount
            
            # Clean old trades
            cursor = conn.execute(
                'DELETE FROM trades WHERE timestamp < ?', 
                (cutoff_timestamp,)
            )
            trades_deleted = cursor.rowcount
            
            conn.commit()
        
        return jsonify({
            "message": "Database cleanup completed",
            "market_data_deleted": market_data_deleted,
            "trades_deleted": trades_deleted
        })
    except Exception as e:
        logger.error(f"Error cleaning database: {e}")
        return jsonify({"error": str(e)}), 500

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({"error": "Internal server error"}), 500

@app.errorhandler(Exception)
def handle_exception(e):
    logger.error(f"Unhandled exception: {e}")
    return jsonify({"error": "An unexpected error occurred"}), 500

# Startup function
def initialize_system():
    """Initialize the trading system on startup"""
    try:
        logger.info("Initializing trading system...")
        
        # Validate environment variables
        if not DELTA_API_KEY or DELTA_API_KEY == 'your_api_key_here':
            logger.warning("DELTA_API_KEY not set - API functionality will be limited")
        
        if not DELTA_API_SECRET or DELTA_API_SECRET == 'your_api_secret_here':
            logger.warning("DELTA_API_SECRET not set - API functionality will be limited")
        
        if not AI_STUDIO_SECRET:
            logger.warning("AI_STUDIO_SECRET not set - API endpoints will not be protected")
        
        # Start the trading system
        trading_system.start()
        
        logger.info("Trading system initialized successfully")
        
    except Exception as e:
        logger.error(f"Failed to initialize trading system: {e}")

# Main execution
if __name__ == '__main__':
    # Initialize system
    initialize_system()
    
    # Get port from environment variable (for Render deployment)
    port = int(os.environ.get('PORT', 5000))
    
    # Run Flask app
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,  # Set to False for production
        threaded=True
    )
