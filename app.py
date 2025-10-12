import os
import requests
from flask import Flask, request, jsonify
from delta_rest_client import DeltaRestClient, OrderType, TimeInForce

# --- Application Setup ---
app = Flask(__name__)

# --- Configuration ---
DELTA_BASE_URL = os.getenv('DELTA_BASE_URL', 'https://api.delta.exchange')
DELTA_API_KEY = os.getenv('DELTA_API_KEY')
DELTA_API_SECRET = os.getenv('DELTA_API_SECRET')

# --- Basic Validation ---
if not DELTA_API_KEY or not DELTA_API_SECRET:
    raise ValueError("FATAL: DELTA_API_KEY and DELTA_API_SECRET environment variables must be set.")

# --- Initialize Delta Client ---
try:
    delta_client = DeltaRestClient(
        base_url=DELTA_BASE_URL,
        api_key=DELTA_API_KEY,
        api_secret=DELTA_API_SECRET,
        raise_for_status=True
    )
    print(f"Successfully connected to Delta Exchange at {DELTA_BASE_URL}")
except Exception as e:
    raise ConnectionError(f"Failed to initialize DeltaRestClient: {e}")


# --- API Helper for Error Responses ---
def api_error(message, status_code=400):
    """Creates a standardized JSON error response."""
    return jsonify({"error": message}), status_code

# --- API Endpoints ---

@app.route('/', methods=['GET'])
def index():
    return jsonify({"status": "Delta Exchange API service is running"}), 200

@app.route('/api/health')
def health_check():
    return jsonify({"status": "healthy"}), 200

# --- Market Data Endpoints ---

# <<< NEW ENDPOINT: HISTORICAL DATA >>>
@app.route('/history/candles/<int:product_id>', methods=['GET'])
def get_historical_data(product_id):
    """
    Get historical OHLCV data.
    Query Params:
    - resolution: Required (e.g., '1m', '1h', '1d')
    - start: Optional (timestamp)
    - end: Optional (timestamp)
    """
    resolution = request.args.get('resolution')
    if not resolution:
        return api_error("'resolution' query parameter is required (e.g., '1m', '5m', '1h', '1d').")
    
    start_time = request.args.get('start')
    end_time = request.args.get('end')

    try:
        candles = delta_client.get_historical_candles(
            product_id=product_id,
            resolution=resolution,
            start=start_time,
            end=end_time
        )
        return jsonify(candles), 200
    except requests.exceptions.HTTPError as e:
        return api_error(f"Failed to get historical data: {e.response.text}", e.response.status_code)
    except Exception as e:
        return api_error(str(e), 500)

# <<< NEW ENDPOINT: MARKET DEPTH >>>
@app.route('/orderbook/<int:product_id>', methods=['GET'])
def get_market_depth(product_id):
    """
    Get the order book (market depth).
    Query Params:
    - depth: Optional (number of levels, default is 20)
    """
    depth = request.args.get('depth', default=20, type=int)
    try:
        # Note: L2 order book is the standard market depth view
        orderbook = delta_client.get_l2_orderbook(product_id, depth)
        return jsonify(orderbook), 200
    except requests.exceptions.HTTPError as e:
        return api_error(f"Failed to get order book: {e.response.text}", e.response.status_code)
    except Exception as e:
        return api_error(str(e), 500)

# <<< NEW ENDPOINT: LIVE QUOTES >>>
@app.route('/quotes/live/<int:product_id>', methods=['GET'])
def get_live_quote(product_id):
    """
    Get live ticker/quote data (a snapshot of the current market).
    This is not a real-time stream, but the latest available data.
    """
    try:
        ticker = delta_client.get_ticker(product_id)
        return jsonify(ticker), 200
    except requests.exceptions.HTTPError as e:
        return api_error(f"Failed to get live quote: {e.response.text}", e.response.status_code)
    except Exception as e:
        return api_error(str(e), 500)

# --- Existing and Improved Endpoints ---

@app.route('/product/<int:product_id>', methods=['GET'])
def get_product(product_id):
    """Get details for a specific product."""
    try:
        product = delta_client.get_product(product_id)
        return jsonify(product), 200
    except requests.exceptions.HTTPError as e:
        return api_error(f"Failed to get product: {e.response.text}", e.response.status_code)
    except Exception as e:
        return api_error(str(e), 500)

@app.route('/position/<int:product_id>', methods=['GET'])
def get_position(product_id):
    """Get your current position for a specific product."""
    try:
        position = delta_client.get_position(product_id)
        return jsonify(position), 200
    except requests.exceptions.HTTPError as e:
        return api_error(f"Failed to get position: {e.response.text}", e.response.status_code)
    except Exception as e:
        return api_error(str(e), 500)
        
@app.route('/balances/<int:asset_id>', methods=['GET'])
def get_balances(asset_id):
    """Get your wallet balance for a specific asset."""
    try:
        wallet = delta_client.get_balances(asset_id)
        return jsonify(wallet), 200
    except requests.exceptions.HTTPError as e:
        return api_error(f"Failed to get balances: {e.response.text}", e.response.status_code)
    except Exception as e:
        return api_error(str(e), 500)

# --- Order Management Endpoints ---

@app.route('/order', methods=['POST'])
def place_order():
    """Place a new order."""
    data = request.get_json()
    if not data:
        return api_error("Invalid JSON body.")
    required_fields = ['product_id', 'size', 'side', 'limit_price']
    if not all(field in data for field in required_fields):
        return api_error(f"Missing required fields. Must include: {', '.join(required_fields)}")
    try:
        new_order = delta_client.place_order(
            product_id=data['product_id'],
            size=data['size'],
            side=data['side'],
            limit_price=data['limit_price'],
            time_in_force=TimeInForce.GTC
        )
        return jsonify(new_order), 201
    except requests.exceptions.HTTPError as e:
        return api_error(f"Failed to place order: {e.response.text}", e.response.status_code)
    except Exception as e:
        return api_error(str(e), 500)

# <<< IMPROVED ENDPOINT: MANAGE A SINGLE ORDER (GET/DELETE) >>>
@app.route('/order/<int:product_id>/<order_id>', methods=['GET', 'DELETE'])
def manage_single_order(product_id, order_id):
    """Get status of a single order or cancel it."""
    try:
        if request.method == 'GET':
            order = delta_client.get_order(product_id, order_id)
            return jsonify(order), 200
        elif request.method == 'DELETE':
            cancelled_order = delta_client.cancel_order(product_id, order_id)
            return jsonify(cancelled_order), 200
    except requests.exceptions.HTTPError as e:
        return api_error(f"Failed to manage order: {e.response.text}", e.response.status_code)
    except Exception as e:
        return api_error(str(e), 500)

@app.route('/orders/live/<int:product_id>', methods=['GET'])
def get_live_orders(product_id):
    """Get all live (open, pending) orders for a specific product."""
    try:
        orders = delta_client.get_live_orders(query={'product_ids': product_id, 'states': 'open,pending'})
        return jsonify(orders), 200
    except requests.exceptions.HTTPError as e:
        return api_error(f"Failed to get live orders: {e.response.text}", e.response.status_code)
    except Exception as e:
        return api_error(str(e), 500)

# <<< NEW ENDPOINT: GET ORDER HISTORY >>>
@app.route('/orders/all/<int:product_id>', methods=['GET'])
def get_all_orders_for_product(product_id):
    """Get all orders (filled, cancelled, etc.) for a specific product."""
    try:
        # The get_orders method without a state filter gets all orders
        all_orders = delta_client.get_orders(query={'product_ids': product_id})
        return jsonify(all_orders), 200
    except requests.exceptions.HTTPError as e:
        return api_error(f"Failed to get all orders: {e.response.text}", e.response.status_code)
    except Exception as e:
        return api_error(str(e), 500)

@app.route('/leverage', methods=['POST'])
def set_leverage():
    """Set leverage for a product."""
    data = request.get_json()
    if not data or 'product_id' not in data or 'leverage' not in data:
        return api_error("Request must include 'product_id' and 'leverage'.")
    try:
        result = delta_client.set_leverage(data['product_id'], data['leverage'])
        return jsonify(result), 200
    except requests.exceptions.HTTPError as e:
        return api_error(f"Failed to set leverage: {e.response.text}", e.response.status_code)
    except Exception as e:
        return api_error(str(e), 500)

# --- Main entry point for running the app ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
