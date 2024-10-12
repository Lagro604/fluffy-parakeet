import os
import json
import requests
import asyncio
import websockets
from flask import Flask
from threading import Thread
import time
import logging

app = Flask(__name__)

# 환경 변수에서 토큰과 채팅 ID를 가져옵니다.
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# 중복 메시지 방지를 위한 상태 저장
last_messages = {
    'upbit': None,
    'binance': None,
}

# 텔레그램 메시지 전송 함수
def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        'chat_id': CHAT_ID,
        'text': message,
        'parse_mode': 'Markdown'
    }
    requests.post(url, json=payload)

# 업비트 웹소켓
async def upbit_websocket():
    uri = "wss://api.upbit.com/websocket/v1"
    async with websockets.connect(uri) as websocket:
        # 요청 데이터 생성
        request_data = [
            {"ticket": "test"},
            {"type": "transaction", "codes": ["KRW-BTC", "KRW-SOL", "KRW-ETH", "KRW-SHIB", "KRW-DOGE"]},
            {"type": "orderbook", "codes": ["KRW-BTC"]}
        ]
        await websocket.send(json.dumps(request_data))

        while True:
            response = await websocket.recv()
            data = json.loads(response)
            handle_upbit_message(data)

# 바이낸스 웹소켓
async def binance_websocket():
    uri = "wss://stream.binance.com:9443/ws"
    async with websockets.connect(uri) as websocket:
        # 요청 데이터 생성 (선물 거래를 위한 예시)
        # 거래대금 상위 100개 코인 정보를 구독
        await websocket.send(json.dumps({
            "method": "SUBSCRIBE",
            "params": [
                "btcusdt@trade",
                # 여기에 다른 거래쌍 추가
            ],
            "id": 1
        }))

        while True:
            response = await websocket.recv()
            data = json.loads(response)
            handle_binance_message(data)

# 업비트 메시지 처리
def handle_upbit_message(data):
    global last_messages

    # 거래 데이터 예시 처리
    if 'price' in data and 'ask_price' in data:
        price = float(data['price'])
        volume = float(data['trade_volume'])
        coin_type = data['market']
        
        # 업비트 메시지 조건 체크
        if (coin_type in ["KRW-BTC", "KRW-SOL", "KRW-ETH", "KRW-SHIB", "KRW-DOGE"] and volume >= 70000000) or \
           (coin_type not in ["KRW-BTC", "KRW-SOL", "KRW-ETH", "KRW-SHIB", "KRW-DOGE"] and volume >= 20000000):
            message = f"{coin_type} - 가격: {price} / 거래량: {volume}"
            if message != last_messages['upbit']:
                send_telegram_message(message)
                last_messages['upbit'] = message

# 바이낸스 메시지 처리
def handle_binance_message(data):
    global last_messages

    # 거래 데이터 예시 처리
    if 's' in data and 'p' in data and 'q' in data:
        price = float(data['p'])
        volume = float(data['q'])
        coin_type = data['s']

        # 바이낸스 메시지 조건 체크
        if volume >= 200000000:  # 2억 이상
            message = f"{coin_type} - 가격: {price} / 거래량: {volume}"
            if message != last_messages['binance']:
                send_telegram_message(message)
                last_messages['binance'] = message

# Flask 라우트
@app.route('/')
def index():
    return "Flask application is running!"

# 웹소켓 스레드 실행 함수
def start_websockets():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(asyncio.gather(upbit_websocket(), binance_websocket()))

if __name__ == '__main__':
    websocket_thread = Thread(target=start_websockets)
    websocket_thread.start()
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 8080)))

