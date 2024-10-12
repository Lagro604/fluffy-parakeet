import os
import hashlib
import asyncio
import logging
import httpx
import websockets
import json
from flask import Flask, request
from time import time
from threading import Event

app = Flask(__name__)

# 환경 변수 설정
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

# 직접 설정하는 값들
UPBIT_TRADE_THRESHOLD = 20000000  # 업비트 거래 기준
EXCLUDED_TRADE_THRESHOLD = 70000000  # 제외된 코인 기준
EXCLUDED_COINS = ['KRW-BTC', 'KRW-SOL', 'KRW-ETH', 'KRW-SHIB', 'KRW-DOGE']  # 제외할 코인
BINANCE_FUTURE_TRADE_THRESHOLD = 200000000  # 바이낸스 거래 기준
PORT = int(os.getenv('PORT', 8080))

recent_messages = {}
MESSAGE_EXPIRATION_TIME = 10800

# 로그 설정
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

async def get_upbit_websocket():
    uri = "wss://api.upbit.com/websocket/v1"
    
    subscribe_message = [
        {"ticket": "test"},
        {"type": "trade", "codes": ["KRW-BTC", "KRW-SOL", "KRW-ETH", "KRW-SHIB", "KRW-DOGE"]},
        {"type": "trade", "codes": ["KRW-" + coin for coin in await get_all_krw_coins()]}
    ]

    async with websockets.connect(uri) as websocket:
        await websocket.send(json.dumps(subscribe_message))
        logger.info("Upbit WebSocket connected and subscribed.")

        while not shutdown_event.is_set():
            try:
                response = await websocket.recv()
                data = json.loads(response)

                if data['type'] == 'trade':
                    await process_upbit_trade(data)

            except Exception as e:
                logger.error(f"Error in Upbit WebSocket: {e}")

async def process_upbit_trade(data):
    market = data['code']
    trade_price = data['trade_price']
    trade_volume = data['trade_volume']
    trade_value = trade_price * trade_volume
    trade_type = "매수" if data['ask_bid'] == "BID" else "매도"

    # 메시지 조건 판단
    if (market in EXCLUDED_COINS and trade_value >= EXCLUDED_TRADE_THRESHOLD) or \
       (market not in EXCLUDED_COINS and trade_value >= UPBIT_TRADE_THRESHOLD):
        
        # 전일 대비 % 계산
        percent_change = await get_percent_change(market)

        message = (
            f"[업비트] {trade_type} 알림: {market}\n"
            f"체결 가격: {trade_price:,.0f}원\n"
            f"체결 금액: {trade_value:,.0f}원\n"
            f"전일 대비: {percent_change:.2f}%"
        )

        await send_message_if_new(message)

    # BTC 호가창 개수 체크
    if market == "KRW-BTC":
        await check_btc_order_book()

async def get_percent_change(market):
    url = f'https://api.upbit.com/v1/candles/days?market={market}&count=1'
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            yesterday_price = data[0]['trade_price']
            return ((data[0]['trade_price'] - yesterday_price) / yesterday_price) * 100
        except httpx.HTTPError as e:
            logger.error(f'Error fetching percent change: {e}')
            return 0.0

async def check_btc_order_book():
    url = "https://api.upbit.com/v1/orderbook?markets=KRW-BTC"
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
            orderbook_data = response.json()
            if orderbook_data and len(orderbook_data[0]['orderbook_units']) >= 50:
                message = "[업비트] BTC 호가창 알림: BTC의 호가창에 50개 이상의 주문이 있습니다."
                await send_message_if_new(message)
        except httpx.HTTPError as e:
            logger.error(f'Error fetching BTC order book: {e}')

async def get_binance_websocket():
    uri = "wss://fstream.binance.com/ws/!markPrice@arr"

    async with websockets.connect(uri) as websocket:
        logger.info("Binance WebSocket connected and subscribed to mark prices.")

        while not shutdown_event.is_set():
            try:
                response = await websocket.recv()
                data = json.loads(response)

                for symbol_data in data:
                    await process_binance_data(symbol_data)

            except Exception as e:
                logger.error(f"Error in Binance WebSocket: {e}")

async def process_binance_data(symbol_data):
    symbol = symbol_data['s']
    mark_price = float(symbol_data['p'])
    trade_volume = float(symbol_data['q'])  # 거래량
    trade_value = trade_volume * mark_price  # 거래 금액 계산

    if trade_value >= BINANCE_FUTURE_TRADE_THRESHOLD:
        trade_type = "매수" if symbol_data['m'] else "매도"  # 'm'이 true이면 매수, false이면 매도
        
        message = (
            f"[바이낸스] {trade_type} 알림: {symbol}\n"
            f"마크 가격: {mark_price:,.0f}원\n"
            f"체결 금액: {trade_value:,.0f}원"
        )
        await send_message_if_new(message)

async def send_message_if_new(message):
    msg_id = hashlib.md5(message.encode()).hexdigest()
    if msg_id not in recent_messages:
        await send_telegram_message(message)
        recent_messages[msg_id] = time()
    delete_old_hashes()

async def run_websockets():
    await asyncio.gather(get_upbit_websocket(), get_binance_websocket())

@app.route('/health')
def health():
    return "OK", 200

def run_flask():
    app.run(host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(run_websockets())
    run_flask()
