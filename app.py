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
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any, Callable
from collections import defaultdict
import sqlite3
from contextlib import contextmanager

# --- Basic Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
app = Flask(__name__)

# --- Environment Variables ---
DELTA_API_KEY = os.getenv('DELTA_API_KEY')
DELTA_API_SECRET = os.getenv('DELTA_API_SECRET')
AI_STUDIO_SECRET = os.getenv('AI_STUDIO_SECRET')
ENVIRONMENT = os.getenv('ENVIRONMENT', 'testnet').strip().strip("'\"")

# --- API Configuration ---
if ENVIRONMENT == 'production':
    BASE_URL = 'https://api.india.delta.exchange'
    WS_URL = 'wss://socket.india.delta.exchange'
else:
    BASE_URL = 'https://cdn-ind.testnet.deltaex.org'
    WS_URL = 'wss://socket-ind.testnet.deltaex.org'

logger.info(f"Environment: {ENVIRONMENT}")
logger.info(f"Base URL: {BASE_URL}")

# --- Data Models (dataclasses are great, no changes needed here) ---
@dataclass
class Order:
    # ... [Your Order dataclass is fine, no changes needed]
    id: Optional[int] = None; product_id: int = 0; product_symbol: str = ""; size: int = 0; side: str = ""; order_type: str = "market_order"; limit_price: Optional[str] = None; stop_price: Optional[str] = None; state: str = "pending"; client_order_id: Optional[str] = None; created_at: Optional[str] = None; unfilled_size: int = 0; average_fill_price: Optional[str] = None

# ... [Other dataclasses like Position, Trade, MarketData are also fine]

# --- Delta Exchange API Client ---
class DeltaExchangeAPI:
    def __init__(self, api_key: str, api_secret: str, base_url: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.session = requests.Session()

    def generate_signature(self, message: str) -> str:
        # ... [Your signature generation is correct, no changes needed]
        return hmac.new(bytes(self.api_secret, 'utf-8'), bytes(message, 'utf-8'), hashlib.sha256).hexdigest()

    def make_request(self, method: str, path: str, params: Optional[Dict] = None, data: Optional[Dict] = None) -> Any:
        # ... [Your request logic is solid, no changes needed]
        timestamp = str(int(time.time()))
        query_string = ""
        if params:
            sorted_params = sorted(params.items())
            query_string = "?" + "&".join([f"{k}={v}" for k, v in sorted_params])
        
        payload = json.dumps(data) if data else ""
        signature_data = method + timestamp + path + query_string + payload
        signature = self.generate_signature(signature_data)
        
        headers = { 'api-key': self.api_key, 'timestamp': timestamp, 'signature': signature, 'Content-Type': 'application/json' }
        url = f"{self.base_url}{path}"
        
        try:
            response = self.session.request(method, url, params=params, data=payload, headers=headers, timeout=15)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"API request to {url} failed: {e}")
            if e.response is not None:
                logger.error(f"Response Body: {e.response.text}")
            raise

    def get_products(self) -> List[Dict]:
        return self.make_request("GET", "/v2/products")

    def get_positions(self) -> List[Dict]:
        return self.make_request("GET", "/v2/positions")

    def place_order(self, order: Order) -> Dict:
        data = { "product_symbol": order.product_symbol, "size": order.size, "side": order.side, "order_type": order.order_type }
        if order.limit_price: data["limit_price"] = order.limit_price
        return self.make_request("POST", "/v2/orders", data=data)

    # --- THIS IS THE CRITICAL NEW FUNCTION FOR YOUR AI PLAN ---
    def get_historical_candles(self, symbol: str, resolution: str = "60", limit: int = 200) -> List[Dict]:
        """ Fetches historical candle data from Delta Exchange. Resolution in seconds. """
        path = f"/v2/history/candles/{symbol}/{resolution}"
        params = {"limit": limit}
        # Note: This endpoint does not require authentication on Delta Exchange
        # We can make a simpler request for this specific case.
        url = f"{self.base_url}{path}"
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            return response.json().get('result', [])
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get historical candles for {symbol}: {e}")
            raise

# --- WebSocket Manager and Trading System (Your existing code is good, no major changes needed for the AI plan) ---
# ... [Your WebSocketManager class]
# ... [Your TradingStrategy classes]
# ... [Your BacktestEngine class]
# ... [Your TradingSystem class]

class WebSocketManager:
    # Your existing WebSocketManager code is fine for the backend's internal strategies.
    # It does not affect the frontend-driven AI plan.
    pass # Placeholder for brevity, use your full class

class TradingSystem:
    # Your existing TradingSystem code is fine. It correctly initializes the API client.
    def __init__(self):
        self.api_client = DeltaExchangeAPI(DELTA_API_KEY, DELTA_API_SECRET, BASE_URL)
        # ... rest of your init
        self.is_running = True # Simplified for this example
    def get_system_status(self):
        return { "is_running": self.is_running, "environment": ENVIRONMENT }

# --- Initialize trading system ---
trading_system = TradingSystem()

# --- Authentication decorator ---
def ai_studio_auth_required(f: Callable) -> Callable:
    @wraps(f)
    def decorated_function(*args: Any, **kwargs: Any) -> Any:
        if not AI_STUDIO_SECRET:
            logger.critical("AI_STUDIO_SECRET is not set. API is insecure.")
            return jsonify({"error": "Server configuration error"}), 500
        
        provided_secret = request.headers.get('X-AI-Studio-Secret')
        if provided_secret == AI_STUDIO_SECRET:
            return f(*args, **kwargs)
        else:
            logger.warning(f"Unauthorized access attempt to {request.path}")
            return jsonify({"error": "Unauthorized"}), 401
    return decorated_function


# --- CORE API ENDPOINTS FOR YOUR AI FRONTEND ---

@app.route('/api/health', methods=['GET'])
def health_check():
    """ Public endpoint to check if the API is alive. """
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})

@app.route('/api/status', methods=['GET'])
@ai_studio_auth_required
def api_status():
    """ Check the status of the trading system and its connection. """
    return jsonify(trading_system.get_system_status())

@app.route('/api/products', methods=['GET'])
@ai_studio_auth_required
def get_products():
    """ Get all available products/symbols for trading. """
    try:
        return jsonify(trading_system.api_client.get_products())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- THIS IS THE CRITICAL NEW ENDPOINT FOR YOUR AI PLAN ---
@app.route('/api/historical-data/<symbol>', methods=['GET'])
@ai_studio_auth_required
def get_historical_data_api(symbol: str):
    """
    Provides historical OHLCV data for a given symbol, used by the frontend for AI analysis.
    Query Params: ?limit=200&resolution=60 (limit: 1-1000, resolution: in seconds e.g., 60, 300, 3600)
    """
    try:
        limit = request.args.get('limit', 200, type=int)
        resolution = request.args.get('resolution', '60', type=str)
        # Add validation and caps for security
        limit = min(max(1, limit), 1000)
        
        result = trading_system.api_client.get_historical_candles(symbol.upper(), resolution, limit)
        return jsonify(result)
    except requests.exceptions.HTTPError as e:
        if e.response and e.response.status_code == 404:
            return jsonify({"error": f"Symbol '{symbol}' not found"}), 404
        return jsonify({"error": f"Error from exchange API: {str(e)}"}), 502 # 502 Bad Gateway
    except Exception as e:
        logger.exception(f"Error getting historical data for {symbol}:")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route('/api/orders', methods=['POST'])
@ai_studio_auth_required
def place_order_api():
    """ Places a new order. The AI frontend calls this after analysis. """
    try:
        data = request.get_json()
        if not all(k in data for k in ['product_symbol', 'size', 'side']):
            return jsonify({"error": "Missing required fields: product_symbol, size, side"}), 400
        
        order = Order(
            product_symbol=data['product_symbol'],
            size=int(data['size']),
            side=data['side'],
            order_type=data.get('order_type', 'market_order'),
            limit_price=data.get('limit_price')
        )
        result = trading_system.api_client.place_order(order)
        return jsonify(result)
    except Exception as e:
        logger.exception("Error placing order:")
        return jsonify({"error": str(e)}), 500

@app.route('/api/positions', methods=['GET'])
@ai_studio_auth_required
def get_positions():
    """ Get current open positions. """
    try:
        return jsonify(trading_system.api_client.get_positions())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- Error Handlers ---
@app.errorhandler(404)
def not_found_error(error):
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(500)
def internal_error(error):
    logger.exception("Internal server error caught by handler:")
    return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    # Check for critical environment variables on startup
    if not all([DELTA_API_KEY, DELTA_API_SECRET, AI_STUDIO_SECRET]):
        logger.critical("CRITICAL ERROR: One or more environment variables (DELTA_API_KEY, DELTA_API_SECRET, AI_STUDIO_SECRET) are missing.")
        # In a real scenario, you might exit here. For Render, it will just log the error.
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 10000)))
