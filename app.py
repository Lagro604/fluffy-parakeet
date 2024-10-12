import os
import json
import requests
import threading
import logging
import time
from flask import Flask, request
from collections import defaultdict

app = Flask(__name__)

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 환경변수에서 텔레그램 토큰 및 채팅 ID 가져오기
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

# 중복 메시지를 방지하기 위한 딕셔너리
last_messages = defaultdict(str)

# 거래 조건 정의
UPBIT_CONDITIONS = {
    'default': 20000000,  # 기본 체결 금액 (2000만원)
    'high_value': 70000000,  # BTC, SOL, ETH, SHIB, DOGE에 대한 체결 금액 (7000만원)
}
BINANCE_CONDITION = 200000000  # 바이낸스 체결 금액 (2억원)

# 업비트 데이터 요청 함수
def fetch_upbit_data():
    url = "https://api.upbit.com/v1/trades/ticks?market=KRW-BTC&count=10"  # 예시로 비트코인 데이터를 가져옴
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    return []

# 바이낸스 데이터 요청 함수
def fetch_binance_data():
    url = "https://api.binance.com/api/v3/aggTrades?symbol=BTCUSDT&limit=10"  # 예시로 비트코인 데이터를 가져옴
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    return []

# 메시지 전송 함수
def send_telegram_message(message):
    if message not in last_messages.values():
        last_messages[message] = message
        response = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={
            'chat_id': CHAT_ID,
            'text': message
        })
        if response.status_code == 200:
            logger.info(f"메시지 전송 성공: {message}")
        else:
            logger.error(f"메시지 전송 실패: {response.status_code}, {response.text}")

# 거래 데이터 확인 및 알림 함수
def check_trades():
    while True:
        # 업비트 거래 확인
        upbit_data = fetch_upbit_data()
        for trade in upbit_data:
            price = trade['trade_price']
            volume = trade['trade_volume']
            trade_value = price * volume
            if trade_value >= UPBIT_CONDITIONS['high_value']:  # 조건 확인
                message = f"업비트 알림: BTC - 체결 금액: {trade_value} 원"
                send_telegram_message(message)

        # 바이낸스 거래 확인
        binance_data = fetch_binance_data()
        for trade in binance_data:
            price = float(trade['p'])
            quantity = float(trade['q'])
            trade_value = price * quantity
            if trade_value >= BINANCE_CONDITION:  # 조건 확인
                message = f"바이낸스 알림: BTC - 체결 금액: {trade_value} 원"
                send_telegram_message(message)

        time.sleep(10)  # 10초 간격으로 반복

@app.route('/webhook', methods=['POST'])
def webhook():
    # 웹훅으로 수신한 요청 처리
    payload = request.json
    logger.info(f"웹훅 요청 수신: {json.dumps(payload, indent=2)}")

    # 특정 필드에 대한 처리 예시
    if 'event_type' in payload and payload['event_type'] == 'trade':
        # 거래 관련 데이터 처리
        trade_data = payload.get('data')
        if trade_data:
            # 예: 거래 알림 전송
            send_telegram_message(f"새 거래 발생: {trade_data}")

    return 'Webhook received!', 200

if __name__ == '__main__':
    # 알림 체크 스레드 시작
    threading.Thread(target=check_trades, daemon=True).start()
    # Flask 앱 실행
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
