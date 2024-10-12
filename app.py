import os
import hashlib
import asyncio
import logging
import httpx
from flask import Flask, request
from threading import Thread
from collections import deque

app = Flask(__name__)

# 환경 변수에서 설정
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
TRADE_THRESHOLD = 20000000  # 2천만 원
EXCLUDED_TRADE_THRESHOLD = 70000000  # 7천만 원
BITCOIN_ORDERBOOK_THRESHOLD = 3000000000  # 30억 원
BINANCE_FUTURES_THRESHOLD = 200000000  # 2억 원
EXCLUDED_COINS = ['KRW-SOL', 'KRW-ETH', 'KRW-SHIB', 'KRW-DOGE', 'KRW-USDT', 'KRW-XRP']
recent_messages = set()  # 최근 메시지 중복 방지
logging.basicConfig(level=logging.DEBUG)  # 로그 레벨 설정

# Binance API 엔드포인트
BINANCE_FUTURES_API = 'https://fapi.binance.com/fapi/v1'

# 최근에 처리한 거래 ID를 저장하는 딕셔너리
processed_trades = {}

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

async def get_binance_futures_top50():
    url = f'{BINANCE_FUTURES_API}/ticker/24hr'
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        if response.status_code == 200:
            data = response.json()
            sorted_data = sorted(data, key=lambda x: float(x['volume']), reverse=True)
            return sorted_data[:50]
        logging.error(f'Error fetching Binance futures data: {response.text}')
        return []

async def monitor_binance_futures():
    while True:
        top_50 = await get_binance_futures_top50()
        for symbol in top_50:
            symbol_name = symbol['symbol']
            trades_url = f'{BINANCE_FUTURES_API}/trades'
            params = {'symbol': symbol_name, 'limit': 100}  # 최근 100개 거래만 조회
            async with httpx.AsyncClient() as client:
                response = await client.get(trades_url, params=params)
                if response.status_code == 200:
                    trades = response.json()
                    # 가장 최근 거래부터 처리 (역순으로 정렬)
                    for trade in sorted(trades, key=lambda x: int(x['id']), reverse=True):
                        trade_id = int(trade['id'])
                        if symbol_name not in processed_trades:
                            processed_trades[symbol_name] = deque(maxlen=1000)  # 최대 1000개의 거래 ID 저장
                        
                        # 이미 처리한 거래인지 확인
                        if trade_id in processed_trades[symbol_name]:
                            break  # 이전에 처리한 거래를 만나면 루프 종료
                        
                        processed_trades[symbol_name].append(trade_id)
                        
                        trade_value = float(trade['price']) * float(trade['qty'])
                        if trade_value * 1300 >= BINANCE_FUTURES_THRESHOLD:  # 대략적인 원화 환산 (1달러 = 1300원 가정)
                            position = "롱" if trade['isBuyerMaker'] else "숏"
                            
                            message = (
                                f"바이낸스 선물 거래 알림: {symbol_name} ({position})\n"
                                f"거래 금액: ${trade_value:,.2f} (약 {trade_value*1300:,.0f}원)\n"
                                f"가격: ${float(trade['price']):,.2f}\n"
                                f"수량: {float(trade['qty']):,.4f}"
                            )
                            msg_id = hashlib.md5(message.encode()).hexdigest()
                            if msg_id not in recent_messages:
                                await send_telegram_message(message)
                                recent_messages.add(msg_id)
                else:
                    logging.error(f'Error fetching Binance futures trades: {response.text}')
        await asyncio.sleep(10)  # 10초 대기

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

                            message = (
                                f"비트코인 알림: {market_id} ({coin_name})\n"
                                f"현재 가격: {format_krw(current_price)}, 전일 대비: {change_percentage:.2f}%"
                            )
                            msg_id = hashlib.md5(message.encode()).hexdigest()
                            if msg_id not in recent_messages:
                                await send_telegram_message(message)
                                recent_messages.add(msg_id)

                continue

            recent_trades = await get_recent_trades(market_id)
            logging.debug(f"Recent trades for {market_id}: {recent_trades}")
            if isinstance(recent_trades, list) and recent_trades:
                total_trade_value = 0

                for trade in recent_trades:
                    trade_value = trade['trade_price'] * trade['trade_volume']
                    total_trade_value += trade_value
                    trade_type = "매수" if trade['ask_bid'] == "BID" else "매도"

                    if trade_value >= TRADE_THRESHOLD and market_id not in EXCLUDED_COINS:
                        ticker_data = await get_ticker(market_id)
                        current_price = ticker_data[0]['trade_price'] if ticker_data and isinstance(ticker_data, list) else 0
                        yesterday_price = ticker_data[0]['prev_closing_price'] if ticker_data and isinstance(ticker_data, list) else 0
                        change_percentage = ((current_price - yesterday_price) / yesterday_price * 100) if yesterday_price else 0

                        message = (
                            f"{trade_type} 알림: {market_id} ({coin_name})\n"
                            f"최근 거래: {format_krw(trade_value)}\n"
                            f"총 체결 금액: {format_krw(total_trade_value)}\n"
                            f"현재 가격: {format_krw(current_price)}, 전일 대비: {change_percentage:.2f}%"
                        )
                        msg_id = hashlib.md5(message.encode()).hexdigest()
                        if msg_id not in recent_messages:
                            await send_telegram_message(message)
                            recent_messages.add(msg_id)

                    elif trade_value >= EXCLUDED_TRADE_THRESHOLD and market_id in EXCLUDED_COINS:
                        ticker_data = await get_ticker(market_id)
                        current_price = ticker_data[0]['trade_price'] if ticker_data and isinstance(ticker_data, list) else 0
                        yesterday_price = ticker_data[0]['prev_closing_price'] if ticker_data and isinstance(ticker_data, list) else 0
                        change_percentage = ((current_price - yesterday_price) / yesterday_price * 100) if yesterday_price else 0

                        message = (
                            f"{trade_type} 알림 (제외 코인): {market_id} ({coin_name})\n"
                            f"최근 거래: {format_krw(trade_value)}\n"
                            f"총 체결 금액: {format_krw(total_trade_value)}\n"
                            f"현재 가격: {format_krw(current_price)}, 전일 대비: {change_percentage:.2f}%"
                        )
                        msg_id = hashlib.md5(message.encode()).hexdigest()
                        if msg_id not in recent_messages:
                            await send_telegram_message(message)
                            recent_messages.add(msg_id)

        await asyncio.sleep(9)  # 4초 대기

async def main():
    await asyncio.gather(
        monitor_market(),
        monitor_binance_futures()
    )

def run_async_monitor():
    asyncio.run(main())

@app.route('/')
def index():
    return "Hello, World!"

# 애플리케이션 시작 시 백그라운드 태스크 실행
background_thread = Thread(target=run_async_monitor)
background_thread.start()

if __name__ == "__main__":
    app.run(debug=True)