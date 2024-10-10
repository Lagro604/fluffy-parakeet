import os
import hashlib
import asyncio
import logging
import httpx
from flask import Flask, request
import threading

app = Flask(__name__)

# 환경 변수에서 설정
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
BITCOIN_TRADE_AMOUNT = 0.1
TRADE_THRESHOLD = 100000
EXCLUDED_COINS = ['KRW-BTC']  # 예외로 처리할 코인
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
            if market_id == "KRW-BTC":
                orderbook_data = await get_orderbook(market_id)
                logging.debug(f"Orderbook data for {market_id}: {orderbook_data}")  # 주문서 데이터 로그 추가
                if orderbook_data and isinstance(orderbook_data, list) and len(orderbook_data) > 0:
                    ask_units = orderbook_data[0].get('orderbook_units', [])
                    if ask_units:
                        ask_size = ask_units[0]['ask_size']
                        bid_size = ask_units[0]['bid_size']

                        if ask_size >= BITCOIN_TRADE_AMOUNT:
                            message = f"비트코인 매도 알림: {market_id} ({coin_name})"
                            msg_id = hashlib.md5(message.encode()).hexdigest()
                            if msg_id not in recent_messages:
                                await send_telegram_message(message)
                                recent_messages.add(msg_id)

                        if bid_size >= BITCOIN_TRADE_AMOUNT:
                            message = f"비트코인 매수 알림: {market_id} ({coin_name})"
                            msg_id = hashlib.md5(message.encode()).hexdigest()
                            if msg_id not in recent_messages:
                                await send_telegram_message(message)
                                recent_messages.add(msg_id)

                continue

            recent_trades = await get_recent_trades(market_id)
            logging.debug(f"Recent trades for {market_id}: {recent_trades}")  # 최근 거래 로그 추가
            if isinstance(recent_trades, list) and recent_trades:
                trade_found = any(trade['trade_price'] * trade['trade_volume'] >= TRADE_THRESHOLD for trade in recent_trades)

                ticker_data = await get_ticker(market_id)
                current_price = ticker_data[0]['trade_price'] if ticker_data and isinstance(ticker_data, list) else 0
                yesterday_price = ticker_data[0]['prev_closing_price'] if ticker_data and isinstance(ticker_data, list) else 0
                change_percentage = ((current_price - yesterday_price) / yesterday_price * 100) if yesterday_price else 0

                if market_id not in EXCLUDED_COINS and trade_found:
                    total_trade_value = sum(trade['trade_price'] * trade['trade_volume'] for trade in recent_trades if trade['trade_price'] * trade['trade_volume'] >= TRADE_THRESHOLD)
                    message = f"매수 알림: {market_id} ({coin_name})\n최근 거래 중 총 체결 금액이 {format_krw(total_trade_value)}으로 매수 체결되었습니다.\n현재 가격: {format_krw(current_price)}, 전일 대비: {change_percentage:.2f}%"
                    msg_id = hashlib.md5(message.encode()).hexdigest()
                    if msg_id not in recent_messages:
                        await send_telegram_message(message)
                        recent_messages.add(msg_id)

        await asyncio.sleep(10)  # 10초 대기

@app.route('/')
def index():
    return "Hello, World!"

if __name__ == '__main__':
    threading.Thread(target=asyncio.run, args=(monitor_market(),), daemon=True).start()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)