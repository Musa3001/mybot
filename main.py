import ccxt
import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
import time
import logging
from datetime import datetime, timedelta
from flask import Flask
import threading

# --- Keep Alive Web Server ---
app = Flask('')

@app.route('/')
def home():
    return "I'm alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

threading.Thread(target=run).start()


# Your bot code continues below...

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- Binance API keys ---
api_key = 'l8Y2u7neNyQOHG7MMFvyqDx3rxdHJDsoz1qReT4eZvs04iv0VrDsgufqWymBMpdl'
api_secret = 'rVYVtNERlf1A5r8dkB3nt2g6PtdSczwO6kBt9LFxsuzTjXjM5KsGZrM2kYJEbqP2'

# --- Initialize Exchange ---
exchange = ccxt.binance({
    'apiKey': api_key,
    'secret': api_secret,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future'
    }
})

# --- Configurations ---
symbol = 'LTC/USDT'
market_symbol = symbol.replace("/", "")
usdt_to_use = 1.2
leverage = 20
tp_percent = 0.0035  # 0.35%
sl_percent = 0.0015  # 0.15%
slippage_threshold = 0.001  # 0.1%
max_concurrent_trades = 3
open_trade_ids = []
last_trade_time = None
cooldown_period = timedelta(hours=4)


# --- Fetch OHLCV Data ---
def fetch_data(symbol, timeframe='15m', limit=100):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(
            ohlcv,
            columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df
    except Exception as e:
        logging.error(f"Error fetching data: {e}")
        return None


# --- Indicator Calculation ---
def analyze(df):
    try:
        df['RSI'] = RSIIndicator(close=df['close'], window=14).rsi()
        bb = BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bb_upper'] = bb.bollinger_hband()
        df['bb_lower'] = bb.bollinger_lband()
        return df
    except Exception as e:
        logging.error(f"Error in analysis: {e}")
        return None


# --- Wallet Balance ---
def get_wallet_balance():
    try:
        balance = exchange.fetch_balance()
        usdt = balance['total']['USDT']
        logging.info(f'💰 Wallet Balance: {usdt:.2f} USDT')
        return usdt
    except Exception as e:
        logging.error(f"Error fetching balance: {e}")
        return 0


# --- Market Price ---
def get_market_price(symbol):
    try:
        ticker = exchange.fetch_ticker(symbol)
        return ticker['last']
    except Exception as e:
        logging.error(f"Error fetching price: {e}")
        return None


# --- Slippage Detection & Auto Exit ---
def check_slippage_and_exit(order, intended_price, side):
    try:
        actual_price = float(
            order['average']) if 'average' in order else float(order['price'])
        slippage = abs(actual_price - intended_price) / intended_price

        if slippage > slippage_threshold:
            logging.warning(
                f"⚠️ Slippage too high! {slippage*100:.3f}% > {slippage_threshold*100:.2f}%. Auto-exiting trade."
            )
            close_side = 'sell' if side == 'buy' else 'buy'
            exchange.create_market_order(market_symbol, close_side,
                                         order['amount'], {'reduceOnly': True})
            logging.info("🚪 Trade exited due to high slippage.")
            return True
    except Exception as e:
        logging.error(f"❌ Error during slippage exit: {e}")
    return False


# --- Place Trade & Set TP/SL ---
def place_entry_and_exit_orders(side, qty, entry_price):
    global open_trade_ids, last_trade_time
    try:
        if len(open_trade_ids) >= max_concurrent_trades:
            logging.info("🚫 Max open trades reached. No new trades allowed.")
            return

        opposite = 'sell' if side == 'buy' else 'buy'
        tp_price = round(entry_price *
                         (1 + tp_percent), 6) if side == 'buy' else round(
                             entry_price * (1 - tp_percent), 6)
        sl_price = round(entry_price *
                         (1 - sl_percent), 6) if side == 'buy' else round(
                             entry_price * (1 + sl_percent), 6)

        exchange.set_leverage(leverage, symbol)
        exchange.set_margin_mode('isolated', symbol)

        order = exchange.create_market_order(market_symbol, side, qty)
        logging.info(
            f"✅ Entry {side.upper()} at {entry_price:.4f}, qty: {qty}")

        if check_slippage_and_exit(order, entry_price, side):
            return

        open_trade_ids.append(order['id'])

        exchange.create_order(market_symbol, 'take_profit_market', opposite,
                              qty, None, {
                                  'stopPrice': tp_price,
                                  'closePosition': True
                              })
        logging.info(f"🎯 Take Profit set at {tp_price}")

        exchange.create_order(market_symbol, 'stop_market', opposite, qty,
                              None, {
                                  'stopPrice': sl_price,
                                  'closePosition': True
                              })
        logging.info(f"🛑 Stop Loss set at {sl_price}")

        last_trade_time = datetime.now()

    except Exception as e:
        logging.error(f"❌ Error placing orders: {e}")


# --- Update Open Trades ---
def update_open_trades():
    global open_trade_ids
    try:
        positions = exchange.fetch_positions()
        active_ids = []
        for pos in positions:
            if pos['symbol'] == symbol and float(pos['contracts']) > 0:
                active_ids.append(pos['info']['positionId'] if 'positionId' in
                                  pos['info'] else pos['symbol'])
        open_trade_ids = [tid for tid in open_trade_ids if tid in active_ids]
    except Exception as e:
        logging.error(f"Error updating open trades: {e}")


# --- Main Bot Loop ---
while True:
    try:
        df = fetch_data(symbol)
        if df is None:
            time.sleep(60)
            continue

        df = analyze(df)
        if df is None:
            time.sleep(60)
            continue

        update_open_trades()
        logging.info(
            f"📈 Active bot trades: {len(open_trade_ids)} / {max_concurrent_trades}"
        )
        get_wallet_balance()

        last = df.iloc[-1]
        rsi = last['RSI']
        close = last['close']
        upper = last['bb_upper']
        lower = last['bb_lower']

        logging.info(f'🔍 Analyzing Coin: {symbol}')
        logging.info(
            f'📊 RSI: {rsi:.2f}, Close: {close:.4f}, BB_Upper: {upper:.4f}, BB_Lower: {lower:.4f}'
        )

        now = datetime.now()
        if len(open_trade_ids) < max_concurrent_trades:
            if last_trade_time and now - last_trade_time < cooldown_period:
                remaining = cooldown_period - (now - last_trade_time)
                logging.info(
                    f"⏳ Cooldown active. Next trade allowed in {remaining}.")
                time.sleep(60)
                continue

            price = get_market_price(symbol)
            if price is None:
                time.sleep(60)
                continue

            qty = round((usdt_to_use * leverage) / price, 3)

            if rsi < 30 and close < lower:
                place_entry_and_exit_orders('buy', qty, price)
            elif rsi > 70 and close > upper:
                place_entry_and_exit_orders('sell', qty, price)
        else:
            logging.info("⏳ Waiting: Trade limit reached.")

    except Exception as e:
        logging.error(f"⚠️ Error in main loop: {e}")

    time.sleep(2)
