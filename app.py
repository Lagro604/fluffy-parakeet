import os
import hashlib
import asyncio
import logging
import httpx
from flask import Flask, request
from threading import Thread
from datetime import datetime

app = Flask(__name__)

# 환경 변수에서 설정
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
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
    url = f'https://api.upbit.com/v1/trades/ticks?market={market_id}&count=15'  # 최근 거래 15개 요청
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

async def process_market(market_id, coin_name):
    if market_id == "KRW-BTC":
        orderbook_data = await get_orderbook(market_id)
        if orderbook_data:
            ask_units = orderbook_data[0].get('orderbook_units', [])
            if ask_units:
                ask_size = ask_units[0]['ask_size']
                bid_size = ask_units[0]['bid_size']

                if ask_size >= BITCOIN_ORDERBOOK_THRESHOLD or bid_size >= BITCOIN_ORDERBOOK_THRESHOLD:
                    ticker_data = await get_ticker(market_id)
                    if ticker_data:
                        current_price = ticker_data[0]['trade_price']
                        yesterday_price = ticker_data[0]['prev_closing_price']
                        change_percentage = ((current_price - yesterday_price) / yesterday_price * 100)
                        message = (
                            f"비트코인 알림: {market_id} ({coin_name})\n"
                            f"현재 가격: {format_krw(current_price)}, 전일 대비: {change_percentage:.2f}%"
                        )
                        msg_id = hashlib.md5(message.encode()).hexdigest()
                        if msg_id not in recent_messages:
                            await send_telegram_message(message)
                            recent_messages.add(msg_id)

    else:
        recent_trades = await get_recent_trades(market_id)
        if recent_trades:
            total_trade_value = sum(trade['trade_price'] * trade['trade_volume'] for trade in recent_trades)
            for trade in recent_trades:
                trade_value = trade['trade_price'] * trade['trade_volume']
                trade_type = "매수" if trade['ask_bid'] == "BID" else "매도"
                trade_timestamp = trade.get('trade_timestamp', None)
                if trade_timestamp:
                    trade_time = datetime.fromtimestamp(trade_timestamp / 1000).strftime('%Y-%m-%d %H:%M:%S')
                else:
                    trade_time = "시간 정보 없음"
                trade_id = trade.get('sequential_id', trade_timestamp)

                if trade_value >= TRADE_THRESHOLD and market_id not in EXCLUDED_COINS:
                    ticker_data = await get_ticker(market_id)
                    if ticker_data:
                        current_price = ticker_data[0]['trade_price']
                        yesterday_price = ticker_data[0]['prev_closing_price']
                        change_percentage = ((current_price - yesterday_price) / yesterday_price * 100)
                        message = (
                            f"{trade_type} 알림: {market_id} ({coin_name})\n"
                            f"최근 거래: {format_krw(trade_value)} (거래 가격: {format_krw(trade['trade_price'])}원)\n"
                            f"거래 시각: {trade_time}\n"
                            f"거래 ID: {trade_id}\n"
                            f"총 체결 금액: {format_krw(total_trade_value)}\n"
                            f"현재 가격: {format_krw(current_price)}, 전일 대비: {change_percentage:.2f}%"
                        )
                        msg_id = hashlib.md5(message.encode()).hexdigest()
                        if msg_id not in recent_messages:
                            await send_telegram_message(message)
                            recent_messages.add(msg_id)

                elif trade_value >= EXCLUDED_TRADE_THRESHOLD and market_id in EXCLUDED_COINS:
                    ticker_data = await get_ticker(market_id)
                    if ticker_data:
                        current_price = ticker_data[0]['trade_price']
                        yesterday_price = ticker_data[0]['prev_closing_price']
                        change_percentage = ((current_price - yesterday_price) / yesterday_price * 100)
                        message = (
                            f"{trade_type} 알림 (제외 코인): {market_id} ({coin_name})\n"
                            f"최근 거래: {format_krw(trade_value)} (거래 가격: {format_krw(trade['trade_price'])}원)\n"
                            f"거래 시각: {trade_time}\n"
                            f"거래 ID: {trade_id}\n"
                            f"총 체결 금액: {format_krw(total_trade_value)}\n"
                            f"현재 가격: {format_krw(current_price)}, 전일 대비: {change_percentage:.2f}%"
                        )
                        msg_id = hashlib.md5(message.encode()).hexdigest()
                        if msg_id not in recent_messages:
                            await send_telegram_message(message)
                            recent_messages.add(msg_id)

async def monitor_market():
    COIN_NAMES = await get_coin_names()
    logging.info("Monitoring market started.")

    while True:
        logging.debug("Checking markets...")
        tasks = []  # 병렬 처리할 작업 리스트

        for market_id, coin_name in COIN_NAMES.items():
            # 각 코인에 대해 비동기 작업을 생성하여 tasks에 추가
            tasks.append(process_market(market_id, coin_name))

        # 병렬로 작업 처리
        await asyncio.gather(*tasks)

        await asyncio.sleep(10)  # 호출 간격을 30초로 조절

def run_async_monitor():
    asyncio.run(monitor_market())

@app.route('/')
def index():
    return "Hello, World!"

# 애플리케이션 시작 시 백그라운드 태스크 실행
background_thread = Thread(target=run_async_monitor)
background_thread.start()
