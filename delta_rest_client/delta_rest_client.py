# delta_rest_client.py -- VERIFY THIS IS YOUR FILE'S CONTENT

import time
import hmac
import hashlib
import json
import requests
from enum import Enum

class OrderType(Enum):
    MARKET = 'market_order'
    LIMIT = 'limit_order'
    STOP_MARKET = 'stop_market_order'
    STOP_LIMIT = 'stop_limit_order'
    BRACKET = 'bracket_order'
    MODIFY = 'modify_order'

class TimeInForce(Enum):
    GTC = 'good_till_cancel'
    FOK = 'fill_or_kill'
    IOC = 'immediate_or_cancel'

class DeltaRestClient:
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
        return hmac.new(
            self.api_secret.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

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
                return {"status": "success"}
            return response.json()
        except requests.exceptions.HTTPError as e:
            raise e
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Network error communicating with Delta API: {e}") from e

    # --- Market Data Endpoints ---
    def get_historical_candles(self, product_id, resolution, start=None, end=None):
        """ THIS IS THE METHOD THAT IS MISSING ON YOUR SERVER """
        params = {'resolution': resolution}
        if start: params['start'] = start
        if end: params['end'] = end
        return self._request('GET', f"/products/{product_id}/candles", params=params, authenticated=False)

    def get_l2_orderbook(self, product_id, depth=20):
        return self._request('GET', f"/products/{product_id}/orderbook", params={'depth': depth}, authenticated=False)
        
    def get_ticker(self, product_id):
        return self._request('GET', f"/products/{product_id}/ticker", authenticated=False)

    def get_product(self, product_id):
        return self._request('GET', f'/products/{product_id}', authenticated=False)

    # --- Account & Position Management Endpoints ---
    def get_position(self, product_id):
        return self._request('GET', '/positions', params={'product_id': product_id})

    def get_balances(self, asset_id):
        return self._request('GET', f'/wallets/{asset_id}')

    def set_leverage(self, product_id, leverage):
        return self._request('POST', '/positions/leverage', data={'product_id': product_id, 'leverage': leverage})

    # --- Order Management Endpoints ---
    def place_order(self, **kwargs):
        return self._request('POST', '/orders', data=kwargs)
        
    def get_order(self, product_id, order_id):
        return self._request('GET', f'/orders/{order_id}', params={'product_id': product_id})

    def cancel_order(self, product_id, order_id):
        return self._request('DELETE', f'/orders/{order_id}', data={'product_id': product_id})

    def get_orders(self, query=None):
        return self._request('GET', '/orders', params=query)

    def get_live_orders(self, query=None):
        if query is None: query = {}
        query['states'] = 'open,pending'
        return self.get_orders(query=query)
