import os
import hashlib
import asyncio
import logging
import httpx
import websockets
import json
from flask import Flask, request
from time import time
import signal
from threading import Event

app = Flask(__name__)

# 환경 변수 설정 (토큰과 채팅 ID만)
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

# 직접 설정하는 값들
UPBIT_TRADE_THRESHOLD = 20000000
EXCLUDED_TRADE_THRESHOLD = 70000000
EXCLUDED_COINS = ['KRW-SOL', 'KRW-ETH', 'KRW-SHIB', 'KRW-DOGE', 'KRW-USDT', 'KRW-XRP']
BINANCE_FUTURE_TRADE_THRESHOLD = 200000000
PORT = int(os.getenv('PORT', 8080))

recent_messages = {}
MESSAGE_EXPIRATION_TIME = 10800
coin_name_dict = {}

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 종료 플래그
shutdown_event = Event()

async def send_telegram_message(message):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    payload = {'chat_id': CHAT_ID, 'text': message}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(f'Error sending message: {e}')

def delete_old_hashes():
    current_time = time()
    keys_to_delete = [key for key, timestamp in recent_messages.items() if current_time - timestamp > MESSAGE_EXPIRATION_TIME]
    for key in keys_to_delete:
        del recent_messages[key]
    logger.debug(f"Deleted {len(keys_to_delete)} old messages from recent_messages")

async def get_all_krw_coins():
    url = 'https://api.upbit.com/v1/market/all'
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            markets = response.json()
            for market in markets:
                if market['market'].startswith('KRW-'):
                    coin_name_dict[market['market']] = market['korean_name']
            return coin_name_dict
        except httpx.HTTPError as e:
            logger.error(f'Error fetching market data: {e}')
            return {}

async def upbit_websocket():
    uri = "wss://api.upbit.com/websocket/v1"
    
    await get_all_krw_coins()
    
    subscribe_message = [
        {"ticket": "test"},
        {"type": "ticker", "codes": list(coin_name_dict.keys())},
        {"type": "trade", "codes": list(coin_name_dict.keys())}
    ]

    while not shutdown_event.is_set():
        try:
            async with websockets.connect(uri) as websocket:
                await websocket.send(json.dumps(subscribe_message))
                logger.info("Upbit WebSocket connected and subscribed.")
                logger.debug(f"Subscription message sent: {subscribe_message}")

                while not shutdown_event.is_set():
                    try:
                        response = await asyncio.wait_for(websocket.recv(), timeout=30)
                        data = json.loads(response)

                        logger.debug(f"Data received from Upbit WebSocket: {data}")

                        if data['type'] == 'ticker':
                            await process_upbit_ticker(data)
                        elif data['type'] == 'trade':
                            await process_upbit_trade(data)
                    except asyncio.TimeoutError:
                        continue

        except websockets.exceptions.ConnectionClosed:
            logger.warning("Upbit WebSocket connection closed. Reconnecting...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Unexpected error in Upbit WebSocket: {e}")
            await asyncio.sleep(5)

async def process_upbit_ticker(data):
    market = data['code']
    korean_name = coin_name_dict.get(market, '알 수 없음')
    current_price = data['trade_price']
    change_rate = data['signed_change_rate'] * 100

    message = (
        f"[업비트] 티커 알림: {market} ({korean_name})\n"
        f"현재 가격: {current_price:,.0f}원\n"
        f"전일 대비: {change_rate:.2f}%"
    )

    await send_message_if_new(message)

async def process_upbit_trade(data):
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
            f"체결 금액: {trade_value:,.0f}원"
        )

        await send_message_if_new(message)

async def binance_websocket():
    uri = "wss://fstream.binance.com/ws/!markPrice@arr"

    while not shutdown_event.is_set():
        try:
            async with websockets.connect(uri) as websocket:
                logger.info("Binance WebSocket connected and subscribed to mark prices.")

                while not shutdown_event.is_set():
                    try:
                        response = await asyncio.wait_for(websocket.recv(), timeout=30)
                        data = json.loads(response)

                        for symbol_data in data:
                            await process_binance_data(symbol_data)
                    except asyncio.TimeoutError:
                        continue

        except websockets.exceptions.ConnectionClosed:
            logger.warning("Binance WebSocket connection closed. Reconnecting...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Unexpected error in Binance WebSocket: {e}")
            await asyncio.sleep(5)

async def process_binance_data(symbol_data):
    symbol = symbol_data['s']
    korean_name = coin_name_dict.get(symbol, '알 수 없음')
    mark_price = float(symbol_data['p'])
    trade_volume = float(symbol_data['q'])  # 거래량
    trade_value = trade_volume * mark_price  # 거래 금액 계산

    if trade_value >= BINANCE_FUTURE_TRADE_THRESHOLD:
        trade_type = "매수" if symbol_data['m'] else "매도"  # 'm'이 true이면 매수, false이면 매도
        
        message = (
            f"[바이낸스] {trade_type} 알림: {symbol} ({korean_name})\n"
            f"마크 가격: {mark_price:,.0f}원\n"
            f"체결 금액: {trade_value:,.0f}원"
        )
        await send_message_if_new(message)

    if trade_value >= 500000000:  # 5억 이상 체결 시 추가 메시지
        high_value_message = (
            f"[바이낸스] 높은 거래 체결 알림: {symbol} ({korean_name})\n"
            f"마크 가격: {mark_price:,.0f}원\n"
            f"체결 금액: {trade_value:,.0f}원"
        )
        await send_message_if_new(high_value_message)

async def send_message_if_new(message):
    msg_id = hashlib.md5(message.encode()).hexdigest()
    if msg_id not in recent_messages:
        await send_telegram_message(message)
        recent_messages[msg_id] = time()
    delete_old_hashes()

async def run_websockets():
    await asyncio.gather(upbit_websocket(), binance_websocket())

async def shutdown(signal, loop):
    logger.info(f"Received exit signal {signal.name}...")
    logger.info("Shutting down...")
    shutdown_event.set()
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    [task.cancel() for task in tasks]
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()

@app.route('/')
def index():
    return "Crypto Price Alert Service is running!"

@app.route('/health')
def health():
    return "OK", 200

@app.route('/test')
def test():
    return "Test route is working!", 200

@app.route('/your_webhook_endpoint', methods=['POST'])
def webhook():
    update = request.get_json()
    logger.info(f"Received update: {update}")  # 수신된 업데이트 로그 출력
    return 'OK', 200

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

def create_app():
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s, loop)))
    
    loop.create_task(run_websockets())
    return app

# Gunicorn용 앱 객체
application = create_app()

if __name__ == "__main__":
    run_flask()
