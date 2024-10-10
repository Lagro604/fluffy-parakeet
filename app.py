import aiohttp
import asyncio
import threading
import logging
import hashlib
import os
import sys
import traceback
from telegram import Bot
from flask import Flask, request
from dotenv import load_dotenv
from flask_cors import CORS

load_dotenv()

# 로깅 설정
logging.basicConfig(level=logging.DEBUG)

# Flask 애플리케이션 초기화
app = Flask(__name__)
CORS(app)

# 텔레그램 봇 설정
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID') 
bot = Bot(token=TELEGRAM_TOKEN)

# 제외할 코인 리스트
EXCLUDED_COINS = ["KRW-SOL", "KRW-ETH", "KRW-SHIB", "KRW-DOGE", "KRW-USDT", "KRW-XRP"]

# 기준
TRADE_THRESHOLD = 20_000_000  # 2000만원
HIGH_TRADE_THRESHOLD = 70_000_000  # 7000만원
BITCOIN_TRADE_AMOUNT = 50  # 비트코인 수량 기준

# 최근 메시지 기록
recent_messages = set()

# 비동기 메시지 전송 함수
async def send_telegram_message(message):
    logging.info(f"Sending message to {CHAT_ID}: {message}")  
    await bot.send_message(chat_id=CHAT_ID, text=message)

# Upbit API 호출 함수
async def fetch(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.json()

async def get_orderbook(market_id):
    url = f'https://api.upbit.com/v1/orderbook?markets={market_id}'
    return await fetch(url)

async def get_recent_trades(market_id):
    url = f'https://api.upbit.com/v1/trades/ticks?market={market_id}&count=50'
    return await fetch(url)

async def get_ticker(market_id):
    url = f'https://api.upbit.com/v1/ticker?markets={market_id}'
    return await fetch(url)

async def get_coin_names():
    url = 'https://api.upbit.com/v1/market/all?is_open=true&market=KRW'
    markets = await fetch(url)
    return {market['market']: market['korean_name'] for market in markets if isinstance(market, dict)}

# 거래 금액 포맷 함수
def format_krw(value):
    return f"{value:,.0f} 원"

async def monitor_market():
    COIN_NAMES = await get_coin_names()
    logging.info("Monitoring market started.")

    while True:
        for market_id, coin_name in COIN_NAMES.items():
            if market_id == "KRW-BTC":
                orderbook_data = await get_orderbook(market_id)
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
            if isinstance(recent_trades, list) and recent_trades:
                trade_found = any(trade['total_value'] >= TRADE_THRESHOLD for trade in recent_trades)

                ticker_data = await get_ticker(market_id)
                current_price = ticker_data[0]['trade_price'] if ticker_data and isinstance(ticker_data, list) else 0
                yesterday_price = ticker_data[0]['prev_closing_price'] if ticker_data and isinstance(ticker_data, list) else 0
                change_percentage = ((current_price - yesterday_price) / yesterday_price * 100) if yesterday_price else 0

                if market_id not in EXCLUDED_COINS and trade_found:
                    total_trade_value = sum(trade['total_value'] for trade in recent_trades if trade['total_value'] >= TRADE_THRESHOLD)
                    message = f"매수 알림: {market_id} ({coin_name})\n최근 거래 중 총 체결 금액이 {format_krw(total_trade_value)}으로 매수 체결되었습니다.\n현재 가격: {format_krw(current_price)}, 전일 대비: {change_percentage:.2f}%"
                    msg_id = hashlib.md5(message.encode()).hexdigest()
                    if msg_id not in recent_messages:
                        await send_telegram_message(message)
                        recent_messages.add(msg_id)

        await asyncio.sleep(10)

# 비동기 루프 실행 함수
def run_asyncio_loop():
    asyncio.run(monitor_market())

# Flask 라우트
@app.route('/')
def index():
    return "Server is running!"

# 서버 실행
if __name__ == '__main__':
    threading.Thread(target=run_asyncio_loop).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
