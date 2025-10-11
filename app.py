import os
import hmac
import hashlib
import json
import time
import threading
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

# Delta Exchange credentials
DELTA_API_KEY = os.getenv("DELTA_API_KEY")
DELTA_API_SECRET = os.getenv("DELTA_API_SECRET")
DELTA_BASE_URL = "https://api.delta.exchange/v2"

# ==========================
# Signature generation
# ==========================
def generate_signature(payload: str):
    return hmac.new(
        DELTA_API_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

# ==========================
# Health check / login
# ==========================
@app.route("/login", methods=["POST"])
def login():
    if DELTA_API_KEY and DELTA_API_SECRET:
        return jsonify({"status": "success", "message": "API key configured"})
    return jsonify({"status": "error", "message": "API key/secret missing"}), 400

@app.route("/test")
def test_connection():
    try:
        r = requests.get(f"{DELTA_BASE_URL}/products")
        r.raise_for_status()
        return jsonify({"status": "success", "data": r.json()})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# ==========================
# Place order
# ==========================
@app.route("/order", methods=["POST"])
def place_order():
    try:
        data = request.json
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400
        body = json.dumps(data)
        signature = generate_signature(body)
        headers = {
            "X-Delta-API-Key": DELTA_API_KEY,
            "X-Delta-Signature": signature,
            "Content-Type": "application/json"
        }
        response = requests.post(f"{DELTA_BASE_URL}/orders", headers=headers, data=body, timeout=10)
        response.raise_for_status()
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
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400
        body = json.dumps(data)
        signature = generate_signature(body)
        headers = {
            "X-Delta-API-Key": DELTA_API_KEY,
            "X-Delta-Signature": signature,
            "Content-Type": "application/json"
        }
        response = requests.post(f"{DELTA_BASE_URL}/orders/cancel", headers=headers, data=body, timeout=10)
        response.raise_for_status()
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ==========================
# GET API endpoint
# ==========================
@app.route("/api/<path:endpoint>", methods=["GET"])
def get_api(endpoint):
    try:
        payload = ""
        signature = generate_signature(payload)
        headers = {
            "X-Delta-API-Key": DELTA_API_KEY,
            "X-Delta-Signature": signature,
            "Content-Type": "application/json"
        }
        url = f"{DELTA_BASE_URL}/{endpoint}"
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# ==========================
# Advanced WebSocket
# ==========================
clients = set()
clients_lock = threading.Lock()

def broadcast(message):
    with clients_lock:
        for ws in clients.copy():
            try:
                ws.send(message)
            except:
                clients.remove(ws)

def fetch_live_data():
    while True:
        try:
            response = requests.get(f"{DELTA_BASE_URL}/tickers", timeout=10)
            response.raise_for_status()
            data = response.json()
            broadcast(json.dumps(data))
        except Exception as e:
            print("Live data fetch error:", e)
        time.sleep(2)

threading.Thread(target=fetch_live_data, daemon=True).start()

@sock.route("/ws")
def websocket(ws):
    with clients_lock:
        clients.add(ws)
    try:
        while True:
            msg = ws.receive()
            if msg:
                ws.send(f"Received: {msg}")
    except Exception as e:
        print("WebSocket error:", e)
    finally:
        with clients_lock:
            clients.discard(ws)

# ==========================
# Backtesting simulation
# ==========================
@app.route("/backtest", methods=["POST"])
def backtest():
    try:
        data = request.json
        symbol = data.get("symbol")
        start_date = data.get("start_date")
        end_date = data.get("end_date")
        # Placeholder simulation logic
        result = {
            "symbol": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "profit": 0,
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
