import os
import logging
import asyncio
import httpx
from flask import Flask, request
from telegram import Bot
from dotenv import load_dotenv
from flask_cors import CORS

# .env 파일에서 환경 변수 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(level=logging.INFO)

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

async def send_telegram_message(message):
    try:
        logging.info(f"Sending message to {CHAT_ID}: {message}")
        await bot.send_message(chat_id=CHAT_ID, text=message)
    except Exception as e:
        logging.error(f"Failed to send message: {e}")

async def fetch(url):
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        return response.json()

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

def format_krw(value):
    return f"{value:,.0f} 원"

async def monitor_market():
    COIN_NAMES = await get_coin_names()
    logging.info("Monitoring market started.")
    
    while True:
        all_trade_values = []
        
        for market_id, coin_name in COIN_NAMES.items():
            if market_id == "KRW-BTC":
                orderbook_data = await get_orderbook(market_id)
                if orderbook_data and isinstance(orderbook_data, list) and len(orderbook_data) > 0:
                    ask_units = orderbook_data[0].get('orderbook_units', [])
                    if ask_units:
                        ask_size = ask_units[0]['ask_size']
                        bid_size = ask_units[0]['bid_size']
                        
                        if ask_size >= BITCOIN_TRADE_AMOUNT:
                            message = f"비트코인 매도 알림: {market_id} ({coin_name})\n매도 호가 수량: {ask_size:.0f}개 이상입니다."
                            await send_telegram_message(message)

                        if bid_size >= BITCOIN_TRADE_AMOUNT:
                            message = f"비트코인 매수 알림: {market_id} ({coin_name})\n매수 호가 수량: {bid_size:.0f}개 이상입니다."
                            await send_telegram_message(message)
                continue
            
            recent_trades = await get_recent_trades(market_id)
            if isinstance(recent_trades, list) and recent_trades:
                trades_with_value = [{**trade, 'total_value': trade['trade_price'] * trade['trade_volume']} for trade in recent_trades]
                all_trade_values.extend(trades_with_value)

                trade_found = any(trade['total_value'] >= TRADE_THRESHOLD for trade in trades_with_value)
                ticker_data = await get_ticker(market_id)
                current_price = ticker_data[0]['trade_price'] if ticker_data and isinstance(ticker_data, list) else 0
                yesterday_price = ticker_data[0]['prev_closing_price'] if ticker_data and isinstance(ticker_data, list) else 0
                change_percentage = ((current_price - yesterday_price) / yesterday_price * 100) if yesterday_price else 0

                if market_id not in EXCLUDED_COINS:
                    if trade_found:
                        total_trade_value = sum(trade['total_value'] for trade in trades_with_value if trade['total_value'] >= TRADE_THRESHOLD)
                        message = (f"매수 알림: {market_id} ({coin_name})\n"
                                   f"최근 거래 중 총 체결 금액이 {format_krw(total_trade_value)}으로 매수 체결되었습니다.\n"
                                   f"현재 가격: {format_krw(current_price)}, 전일 대비: {change_percentage:.2f}%")
                        await send_telegram_message(message)
                else:
                    high_trade_found = any(trade['total_value'] >= HIGH_TRADE_THRESHOLD for trade in trades_with_value)
                    if high_trade_found:
                        total_high_trade_value = sum(trade['total_value'] for trade in trades_with_value if trade['total_value'] >= HIGH_TRADE_THRESHOLD)
                        message = (f"매도 알림: {market_id} ({coin_name})\n"
                                   f"최근 거래 중 7000만원 이상 체결된 거래가 {format_krw(total_high_trade_value)}으로 매도 체결되었습니다.\n"
                                   f"현재 가격: {format_krw(current_price)}, 전일 대비: {change_percentage:.2f}%")
                        await send_telegram_message(message)

        await asyncio.sleep(10)

@app.errorhandler(Exception)
def handle_exception(e):
    logging.error(f"Unhandled Exception: {e}")
    return "Internal Server Error", 500

@app.route('/')
def index():
    ip = request.remote_addr
    logging.info(f"Request from IP: {ip}")
    return "Welcome to my application!"

@app.route('/test')
def test():
    try:
        return 'Test route is working!'
    except Exception as e:
        logging.error(f"Error occurred: {e}")
        return f"Error occurred: {e}", 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
    asyncio.run(monitor_market())
