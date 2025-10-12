web: gunicorn -w 1 -b 0.0.0.0:${PORT:-5000} app:app --preload
worker: python -c "from app import initialize_system; initialize_system(); import time; while True: time.sleep(3600)"
gunicorn --worker-class geventwebsocket.gunicorn.workers.GeckoWebSocketWorker -w 1 app:app
