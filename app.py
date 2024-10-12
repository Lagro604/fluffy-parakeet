import os
import json
import requests
import websocket
import threading
import logging
from flask import Flask, request
from collections import defaultdict

app = Flask(__name__)

# 로그 설정
logging.basicConfig(level=logging.DEBUG)

# 환경변수에서 텔레그램 토큰 및 채팅 ID 가져오기
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

# 중복 메시지를 방지하기 위한 딕셔너리
last_messages = defaultdict(str)

# 업비트 웹소켓 URL
UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"
# 바이낸스 웹소켓 URL
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws"

# 거래 조건 정의
UPBIT_CONDITIONS = {
    'default': 20000000,  # 기본 체결 금액 (2000만원)
    'high_value': 70000000,  # BTC, SOL, ETH, SHIB, DOGE에 대한 체결 금액 (7000만원)
}
BINANCE_CONDITION = 200000000  # 바이낸스 체결 금액 (2억원)

# 업비트 웹소켓 처리 함수
def on_upbit_message(ws, message):
    try:
        data = json.loads(message)
        app.logger.info(f"업비트 메시지 수신: {data}")  # 수신된 데이터 로그
        if 'trade_price' in data:
            price = data['trade_price']
            volume = data['trade_volume']
            symbol = data['market']

            # 매수/매도 판단 (체결금액 계산)
            trade_value = price * volume
            if symbol in ['KRW-BTC', 'KRW-SOL', 'KRW-ETH', 'KRW-SHIB', 'KRW-DOGE']:
                condition = UPBIT_CONDITIONS['high_value']
            else:
                condition = UPBIT_CONDITIONS['default']

            # 체결 금액이 조건을 초과할 때만 메시지 전송
            if trade_value >= condition:
                message = f"업비트 알림: {symbol} - 체결 금액: {trade_value} 원, 전일대비: {data.get('signed_change_price', 0)}"
                send_telegram_message(message)
    except Exception as e:
        app.logger.error(f"업비트 메시지 처리 중 오류 발생: {e}")

# 바이낸스 웹소켓 처리 함수
def on_binance_message(ws, message):
    try:
        data = json.loads(message)
        app.logger.info(f"바이낸스 메시지 수신: {data}")  # 수신된 데이터 로그
        if 'e' in data and data['e'] == 'aggTrade':
            price = float(data['p'])
            quantity = float(data['q'])
            symbol = data['s']

            # 체결 금액 계산
            trade_value = price * quantity
            if trade_value >= BINANCE_CONDITION:
                message = f"바이낸스 알림: {symbol} - 체결 금액: {trade_value} 원"
                send_telegram_message(message)
    except Exception as e:
        app.logger.error(f"바이낸스 메시지 처리 중 오류 발생: {e}")

# 텔레그램 메시지 전송 함수
def send_telegram_message(message):
    if message not in last_messages.values():
        last_messages[message] = message
        try:
            response = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data={
                'chat_id': CHAT_ID,
                'text': message
            })
            app.logger.info(f"텔레그램 메시지 전송: {message} (상태 코드: {response.status_code})")
        except Exception as e:
            app.logger.error(f"텔레그램 메시지 전송 중 오류 발생: {e}")

# 업비트 및 바이낸스 웹소켓 연결
def start_upbit_ws():
    upbit_ws = websocket.WebSocketApp(UPBIT_WS_URL,
                                      on_message=on_upbit_message)
    upbit_ws.run_forever()

def start_binance_ws():
    binance_ws = websocket.WebSocketApp(BINANCE_WS_URL,
                                         on_message=on_binance_message)
    binance_ws.run_forever()

@app.route('/webhook', methods=['POST'])
def webhook():
    # 웹훅으로 수신한 요청 처리
    app.logger.info(f"웹훅 요청 수신: {request.json}")  # 웹훅 요청 로그
    return 'Webhook received!', 200

if __name__ == '__main__':
    # 웹소켓 스레드 시작
    threading.Thread(target=start_upbit_ws).start()
    threading.Thread(target=start_binance_ws).start()
    # Flask 앱 실행
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
