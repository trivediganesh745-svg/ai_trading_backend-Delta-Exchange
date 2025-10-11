import os
import time
import hmac
import hashlib
import json
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sock import Sock
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)
sock = Sock(app)

DELTA_API_KEY = os.getenv("DELTA_API_KEY")
DELTA_API_SECRET = os.getenv("DELTA_API_SECRET")
DELTA_BASE_URL = "https://api.delta.exchange/v2"  # v2 endpoints

# --------------------------
# Helper for signing requests
# --------------------------
def generate_signature(payload):
    return hmac.new(
        DELTA_API_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

# --------------------------
# Automated login endpoint
# --------------------------
@app.route("/login", methods=["POST"])
def login():
    # Delta API uses API Key + Secret for all requests, no login token needed
    return jsonify({"status": "success", "message": "API key ready to use"})

# --------------------------
# Place order endpoint
# --------------------------
@app.route("/order", methods=["POST"])
def place_order():
    try:
        data = request.json
        endpoint = "/orders"
        url = DELTA_BASE_URL + endpoint
        body = json.dumps(data)
        signature = generate_signature(body)
        headers = {
            "X-Delta-API-Key": DELTA_API_KEY,
            "X-Delta-Signature": signature,
            "Content-Type": "application/json"
        }
        response = requests.post(url, headers=headers, data=body)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --------------------------
# Generic GET request
# --------------------------
@app.route("/api/<path:endpoint>", methods=["GET"])
def get_api(endpoint):
    try:
        url = f"{DELTA_BASE_URL}/{endpoint}"
        response = requests.get(url)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --------------------------
# WebSocket placeholder
# --------------------------
@sock.route("/ws")
def websocket(ws):
    while True:
        data = ws.receive()
        ws.send(f"Echo: {data}")

# --------------------------
# Backtesting placeholder
# --------------------------
@app.route("/backtest", methods=["POST"])
def backtest():
    data = request.json
    # Placeholder: implement backtesting with Delta historical crypto data
    return jsonify({"status": "success", "result": {"symbol": data.get("symbol"), "profit": 0}})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
