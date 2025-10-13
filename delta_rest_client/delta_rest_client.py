import time
import hmac
import hashlib
import json
import requests
from enum import Enum

# --- Helper Enums ---
# These provide standardized values for order parameters.

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
    GTC = 'good_till_cancel'  # Good Till Cancel
    FOK = 'fill_or_kill'      # Fill Or Kill
    IOC = 'immediate_or_cancel' # Immediate Or Cancel

# --- Main Client Class ---

class DeltaRestClient:
    """
    A REST client for interacting with the Delta Exchange API.
    Handles request signing and provides methods for all major API endpoints.
    """

    def __init__(self, base_url, api_key, api_secret, raise_for_status=False):
        """
        Initializes the DeltaRestClient.

        Args:
            base_url (str): The base URL for the API (e.g., 'https://api.delta.exchange').
            api_key (str): Your Delta Exchange API key.
            api_secret (str): Your Delta Exchange API secret.
            raise_for_status (bool): If True, will raise HTTPError for 4xx/5xx responses.
        """
        self.base_url = base_url
        self.api_key = api_key
        self.api_secret = api_secret
        self.raise_for_status = raise_for_status
        self.session = requests.Session()

    def _create_signature(self, method, path, body, expiry):
        """
        Creates the required signature for authenticated requests.
        
        The signature is a hex-encoded HMAC-SHA256 of the string:
        HTTP_METHOD + URI_PATH + EXPIRY_TIMESTAMP + BODY
        """
        string_to_sign = f"{method.upper()}{path}{expiry}{body}"
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature

    def _request(self, method, path, params=None, data=None, authenticated=True):
        """
        A generic method to make a request to the Delta API.
        
        Args:
            method (str): The HTTP method (GET, POST, PUT, DELETE).
            path (str): The API endpoint path (e.g., '/products').
            params (dict, optional): URL query parameters.
            data (dict, optional): The request body for POST/PUT requests.
            authenticated (bool): Whether the request requires authentication.
            
        Returns:
            dict: The JSON response from the API.
        """
        url = self.base_url + path
        headers = {'Accept': 'application/json'}
        body_str = json.dumps(data) if data else ''

        if authenticated:
            expiry = str(int(time.time()) + 60) # Signature is valid for 60 seconds
            signature = self._create_signature(method, path, body_str, expiry)
            headers['api-key'] = self.api_key
            headers['timestamp'] = expiry
            headers['signature'] = signature

        if method.upper() in ['POST', 'PUT']:
            headers['Content-Type'] = 'application/json'

        try:
            response = self.session.request(
                method, url, params=params, data=body_str.encode('utf-8'), headers=headers, timeout=10
            )
            if self.raise_for_status:
                response.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)
            
            # Handle cases where response might be empty (e.g., successful DELETE)
            if response.status_code == 204:
                return {}
                
            return response.json()
        except requests.exceptions.RequestException as e:
            # Re-raise connection or timeout errors to be handled by the caller
            raise ConnectionError(f"Network error while contacting Delta API: {e}") from e

    # --- Market Data ---

    def get_historical_candles(self, product_id, resolution, start=None, end=None):
        """
        Get historical OHLCV data for a product.
        
        Args:
            product_id (int): The ID of the product.
            resolution (str): The candle resolution (e.g., '1', '5', '15', '60', '240', '1D').
                                Note: The API uses minutes for numbers, '1D' for day.
            start (int, optional): Start timestamp (Unix epoch seconds).
            end (int, optional): End timestamp (Unix epoch seconds).
        
        Returns:
            list: A list of candle data.
        """
        params = {'resolution': resolution}
        if start:
            params['start'] = start
        if end:
            params['end'] = end
        path = f"/products/{product_id}/candles"
        return self._request('GET', path, params=params, authenticated=False)

    def get_l2_orderbook(self, product_id, depth=20):
        """
        Get the Level 2 order book (market depth) for a product.
        
        Args:
            product_id (int): The ID of the product.
            depth (int): The number of levels to retrieve (default 20).
        
        Returns:
            dict: The order book with 'buy' and 'sell' sides.
        """
        path = f"/products/{product_id}/orderbook"
        params = {'depth': depth}
        return self._request('GET', path, params=params, authenticated=False)
        
    def get_ticker(self, product_id):
        """
        Get the 24-hour ticker data for a specific product.
        
        Args:
            product_id (int): The ID of the product.
        
        Returns:
            dict: The ticker data.
        """
        path = f"/products/{product_id}/ticker"
        return self._request('GET', path, authenticated=False)

    def get_product(self, product_id):
        """
        Get details for a specific product.
        
        Args:
            product_id (int): The ID of the product.
        
        Returns:
            dict: The product details.
        """
        return self._request('GET', f'/products/{product_id}', authenticated=False)

    # --- Account & Position Management ---

    def get_position(self, product_id):
        """
        Get your current position for a specific product.
        
        Args:
            product_id (int): The ID of the product.
        
        Returns:
            dict: Your position details.
        """
        return self._request('GET', f'/positions/{product_id}')

    def get_balances(self, asset_id):
        """
        Get your wallet balance for a specific asset.
        
        Args:
            asset_id (int): The ID of the asset.
        
        Returns:
            dict: The wallet balance details.
        """
        # Note: This method corresponds to the `/wallets/{asset_id}` endpoint.
        return self._request('GET', f'/wallets/{asset_id}')

    def set_leverage(self, product_id, leverage):
        """
        Set the leverage for a specific product.
        
        Args:
            product_id (int): The ID of the product.
            leverage (float): The desired leverage.
        
        Returns:
            dict: The result of the operation.
        """
        data = {'product_id': product_id, 'leverage': leverage}
        return self._request('POST', '/positions/leverage', data=data)

    # --- Order Management ---

    def place_order(self, product_id, size, side, limit_price, order_type=OrderType.LIMIT, time_in_force=TimeInForce.GTC, **kwargs):
        """
        Place a new order.
        
        Args:
            product_id (int): The product ID.
            size (int): The order size (number of contracts).
            side (str): 'buy' or 'sell'.
            limit_price (str): The limit price for the order.
            order_type (OrderType): The type of order.
            time_in_force (TimeInForce): The time in force policy.
            **kwargs: Additional optional parameters like stop_price, post_only, etc.
        
        Returns:
            dict: The newly created order.
        """
        data = {
            'product_id': product_id,
            'order_type': order_type.value,
            'size': size,
            'side': side,
            'limit_price': str(limit_price),
            'time_in_force': time_in_force.value
        }
        data.update(kwargs)
        return self._request('POST', '/orders', data=data)
        
    def get_order(self, product_id, order_id):
        """
        Get details of a single order by its ID.
        
        Args:
            product_id (int): The ID of the product the order was placed on.
            order_id (str): The ID of the order.
        
        Returns:
            dict: The order details.
        """
        return self._request('GET', f'/orders/{order_id}', params={'product_id': product_id})

    def cancel_order(self, product_id, order_id):
        """
        Cancel a single order by its ID.
        
        Args:
            product_id (int): The ID of the product.
            order_id (str): The ID of the order to cancel.
        
        Returns:
            dict: The details of the cancelled order.
        """
        data = {'product_id': product_id}
        return self._request('DELETE', f'/orders/{order_id}', data=data)

    def get_orders(self, query=None):
        """
        Get a list of your orders, with optional filtering.
        This is a versatile method for getting live, filled, or all orders.
        
        Args:
            query (dict, optional): A dictionary of query parameters to filter orders.
                                    e.g., {'product_ids': 1, 'states': 'open,pending'}
        
        Returns:
            list: A list of orders matching the query.
        """
        return self._request('GET', '/orders', params=query)

    def get_live_orders(self, query=None):
        """
        Convenience method to get all live (open, pending) orders.
        
        Args:
            query (dict, optional): Additional query parameters.
        
        Returns:
            list: A list of live orders.
        """
        live_states = 'open,pending'
        if query is None:
            query = {}
        
        if 'states' in query:
            # If user provides states, we append ours if not already present
            user_states = set(s.strip() for s in query['states'].split(','))
            if 'open' not in user_states: user_states.add('open')
            if 'pending' not in user_states: user_states.add('pending')
            query['states'] = ','.join(user_states)
        else:
            query['states'] = live_states

        return self.get_orders(query)
