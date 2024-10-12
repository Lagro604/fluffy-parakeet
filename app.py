import os
import json
import requests
import time
import logging
from flask import Flask, request
from binance.client import Client
from binance.websockets import BinanceSocketManager
from flask import jsonify

app = Flask(__name__)

# Logging 설정
logging.basicConfig(level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

@app.route('/')
def home():
    return "Hello, World!"

# 여기에 추가적인 라우트를 추가합니다.
@app.route('/test', methods=['GET'])
def test():
    return 'Test route is working!'

def send_message_to_telegram(message):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    payload = {
        'chat_id': CHAT_ID,
        'text': message,
    }
    requests.post(url, json=payload)

def process_trade_data(data):
    # 트레이드 데이터를 처리하는 로직을 여기에 추가하세요.
    pass

def start_socket():
    bsm = BinanceSocketManager(client)
    conn_key = bsm.start_trade_socket('btcusdt', process_trade_data)
    bsm.start()

if __name__ == "__main__":
    start_socket()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
