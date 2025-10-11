import os
import hmac
import hashlib
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sock import Sock
from dotenv import load_dotenv

# Load env vars locally (optional)
load_dotenv()

app = Flask(__name__)
CORS(app)
sock = Sock(app)

DELTA_API_KEY = os.getenv("DELTA_API_KEY")
DELTA_API_SECRET = os.getenv("DELTA_API_SECRET")
DELTA_BASE_URL = "https://api.delta.exchange/v2"

# ==========================
# Signature generation
# ==========================
def generate_signature(payload: str):
    """
    Delta Exchange requires HMAC SHA256 signature of payload.
    For GET requests, payload is empty string.
    """
    return hmac.new(
        DELTA_API_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

# ==========================
# Test connection
# ==========================
@app.route("/test")
def test_connection():
    try:
        r = requests.get(f"{DELTA_BASE_URL}/products")
        return jsonify({"status": "success", "data": r.json()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# ==========================
# Login / Health check
# ==========================
@app.route("/login", methods=["POST"])
def login():
    if DELTA_API_KEY and DELTA_API_SECRET:
        return jsonify({"status": "success", "message": "API key is configured"})
    else:
        return jsonify({"status": "error", "message": "API key/secret missing"}), 400

# ==========================
# Place order
# ==========================
@app.route("/order", methods=["POST"])
def place_order():
    try:
        data = request.json
        body = json.dumps(data)
        signature = generate_signature(body)
        headers = {
            "X-Delta-API-Key": DELTA_API_KEY,
            "X-Delta-Signature": signature,
            "Content-Type": "application/json"
        }
        response = requests.post(f"{DELTA_BASE_URL}/orders", headers=headers, data=body)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ==========================
# GET API endpoint
# ==========================
@app.route("/api/<path:endpoint>", methods=["GET"])
def get_api(endpoint):
    """
    Handles public and private GET endpoints.
    For private endpoints, signature is generated for empty payload.
    """
    try:
        payload = ""
        signature = generate_signature(payload)
        headers = {
            "X-Delta-API-Key": DELTA_API_KEY,
            "X-Delta-Signature": signature,
            "Content-Type": "application/json"
        }
        url = f"{DELTA_BASE_URL}/{endpoint}"
        response = requests.get(url, headers=headers)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ==========================
# Cancel order
# ==========================
@app.route("/order/cancel", methods=["POST"])
def cancel_order():
    try:
        data = request.json
        body = json.dumps(data)
        signature = generate_signature(body)
        headers = {
            "X-Delta-API-Key": DELTA_API_KEY,
            "X-Delta-Signature": signature,
            "Content-Type": "application/json"
        }
        response = requests.post(f"{DELTA_BASE_URL}/orders/cancel", headers=headers, data=body)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ==========================
# WebSocket Echo (for testing / future live data)
# ==========================
@sock.route("/ws")
def websocket(ws):
    while True:
        data = ws.receive()
        ws.send(f"Echo: {data}")

# ==========================
# Backtesting simulation
# ==========================
@app.route("/backtest", methods=["POST"])
def backtest():
    try:
        data = request.json
        # Placeholder simulation logic
        symbol = data.get("symbol")
        start_date = data.get("start_date")
        end_date = data.get("end_date")
        result = {
            "symbol": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "profit": 0,  # replace with real calculation later
            "trades": []
        }
        return jsonify({"status": "success", "result": result})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ==========================
# Run server
# ==========================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
