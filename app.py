import os
import hashlib
import asyncio
import logging
import httpx
import websockets
import json
from flask import Flask
from threading import Thread
from time import time
import click
from flask.cli import with_appcontext

app = Flask(__name__)

# 환경 변수에서 설정
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
UPBIT_TRADE_THRESHOLD = 20000000  # 업비트 기본 2천만 원
EXCLUDED_TRADE_THRESHOLD = 70000000  # 제외된 코인은 7천만 원
EXCLUDED_COINS = ['KRW-SOL', 'KRW-ETH', 'KRW-SHIB', 'KRW-DOGE', 'KRW-USDT', 'KRW-XRP']
recent_messages = {}  # 최근 메시지 중복 방지 (메시지 해시 값과 타임스탬프 저장)
MESSAGE_EXPIRATION_TIME = 7200  # 2시간 (7200초) 후에 메시지 해시 값 삭제
logging.basicConfig(level=logging.INFO)  # 로그 레벨 설정

# 바이낸스 선물 상위 100개 구독용
BINANCE_FUTURE_TRADE_THRESHOLD = 200000000  # 2억 원 기준
BINANCE_EXCLUDED_TRADE_THRESHOLD = 500000000  # 제외된 코인은 5억 원
BINANCE_TOP_100_COINS = []  # 상위 100개 코인 목록

# 코인 한글 이름을 저장하는 딕셔너리
coin_name_dict = {}

async def send_telegram_message(message):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    payload = {'chat_id': CHAT_ID, 'text': message}
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload)
        if response.status_code != 200:
            logging.error(f'Error sending message: {response.text}')

def delete_old_hashes():
    """오래된 메시지 해시 값 삭제"""
    current_time = time()
    keys_to_delete = [key for key, timestamp in recent_messages.items() if current_time - timestamp > MESSAGE_EXPIRATION_TIME]
    for key in keys_to_delete:
        del recent_messages[key]
    logging.debug(f"Deleted {len(keys_to_delete)} old messages from recent_messages")

async def get_all_krw_coins():
    """업비트의 모든 KRW 마켓 코인 리스트 가져오기"""
    url = 'https://api.upbit.com/v1/market/all'
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        if response.status_code == 200:
            markets = response.json()
            for market in markets:
                if market['market'].startswith('KRW-'):
                    coin_name_dict[market['market']] = market['korean_name']
            return coin_name_dict
        logging.error(f'Error fetching market data: {response.text}')
        return {}

async def upbit_websocket():
    uri = "wss://api.upbit.com/websocket/v1"
    await get_all_krw_coins()
    subscribe_message = [
        {"ticket": "test"},
        {"type": "ticker", "codes": list(coin_name_dict.keys())},  # 모든 KRW 코인에 대해 티커 구독
        {"type": "trade", "codes": list(coin_name_dict.keys())}    # 모든 KRW 코인에 대해 체결 구독
    ]

    reconnect_attempts = 0  # 재연결 시도 횟수
    max_reconnect_attempts = 5  # 최대 재연결 시도 횟수
    backoff_time = 2  # 초기 백오프 시간 (초)

    while reconnect_attempts < max_reconnect_attempts:
        try:
            async with websockets.connect(uri) as websocket:
                await websocket.send(json.dumps(subscribe_message))
                logging.info("WebSocket connected and subscribed.")
                reconnect_attempts = 0  # 연결 성공 시 재연결 횟수 초기화

                while True:
                    response = await websocket.recv()
                    data = json.loads(response)

                    if data['type'] == 'ticker':
                        market = data['code']
                        korean_name = coin_name_dict.get(market, '알 수 없음')
                        current_price = data['trade_price']
                        change_rate = data['signed_change_rate'] * 100  # 전일 대비 비율

                        message = (
                            f"[업비트] 티커 알림: {market} ({korean_name})\n"
                            f"현재 가격: {current_price:,.0f}원\n"
                            f"전일 대비: {change_rate:.2f}%"
                        )

                        msg_id = hashlib.md5(message.encode()).hexdigest()
                        if msg_id not in recent_messages:
                            await send_telegram_message(message)
                            recent_messages[msg_id] = time()  # 메시지 해시와 타임스탬프 저장
                        delete_old_hashes()  # 오래된 해시값 삭제

                    elif data['type'] == 'trade':
                        market = data['code']
                        korean_name = coin_name_dict.get(market, '알 수 없음')
                        trade_price = data['trade_price']
                        trade_volume = data['trade_volume']
                        trade_value = trade_price * trade_volume
                        trade_type = "매수" if data['ask_bid'] == "BID" else "매도"

                        if (market in EXCLUDED_COINS and trade_value >= EXCLUDED_TRADE_THRESHOLD) or \
                           (market not in EXCLUDED_COINS and trade_value >= UPBIT_TRADE_THRESHOLD):
                            message = (
                                f"[업비트] {trade_type} 알림: {market} ({korean_name})\n"
                                f"체결 가격: {trade_price:,.0f}원\n"
                                f"체결 금액: {trade_value:,.0f}원\n"
                                f"전일 대비: {change_rate:.2f}%"
                            )

                            msg_id = hashlib.md5(message.encode()).hexdigest()
                            if msg_id not in recent_messages:
                                await send_telegram_message(message)
                                recent_messages[msg_id] = time()  # 메시지 해시와 타임스탬프 저장
                            delete_old_hashes()  # 오래된 해시값 삭제

        except (websockets.ConnectionClosed, websockets.WebSocketException) as e:
            logging.error(f"WebSocket error: {e}. Reconnecting in {backoff_time} seconds...")
            await asyncio.sleep(backoff_time)
            reconnect_attempts += 1
            backoff_time = min(backoff_time * 2, 60)  # 백오프 시간 증가, 최대 60초로 제한

        except Exception as e:
            logging.error(f"Unexpected error: {e}. Reconnecting in {backoff_time} seconds...")
            await asyncio.sleep(backoff_time)
            reconnect_attempts += 1
            backoff_time = min(backoff_time * 2, 60)

    logging.error("Max reconnect attempts reached. Stopping websocket.")

async def binance_websocket():
    uri = "wss://fstream.binance.com/ws/!markPrice@arr"

    async with websockets.connect(uri) as websocket:
        logging.info("Binance WebSocket connected and subscribed to mark prices.")
       
        while True:
            response = await websocket.recv()
            data = json.loads(response)

            for symbol_data in data:
                symbol = symbol_data['s']
                korean_name = coin_name_dict.get(symbol, '알 수 없음')
                mark_price = float(symbol_data['p'])
               
                if mark_price > BINANCE_FUTURE_TRADE_THRESHOLD:  # 설정한 금액 기준
                    message = f"[바이낸스] 선물 알림: {symbol} ({korean_name})\n" \
                              f"마크 가격: {mark_price}"
                    msg_id = hashlib.md5(message.encode()).hexdigest()
                    if msg_id not in recent_messages:
                        await send_telegram_message(message)
                        recent_messages[msg_id] = time()  # 메시지 해시와 타임스탬프 저장

@app.cli.command("init_app")
@with_appcontext
def init_app():
    """Flask 애플리케이션 초기화 및 웹소켓 스레드 시작"""
    logging.info("Initializing app and starting WebSocket threads.")
    loop = asyncio.get_event_loop()

    # 웹소켓 실행
    loop.create_task(upbit_websocket())
    loop.create_task(binance_websocket())

# main guard
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), use_reloader=False)
