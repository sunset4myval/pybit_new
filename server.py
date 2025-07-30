from fastapi import FastAPI, Request, HTTPException, Query
from pybit.unified_trading import HTTP
from loguru import logger
import os


API_KEY = os.environ.get("API_KEY")
API_SECRET = os.environ.get("API_SECRET")
TESTNET = os.environ.get("TESTNET", "True") == "True"
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")

session = HTTP(testnet=TESTNET, api_key=API_KEY, api_secret=API_SECRET)

app = FastAPI()

logger.add("webhook_logs.log", rotation="10 MB", retention="7 days", compression="zip")


def get_last_price(symbol: str):
    ticker = session.get_tickers(category="spot", symbol=symbol)
    return float(ticker["result"]["list"][0]["lastPrice"])


def get_min_order(symbol: str):
    info = session.get_instruments_info(category="spot", symbol=symbol)
    instruments = info.get("result", {}).get("list", [])

    if not instruments:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found in spot market.")

    min_notional = float(instruments[0]["lotSizeFilter"]["minOrderAmt"])
    return min_notional


@app.get("/")
def test():
    return {"status": "test"}

@app.head("/")
def head_root():
    return Response(status_code=200)

@app.get("/min_order")
async def min_order(symbol: str = Query(..., description="Trading pair symbol, e.g. BTCUSDT")):
    try:
        min_order_amount = get_min_order(symbol)
        return {"symbol": symbol, "min_order_amount": min_order_amount}

    except HTTPException as e:
        raise e  # красиво пробрасываем свою ошибку

    except Exception as e:
        logger.error(f"Ошибка в min_order: {e}")
        raise HTTPException(status_code=500, detail="Ошибка сервера.")


@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
        logger.info(f"Получен сигнал: {data}")

        if data.get("secret") != WEBHOOK_SECRET:
            logger.warning("Попытка несанкционированного доступа.")
            raise HTTPException(status_code=403, detail="Запрещено")

        action = data.get("action").lower()

        # Жёстко запрещаем long/short
        if action not in ["buy", "sell"]:
            logger.warning(f"Недопустимое действие: {action}")
            return {"status": "Недопустимое действие. Разрешены только buy и sell."}

        symbol = data.get("symbol", "BTCUSDT")
        usdt_amount = float(data.get("usdt_amount", 10))

        last_price = get_last_price(symbol)
        logger.info(f"Текущая цена {symbol}: {last_price} USDT")

        min_notional = get_min_order(symbol)
        logger.info(f"Минимально допустимая стоимость ордера: {min_notional} USDT")

        if usdt_amount < min_notional:
            logger.warning(f"Переданная сумма {usdt_amount} USDT меньше минимальной {min_notional} USDT")
            return {"status": "Сумма меньше минимально допустимой", "min_order_amount": min_notional}

        qty = round(usdt_amount / last_price, 6)
        logger.info(f"Рассчитанное количество: {qty} {symbol.split('USDT')[0]} по цене {last_price} USDT")

        if action == "buy":
            logger.info(f"Отправка ордера: side={action.upper()}, qty={qty}, usdt_amount={usdt_amount}, price={last_price}")
            order = session.place_order(
                category="spot",
                symbol=symbol,
                side="Buy",
                order_type="Market",
                qty=qty,
                marketUnit="baseCoin"
            )
            logger.info(f"Ордер на покупку отправлен: {order}")
            return {"status": "Buy order sent", "order": order}

        elif action == "sell":
            logger.info(f"Отправка ордера: side={action.upper()}, qty={qty}, usdt_amount={usdt_amount}, price={last_price}")
            order = session.place_order(
                category="spot",
                symbol=symbol,
                side="Sell",
                order_type="Market",
                qty=qty,
                marketUnit="baseCoin"
            )
            logger.info(f"Ордер на продажу отправлен: {order}")
            return {"status": "Sell order sent", "order": order}

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")
