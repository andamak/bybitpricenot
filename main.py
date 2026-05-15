import asyncio
import logging
import sqlite3
import os
from contextlib import closing
from urllib.parse import urlsplit, urlunsplit

import requests
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, BotCommand, ReplyKeyboardRemove
from aiogram.client.session.aiohttp import AiohttpSession


# ---------------- БАЗОВЫЕ НАСТРОЙКИ ----------------

# Загружаем переменные окружения из .env
load_dotenv()

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

#API_TOKEN = TOKEN
BASE = "https://api.bybit.com"

# Путь к файлу БД рядом с этим скриптом
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "bot.db")

def build_tg_proxy_url() -> str:
    proxy_url = (os.getenv("TG_PROXY_URL") or "").strip()
    if proxy_url:
        return proxy_url

    host = (os.getenv("TG_PROXY_HOST") or "").strip()
    port = (os.getenv("TG_PROXY_PORT") or "").strip()
    user = (os.getenv("TG_PROXY_USER") or "").strip()
    password = (os.getenv("TG_PROXY_PASS") or "").strip()

    if not host or not port:
        return ""

    auth = ""
    if user and password:
        auth = f"{user}:{password}@"
    elif user:
        auth = f"{user}@"

    return f"socks5://{auth}{host}:{port}"


def mask_proxy_url(proxy_url: str) -> str:
    if not proxy_url:
        return ""

    parsed = urlsplit(proxy_url)
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""

    if parsed.username and parsed.password:
        netloc = f"{parsed.username}:***@{hostname}{port}"
    elif parsed.username:
        netloc = f"{parsed.username}@{hostname}{port}"
    else:
        netloc = f"{hostname}{port}"

    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


TG_PROXY_URL = build_tg_proxy_url()


# ---------------- ЛОГИРОВАНИЕ ----------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# в файл
file_handler = logging.FileHandler(os.path.join(BASE_DIR, "bybitnot.log"))
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler.setFormatter(file_formatter)

# в консоль
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter("%(levelname)s - %(message)s")
console_handler.setFormatter(console_formatter)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

# ---------------- AIROGRAM ОБЪЕКТЫ ----------------
dp = Dispatcher()

def create_bot() -> Bot:
    if TG_PROXY_URL:
        logger.info("Telegram proxy enabled: %s", mask_proxy_url(TG_PROXY_URL))
        bot_session = AiohttpSession(proxy=TG_PROXY_URL)
        return Bot(token=TOKEN, session=bot_session)

    logger.info("Telegram proxy disabled")
    return Bot(token=TOKEN)


# ---------------- БАЗА ДАННЫХ ----------------

def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER,
            symbol TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            symbol TEXT,
            direction TEXT,    -- 'up' или 'down'
            target REAL,
            interval_sec INTEGER,
            active INTEGER DEFAULT 1
        )
        """)
        conn.commit()


# ---------------- BYBIT API ----------------

def get_price(symbol: str) -> float:
    endpoint = "/v5/market/tickers"
    params = {"category": "spot", "symbol": symbol.upper()}
    resp = requests.get(BASE + endpoint, params=params, timeout=10)
    data = resp.json()
    if data.get("retCode") != 0 or not data.get("result", {}).get("list"):
        raise ValueError(f"Bybit error: {data}")
    return float(data["result"]["list"][0]["lastPrice"])


# ---------------- ХЕЛПЕР НА СТАРТ ----------------

async def on_startup(bot: Bot):
    await bot.set_my_commands([
        BotCommand(command="start", description="Начало работы"),
        BotCommand(command="id", description="Показать chat_id и user_id"),
        BotCommand(command="price", description="Цена пары или избранного"),
        BotCommand(command="add", description="Добавить пару в избранное"),
        BotCommand(command="list", description="Показать избранные пары"),
        BotCommand(command="del", description="Удалить пару из избранного"),
        BotCommand(command="watch", description="Создать ценовой алерт"),
        BotCommand(command="keyboard", description="Сбросить reply keyboard"),
        ])
    logger.info("Бот запускается")
    if CHAT_ID:
        try:
            await bot.send_message(chat_id=CHAT_ID, text="Чат‑бот ByBitNot онлайн")
        except Exception as e:
            logger.warning(f"Не удалось отправить стартовое сообщение: {e}")
    else:
        logger.warning("CHAT_ID не задан в .env, стартовое сообщение не отправлено")


# ---------------- ХЭНДЛЕРЫ КОМАНД ----------------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я бот для отслеживания цен на Bybit.\n"
        "/add SYMBOL — добавить пару в избранное (например, /add BTCUSDT)\n"
        "/price [SYMBOL] — показать текущую цену пары или всех избранных\n"
        "/list — показать избранные\n"
        "/del SYMBOL — удалить из избранных\n"
        "/watch SYMBOL DIRECTION PRICE INTERVAL — создать алерт\n"
        "/id — показать chat_id и user_id\n"
        "/keyboard — убрать reply keyboard и обновить интерфейс\n"
        "Пример: /watch BTCUSDT up 65000 60"
    )


@dp.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(
        f"chat_id: <b>{message.chat.id}</b>\n"
        f"user_id: <b>{message.from_user.id}</b>"
    )


@dp.message(Command("keyboard"))
async def cmd_keyboard(message: Message):
    await message.answer(
        "Reply keyboard сброшена. Если список команд в кнопке Menu не обновился сразу, закрой и снова открой чат.",
        reply_markup=ReplyKeyboardRemove()
    )


@dp.message(Command("add"))
async def cmd_add(message: Message):
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: /add BTCUSDT")
        return
    symbol = parts[1].upper()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO favorites(user_id, symbol) VALUES (?, ?)",
            (message.from_user.id, symbol)
        )
        conn.commit()
    await message.answer(f"Пара {symbol} добавлена в избранные.")


@dp.message(Command("list"))
async def cmd_list(message: Message):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT symbol FROM favorites WHERE user_id = ?",
            (message.from_user.id,)
        )
        rows = cur.fetchall()

    if not rows:
        await message.answer("Избранных пар пока нет.")
        return

    lines = []
    for (symbol,) in rows:
        try:
            price = get_price(symbol)
            lines.append(f"{symbol}: <b>{price}</b>")
        except Exception as e:
            lines.append(f"{symbol}: ошибка получения цены ({e})")
    await message.answer("\n".join(lines))


@dp.message(Command("price"))
async def cmd_price(message: Message):
    parts = message.text.split()

    if len(parts) == 1:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT symbol FROM favorites WHERE user_id = ?",
                (message.from_user.id,)
            )
            rows = cur.fetchall()
        if not rows:
            await message.answer(
                "В избранном пока нет пар. Добавь пару через /add BTCUSDT"
            )
            return

    lines = []
    for (symbol,) in rows:
        try:
            price = get_price(symbol)
            lines.append(f"{symbol}: <b>{price}</b>")
        except Exception as e:
            lines.append(f"{symbol}: ошибка получения цены ({e})")

    await message.answer("\n".join(lines))
    return

    if len(parts) != 2:
        await message.answer("Использование: /price BTCUSDT или просто /price")
        return

    symbol = parts[1].upper()
    try:
        price = get_price(symbol)
    except Exception as e:
        await message.answer(f"Не удалось получить цену для {symbol}: {e}")
        return

    await message.answer(f"Текущая цена <b>{symbol}</b>: <b>{price}</b>")


@dp.message(Command("del"))
async def cmd_del(message: Message):
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: /del BTCUSDT")
        return
    symbol = parts[1].upper()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM favorites WHERE user_id = ? AND symbol = ?",
            (message.from_user.id, symbol)
        )
        conn.commit()
    await message.answer(f"Пара {symbol} удалена из избранных (если была).")


@dp.message(Command("watch"))
async def cmd_watch(message: Message):
    parts = message.text.split()
    if len(parts) != 5:
        await message.answer(
            "Использование: /watch SYMBOL DIRECTION PRICE INTERVAL\n"
            "Например: /watch BTCUSDT up 65000 60"
        )
        return

    symbol = parts[1].upper()
    direction = parts[2].lower()   # up/down
    try:
        price = float(parts[3])
        interval = int(parts[4])
    except ValueError:
        await message.answer("PRICE должно быть числом, INTERVAL — целым числом секунд.")
        return

    if direction not in ("up", "down"):
        await message.answer("DIRECTION должно быть 'up' или 'down'.")
        return

    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO alerts(user_id, symbol, direction, target, interval_sec, active) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (message.from_user.id, symbol, direction, price, interval)
        )
        conn.commit()

    await message.answer(
        f"Алерт создан: {symbol} {direction} {price}, интервал {interval} сек."
    )


# ---------------- ФОНОВЫЙ ВОРКЕР ----------------

async def alerts_worker(bot: Bot):
    while True:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, user_id, symbol, direction, target, interval_sec "
                "FROM alerts WHERE active = 1"
            )
            alerts = cur.fetchall()

        for alert_id, user_id, symbol, direction, target, interval_sec in alerts:
            try:
                price = get_price(symbol)
            except Exception as e:
                logger.warning(f"Ошибка получения цены {symbol}: {e}")
                continue

            is_trigger = (
                (direction == "up" and price >= target) or
                (direction == "down" and price <= target)
            )
            if is_trigger:
                text = (
                    f"Алерт по <b>{symbol}</b>!\n"
                    f"Текущая цена: {price}, условие: {direction} {target}"
                )
                try:
                    await bot.send_message(chat_id=user_id, text=text)
                except Exception as e:
                    logger.warning(f"Ошибка отправки сообщения: {e}")
                # одноразовый алерт — деактивируем
                with closing(sqlite3.connect(DB_PATH)) as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "UPDATE alerts SET active = 0 WHERE id = ?",
                        (alert_id,)
                    )
                    conn.commit()

            await asyncio.sleep(interval_sec)

        await asyncio.sleep(5)


# ---------------- ТОЧКА ВХОДА ----------------

async def main():
    bot = create_bot()
    init_db()
    await on_startup(bot)
    asyncio.create_task(alerts_worker(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())