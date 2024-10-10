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

# Exception handler 설정
def handle_exception(exc_type, exc_value, exc_traceback):
    print("An uncaught exception occurred:")
    print("Type:", exc_type)
    print("Value:", exc_value)
    print("Traceback:")
    traceback.print_tb(exc_traceback)

sys.excepthook = handle_exception

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

# 비동기 메시지 전송 함수
async def send_telegram_message(message):
    try:
        logging.info(f"Sending message to {CHAT_ID}: {message}")  # 디버깅용 로그
        await bot.send_message(chat_id=CHAT_ID, text=message)
    except Exception as e:
        logging.error(f"Failed to send message: {e}")  # 오류 로그

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
    return f"{value:,.0f} 원"  # 천 단위 쉼표 및 원 표시

# 시장 모니터링 비동기 함수
async def monitor_market():
    COIN_NAMES = await get_coin_names()  # 원화 마켓의 모든 코인 이름 매핑
    logging.info("Monitoring market started.")  # 모니터링 시작 로그
    
    while True:
        all_trade_values = []  # 모든 코인의 거래 대금을 저장할 리스트
        
        for market_id, coin_name in COIN_NAMES.items():
            # 비트코인 처리
            if market_id == "KRW-BTC":
                orderbook_data = await get_orderbook(market_id)
                
                if orderbook_data and isinstance(orderbook_data, list) and len(orderbook_data) > 0:
                    ask_units = orderbook_data[0].get('orderbook_units', [])
                    
                    if ask_units:
                        ask_price = ask_units[0]['ask_price']
                        ask_size = ask_units[0]['ask_size']
                        bid_price = ask_units[0]['bid_price']
                        bid_size = ask_units[0]['bid_size']

                        # 비트코인 수량 출력
                        logging.info(f"비트코인 현재 호가 수량 - 매도: {ask_size}, 매수: {bid_size}")

                        if ask_size >= BITCOIN_TRADE_AMOUNT:
                            message = (f"비트코인 매도 알림: {market_id} ({coin_name})\n"
                                       f"매도 호가 수량: {ask_size:.0f}개 이상입니다.")
                            msg_id = hashlib.md5(message.encode()).hexdigest()
                            if msg_id not in recent_messages:  # 중복 메시지 방지
                                await send_telegram_message(message)
                                recent_messages.add(msg_id)

                        if bid_size >= BITCOIN_TRADE_AMOUNT:
                            message = (f"비트코인 매수 알림: {market_id} ({coin_name})\n"
                                       f"매수 호가 수량: {bid_size:.0f}개 이상입니다.")
                            msg_id = hashlib.md5(message.encode()).hexdigest()
                            if msg_id not in recent_messages:  # 중복 메시지 방지
                                await send_telegram_message(message)
                                recent_messages.add(msg_id)

                continue
            
            # 원화 마켓에서 코인 처리
            recent_trades = await get_recent_trades(market_id)

            if isinstance(recent_trades, list) and recent_trades:
                # 거래 대금 계산하여 전체 리스트에 추가
                trades_with_value = [
                    {**trade, 'total_value': trade['trade_price'] * trade['trade_volume']}
                    for trade in recent_trades
                ]
                all_trade_values.extend(trades_with_value)

                trade_found = any(trade['total_value'] >= TRADE_THRESHOLD for trade in trades_with_value)

                # 현재 가격 및 전일 대비 % 가져오기
                ticker_data = await get_ticker(market_id)
                current_price = ticker_data[0]['trade_price'] if ticker_data and isinstance(ticker_data, list) else 0
                yesterday_price = ticker_data[0]['prev_closing_price'] if ticker_data and isinstance(ticker_data, list) else 0
                change_percentage = ((current_price - yesterday_price) / yesterday_price * 100) if yesterday_price else 0

                if market_id not in EXCLUDED_COINS:
                    if trade_found:
                        # 2000만원 이상 체결된 거래에서 총 금액을 계산
                        total_trade_value = sum(trade['total_value'] for trade in trades_with_value if trade['total_value'] >= TRADE_THRESHOLD)

                        message = (f"매수 알림: {market_id} ({coin_name})\n"
                                   f"최근 거래 중 총 체결 금액이 {format_krw(total_trade_value)}으로 매수 체결되었습니다.\n"
                                   f"현재 가격: {format_krw(current_price)}, 전일 대비: {change_percentage:.2f}%")
                        msg_id = hashlib.md5(message.encode()).hexdigest()
                        if msg_id not in recent_messages:  # 중복 메시지 방지
                            await send_telegram_message(message)
                            recent_messages.add(msg_id)
                else:  # 제외된 코인
                    high_trade_found = any(trade['total_value'] >= HIGH_TRADE_THRESHOLD for trade in trades_with_value)
                    if high_trade_found:
                        total_high_trade_value = sum(trade['total_value'] for trade in trades_with_value if trade['total_value'] >= HIGH_TRADE_THRESHOLD)
                        message = (f"매도 알림: {market_id} ({coin_name})\n"
                                   f"최근 거래 중 7000만원 이상 체결된 거래가 {format_krw(total_high_trade_value)}으로 매도 체결되었습니다.\n"
                                   f"현재 가격: {format_krw(current_price)}, 전일 대비: {change_percentage:.2f}%")
                        msg_id = hashlib.md5(message.encode()).hexdigest()
                        if msg_id not in recent_messages:  # 중복 메시지 방지
                            await send_telegram_message(message)
                            recent_messages.add(msg_id)

        await asyncio.sleep(10)

def run_asyncio_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(monitor_market())

# 전체 예외를 처리하는 에러 핸들러
@app.errorhandler(Exception)
def handle_exception(e):
    logging.error(f"Unhandled Exception: {e}")
    return "Internal Server Error", 500

@app.route('/')
def index():
    ip = request.remote_addr
    print(f"Request from IP: {ip}")  # 요청 IP 확인
    return "Welcome to my application!"

@app.route('/test')
def test():
    try:
        # 여기에 테스트용 코드를 추가할 수 있습니다.
        return 'Test route is working!'
    except Exception as e:
        return f"Error occurred: {e}", 500

if __name__ == '__main__':
    try:
        # 비동기 루프를 새로운 스레드에서 실행
        threading.Thread(target=run_asyncio_loop, daemon=True).start()
        # Railway에서 사용할 포트 설정
        port = int(os.environ.get('PORT', 5000))  # PORT 환경 변수 사용
        app.run(host='0.0.0.0', port=port)  # 포트 변경
    except Exception as e:
        logging.error(f"Failed to start the application: {e}")

