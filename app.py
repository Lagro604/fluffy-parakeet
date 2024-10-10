import os
import hashlib
import asyncio
import logging
import httpx
from flask import Flask, request
from threading import Thread

app = Flask(__name__)

# 환경 변수에서 설정
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
BITCOIN_TRADE_AMOUNT = 0.1
TRADE_THRESHOLD = 20000000  # 2천만 원
EXCLUDED_TRADE_THRESHOLD = 70000000  # 7천만 원
BITCOIN_ORDERBOOK_THRESHOLD = 3000000000  # 30억 원
EXCLUDED_COINS = ['KRW-SOL', 'KRW-ETH', 'KRW-SHIB', 'KRW-DOGE', 'KRW-USDT', 'KRW-XRP']
recent_messages = set()  # 최근 메시지 중복 방지
logging.basicConfig(level=logging.DEBUG)  # 로그 레벨 설정

async def send_telegram_message(message):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    payload = {'chat_id': CHAT_ID, 'text': message}
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload)
        if response.status_code != 200:
            logging.error(f'Error sending message: {response.text}')

async def get_orderbook(market_id):
    url = f'https://api.upbit.com/v1/orderbook?markets={market_id}'
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        if response.status_code == 200:
            return response.json()
        logging.error(f'Error fetching orderbook for {market_id}: {response.text}')
        return None

async def get_recent_trades(market_id):
    url = f'https://api.upbit.com/v1/trades/ticks?market={market_id}&count=1'  # 최근 거래 1개만 요청
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        if response.status_code == 200:
            return response.json()
        logging.error(f'Error fetching recent trades for {market_id}: {response.text}')
        return None

async def get_ticker(market_id):
    url = f'https://api.upbit.com/v1/ticker?markets={market_id}'
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        if response.status_code == 200:
            return response.json()
        logging.error(f'Error fetching ticker for {market_id}: {response.text}')
        return None

async def get_coin_names():
    url = 'https://api.upbit.com/v1/market/all'
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        if response.status_code == 200:
            return {coin['market']: coin['korean_name'] for coin in response.json()}
        logging.error(f'Error fetching coin names: {response.text}')
        return {}

def format_krw(value):
    return f"{value:,.0f}원"

async def monitor_market():
    COIN_NAMES = await get_coin_names()
    logging.info("Monitoring market started.")

    while True:
        logging.debug("Checking markets...")
        for market_id, coin_name in COIN_NAMES.items():
            if market_id == "KRW-BTC":
                orderbook_data = await get_orderbook(market_id)
                logging.debug(f"Orderbook data for {market_id}: {orderbook_data}")
                if orderbook_data and isinstance(orderbook_data, list) and len(orderbook_data) > 0:
                    ask_units = orderbook_data[0].get('orderbook_units', [])
                    if ask_units:
                        ask_size = ask_units[0]['ask_size']
                        bid_size = ask_units[0]['bid_size']

                        if ask_size >= BITCOIN_ORDERBOOK_THRESHOLD or bid_size >= BITCOIN_ORDERBOOK_THRESHOLD:
                            ticker_data = await get_ticker(market_id)
                            current_price = ticker_data[0]['trade_price'] if ticker_data and isinstance(ticker_data, list) else 0
                            yesterday_price = ticker_data[0]['prev_closing_price'] if ticker_data and isinstance(ticker_data, list) else 0
                            change_percentage = ((current_price - yesterday_price) / yesterday_price * 100) if yesterday_price else 0

                            message = f"비트코인 알림: {market_id} ({coin_name})\n현재 가격: {format_krw(current_price)}, 전일 대비: {change_percentage:.2f}%"
                            msg_id = hashlib.md5(message.encode()).hexdigest()
                            if msg_id not in recent_messages:
                                await send_telegram_message(message)
                                recent_messages.add(msg_id)

                continue

            recent_trades = await get_recent_trades(market_id)
            logging.debug(f"Recent trades for {market_id}: {recent_trades}")
            if isinstance(recent_trades, list) and recent_trades:
                for trade in recent_trades:
                    trade_value = trade['trade_price'] * trade['trade_volume']

                    if trade_value >= TRADE_THRESHOLD and market_id not in EXCLUDED_COINS:
                        ticker_data = await get_ticker(market_id)
                        current_price = ticker_data[0]['trade_price'] if ticker_data and isinstance(ticker_data, list) else 0
                        yesterday_price = ticker_data[0]['prev_closing_price'] if ticker_data and isinstance(ticker_data, list) else 0
                        change_percentage = ((current_price - yesterday_price) / yesterday_price * 100) if yesterday_price else 0

                        message = f"매수 알림: {market_id} ({coin_name})\n최근 거래 중 체결된 금액이 {format_krw(trade_value)} 이상입니다.\n현재 가격: {format_krw(current_price)}, 전일 대비: {change_percentage:.2f}%"
                        msg_id = hashlib.md5(message.encode()).hexdigest()
                        if msg_id not in recent_messages:
                            await send_telegram_message(message)
                            recent_messages.add(msg_id)

                    elif trade_value >= EXCLUDED_TRADE_THRESHOLD and market_id in EXCLUDED_COINS:
                        ticker_data = await get_ticker(market_id)
                        current_price = ticker_data[0]['trade_price'] if ticker_data and isinstance(ticker_data, list) else 0
                        yesterday_price = ticker_data[0]['prev_closing_price'] if ticker_data and isinstance(ticker_data, list) else 0
                        change_percentage = ((current_price - yesterday_price) / yesterday_price * 100) if yesterday_price else 0

                        message = f"제외 코인 매수 알림: {market_id} ({coin_name})\n최근 거래 중 체결된 금액이 {format_krw(trade_value)} 이상입니다.\n현재 가격: {format_krw(current_price)}, 전일 대비: {change_percentage:.2f}%"
                        msg_id = hashlib.md5(message.encode()).hexdigest()
                        if msg_id not in recent_messages:
                            await send_telegram_message(message)
                            recent_messages.add(msg_id)

        await asyncio.sleep(10)  # 10초 대기

def run_async_monitor():
    asyncio.run(monitor_market())

@app.route('/')
def index():
    return "Hello, World!"

# 애플리케이션 시작 시 백그라운드 태스크 실행
background_thread = Thread(target=run_async_monitor)
background_thread.start()
