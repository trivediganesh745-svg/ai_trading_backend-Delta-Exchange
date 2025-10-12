import os
import requests
from flask import Flask, request, jsonify
from delta_rest_client import DeltaRestClient, OrderType, TimeInForce

# --- Application Setup ---
app = Flask(__name__)

# --- Configuration ---
# Load credentials and settings from environment variables for security
# For production, use 'https://api.delta.exchange'
DELTA_BASE_URL = os.getenv('DELTA_BASE_URL', 'https://api.delta.exchange')
DELTA_API_KEY = os.getenv('DELTA_API_KEY')
DELTA_API_SECRET = os.getenv('DELTA_API_SECRET')

# --- Basic Validation ---
if not DELTA_API_KEY or not DELTA_API_SECRET:
    raise ValueError("FATAL: DELTA_API_KEY and DELTA_API_SECRET environment variables must be set.")

# --- Initialize Delta Client ---
# This client will be reused across all requests
try:
    delta_client = DeltaRestClient(
        base_url=DELTA_BASE_URL,
        api_key=DELTA_API_KEY,
        api_secret=DELTA_API_SECRET,
        raise_for_status=True  # Raise exceptions on API errors
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

# <<< FIX: ADD THIS HEALTH CHECK ENDPOINT >>>
@app.route('/api/health')
def health_check():
    """
    Health check endpoint for the hosting platform (e.g., Render).
    Returns a 200 OK status to indicate the service is running.
    """
    return jsonify({"status": "healthy"}), 200
# <<< END OF FIX >>>

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

@app.route('/order', methods=['POST'])
def place_order():
    """
    Place a new order.
    Requires a JSON body with:
    {
        "product_id": 84,
        "size": 10,
        "side": "buy",
        "limit_price": 8800
    }
    """
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
            time_in_force=TimeInForce.GTC # Or make this part of the request
        )
        return jsonify(new_order), 201  # 201 Created
    except requests.exceptions.HTTPError as e:
        return api_error(f"Failed to place order: {e.response.text}", e.response.status_code)
    except Exception as e:
        return api_error(str(e), 500)

@app.route('/order/<int:product_id>/<order_id>', methods=['DELETE'])
def cancel_order(product_id, order_id):
    """Cancel a specific order by its ID."""
    try:
        cancelled_order = delta_client.cancel_order(product_id, order_id)
        return jsonify(cancelled_order), 200
    except requests.exceptions.HTTPError as e:
        return api_error(f"Failed to cancel order: {e.response.text}", e.response.status_code)
    except Exception as e:
        return api_error(str(e), 500)

@app.route('/leverage', methods=['POST'])
def set_leverage():
    """
    Set leverage for a product.
    Requires a JSON body with:
    {
        "product_id": 84,
        "leverage": 5
    }
    """
    data = request.get_json()
    if not data:
        return api_error("Invalid JSON body.")

    if 'product_id' not in data or 'leverage' not in data:
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
    # Use 0.0.0.0 to make it accessible on the network
    # The port can be configured via the PORT environment variable (common on hosting platforms)
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
