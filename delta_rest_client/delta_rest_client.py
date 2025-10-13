# delta_rest_client.py

import time
import hmac
import hashlib
import json
import requests
from enum import Enum
from decimal import Decimal, ROUND_HALF_UP

# --- Helper Enums ---
class OrderType(Enum):
    """Enumeration for order types."""
    MARKET = 'market_order'
    LIMIT = 'limit_order'
    STOP_MARKET = 'stop_market_order'
    STOP_LIMIT = 'stop_limit_order'
    BRACKET = 'bracket_order'
    MODIFY = 'modify_order'

class TimeInForce(Enum):
    """Enumeration for time in force options."""
    GTC = 'good_till_cancel'
    FOK = 'fill_or_kill'
    IOC = 'immediate_or_cancel'

# --- Missing Helper Functions (Now Included) ---

def create_order_format(product_id: int, size: int, side: str, limit_price: str, **kwargs) -> dict:
    """
    Creates a dictionary payload for placing a new order.
    This function is included for compatibility with other parts of your project.
    """
    order_payload = {
        'product_id': product_id,
        'order_type': OrderType.LIMIT.value,
        'size': size,
        'side': side,
        'limit_price': str(limit_price),
        'time_in_force': TimeInForce.GTC.value
    }
    order_payload.update(kwargs)
    return order_payload

def cancel_order_format(product_id: int) -> dict:
    """
    Creates a dictionary payload for cancelling an order.
    Delta's API requires the product_id in the body for cancellation.
    This function is included for compatibility.
    """
    return {'product_id': product_id}

def round_by_tick_size(price: float, tick_size: float) -> str:
    """
    Rounds a price to the nearest valid tick size for the exchange.
    Example: round_by_tick_size(29000.7, 0.5) -> "29000.5"
    This function is included for compatibility.
    """
    tick_size_decimal = Decimal(str(tick_size))
    price_decimal = Decimal(str(price))
    rounded_price = (price_decimal / tick_size_decimal).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * tick_size_decimal
    return str(rounded_price)


# --- Main Client Class (Corrected and Improved) ---
class DeltaRestClient:
    """A robust REST client for interacting with the Delta Exchange API."""

    def __init__(self, base_url, api_key, api_secret, raise_for_status=False):
        if base_url.endswith('/'):
            base_url = base_url[:-1]
        self.base_url = base_url
        self.api_key = api_key
        self.api_secret = api_secret
        self.raise_for_status = raise_for_status
        self.session = requests.Session()

    def _create_signature(self, method, path, body, timestamp):
        string_to_sign = f"{method.upper()}{path}{timestamp}{body}"
        return hmac.new(self.api_secret.encode('utf-8'), string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()

    def _request(self, method, path, params=None, data=None, authenticated=True):
        url = self.base_url + path
        headers = {'Accept': 'application/json'}
        body_str = json.dumps(data) if data else ''

        if authenticated:
            timestamp = str(int(time.time()) + 60)
            signature = self._create_signature(method, path, body_str, timestamp)
            headers['api-key'] = self.api_key
            headers['timestamp'] = timestamp
            headers['signature'] = signature

        if method.upper() in ['POST', 'PUT', 'DELETE']:
            headers['Content-Type'] = 'application/json'

        try:
            response = self.session.request(
                method, url, params=params, data=body_str.encode('utf-8'), headers=headers, timeout=15
            )
            if self.raise_for_status:
                response.raise_for_status()
            if response.status_code == 204:
                return {"status": "success", "message": "Request processed successfully."}
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise e
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Network error communicating with Delta API: {e}") from e

    # --- Market Data Endpoints ---
    def get_historical_candles(self, product_id, resolution, start=None, end=None):
        """
        Fetches historical candle (OHLCV) data for a product.
        """
        params = {'resolution': resolution}
        if start: params['start'] = start
        if end: params['end'] = end
        # CORRECTED PATH: Changed from "/products/{product_id}/candles" to "/history/candles/{product_id}"
        return self._request('GET', f"/history/candles/{product_id}", params=params, authenticated=False)

    def get_l2_orderbook(self, product_id, depth=20):
        """
        Fetches the Level 2 order book for a product.
        """
        return self._request('GET', f"/products/{product_id}/orderbook", params={'depth': depth}, authenticated=False)
        
    def get_ticker(self, product_id):
        """
        Fetches the ticker for a specific product.
        """
        return self._request('GET', f"/products/{product_id}/ticker", authenticated=False)

    def get_product(self, product_id):
        """
        Fetches details for a specific product.
        """
        return self._request('GET', f'/products/{product_id}', authenticated=False)

    # --- Account & Position Management Endpoints ---
    def get_position(self, product_id):
        """
        Fetches the user's position for a specific product.
        """
        return self._request('GET', '/positions', params={'product_id': product_id})

    def get_balances(self, asset_id):
        """
        Fetches the user's wallet balance for a specific asset.
        """
        return self._request('GET', f'/wallets/{asset_id}')

    def set_leverage(self, product_id, leverage):
        """
        Sets the leverage for a specific product.
        """
        return self._request('POST', '/positions/leverage', data={'product_id': product_id, 'leverage': leverage})

    # --- Order Management Endpoints ---
    def place_order(self, **kwargs):
        """
        Places a new order.
        """
        return self._request('POST', '/orders', data=kwargs)
        
    def get_order(self, product_id, order_id):
        """
        Retrieves a specific order by its ID.
        """
        return self._request('GET', f'/orders/{order_id}', params={'product_id': product_id})

    def cancel_order(self, product_id, order_id):
        """
        Cancels a specific order.
        """
        data = cancel_order_format(product_id) # Using the helper function for consistency
        return self._request('DELETE', f'/orders/{order_id}', data=data)

    def get_orders(self, query=None):
        """
        Fetches a list of orders based on a query.
        """
        return self._request('GET', '/orders', params=query)

    def get_live_orders(self, query=None):
        """
        Fetches all live (open or pending) orders.
        """
        if query is None: query = {}
        query['states'] = 'open,pending'
        return self.get_orders(query=query)
