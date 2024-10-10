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
TRADE_THRESHOLD = 20000000  # 일반 코인 체결액 기준 (2,000만원)
EXCLUDED_COINS = ['KRW-SOL', 'KRW-ETH', 'KRW-SHIB', 'KRW-DOGE', 'KRW-USDT', 'KRW-XRP']  # 제외할 코인 리스트
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
    url = f'https://api.upbit.com/v1/trades/ticks?market={market_id}&count=5'
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
    logging.info("Monitoring market started.")  # 모니터링 시작 로그 추가

    while True:
        logging.debug("Checking markets...")  # 루프 시작 로그 추가
        for market_id, coin_name in COIN_NAMES.items():
            recent_trades = await get_recent_trades(market_id)
            logging.debug(f"Recent trades for {market_id}: {recent_trades}")  # 최근 거래 로그 추가

            # 비트코인 처리
            if market_id == "KRW-BTC":
                orderbook_data = await get_orderbook(market_id)
                logging.debug(f"Orderbook data for {market_id}: {orderbook_data}")  # 주문서 데이터 로그 추가
                if orderbook_data and isinstance(orderbook_data, list) and len(orderbook_data) > 0:
                    ask_units = orderbook_data[0].get('orderbook_units', [])
                    if ask_units:
                        ask_size = ask_units[0]['ask_size']  # 매도 잔량
                        bid_size = ask_units[0]['bid_size']   # 매수 잔량

                        # 매도 잔량이 30억 이상일 경우 메시지 전송
                        if ask_size >= 3000000000:
                            message = f"비트코인 매도 알림: {market_id} ({coin_name})\n호가창 매도 잔량: {format_krw(ask_size)}"
                            await send_telegram_message(message)

                        # 매수 잔량이 30억 이상일 경우 메시지 전송
                        if bid_size >= 3000000000:
                            message = f"비트코인 매수 알림: {market_id} ({coin_name})\n호가창 매수 잔량: {format_krw(bid_size)}"
                            await send_telegram_message(message)

                continue  # 비트코인 처리가 끝났으므로 다음 코인으로 넘어감

            # 일반 코인 처리
            if isinstance(recent_trades, list) and recent_trades:
                # 제외된 코인인지 확인
                if market_id in EXCLUDED_COINS:
                    # 체결액이 7,000만원 이상인지 확인
                    trade_found = any(trade['trade_price'] * trade['trade_volume'] >= 70000000 for trade in recent_trades)
                else:
                    # 체결액이 2,000만원 이상인지 확인
                    trade_found = any(trade['trade_price'] * trade['trade_volume'] >= TRADE_THRESHOLD for trade in recent_trades)

                if trade_found:
                    total_trade_value = sum(trade['trade_price'] * trade['trade_volume'] for trade in recent_trades)
                    message = f"매수 알림: {market_id} ({coin_name})\n최근 거래 중 총 체결 금액이 {format_krw(total_trade_value)}으로 매수 체결되었습니다."
                    await send_telegram_message(message)

        await asyncio.sleep(10)  # 10초 대기

def run_async_monitor():
    asyncio.run(monitor_market())

@app.route('/')
def index():
    return "Hello, World!"

# 애플리케이션 시작 시 백그라운드 태스크 실행
background_thread = Thread(target=run_async_monitor)
background_thread.start()
