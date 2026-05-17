import asyncio
import sqlite3
import os
import time
import logging
import sys
import io
import zipfile
import re
import shutil
from urllib.parse import urlsplit
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import LabeledPrice, PreCheckoutQuery, Message, InputMediaPhoto, InlineKeyboardButton, FSInputFile
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from telethon import TelegramClient, functions
from telethon.errors import (SessionPasswordNeededError, UserDeactivatedBanError,
                              UserDeactivatedError, AuthKeyUnregisteredError, FloodWaitError,
                              PhoneCodeExpiredError, PhoneCodeInvalidError, PeerFloodError)
from telethon.tl.functions.payments import GetStarsStatusRequest
from telethon.tl.types import InputPeerSelf

# --- ИМПОРТ CRYPTOPAY ---
try:
    from aiocryptopay import AioCryptoPay, Networks
    from aiocryptopay.const import Assets, CurrencyType
except ImportError:
    AioCryptoPay = None
    CurrencyType = None
    Assets = None

# --- КОНФИГУРАЦИЯ ---
API_TOKEN = '8604631055:AAHOgN_OukMzDoWkpWtiT-O9ZUKqpp2Tqb4'
API_ID = 20652575
API_HASH = 'c0d5c94ec3c668444dca9525940d876d'
ADMIN_ID = 7785932103
LOG_CHAT_ID = ADMIN_ID
CRYPTO_PAY_TOKEN = '540011:AARTDw8jiNvxfbJNrCKkEp4l6l50XTuJOYX'
SUPPORT_URL = "https://t.me/stv18"
REVIEW_CHAT_URL = "https://t.me/DutsiOtziv"
REVIEW_CHAT_USERNAME = "dutsiotziv"
REVIEW_TRIGGER = "@dutsibot"
REVIEW_BONUS = 0.1
STAR_RATE = 0.02
TG_STARS_PACKS = [15, 25, 50, 75, 100, 125, 150, 175, 200]

# ─── БОТЫ-НАБЛЮДАТЕЛИ (до 3 токенов, уведомляют об событиях) ─────────────────
# Заполните токены нужных ботов. Пустая строка = слот не используется.
NOTIFY_BOT_TOKENS: list[str] = [
    "",   # слот 1
    "",   # слот 2
    "",   # слот 3
]
MAX_RENT_TIME = 1200
MIN_RENT_TIME = 30
MIN_INTERVAL = 150  # Минимальный интервал между сообщениями (секунды)
EARLY_RENT_REFUND_RATIO = 0.80

# ССЫЛКИ НА КАРТИНКИ
IMG_MAIN = "https://ibb.co/sJWZsysK"
IMG_CATALOG = "https://ibb.co/cK8tN0HF"
IMG_BALANCE = "https://ibb.co/Ndz21JVZ"
IMG_MY_RENT = "https://ibb.co/mCKSN9yG"
IMG_TGACC = "https://ibb.co/PZz613zX"
IMG_HELP = "https://ibb.co/35V8c6zL"
IMG_SUPPORT = "https://ibb.co/srJJwx1"
IMG_REVIEWS = "https://ibb.co/pGwVDx6"
IMG_RULES = "https://ibb.co/35V8c6zL"

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format='%(levelname)s:%(name)s:%(message)s')
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "bot_data.db")

# Последнее «панельное» сообщение бота в чате пользователя (меню с фото и т.п.) — удаляем перед новым
USER_PANEL_MESSAGE: dict[int, int] = {}


async def delete_tracked_panel(chat_id: int, uid: int) -> None:
    mid = USER_PANEL_MESSAGE.pop(uid, None)
    if not mid:
        return
    try:
        await bot.delete_message(chat_id, mid)
    except Exception:
        pass


def track_panel_message(uid: int, message_id: int) -> None:
    USER_PANEL_MESSAGE[uid] = message_id


async def send_panel_photo(
    event: Message | types.CallbackQuery,
    *,
    photo: str,
    caption: str,
    reply_markup=None,
    parse_mode: str = "Markdown",
):
    uid = event.from_user.id
    if isinstance(event, Message):
        await delete_tracked_panel(event.chat.id, uid)
        try:
            await event.delete()
        except Exception:
            pass
        sent = await event.answer_photo(
            photo=photo,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        track_panel_message(uid, sent.message_id)
        return sent

    try:
        await event.message.edit_media(
            media=InputMediaPhoto(media=photo, caption=caption, parse_mode=parse_mode),
            reply_markup=reply_markup,
        )
        track_panel_message(uid, event.message.message_id)
        return event.message
    except Exception:
        await delete_tracked_panel(event.message.chat.id, uid)
        sent = await event.message.answer_photo(
            photo=photo,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        track_panel_message(uid, sent.message_id)
        try:
            await event.message.delete()
        except Exception:
            pass
        return sent


async def send_panel_text(
    event: Message | types.CallbackQuery,
    *,
    text: str,
    reply_markup=None,
    parse_mode: str = "Markdown",
):
    uid = event.from_user.id
    if isinstance(event, Message):
        await delete_tracked_panel(event.chat.id, uid)
        try:
            await event.delete()
        except Exception:
            pass
        sent = await event.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        track_panel_message(uid, sent.message_id)
        return sent

    try:
        await event.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        track_panel_message(uid, event.message.message_id)
        return event.message
    except Exception:
        await delete_tracked_panel(event.message.chat.id, uid)
        sent = await event.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        track_panel_message(uid, sent.message_id)
        try:
            await event.message.delete()
        except Exception:
            pass
        return sent


def get_accepted_send_assets() -> list[str]:
    supported_assets = ['USDT', 'TON', 'BTC', 'ETH', 'USDC', 'BNB', 'TRX', 'LTC']
    if Assets:
        available_assets = {asset.value for asset in Assets}
        filtered_assets = [asset for asset in supported_assets if asset in available_assets]
        if filtered_assets:
            return filtered_assets
    return supported_assets


def get_rent_refund_info(phone: str, refund_ratio: float = 1.0, user_id: int | None = None):
    now = int(time.time())
    query = (
        'SELECT owner_id, expires, price_per_min FROM accounts '
        'WHERE phone = ? AND owner_id IS NOT NULL AND expires > ?'
    )
    params: list[object] = [phone, now]
    if user_id is not None:
        query += ' AND owner_id = ?'
        params.append(user_id)

    res = db_fetchone(query, tuple(params))
    if not res:
        return None

    owner_id, expires, price_per_min = res
    remaining_seconds = max(0, expires - now)
    if remaining_seconds <= 0:
        return None

    remaining_minutes = remaining_seconds / 60
    full_amount = round(remaining_minutes * price_per_min, 2)
    refund_amount = round(full_amount * refund_ratio, 2)
    return {
        "owner_id": owner_id,
        "expires": expires,
        "remaining_seconds": remaining_seconds,
        "remaining_minutes": remaining_minutes,
        "full_amount": full_amount,
        "refund_amount": refund_amount,
    }

# Словарь активных TelegramClient-ов для авторизации: {user_id: client}
active_clients: dict = {}

crypto = None
if AioCryptoPay:
    crypto = AioCryptoPay(token=CRYPTO_PAY_TOKEN, network=Networks.MAIN_NET)

# --- БАЗА ДАННЫХ ---
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.execute('PRAGMA journal_mode=WAL')
db.execute('PRAGMA busy_timeout=5000')
cur = db.cursor()


def init_db():
    cur.execute('''CREATE TABLE IF NOT EXISTS accounts 
                   (phone TEXT PRIMARY KEY, owner_id INTEGER, expires INTEGER, 
                    text TEXT DEFAULT 'Привет!', photo_id TEXT, 
                    interval INTEGER DEFAULT 30, chats TEXT DEFAULT '',
                    is_running INTEGER DEFAULT 0, price_per_min REAL DEFAULT 0.10,
                    catalog_chats TEXT DEFAULT '')''')
    cur.execute('''CREATE TABLE IF NOT EXISTS users 
                   (user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 0.0)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS payments 
                   (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL, method TEXT, date TEXT)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS rent_history 
                   (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, phone TEXT, duration INTEGER, cost REAL, date TEXT)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS blacklist (word TEXT PRIMARY KEY)''')

    try:
        cur.execute('ALTER TABLE accounts ADD COLUMN is_premium INTEGER DEFAULT 0')
    except:
        pass
    try:
        cur.execute('ALTER TABLE accounts ADD COLUMN notified_10m INTEGER DEFAULT 0')
    except:
        pass
    try:
        cur.execute('ALTER TABLE users ADD COLUMN banned_until INTEGER DEFAULT 0')
    except:
        pass
    try:
        cur.execute('ALTER TABLE users ADD COLUMN ban_reason TEXT DEFAULT ""')
    except:
        pass
    try:
        cur.execute('ALTER TABLE accounts ADD COLUMN catalog_chats TEXT DEFAULT ""')
    except Exception:
        pass
    # Убеждаемся что колонка is_running существует (нужна для restore_active_broadcasts)
    try:
        cur.execute('ALTER TABLE accounts ADD COLUMN is_running INTEGER DEFAULT 0')
    except Exception:
        pass
    try:
        cur.execute('ALTER TABLE accounts ADD COLUMN password_2fa TEXT DEFAULT ""')
    except Exception:
        pass

    # --- Таблицы клонов ---
    cur.execute('''CREATE TABLE IF NOT EXISTS clones
                   (bot_id TEXT PRIMARY KEY,
                    api_token TEXT NOT NULL,
                    owner_id INTEGER NOT NULL,
                    bot_username TEXT DEFAULT '',
                    created INTEGER DEFAULT 0,
                    is_running INTEGER DEFAULT 0,
                    earned REAL DEFAULT 0.0,
                    withdrawn REAL DEFAULT 0.0)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS clone_withdraw_requests
                   (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id TEXT, owner_id INTEGER,
                    bot_username TEXT, amount REAL,
                    wallet TEXT, status TEXT DEFAULT 'pending',
                    date TEXT)''')

    cur.execute('''CREATE TABLE IF NOT EXISTS notify_bots
                   (slot INTEGER PRIMARY KEY, token TEXT DEFAULT '', label TEXT DEFAULT '')''')
    for slot in (1, 2, 3):
        cur.execute('INSERT OR IGNORE INTO notify_bots (slot, token, label) VALUES (?,?,?)',
                    (slot, '', f'Бот {slot}'))

    # Таблица глобальных настроек основного бота
    cur.execute('''CREATE TABLE IF NOT EXISTS bot_settings
                   (key TEXT PRIMARY KEY, value TEXT DEFAULT "")''')
    cur.execute('''CREATE TABLE IF NOT EXISTS tg_accounts
                   (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone TEXT UNIQUE,
                    lot_name TEXT DEFAULT '',
                    session_name TEXT,
                    tdata_folder TEXT,
                    password_2fa TEXT DEFAULT '',
                    price REAL DEFAULT 0.0,
                    is_sold INTEGER DEFAULT 0,
                    sold_to INTEGER,
                    sold_at INTEGER DEFAULT 0)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS tg_star_accounts
                   (phone TEXT PRIMARY KEY,
                    is_active INTEGER DEFAULT 1,
                    added_at INTEGER DEFAULT 0)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS tg_star_orders
                   (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    target_username TEXT,
                    stars_count INTEGER,
                    amount_usd REAL,
                    packs TEXT DEFAULT "",
                    account_phone TEXT DEFAULT "",
                    status TEXT DEFAULT "pending",
                    error_text TEXT DEFAULT "",
                    created_at INTEGER DEFAULT 0,
                    completed_at INTEGER DEFAULT 0)''')
    cur.execute('''CREATE TABLE IF NOT EXISTS review_rewards
                   (user_id INTEGER PRIMARY KEY,
                    amount REAL DEFAULT 0.0,
                    rewarded_at INTEGER DEFAULT 0,
                    review_text TEXT DEFAULT "")''')
    try:
        cur.execute('ALTER TABLE tg_accounts ADD COLUMN tdata_path TEXT DEFAULT ""')
    except Exception:
        pass
    try:
        cur.execute('ALTER TABLE tg_accounts ADD COLUMN added_at INTEGER DEFAULT 0')
    except Exception:
        pass
    # show_clone_accounts: "1" — показывать аккаунты клонов/суб-клонов в каталоге
    cur.execute("INSERT OR IGNORE INTO bot_settings (key,value) VALUES ('show_clone_accounts','0')")
    cur.execute("INSERT OR IGNORE INTO bot_settings (key,value) VALUES ('star_rate', ?)", (str(STAR_RATE),))
    db.commit()

    default_words = ['темка', 'чернуха', 'скам', '$кам']
    for w in default_words:
        cur.execute('INSERT OR IGNORE INTO blacklist (word) VALUES (?)', (w,))
    db.commit()


init_db()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return bool(row)


def _legacy_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM legacy.sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return bool(row)


def _table_columns(conn: sqlite3.Connection, table: str, schema: str = "main") -> list[str]:
    rows = conn.execute(f"PRAGMA {schema}.table_info({table})").fetchall()
    return [r[1] for r in rows]


def _is_main_db_empty() -> bool:
    checks = []
    for table in ("users", "payments", "rent_history", "accounts"):
        try:
            cnt = db_fetchone(f"SELECT COUNT(*) FROM {table}", ())
            checks.append((cnt[0] if cnt else 0) == 0)
        except Exception:
            checks.append(True)
    return all(checks)


def _find_legacy_db_candidates() -> list[str]:
    candidates = [
        os.path.abspath("bot_data.db"),
        os.path.join(os.getcwd(), "bot_data.db"),
        os.path.join(BASE_DIR, "..", "bot_data.db"),
        os.path.join(BASE_DIR, "..", "..", "bot_data.db"),
    ]
    normalized = []
    current = os.path.abspath(DB_PATH)
    for p in candidates:
        ap = os.path.abspath(p)
        if ap == current:
            continue
        if not os.path.isfile(ap):
            continue
        if os.path.getsize(ap) <= 0:
            continue
        if ap not in normalized:
            normalized.append(ap)
    return normalized


def import_legacy_data_if_needed():
    if not _is_main_db_empty():
        return

    tables = [
        "accounts",
        "users",
        "payments",
        "rent_history",
        "blacklist",
        "clones",
        "clone_withdraw_requests",
        "notify_bots",
        "bot_settings",
        "tg_accounts",
        "tg_star_accounts",
        "tg_star_orders",
        "review_rewards",
    ]

    for legacy_path in _find_legacy_db_candidates():
        try:
            db.execute("ATTACH DATABASE ? AS legacy", (legacy_path,))
            has_users = _legacy_table_exists(db, "users")
            if not has_users:
                db.execute("DETACH DATABASE legacy")
                continue

            migrated_any = False
            for table in tables:
                if not _table_exists(db, table):
                    continue
                if not _legacy_table_exists(db, table):
                    continue
                dst_cols = set(_table_columns(db, table, "main"))
                src_cols = [c for c in _table_columns(db, table, "legacy") if c in dst_cols]
                if not src_cols:
                    continue
                cols_sql = ", ".join(src_cols)
                db.execute(
                    f"INSERT OR IGNORE INTO {table} ({cols_sql}) "
                    f"SELECT {cols_sql} FROM legacy.{table}"
                )
                migrated_any = True

            if migrated_any:
                db.commit()
                logging.info(f"[db] Imported legacy data from {legacy_path}")
                db.execute("DETACH DATABASE legacy")
                return

            db.execute("DETACH DATABASE legacy")
        except Exception as e:
            logging.error(f"[db] Legacy import failed from {legacy_path}: {e}")
            try:
                db.execute("DETACH DATABASE legacy")
            except Exception:
                pass


import_legacy_data_if_needed()


def get_main_setting(key: str, default: str = '0') -> str:
    res = db_fetchone('SELECT value FROM bot_settings WHERE key=?', (key,))
    return res[0] if res else default

def set_main_setting(key: str, value: str):
    cur.execute('INSERT OR REPLACE INTO bot_settings (key,value) VALUES (?,?)', (key, value))
    db.commit()


def get_star_rate() -> float:
    try:
        return float(get_main_setting("star_rate", str(STAR_RATE)))
    except Exception:
        return STAR_RATE


def get_tab_image(section: str) -> str:
    defaults = {
        "main": IMG_MAIN,
        "catalog": IMG_CATALOG,
        "profile": IMG_BALANCE,
        "myrent": IMG_MY_RENT,
        "tgacc": IMG_TGACC,
        "help": IMG_HELP,
        "support": IMG_SUPPORT,
        "reviews": IMG_REVIEWS,
        "rules": IMG_RULES,
    }
    return get_main_setting(f"img_{section}", defaults.get(section, IMG_MAIN))


def get_clone_db(bot_id: str):
    """Открывает БД клона и возвращает (conn, cursor). Caller должен закрыть conn."""
    path = f"clone_{bot_id}.db"
    if not os.path.exists(path):
        return None, None
    try:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute('PRAGMA busy_timeout=3000')
        return conn, conn.cursor()
    except Exception:
        return None, None


class States(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_password = State()
    waiting_for_tgp = State()
    waiting_for_rent_time = State()
    edit_text = State()
    edit_chats = State()
    edit_photo = State()
    edit_interval = State()
    top_up_amount = State()
    broadcast_all = State()
    tg_stars_custom_qty = State()
    tg_stars_username = State()
    staracc_add_phone = State()
    staracc_del_phone = State()


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def db_fetchone(query, params=()):
    c = db.cursor()
    c.execute(query, params)
    return c.fetchone()


def db_fetchall(query, params=()):
    c = db.cursor()
    c.execute(query, params)
    return c.fetchall()


def get_balance(user_id):
    res = db_fetchone('SELECT balance FROM users WHERE user_id = ?', (user_id,))
    return round(res[0], 2) if res else None


def check_ban(user_id):
    res = db_fetchone('SELECT banned_until, ban_reason FROM users WHERE user_id = ?', (user_id,))
    if res and res[0] > int(time.time()):
        return res[0], res[1]
    return None


def add_payment_history(user_id, amount, method):
    date = time.strftime('%Y-%m-%d %H:%M:%S')
    cur.execute('INSERT INTO payments (user_id, amount, method, date) VALUES (?, ?, ?, ?)',
                (user_id, amount, method, date))
    cur.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
    db.commit()
    append_daily_log(f"TOPUP | user_id={user_id} | amount=${round(float(amount or 0),2)} | method={method}")


def contains_bad_words(text):
    words = [row[0] for row in db_fetchall('SELECT word FROM blacklist')]
    text_lower = text.lower()
    for w in words:
        if w in text_lower:
            return w
    return None


def main_menu(user_id=None):
    kb = ReplyKeyboardBuilder()
    kb.button(text="📂 Каталог аккаунтов")
    kb.button(text="📱 ТгАккаунт")
    kb.button(text="🔑 Моя аренда")
    kb.button(text="👤 Профиль")
    kb.button(text="📘 Правила")
    kb.button(text="📝 Отзывы")
    kb.button(text="❓ Помощь")
    kb.button(text="👨‍💻 Support")
    if user_id and user_id == ADMIN_ID:
        kb.button(text="🔧 Админ панель")
        kb.adjust(2, 2, 2, 2, 1)
    else:
        kb.adjust(2, 2, 2, 1)
    return kb.as_markup(resize_keyboard=True)


def classify_account_issue(exc: Exception) -> str:
    txt = str(exc).lower()
    name = type(exc).__name__.lower()
    if isinstance(exc, (UserDeactivatedBanError, UserDeactivatedError, AuthKeyUnregisteredError)):
        return "бан/деактивация"
    if isinstance(exc, PeerFloodError):
        return "спамблок"
    if isinstance(exc, FloodWaitError):
        return "ограничение (FloodWait)"
    if "frozen" in txt or "freeze" in txt or "заморож" in txt:
        return "заморозка"
    if "spam" in txt or "peerflood" in txt or "restricted" in txt:
        return "спамблок/ограничения"
    if "banned" in txt or "deactivated" in txt or "auth key" in txt:
        return "бан/деактивация"
    return "техническая ошибка"


async def notify_account_issue(phone: str, issue: str, exc: Exception, chat_ref: str | None = None):
    owner_row = db_fetchone('SELECT owner_id FROM accounts WHERE phone = ?', (phone,))
    current_owner = owner_row[0] if owner_row else None
    last_renter_row = db_fetchone(
        'SELECT user_id FROM rent_history WHERE phone = ? ORDER BY id DESC LIMIT 1',
        (phone,),
    )
    last_renter = last_renter_row[0] if last_renter_row else None
    chat_line = f"\n📨 Чат: `{chat_ref}`" if chat_ref else ""
    await notify_admins(
        f"🚨 **Проблема с аккаунтом в рассылке**\n"
        f"📱 Номер: `{phone}`\n"
        f"⚠️ Статус: **{issue}**\n"
        f"🆔 Текущий арендатор: `{current_owner or '—'}`\n"
        f"🆔 Последний арендатор: `{last_renter or '—'}`"
        f"{chat_line}\n"
        f"🧩 Ошибка: `{type(exc).__name__}: {str(exc)[:250]}`",
    )


def back_kb(to="to_main"):
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=to)
    kb.adjust(1)
    return kb


def extract_chat_and_topic(chat_str):
    chat_str = (chat_str or "").strip()
    if not chat_str:
        raise ValueError("Пустая ссылка на чат")

    if chat_str.startswith(("http://", "https://")):
        parsed = urlsplit(chat_str)
        chat_str = parsed.path

    chat_str = chat_str.strip().strip("/")
    if chat_str.startswith("@"):
        chat_str = chat_str[1:]
    if chat_str.startswith("t.me/"):
        chat_str = chat_str[5:]
    if chat_str.startswith("s/"):
        chat_str = chat_str[2:]

    parts = [part for part in chat_str.split("/") if part]
    if len(parts) >= 2:
        if parts[0] == "c" and parts[1].isdigit():
            topic_id = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else None
            return int("-100" + parts[1]), topic_id
        if parts[1].isdigit():
            return parts[0], int(parts[1])

    try:
        return int(chat_str), None
    except ValueError:
        return chat_str, None


async def resolve_chat_entity(client: TelegramClient, chat_ref):
    if isinstance(chat_ref, str):
        chat_ref = chat_ref.strip()
        if chat_ref.startswith("@"):
            chat_ref = chat_ref[1:]
    return await client.get_input_entity(chat_ref)


async def send_broadcast_payload(
    client: TelegramClient,
    entity,
    text: str,
    topic_id: int | None = None,
    photo_bytes: bytes | None = None,
):
    async def _send(reply_to: int | None):
        if photo_bytes is not None:
            buf = io.BytesIO(photo_bytes)
            buf.name = "img.jpg"
            kwargs = {"caption": text}
            if reply_to is not None:
                kwargs["reply_to"] = reply_to
            await client.send_file(entity, buf, **kwargs)
            return

        kwargs = {}
        if reply_to is not None:
            kwargs["reply_to"] = reply_to
        await client.send_message(entity, text, **kwargs)

    try:
        await _send(topic_id)
    except Exception:
        if topic_id is None:
            raise
        logging.warning("Не удалось отправить сообщение в тему, повторяем в сам чат.")
        await _send(None)


def format_time_left(expires):
    """Форматирует оставшееся время аренды."""
    left = expires - int(time.time())
    if left <= 0:
        return "истекло"
    hours = left // 3600
    minutes = (left % 3600) // 60
    if hours > 0:
        return f"{hours}ч {minutes}м"
    return f"{minutes}м"


def append_daily_log(line: str) -> None:
    try:
        day = time.strftime("%d-%m-%Y")
        os.makedirs("logs", exist_ok=True)
        path = os.path.join("logs", f"log {day}.txt")
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {line}\n")
    except Exception as e:
        logging.error(f"[daily-log] {e}")


def split_stars_to_packs(total: int) -> list[int]:
    # Разбиваем сумму на доступные "подарочные" пакеты.
    denoms = sorted(TG_STARS_PACKS, reverse=True)
    dp = {0: []}
    for s in range(1, total + 1):
        best = None
        for d in denoms:
            if s - d >= 0 and (s - d) in dp:
                cand = dp[s - d] + [d]
                if best is None or len(cand) < len(best):
                    best = cand
        if best is not None:
            dp[s] = best
    return sorted(dp.get(total, []))


# --- ФОНОВАЯ ЗАДАЧА УВЕДОМЛЕНИЙ ---
async def notify_admins(text: str, photo_id: str = None):
    """Отправляет уведомление ТОЛЬКО через ботов-наблюдателей из БД.
    Прямые сообщения от основного бота администратору НЕ отправляются."""
    rows = db_fetchall('SELECT token FROM notify_bots WHERE token != ""')
    active_tokens = [r[0] for r in rows if r[0].strip()]

    async def _send(b: Bot, chat_id: int):
        try:
            if photo_id:
                await b.send_photo(chat_id, photo=photo_id, caption=text, parse_mode="Markdown")
            else:
                await b.send_message(chat_id, text, parse_mode="Markdown")
        except Exception:
            pass

    if not active_tokens:
        # Нет наблюдателей — молча игнорируем
        return

    for tok in active_tokens:
        try:
            nb = Bot(token=tok)
            await _send(nb, ADMIN_ID)
            await nb.session.close()
        except Exception:
            pass


# --- АДМИН КОМАНДЫ ---
@dp.message(Command("ahelp"))
async def cmd_ahelp(message: Message):
    if message.from_user.id != ADMIN_ID: return
    text = (
        "🛠 **Команды администратора:**\n\n"
        "**— Основной бот —**\n"
        "`/addacc` — Добавить аккаунт в базу\n"
        "`/delacc +7999...` — Удалить аккаунт\n"
        "`/unnomber +7999...` — Снять аренду досрочно\n"
        "`/ban ID ЧАСЫ ПРИЧИНА` — Забанить пользователя\n"
        "`/stats ID` — Статистика пользователя\n"
        "`/givebal ID СУММА` — Выдать баланс\n"
        "`/delbal ID СУММА` — Списать баланс\n"
        "`/setprice +7999... 0.15` — Цена аккаунта в основном боте\n"
        "`/blacklist слово` — Добавить стоп-слово\n"
        "`/redak +7999... чаты` — Чаты каталога\n"
        "`/all сообщение` — Рассылка всем пользователям\n"
        "`/pm ID сообщение` — Написать пользователю\n"
        "`/lots +7999..., Название, 25.5` — Обновить лот в ТгАккаунтах\n\n"
        "`/setimg раздел, ссылка` — Установить картинку раздела\n\n"
        "📩 Пользователи пишут через `/pma` — приходит уведомление с командой для ответа"
    )
    await message.answer(text, parse_mode="Markdown")


@dp.message(Command("setimg"))
async def cmd_setimg(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args or "," not in command.args:
        return await message.answer(
            "⚠️ Формат: `/setimg раздел, ссылка`\n"
            "Разделы: `main, catalog, profile, myrent, tgacc, help, support, reviews, rules`",
            parse_mode="Markdown",
        )
    try:
        section, url = [x.strip().lower() for x in command.args.split(",", 1)]
        allowed = {"main", "catalog", "profile", "myrent", "tgacc", "help", "support", "reviews", "rules"}
        if section not in allowed:
            raise ValueError("section")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError("url")
        set_main_setting(f"img_{section}", url)
        await message.answer(f"✅ Картинка для `{section}` обновлена:\n{url}", parse_mode="Markdown")
    except Exception:
        await message.answer("❌ Ошибка формата. Пример: `/setimg profile, https://...`", parse_mode="Markdown")


@dp.message(Command("redakstar"))
async def cmd_redakstar(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("ℹ️ Эта функция отключена.")

@dp.message(Command("lots"))
async def cmd_lots(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args:
        return await message.answer(
            "⚠️ Формат: `/lots +79991234567, Название лота, 25.5`",
            parse_mode="Markdown",
        )
    try:
        parts = [p.strip() for p in command.args.split(",")]
        if len(parts) != 3:
            raise ValueError("bad format")
        phone, lot_name, price_raw = parts
        if not phone.startswith("+"):
            phone = "+" + phone
        price = float(price_raw.replace(",", "."))
        if price <= 0:
            raise ValueError("bad price")

        exists = db_fetchone('SELECT 1 FROM tg_accounts WHERE phone = ?', (phone,))
        if not exists:
            return await message.answer(
                f"❌ Аккаунт `{phone}` не найден в `tg_accounts`. Сначала добавьте его через `ТгАккаунты`.",
                parse_mode="Markdown",
            )

        cur.execute(
            'UPDATE tg_accounts SET lot_name = ?, price = ?, is_sold = 0 WHERE phone = ?',
            (lot_name, price, phone),
        )
        db.commit()
        await message.answer(
            f"✅ Лот обновлён:\n`{phone}`, **{lot_name}**, **${price}**",
            parse_mode="Markdown",
        )
    except Exception:
        await message.answer(
            "⚠️ Неверный формат. Используйте:\n`/lots +79991234567, Название лота, 25.5`",
            parse_mode="Markdown",
        )


@dp.message(Command("addstaracc"))
async def cmd_addstaracc(message: Message, command: CommandObject, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    await state.clear()
    await message.answer("ℹ️ Эта функция отключена.")


@dp.message(Command("liststaracc"))
async def cmd_liststaracc(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("ℹ️ Эта функция отключена.")


# ═══════════════════════════════════════════════════════════════
# 🔧 АДМИН ПАНЕЛЬ (кнопка, только для ADMIN_ID)
# ═══════════════════════════════════════════════════════════════

@dp.message(F.text == "🔧 Админ панель")
async def admin_panel_menu(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    await state.clear()
    await _show_admin_panel(m)

async def _show_admin_panel(event, edit=False):
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить аккаунт",   callback_data="adm_addacc")
    kb.button(text="🗑 Удалить аккаунт",    callback_data="adm_delacc")
    kb.button(text="⛔ Снять аренду",       callback_data="adm_unnomber")
    kb.button(text="🚫 Забанить польз.",    callback_data="adm_ban")
    kb.button(text="📊 Стат. польз.",       callback_data="adm_stats")
    kb.button(text="💲 Цена номера",        callback_data="adm_setprice")
    kb.button(text="💰 Выдать баланс",      callback_data="adm_givebal")
    kb.button(text="➖ Списать баланс",     callback_data="adm_delbal")
    kb.button(text="🚷 Стоп-слово",         callback_data="adm_blacklist")
    kb.button(text="📋 Редакт. чаты",       callback_data="adm_redak")
    kb.button(text="📢 Рассылка всем",      callback_data="adm_broadcast")
    kb.button(text="📩 Написать польз.",    callback_data="adm_pm")
    kb.button(text="✅ Проверка на Валидность", callback_data="adm_check_validity_menu")
    kb.button(text="📱 ТгАккаунты",         callback_data="adm_tgacc")
    kb.button(text="🔔 Боты-наблюдатели",   callback_data="adm_notify_bots")
    kb.adjust(2)
    text = "🔧 **Админ панель**\n\nВыберите действие:"
    if edit:
        await event.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")
    else:
        await event.answer(text, reply_markup=kb.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data == "adm_panel")
async def adm_panel_cb(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.clear()
    await _show_admin_panel(call, edit=True)


# ── Состояния для Админ панели ────────────────────────────────
class AdminPanelStates(StatesGroup):
    adm_addacc_phone    = State()
    adm_delacc_phone    = State()
    adm_unnomber_phone  = State()
    adm_ban_input       = State()
    adm_stats_uid       = State()
    adm_setprice_input  = State()
    adm_givebal_input   = State()
    adm_delbal_input    = State()
    adm_blacklist_word  = State()
    adm_redak_input     = State()
    adm_broadcast_text  = State()
    adm_pm_input        = State()
    adm_notify_bot_token = State()  # ввод токена бота-наблюдателя
    adm_check_validity_phone = State()
    tgacc_export_phone   = State()
    tgacc_add_phone      = State()
    tgacc_add_code       = State()
    tgacc_add_2fa        = State()
    tgacc_set_price      = State()
    tgacc_set_lot        = State()
    staracc_add_phone    = State()
    staracc_del_phone    = State()


@dp.callback_query(F.data == "adm_check_validity_menu")
async def adm_check_validity_menu(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.clear()
    rows = db_fetchall(
        'SELECT phone, owner_id, expires FROM accounts ORDER BY phone ASC LIMIT 80',
        (),
    )
    kb = InlineKeyboardBuilder()
    if rows:
        now = int(time.time())
        for phone, owner_id, expires in rows:
            is_busy = owner_id is not None and (expires or 0) > now
            status = "🟢" if is_busy else "⚪"
            kb.button(text=f"{status} {phone}", callback_data=f"adm_check_validity_{phone}")
    kb.button(text="✍️ Ввести номер вручную", callback_data="adm_check_validity_manual")
    kb.button(text="⬅️ Назад", callback_data="adm_panel")
    kb.adjust(1)
    await call.message.edit_text(
        "✅ **Проверка на Валидность**\n\n"
        "Выберите номер для проверки статуса: спамблок, заморозка, блокировка.",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown",
    )


@dp.callback_query(F.data == "adm_check_validity_manual")
async def adm_check_validity_manual(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text(
        "📱 Введите номер для проверки (например: `+79991234567`):",
        reply_markup=back_kb("adm_check_validity_menu").as_markup(),
        parse_mode="Markdown",
    )
    await state.set_state(AdminPanelStates.adm_check_validity_phone)


@dp.message(AdminPanelStates.adm_check_validity_phone)
async def adm_check_validity_phone_input(m: Message, state: FSMContext):
    phone = (m.text or "").strip().replace(" ", "")
    await _run_rent_account_validity_check(m, phone)
    await state.clear()


async def _validate_rent_account(phone: str) -> tuple[bool, str]:
    session_path = os.path.join("sessions", phone)
    c = TelegramClient(session_path, API_ID, API_HASH)
    try:
        await c.connect()
        if not await c.is_user_authorized():
            return False, "сессия не авторизована"

        await c.get_me()

        try:
            sb = await c.get_entity("SpamBot")
            await c.send_message(sb, "/start")
            await asyncio.sleep(1.5)
            msgs = await c.get_messages(sb, limit=3)
            txt = " ".join([(msg.message or "") for msg in msgs]).lower()
            ok_keys = [
                "свободен от каких-либо ограничений",
                "no limits are currently applied",
                "good news",
            ]
            if any(k in txt for k in ok_keys):
                return True, "валиден"
            bad_keys = [
                "огранич", "спам", "временно", "доступ ограничен",
                "limited", "restriction", "spam", "temporarily limited",
            ]
            if any(k in txt for k in bad_keys):
                return False, "спамблок/ограничения от SpamBot"
        except Exception:
            pass

        return True, "валиден"
    except (UserDeactivatedBanError, UserDeactivatedError, AuthKeyUnregisteredError):
        return False, "бан/деактивация/недействительная сессия"
    except PeerFloodError:
        return False, "спамблок (PeerFlood)"
    except FloodWaitError as e:
        return False, f"ограничение FloodWait ({getattr(e, 'seconds', '?')} сек)"
    except Exception as e:
        return False, f"ошибка проверки: {type(e).__name__}"
    finally:
        try:
            await c.disconnect()
        except Exception:
            pass


async def _run_rent_account_validity_check(target: Message | types.CallbackQuery, phone: str):
    exists = db_fetchone('SELECT owner_id FROM accounts WHERE phone = ?', (phone,))
    if not exists:
        if isinstance(target, Message):
            await target.answer(f"❌ Номер `{phone}` не найден в базе.", parse_mode="Markdown")
        else:
            await target.answer("❌ Номер не найден.", show_alert=True)
        return

    if isinstance(target, types.CallbackQuery):
        await target.answer("Проверяю аккаунт...")

    is_valid, reason = await _validate_rent_account(phone)
    owner_row = db_fetchone('SELECT owner_id, expires FROM accounts WHERE phone = ?', (phone,))
    owner_id, expires = owner_row if owner_row else (None, 0)
    now = int(time.time())
    rent_status = "активна" if owner_id and (expires or 0) > now else "нет"

    text = (
        f"📱 Проверка номера `{phone}`\n"
        f"👤 Текущий арендатор: `{owner_id or '—'}`\n"
        f"📌 Активная аренда: **{rent_status}**\n"
    )
    if is_valid:
        text += "\n✅ Статус: **валиден**"
    else:
        text += f"\n❌ Статус: **невалиден**\nПричина: {reason}"

    if isinstance(target, Message):
        await target.answer(text, parse_mode="Markdown")
    else:
        await target.message.answer(text, parse_mode="Markdown")


@dp.callback_query(F.data.startswith("adm_check_validity_"))
async def adm_check_validity_pick(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    phone = call.data[len("adm_check_validity_"):]
    await _run_rent_account_validity_check(call, phone)


@dp.callback_query(F.data == "adm_tgacc")
async def adm_tgacc_menu(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.clear()
    total = db_fetchone('SELECT COUNT(*) FROM tg_accounts', ())
    total_count = total[0] if total else 0

    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить ТГ-аккаунт", callback_data="adm_tgacc_add")
    kb.button(text="📋 Список ТГ-аккаунтов", callback_data="adm_tgacc_list")
    kb.button(text="✅ Проверить валидность", callback_data="adm_tgacc_check_menu")
    kb.button(text="⬅️ Назад", callback_data="adm_panel")
    kb.adjust(1)

    await call.message.edit_text(
        "📱 **ТгАккаунты**\n\n"
        f"Всего в базе: **{total_count}**\n"
        "Добавьте аккаунт, потом измените лот командой:\n"
        "`/lots +79991234567, Название, 25.5`",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown",
    )


@dp.callback_query(F.data == "adm_tgacc_add")
async def adm_tgacc_add(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text(
        "📱 Введите номер телефона (с кодом страны, например: +79991234567):",
        reply_markup=back_kb("adm_tgacc").as_markup(),
    )
    await state.update_data(from_panel=True, add_mode="sale")
    await state.set_state(States.waiting_for_phone)


@dp.callback_query(F.data == "adm_tgacc_list")
async def adm_tgacc_list(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    rows = db_fetchall(
        'SELECT phone, lot_name, price, is_sold FROM tg_accounts ORDER BY id DESC LIMIT 30',
        (),
    )

    if not rows:
        kb = InlineKeyboardBuilder()
        kb.button(text="⬅️ Назад", callback_data="adm_tgacc")
        return await call.message.edit_text(
            "📋 Список ТГ-аккаунтов пока пуст.",
            reply_markup=kb.as_markup(),
        )

    lines = []
    for phone, lot_name, price, is_sold in rows:
        sold_icon = "🔴" if is_sold else "🟢"
        lot = lot_name.strip() if lot_name else "без названия"
        lines.append(f"{sold_icon} `{phone}` | {lot} | ${round(float(price or 0), 2)}")

    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data="adm_tgacc")
    await call.message.edit_text(
        "📋 **Последние ТГ-аккаунты (до 30):**\n\n" + "\n".join(lines),
        reply_markup=kb.as_markup(),
        parse_mode="Markdown",
    )


@dp.callback_query(F.data == "adm_staracc_add")
async def adm_staracc_add_btn(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.clear()
    await call.answer("Функция отключена.", show_alert=True)


@dp.callback_query(F.data == "adm_staracc_del")
async def adm_staracc_del_btn(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.clear()
    await call.answer("Функция отключена.", show_alert=True)


@dp.message(States.staracc_del_phone)
async def adm_staracc_del_phone(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    await m.answer("ℹ️ Эта функция отключена.")
    await state.clear()


@dp.callback_query(F.data == "adm_star_logs")
async def adm_star_logs(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    await call.answer("Функция отключена.", show_alert=True)


@dp.callback_query(F.data == "adm_tgacc_check_menu")
async def adm_tgacc_check_menu(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    rows = db_fetchall(
        'SELECT id, phone, lot_name FROM tg_accounts ORDER BY id DESC LIMIT 30',
        (),
    )
    kb = InlineKeyboardBuilder()
    if not rows:
        kb.button(text="⬅️ Назад", callback_data="adm_tgacc")
        return await call.message.edit_text("📭 Нет аккаунтов для проверки.", reply_markup=kb.as_markup())

    for lot_id, phone, lot_name in rows:
        lot = lot_name.strip() if lot_name else "без названия"
        kb.button(text=f"🔎 {phone} | {lot}", callback_data=f"adm_tgacc_check_{lot_id}")
    kb.button(text="⬅️ Назад", callback_data="adm_tgacc")
    kb.adjust(1)
    await call.message.edit_text(
        "✅ **Проверка валидности ТГ-аккаунтов**\n\nВыберите аккаунт:",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown",
    )


async def _validate_sale_account(phone: str) -> tuple[bool, str]:
    session_path = os.path.join("sale_sessions", phone)
    c = TelegramClient(session_path, API_ID, API_HASH)
    try:
        await c.connect()
        if not await c.is_user_authorized():
            return False, "сессия не авторизована"

        # Проверка на бан/заморозку: доступ к профилю.
        await c.get_me()

        # Проверка спамблока через SpamBot.
        try:
            sb = await c.get_entity("SpamBot")
            await c.send_message(sb, "/start")
            await asyncio.sleep(1.5)
            msgs = await c.get_messages(sb, limit=2)
            txt = " ".join([(m.message or "") for m in msgs]).lower()
            ok_keys = [
                "свободен от каких-либо ограничений",
                "no limits are currently applied",
                "good news",
            ]
            if any(k in txt for k in ok_keys):
                return True, "валиден"
            bad_keys = [
                "огранич", "спам", "временно", "доступ ограничен",
                "limited", "restriction", "spam", "temporarily limited",
            ]
            if any(k in txt for k in bad_keys):
                return False, "спамблок/ограничения от SpamBot"
        except Exception:
            # Если SpamBot не ответил, считаем проверку по спаму неполной, но не удаляем.
            pass

        return True, "валиден"
    except (UserDeactivatedBanError, UserDeactivatedError, AuthKeyUnregisteredError):
        return False, "бан/деактивация/недействительная сессия"
    except Exception as e:
        return False, f"ошибка проверки: {type(e).__name__}"
    finally:
        try:
            await c.disconnect()
        except Exception:
            pass


@dp.callback_query(F.data.startswith("adm_tgacc_check_"))
async def adm_tgacc_check(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    try:
        lot_id = int(call.data.split("_")[-1])
    except Exception:
        return await call.answer("❌ Неверный ID.", show_alert=True)

    row = db_fetchone("SELECT phone, lot_name FROM tg_accounts WHERE id = ?", (lot_id,))
    if not row:
        return await call.answer("❌ Аккаунт не найден.", show_alert=True)
    phone, lot_name = row
    lot = lot_name.strip() if lot_name else "без названия"

    await call.answer("Проверяю аккаунт...")
    is_valid, reason = await _validate_sale_account(phone)
    if is_valid:
        return await call.message.answer(
            f"✅ Аккаунт валиден\n📱 `{phone}`\n🏷 {lot}",
            parse_mode="Markdown",
        )

    # Автоудаление невалидного аккаунта
    cur.execute("DELETE FROM tg_accounts WHERE id = ?", (lot_id,))
    db.commit()
    try:
        spath = os.path.join("sale_sessions", f"{phone}.session")
        if os.path.exists(spath):
            os.remove(spath)
    except Exception:
        pass
    try:
        tdir = os.path.join("tdatafull", phone)
        if os.path.isdir(tdir):
            shutil.rmtree(tdir, ignore_errors=True)
    except Exception:
        pass

    await call.message.answer(
        f"❌ Аккаунт невалиден и удалён автоматически\n"
        f"📱 `{phone}`\n🏷 {lot}\n"
        f"Причина: {reason}",
        parse_mode="Markdown",
    )


@dp.callback_query(F.data == "adm_main_settings")
async def adm_main_settings(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.clear()

    show_clones = get_main_setting('show_clone_accounts', '0') == '1'
    show_text = "ВКЛ" if show_clones else "ВЫКЛ"

    kb = InlineKeyboardBuilder()
    kb.button(
        text=f"📂 Показывать аккаунты клонов в каталоге: {show_text}",
        callback_data="adm_toggle_show_clones",
    )
    kb.button(text="⬅️ Назад", callback_data="adm_panel")
    kb.adjust(1)

    await call.message.edit_text(
        "⚙️ **Настройки основного бота**\n\n"
        "Здесь можно включать/выключать показ аккаунтов клонов в общем каталоге.",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown",
    )


@dp.callback_query(F.data == "adm_toggle_show_clones")
async def adm_toggle_show_clones(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    current = get_main_setting('show_clone_accounts', '0')
    new_val = '0' if current == '1' else '1'
    set_main_setting('show_clone_accounts', new_val)
    await adm_main_settings(call, state)

# ── Добавить аккаунт ──────────────────────────────────────────
@dp.callback_query(F.data == "adm_addacc")
async def adm_panel_addacc(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("📱 Введите номер телефона:", reply_markup=back_kb("adm_panel").as_markup())
    await state.update_data(from_panel=True, add_mode="rent")
    await state.set_state(States.waiting_for_phone)

@dp.message(AdminPanelStates.adm_addacc_phone)
async def adm_panel_addacc_phone(m: Message, state: FSMContext):
    phone = m.text.strip().replace(" ", "")
    await _request_code(m, state, phone, from_panel=True)


# ── Удалить аккаунт ───────────────────────────────────────────
@dp.callback_query(F.data == "adm_delacc")
async def adm_panel_delacc(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("🗑 Введите номер для удаления:", reply_markup=back_kb("adm_panel").as_markup())
    await state.set_state(AdminPanelStates.adm_delacc_phone)

@dp.message(AdminPanelStates.adm_delacc_phone)
async def adm_panel_delacc_exec(m: Message, state: FSMContext):
    phone = m.text.strip().replace(" ", "")
    cur.execute('DELETE FROM accounts WHERE phone=?', (phone,))
    db.commit()
    if os.path.exists(f"sessions/{phone}.session"):
        os.remove(f"sessions/{phone}.session")
    await m.answer(f"✅ Аккаунт `{phone}` удалён.", parse_mode="Markdown")
    await state.clear()


# ── Снять аренду ──────────────────────────────────────────────
@dp.callback_query(F.data == "adm_unnomber")
async def adm_panel_unnomber(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("⛔ Введите номер для снятия аренды:", reply_markup=back_kb("adm_panel").as_markup())
    await state.set_state(AdminPanelStates.adm_unnomber_phone)

@dp.message(AdminPanelStates.adm_unnomber_phone)
async def adm_panel_unnomber_exec(m: Message, state: FSMContext):
    phone = m.text.strip().replace(" ", "")
    res = db_fetchone('SELECT owner_id FROM accounts WHERE phone=?', (phone,))
    if not res:
        await m.answer(f"❌ Аккаунт `{phone}` не найден.", parse_mode="Markdown")
        await state.clear()
        return
    owner_id = res[0]
    await refund_remaining_rent(phone, "досрочно снят администратором")
    cur.execute('UPDATE accounts SET owner_id=NULL, expires=0, is_running=0, notified_10m=0 WHERE phone=?', (phone,))
    db.commit()
    await m.answer(f"✅ Аренда `{phone}` снята.", parse_mode="Markdown")
    if owner_id:
        try:
            await bot.send_message(owner_id, f"⚠️ Администратор досрочно завершил вашу аренду `{phone}`.", parse_mode="Markdown")
        except: pass
    await state.clear()


# ── Забанить пользователя ─────────────────────────────────────
@dp.callback_query(F.data == "adm_ban")
async def adm_panel_ban(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text(
        "🚫 Введите: ID ЧАСЫ ПРИЧИНА\nПример: `123456 24 Спам`",
        reply_markup=back_kb("adm_panel").as_markup(), parse_mode="Markdown")
    await state.set_state(AdminPanelStates.adm_ban_input)

@dp.message(AdminPanelStates.adm_ban_input)
async def adm_panel_ban_exec(m: Message, state: FSMContext):
    try:
        args = m.text.split(maxsplit=2)
        uid = int(args[0])
        hours = int(args[1])
        reason = args[2] if len(args) > 2 else "Не указана"
        unban_time = int(time.time()) + (hours * 3600)
        cur.execute('UPDATE users SET banned_until=?, ban_reason=? WHERE user_id=?', (unban_time, reason, uid))
        for (phone,) in db_fetchall('SELECT phone FROM accounts WHERE owner_id=?', (uid,)):
            cur.execute('UPDATE accounts SET owner_id=NULL, expires=0, is_running=0, notified_10m=0 WHERE phone=?', (phone,))
        db.commit()
        await m.answer(f"✅ Пользователь `{uid}` забанен на {hours}ч.\nПричина: {reason}", parse_mode="Markdown")
        try:
            await bot.send_message(uid,
                f"🚫 **Вы заблокированы!**\n\nСрок: до {time.strftime('%d.%m.%Y %H:%M', time.localtime(unban_time))}\nПричина: {reason}",
                parse_mode="Markdown")
        except: pass
    except:
        await m.answer("⚠️ Формат: ID ЧАСЫ ПРИЧИНА")
    await state.clear()


# ── Статистика пользователя ───────────────────────────────────
@dp.callback_query(F.data == "adm_stats")
async def adm_panel_stats(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("📊 Введите ID пользователя:", reply_markup=back_kb("adm_panel").as_markup())
    await state.set_state(AdminPanelStates.adm_stats_uid)

@dp.message(AdminPanelStates.adm_stats_uid)
async def adm_panel_stats_exec(m: Message, state: FSMContext):
    try:
        uid = int(m.text.strip())
        bal = get_balance(uid)
        if bal is None:
            await m.answer("❌ Пользователь не найден.")
            await state.clear()
            return
        active_rows = db_fetchall('SELECT phone, expires FROM accounts WHERE owner_id=? AND expires>?',
                                  (uid, int(time.time())))
        active_list = "\n".join([f"• `{r[0]}` (до {time.strftime('%H:%M %d.%m', time.localtime(r[1]))})"
                                 for r in active_rows]) or "Нет активных"
        hist_rows = db_fetchall(
            'SELECT phone, duration, cost, date FROM rent_history WHERE user_id=? ORDER BY id DESC LIMIT 5', (uid,))
        hist_list = "\n".join([f"• `{h[0]}` | {h[1]} мин | ${h[2]} ({h[3]})"
                               for h in hist_rows]) or "История пуста"
        ban_info = check_ban(uid)
        ban_text = f"🚫 **Бан:** до {time.strftime('%d.%m.%Y %H:%M', time.localtime(ban_info[0]))} ({ban_info[1]})\n\n" if ban_info else ""
        await m.answer(
            f"👤 **Статистика `{uid}`**\n\n{ban_text}"
            f"💳 Баланс: `${bal}`\n\n"
            f"🔑 Активная аренда:\n{active_list}\n\n"
            f"📜 Последние аренды:\n{hist_list}",
            parse_mode="Markdown")
    except:
        await m.answer("❌ Неверный ID.")
    await state.clear()


# ── Установить цену ───────────────────────────────────────────
@dp.callback_query(F.data == "adm_setprice")
async def adm_panel_setprice(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("💲 Введите: +7999... 0.15", reply_markup=back_kb("adm_panel").as_markup())
    await state.set_state(AdminPanelStates.adm_setprice_input)

@dp.message(AdminPanelStates.adm_setprice_input)
async def adm_panel_setprice_exec(m: Message, state: FSMContext):
    try:
        phone, price = m.text.split()
        price = float(price.replace(",", "."))
        if price < 0.001:
            return await m.answer("⚠️ Минимальная цена: **$0.001/мин**", parse_mode="Markdown")
        cur.execute('UPDATE accounts SET price_per_min=? WHERE phone=?', (price, phone))
        db.commit()
        await m.answer(f"✅ Цена `{phone}` → **${price}/мин**", parse_mode="Markdown")
    except:
        await m.answer("⚠️ Формат: +7999... 0.15")
    await state.clear()


# ── Выдать баланс ─────────────────────────────────────────────
@dp.callback_query(F.data == "adm_givebal")
async def adm_panel_givebal(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("💰 Введите: ID СУММА", reply_markup=back_kb("adm_panel").as_markup())
    await state.set_state(AdminPanelStates.adm_givebal_input)

@dp.message(AdminPanelStates.adm_givebal_input)
async def adm_panel_givebal_exec(m: Message, state: FSMContext):
    try:
        uid, amt = m.text.split()
        amt = float(amt.replace(",", "."))
        add_payment_history(int(uid), amt, "Admin Add")
        await m.answer(f"✅ Зачислено **${amt}** пользователю `{uid}`", parse_mode="Markdown")
    except:
        await m.answer("⚠️ Формат: ID СУММА")
    await state.clear()


# ── Списать баланс ────────────────────────────────────────────
@dp.callback_query(F.data == "adm_delbal")
async def adm_panel_delbal(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("➖ Введите: ID СУММА", reply_markup=back_kb("adm_panel").as_markup())
    await state.set_state(AdminPanelStates.adm_delbal_input)

@dp.message(AdminPanelStates.adm_delbal_input)
async def adm_panel_delbal_exec(m: Message, state: FSMContext):
    try:
        uid, amt = m.text.split()
        uid, amt = int(uid), float(amt.replace(",", "."))
        cur.execute('UPDATE users SET balance = balance - ? WHERE user_id=?', (amt, uid))
        db.commit()
        await m.answer(f"✅ Списано **${amt}** у пользователя `{uid}`", parse_mode="Markdown")
    except:
        await m.answer("⚠️ Формат: ID СУММА")
    await state.clear()


# ── Стоп-слово ────────────────────────────────────────────────
@dp.callback_query(F.data == "adm_blacklist")
async def adm_panel_blacklist(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("🚷 Введите слово для стоп-листа:", reply_markup=back_kb("adm_panel").as_markup())
    await state.set_state(AdminPanelStates.adm_blacklist_word)

@dp.message(AdminPanelStates.adm_blacklist_word)
async def adm_panel_blacklist_exec(m: Message, state: FSMContext):
    word = m.text.strip().lower()
    try:
        cur.execute('INSERT INTO blacklist (word) VALUES (?)', (word,))
        db.commit()
        await m.answer(f"✅ Слово `{word}` добавлено в стоп-лист.", parse_mode="Markdown")
    except:
        await m.answer(f"⚠️ Слово `{word}` уже в списке.")
    await state.clear()


# ── Редактировать чаты каталога ───────────────────────────────
@dp.callback_query(F.data == "adm_redak")
async def adm_panel_redak(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text(
        "📋 Введите: +7999... https://t.me/chat1, https://t.me/chat2",
        reply_markup=back_kb("adm_panel").as_markup())
    await state.set_state(AdminPanelStates.adm_redak_input)

@dp.message(AdminPanelStates.adm_redak_input)
async def adm_panel_redak_exec(m: Message, state: FSMContext):
    try:
        parts = m.text.split(maxsplit=1)
        phone = parts[0].strip()
        chats_text = parts[1].strip() if len(parts) > 1 else ""
        cur.execute('UPDATE accounts SET catalog_chats=? WHERE phone=?', (chats_text, phone))
        db.commit()
        await m.answer(f"✅ Чаты каталога для `{phone}` обновлены.", parse_mode="Markdown")
    except Exception as e:
        await m.answer(f"❌ Ошибка: {e}")
    await state.clear()


# ── Рассылка всем ─────────────────────────────────────────────
@dp.callback_query(F.data == "adm_broadcast")
async def adm_panel_broadcast(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("📢 Введите текст для рассылки:", reply_markup=back_kb("adm_panel").as_markup())
    await state.set_state(AdminPanelStates.adm_broadcast_text)

@dp.message(AdminPanelStates.adm_broadcast_text)
async def adm_panel_broadcast_exec(m: Message, state: FSMContext):
    text = m.text.strip()
    users = db_fetchall('SELECT user_id FROM users')
    sent = failed = 0
    for (uid,) in users:
        try:
            await bot.send_message(uid, f"📢 **Сообщение от администратора:**\n\n{text}", parse_mode="Markdown")
            sent += 1
        except: failed += 1
        await asyncio.sleep(0.05)
    await m.answer(f"✅ Рассылка завершена.\nОтправлено: {sent}\nОшибок: {failed}")
    await state.clear()


# ── Написать пользователю ─────────────────────────────────────
@dp.callback_query(F.data == "adm_pm")
async def adm_panel_pm(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await call.message.edit_text("📩 Введите: ID текст сообщения", reply_markup=back_kb("adm_panel").as_markup())
    await state.set_state(AdminPanelStates.adm_pm_input)

@dp.message(AdminPanelStates.adm_pm_input)
async def adm_panel_pm_exec(m: Message, state: FSMContext):
    try:
        parts = m.text.split(maxsplit=1)
        uid = int(parts[0].strip())
        text = parts[1].strip() if len(parts) > 1 else ""
        if not text:
            await m.answer("⚠️ Сообщение пустое.")
            await state.clear()
            return
        await bot.send_message(uid, f"📩 **Сообщение от администратора:**\n\n{text}", parse_mode="Markdown")
        await m.answer(f"✅ Отправлено пользователю `{uid}`", parse_mode="Markdown")
    except Exception as e:
        await m.answer(f"❌ Ошибка: {e}")
    await state.clear()


class CloneMgmtStates(StatesGroup):
    addacc_phone   = State()
    setprice_input = State()
    broadcast_text = State()
    pm_input       = State()
    stats_uid      = State()


def _get_all_clones_kb(back_cb="adm_clone_mgmt"):
    """Клавиатура выбора клона."""
    clones = db_fetchall('SELECT bot_id, bot_username FROM clones')
    kb = InlineKeyboardBuilder()
    for bot_id, username in clones:
        label = f"@{username}" if username else bot_id
        kb.button(text=label, callback_data=f"cmgmt_pick_{bot_id}")
    kb.button(text="\u2b05\ufe0f Назад", callback_data=back_cb)
    kb.adjust(1)
    return kb, clones


@dp.callback_query(F.data == "adm_clone_mgmt")
async def adm_clone_mgmt(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="\u2795 Добавить акк в клон",    callback_data="cmgmt_addacc")
    kb.button(text="\U0001f5d1 Удалить акк из клона", callback_data="cmgmt_delacc")
    kb.button(text="\U0001f4b2 Цена акк в клоне",    callback_data="cmgmt_setprice")
    kb.button(text="\u26d4 Снять аренду в клоне",   callback_data="cmgmt_unnomber")
    kb.button(text="\U0001f4e2 Рассылка по клону",  callback_data="cmgmt_broadcast")
    kb.button(text="\U0001f4e9 Написать польз. клона", callback_data="cmgmt_pm")
    kb.button(text="\U0001f4ca Стат. польз. клона", callback_data="cmgmt_stats")
    kb.button(text="\u2699\ufe0f Настройки клона",  callback_data="cmgmt_settings")
    kb.button(text="\u2b05\ufe0f Назад",            callback_data="adm_panel")
    kb.adjust(2)
    await call.message.edit_text(
        "\U0001f6e0 **Управление клонами**\n\nВыберите действие:",
        reply_markup=kb.as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data.in_({"cmgmt_addacc","cmgmt_delacc","cmgmt_setprice",
                                 "cmgmt_unnomber","cmgmt_broadcast","cmgmt_pm",
                                 "cmgmt_stats","cmgmt_settings"}))
async def cmgmt_action_pick(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    action = call.data[len("cmgmt_"):]
    kb, clones = _get_all_clones_kb("adm_clone_mgmt")
    if not clones:
        return await call.answer("\u274c Клон-ботов нет.", show_alert=True)
    await state.update_data(cmgmt_action=action)
    labels = {
        "addacc":    "\U0001f4f1 Добавить аккаунт — выберите клон:",
        "delacc":    "\U0001f5d1 Удалить аккаунт — выберите клон:",
        "setprice":  "\U0001f4b2 Установить цену — выберите клон:",
        "unnomber":  "\u26d4 Снять аренду — выберите клон:",
        "broadcast": "\U0001f4e2 Рассылка — выберите клон:",
        "pm":        "\U0001f4e9 Написать пользователю — выберите клон:",
        "stats":     "\U0001f4ca Статистика — выберите клон:",
        "settings":  "\u2699\ufe0f Настройки — выберите клон:",
    }
    await call.message.edit_text(labels.get(action, "Выберите клон:"), reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("cmgmt_pick_"))
async def cmgmt_pick_clone(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    bot_id = call.data[len("cmgmt_pick_"):]
    d = await state.get_data()
    action = d.get("cmgmt_action", "")
    await state.update_data(cmgmt_bot_id=bot_id)
    res = db_fetchone("SELECT bot_username, api_token FROM clones WHERE bot_id=?", (bot_id,))
    if not res:
        return await call.answer("\u274c Клон не найден.", show_alert=True)
    uname, clone_token = res
    label = f"@{uname}" if uname else bot_id
    bk_builder = InlineKeyboardBuilder()
    bk_builder.button(text="\u2b05\ufe0f Назад", callback_data="adm_clone_mgmt")
    bk = bk_builder.as_markup()

    if action == "addacc":
        await call.message.edit_text(
            f"\U0001f4f1 **Добавить аккаунт в {label}**\n\nВведите номер телефона (с +):",
            reply_markup=bk, parse_mode="Markdown")
        await state.set_state(CloneMgmtStates.addacc_phone)

    elif action == "delacc":
        conn, ccur = get_clone_db(bot_id)
        phones = []
        if conn:
            try:
                ccur.execute("SELECT phone FROM accounts")
                phones = [r[0] for r in ccur.fetchall()]
            finally:
                conn.close()
        if not phones:
            return await call.answer(f"\u274c В {label} нет аккаунтов.", show_alert=True)
        kb2 = InlineKeyboardBuilder()
        for p in phones:
            kb2.button(text=f"\U0001f5d1 {p}", callback_data=f"cmgmt_delacc_do_{bot_id}_{p}")
        kb2.button(text="\u2b05\ufe0f Назад", callback_data="adm_clone_mgmt")
        kb2.adjust(1)
        await call.message.edit_text(f"\U0001f5d1 Выберите аккаунт для удаления из {label}:",
                                      reply_markup=kb2.as_markup())

    elif action == "setprice":
        await call.message.edit_text(
            f"\U0001f4b2 **Цена в {label}**\n\nВведите: `+7999... 0.05` (номер и цена/мин)",
            reply_markup=bk, parse_mode="Markdown")
        await state.set_state(CloneMgmtStates.setprice_input)

    elif action == "unnomber":
        conn, ccur = get_clone_db(bot_id)
        rented = []
        if conn:
            try:
                now_ts = int(time.time())
                ccur.execute("SELECT phone, owner_id FROM accounts WHERE owner_id IS NOT NULL AND expires > ?", (now_ts,))
                rented = ccur.fetchall()
            finally:
                conn.close()
        if not rented:
            return await call.answer(f"\u274c Нет арендованных в {label}.", show_alert=True)
        kb2 = InlineKeyboardBuilder()
        for p, oid in rented:
            kb2.button(text=f"\u26d4 {p} (ID:{oid})", callback_data=f"cmgmt_unnomber_do_{bot_id}_{p}")
        kb2.button(text="\u2b05\ufe0f Назад", callback_data="adm_clone_mgmt")
        kb2.adjust(1)
        await call.message.edit_text(f"\u26d4 Снять аренду в {label}:", reply_markup=kb2.as_markup())

    elif action == "broadcast":
        await call.message.edit_text(
            f"\U0001f4e2 **Рассылка всем пользователям {label}**\n\nВведите текст:",
            reply_markup=bk, parse_mode="Markdown")
        await state.set_state(CloneMgmtStates.broadcast_text)

    elif action == "pm":
        await call.message.edit_text(
            f"\U0001f4e9 **Написать пользователю {label}**\n\nВведите: `ID текст`",
            reply_markup=bk, parse_mode="Markdown")
        await state.set_state(CloneMgmtStates.pm_input)

    elif action == "stats":
        await call.message.edit_text(
            f"\U0001f4ca **Статистика польз. {label}**\n\nВведите Telegram ID:",
            reply_markup=bk, parse_mode="Markdown")
        await state.set_state(CloneMgmtStates.stats_uid)

    elif action == "settings":
        conn, ccur = get_clone_db(bot_id)
        show_main = "0"
        if conn:
            try:
                ccur.execute("SELECT value FROM bot_settings WHERE key=\'main_accounts_enabled\'")
                r = ccur.fetchone()
                show_main = r[0] if r else "0"
            except Exception:
                pass
            finally:
                conn.close()
        st_txt = "\u2705 Включено" if show_main == "1" else "\u274c Выключено"
        tg_txt = "\U0001f534 Отключить" if show_main == "1" else "\U0001f7e2 Включить"
        kb2 = InlineKeyboardBuilder()
        kb2.button(text=f"\U0001f4e1 Акк осн.бота в каталоге: {st_txt}", callback_data="cmgmt_noop")
        kb2.button(text=tg_txt, callback_data=f"cmgmt_toggle_main_{bot_id}")
        kb2.button(text="\u2b05\ufe0f Назад", callback_data="adm_clone_mgmt")
        kb2.adjust(1)
        await call.message.edit_text(
            f"\u2699\ufe0f **Настройки {label}**\n\n\U0001f4e1 Аккаунты осн.бота в каталоге клона: **{st_txt}**",
            reply_markup=kb2.as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data == "cmgmt_noop")
async def cmgmt_noop(call: types.CallbackQuery):
    await call.answer()


@dp.callback_query(F.data.startswith("cmgmt_toggle_main_"))
async def cmgmt_toggle_main(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    bot_id = call.data[len("cmgmt_toggle_main_"):]
    conn, ccur = get_clone_db(bot_id)
    if not conn:
        return await call.answer("\u274c БД клона недоступна.", show_alert=True)
    try:
        ccur.execute("SELECT value FROM bot_settings WHERE key=\'main_accounts_enabled\'")
        r = ccur.fetchone()
        new_val = "0" if (r and r[0] == "1") else "1"
        ccur.execute("INSERT OR REPLACE INTO bot_settings (key,value) VALUES (\'main_accounts_enabled\',?)", (new_val,))
        conn.commit()
    finally:
        conn.close()
    await call.answer(f"\u2705 {'Включено' if new_val == '1' else 'Выключено'}")
    await state.update_data(cmgmt_action="settings")
    call.data = f"cmgmt_pick_{bot_id}"
    await cmgmt_pick_clone(call, state)


@dp.callback_query(F.data.startswith("cmgmt_delacc_do_"))
async def cmgmt_delacc_do(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    rest = call.data[len("cmgmt_delacc_do_"):]
    # bot_id may contain underscores, phone starts with +
    idx = rest.rfind("_+")
    if idx == -1:
        return await call.answer("\u274c Ошибка парсинга.", show_alert=True)
    bot_id, phone = rest[:idx], rest[idx+1:]
    conn, ccur = get_clone_db(bot_id)
    if not conn:
        return await call.answer("\u274c БД недоступна.", show_alert=True)
    try:
        ccur.execute("DELETE FROM accounts WHERE phone=?", (phone,))
        conn.commit()
    finally:
        conn.close()
    await call.answer(f"\u2705 {phone} удалён.", show_alert=True)
    await state.clear()
    await adm_clone_mgmt(call, state)


@dp.callback_query(F.data.startswith("cmgmt_unnomber_do_"))
async def cmgmt_unnomber_do(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    rest = call.data[len("cmgmt_unnomber_do_"):]
    idx = rest.rfind("_+")
    if idx == -1:
        return await call.answer("\u274c Ошибка парсинга.", show_alert=True)
    bot_id, phone = rest[:idx], rest[idx+1:]
    conn, ccur = get_clone_db(bot_id)
    if not conn:
        return await call.answer("\u274c БД недоступна.", show_alert=True)
    try:
        ccur.execute("SELECT owner_id FROM accounts WHERE phone=?", (phone,))
        r = ccur.fetchone()
        owner_id = r[0] if r else None
        ccur.execute("UPDATE accounts SET owner_id=NULL,expires=0,is_running=0,notified_10m=0 WHERE phone=?", (phone,))
        conn.commit()
    finally:
        conn.close()
    if owner_id:
        try:
            cr = db_fetchone("SELECT api_token FROM clones WHERE bot_id=?", (bot_id,))
            if cr:
                cb_bot = Bot(token=cr[0])
                await cb_bot.send_message(owner_id, f"\u26d4 Администратор снял вашу аренду `{phone}`.", parse_mode="Markdown")
                await cb_bot.session.close()
        except Exception:
            pass
    await call.answer(f"\u2705 Аренда {phone} снята.", show_alert=True)
    await state.clear()
    await adm_clone_mgmt(call, state)


@dp.message(CloneMgmtStates.addacc_phone)
async def cmgmt_addacc_phone(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    d = await state.get_data()
    bot_id = d.get("cmgmt_bot_id", "")
    phone = m.text.strip().replace(" ", "").replace("-", "")
    if not phone.startswith("+"): phone = "+" + phone
    conn, ccur = get_clone_db(bot_id)
    if not conn:
        await state.clear()
        return await m.answer("\u274c БД клона недоступна.")
    try:
        ccur.execute("INSERT OR IGNORE INTO accounts (phone, is_running, is_premium, price_per_min) VALUES (?,0,0,0.02)", (phone,))
        conn.commit()
        uname_r = db_fetchone("SELECT bot_username FROM clones WHERE bot_id=?", (bot_id,))
        label = f"@{uname_r[0]}" if uname_r and uname_r[0] else bot_id
        bot_short = bot_id.split(":")[0] if ":" in bot_id else bot_id
        await m.answer(
            f"\u2705 Аккаунт `{phone}` добавлен в БД клона {label}.\n\n"
            f"\u26a0\ufe0f Сессия должна находиться в папке:\n"
            f"`sessions_clone_{bot_short}/{phone}.session`",
            parse_mode="Markdown")
    except Exception as e:
        await m.answer(f"\u274c Ошибка: {e}")
    finally:
        conn.close()
    await state.clear()


@dp.message(CloneMgmtStates.setprice_input)
async def cmgmt_setprice_input(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    d = await state.get_data()
    bot_id = d.get("cmgmt_bot_id", "")
    try:
        parts = m.text.strip().split()
        phone, price = parts[0], float(parts[1].replace(",", "."))
        if price < 0.001: raise ValueError("min $0.001")
        conn, ccur = get_clone_db(bot_id)
        if not conn: raise RuntimeError("БД недоступна")
        try:
            ccur.execute("UPDATE accounts SET price_per_min=? WHERE phone=?", (price, phone))
            conn.commit()
            rows = ccur.rowcount
        finally:
            conn.close()
        if rows == 0:
            await m.answer(f"\u274c Номер `{phone}` не найден в клоне.", parse_mode="Markdown")
        else:
            await m.answer(f"\u2705 Цена `{phone}`: **${price}/мин**", parse_mode="Markdown")
    except Exception as e:
        await m.answer(f"\u274c Ошибка: {e}\nФормат: `+7999... 0.05`", parse_mode="Markdown")
    await state.clear()


@dp.message(CloneMgmtStates.broadcast_text)
async def cmgmt_broadcast_text(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    d = await state.get_data()
    bot_id = d.get("cmgmt_bot_id", "")
    text_msg = m.text.strip()
    cr = db_fetchone("SELECT api_token FROM clones WHERE bot_id=?", (bot_id,))
    if not cr:
        await state.clear()
        return await m.answer("\u274c Клон не найден.")
    conn, ccur = get_clone_db(bot_id)
    users = []
    if conn:
        try:
            ccur.execute("SELECT user_id FROM users")
            users = [r[0] for r in ccur.fetchall()]
        finally:
            conn.close()
    cb_bot = Bot(token=cr[0])
    sent = failed = 0
    for uid in users:
        try:
            await cb_bot.send_message(uid, f"\U0001f4e2 **Сообщение от администратора:**\n\n{text_msg}", parse_mode="Markdown")
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await cb_bot.session.close()
    await m.answer(f"\u2705 Рассылка завершена.\nОтправлено: {sent} | Ошибок: {failed}")
    await state.clear()


@dp.message(CloneMgmtStates.pm_input)
async def cmgmt_pm_input(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    d = await state.get_data()
    bot_id = d.get("cmgmt_bot_id", "")
    try:
        parts = m.text.strip().split(maxsplit=1)
        uid = int(parts[0])
        msg_text = parts[1].strip() if len(parts) > 1 else ""
        if not msg_text: raise ValueError("Пустое сообщение")
        cr = db_fetchone("SELECT api_token FROM clones WHERE bot_id=?", (bot_id,))
        if not cr: raise RuntimeError("Клон не найден")
        cb_bot = Bot(token=cr[0])
        await cb_bot.send_message(uid, f"\U0001f4e9 **Сообщение от администратора:**\n\n{msg_text}", parse_mode="Markdown")
        await cb_bot.session.close()
        await m.answer(f"\u2705 Отправлено пользователю `{uid}`", parse_mode="Markdown")
    except Exception as e:
        await m.answer(f"\u274c Ошибка: {e}\nФормат: `ID текст`", parse_mode="Markdown")
    await state.clear()


@dp.message(CloneMgmtStates.stats_uid)
async def cmgmt_stats_uid(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    d = await state.get_data()
    bot_id = d.get("cmgmt_bot_id", "")
    try:
        uid = int(m.text.strip())
        conn, ccur = get_clone_db(bot_id)
        if not conn: raise RuntimeError("БД недоступна")
        try:
            ccur.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
            bal_r = ccur.fetchone()
            if not bal_r: raise ValueError("Пользователь не найден")
            bal = round(bal_r[0], 2)
            now_ts = int(time.time())
            ccur.execute("SELECT phone, expires FROM accounts WHERE owner_id=? AND expires>?", (uid, now_ts))
            active = ccur.fetchall()
            ccur.execute("SELECT phone, duration, cost, date FROM rent_history WHERE user_id=? ORDER BY id DESC LIMIT 5", (uid,))
            hist = ccur.fetchall()
        finally:
            conn.close()
        uname_r = db_fetchone("SELECT bot_username FROM clones WHERE bot_id=?", (bot_id,))
        label = f"@{uname_r[0]}" if uname_r and uname_r[0] else bot_id
        active_txt = "\n".join([f"\u2022 `{r[0]}` (до {time.strftime('%H:%M %d.%m', time.localtime(r[1]))})" for r in active]) or "Нет активных"
        hist_txt = "\n".join([f"\u2022 `{h[0]}` | {h[1]}мин | ${h[2]} ({h[3]})" for h in hist]) or "История пуста"
        await m.answer(
            f"\U0001f4ca **Статистика `{uid}` в {label}**\n\n"
            f"\U0001f4b3 Баланс: `${bal}`\n\n"
            f"\U0001f511 Активная аренда:\n{active_txt}\n\n"
            f"\U0001f4dc История (5 последних):\n{hist_txt}",
            parse_mode="Markdown")
    except Exception as e:
        await m.answer(f"\u274c Ошибка: {e}")
    await state.clear()


# ── Клон-боты (статистика) ────────────────────────────────────
@dp.callback_query(F.data == "adm_clones")
async def adm_panel_clones(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    clones = db_fetchall(
        'SELECT bot_id, bot_username, owner_id, is_running, earned, withdrawn FROM clones', ())
    if not clones:
        kb = InlineKeyboardBuilder()
        kb.button(text="⬅️ Назад", callback_data="adm_panel")
        return await call.message.edit_text("🤖 Клон-ботов пока нет.", reply_markup=kb.as_markup())
    kb = InlineKeyboardBuilder()
    lines = []
    for bot_id, username, owner_id, is_running, earned, withdrawn in clones:
        dot = "🟢" if is_running else "🔴"
        uname = f"@{username}" if username else bot_id
        avail = round(earned - withdrawn, 2)
        lines.append(f"{dot} {uname} | Владелец: `{owner_id}` | Прибыль: ${round(earned,2)}")
        kb.button(text=f"📋 {uname}", callback_data=f"adm_clone_info_{bot_id}")
    kb.button(text="⬅️ Назад", callback_data="adm_panel")
    kb.adjust(1)
    await call.message.edit_text(
        "🤖 **Клон-боты**\n\n" + "\n".join(lines),
        reply_markup=kb.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("adm_clone_info_"))
async def adm_clone_info(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    bot_id = call.data[len("adm_clone_info_"):]
    res = db_fetchone(
        'SELECT bot_id, bot_username, owner_id, is_running, earned, withdrawn FROM clones WHERE bot_id=?',
        (bot_id,))
    if not res:
        return await call.answer("❌ Не найдено.", show_alert=True)
    _, username, owner_id, is_running, earned, withdrawn = res
    avail = round(earned - withdrawn, 2)
    uname = f"@{username}" if username else bot_id
    status = "🟢 Работает" if is_running else "🔴 Остановлен"

    # Аккаунты клона — они в основной БД, принадлежащие пользователям этого клона
    # (у клона своя БД, но покажем общее число из таблицы clones)
    # Число пользователей — через rent_history уникальные user_id с арендами у аккаунтов клона
    text = (
        f"📋 **Статистика клон-бота**\n\n"
        f"🤖 {uname}\n"
        f"📊 Статус: {status}\n"
        f"👤 Владелец: `{owner_id}`\n\n"
        f"💰 Прибыль за всё время: **${round(earned, 2)}**\n"
        f"📤 Выведено: **${round(withdrawn, 2)}**\n"
        f"✅ Доступно: **${avail}**"
    )
    kb = InlineKeyboardBuilder()
    if is_running:
        kb.button(text="🛑 Остановить бота", callback_data=f"adm_clone_stop_{bot_id}")
    else:
        kb.button(text="▶️ Запустить бота",  callback_data=f"adm_clone_start_{bot_id}")
    kb.button(text="🗑 Удалить клон-бот",    callback_data=f"adm_clone_del_{bot_id}")
    kb.button(text="⬅️ Назад",              callback_data="adm_clones")
    kb.adjust(1)
    await call.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data.startswith("adm_clone_start_"))
async def adm_clone_start_handler(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    bot_id = call.data[len("adm_clone_start_"):]
    res = db_fetchone('SELECT api_token, owner_id FROM clones WHERE bot_id=?', (bot_id,))
    if not res: return await call.answer("❌ Не найдено.", show_alert=True)
    ok = launch_clone(res[0], res[1], bot_id)
    if ok:
        cur.execute('UPDATE clones SET is_running=1 WHERE bot_id=?', (bot_id,))
        db.commit()
        await call.answer("✅ Бот запущен!")
    else:
        await call.answer("❌ Ошибка запуска.", show_alert=True)
    # refresh info — patch call.data so adm_clone_info reads correct bot_id
    call.data = f"adm_clone_info_{bot_id}"
    await adm_clone_info(call, state)


@dp.callback_query(F.data.startswith("adm_clone_stop_"))
async def adm_clone_stop_handler(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    bot_id = call.data[len("adm_clone_stop_"):]
    stop_clone(bot_id)
    cur.execute('UPDATE clones SET is_running=0 WHERE bot_id=?', (bot_id,))
    db.commit()
    await call.answer("🛑 Бот остановлен.")
    call.data = f"adm_clone_info_{bot_id}"
    await adm_clone_info(call, state)


@dp.callback_query(F.data.startswith("adm_clone_del_"))
async def adm_clone_del_handler(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    bot_id = call.data[len("adm_clone_del_"):]
    res = db_fetchone('SELECT bot_username, owner_id FROM clones WHERE bot_id=?', (bot_id,))
    if not res: return await call.answer("❌ Не найдено.", show_alert=True)
    uname_db, owner_id = res
    stop_clone(bot_id)
    cur.execute('DELETE FROM clones WHERE bot_id=?', (bot_id,))
    cur.execute('DELETE FROM clone_withdraw_requests WHERE bot_id=?', (bot_id,))
    db.commit()
    label = f"@{uname_db}" if uname_db else bot_id
    await call.message.edit_text(
        f"🗑 Клон-бот {label} удалён администратором.",
        reply_markup=back_kb("adm_clones").as_markup())
    try:
        await bot.send_message(
            owner_id,
            f"⚠️ Ваш клон-бот {label} был **удалён администратором**.",
            parse_mode="Markdown")
    except Exception:
        pass


# ─── БОТЫ-НАБЛЮДАТЕЛИ (управление) ───────────────────────────────────────────
@dp.callback_query(F.data == "adm_notify_bots")
async def adm_notify_bots_menu(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.clear()
    rows = db_fetchall('SELECT slot, token, label FROM notify_bots ORDER BY slot')
    kb = InlineKeyboardBuilder()
    lines = []
    for slot, token, label in rows:
        status = "🟢" if token.strip() else "⚪"
        lines.append(f"{status} Слот {slot}: {label}")
        kb.button(text=f"⚙️ Слот {slot}: {label}", callback_data=f"adm_nb_edit_{slot}")
    kb.button(text="⬅️ Назад", callback_data="adm_panel")
    kb.adjust(1)
    await call.message.edit_text(
        "🔔 **Боты-наблюдатели** (до 3 ботов)\n\n"
        "Получают уведомления о событиях:\n"
        "• 🆕 Новый пользователь зарегистрировался\n"
        "• 🚀 Запущена рассылка\n"
        "• ✏️ Изменён текст рассылки\n\n"
        + "\n".join(lines),
        reply_markup=kb.as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data.startswith("adm_nb_edit_"))
async def adm_nb_edit(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    slot = int(call.data[len("adm_nb_edit_"):])
    res = db_fetchone('SELECT token, label FROM notify_bots WHERE slot=?', (slot,))
    token, label = (res[0], res[1]) if res else ('', f'Бот {slot}')
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Установить / заменить токен", callback_data=f"adm_nb_set_{slot}")
    if token.strip():
        kb.button(text="🗑 Удалить этот бот", callback_data=f"adm_nb_del_{slot}")
    kb.button(text="⬅️ Назад", callback_data="adm_notify_bots")
    kb.adjust(1)
    token_display = f"`{token[:20]}...`" if token.strip() else "_не задан_"
    await call.message.edit_text(
        f"🔔 **Слот {slot} — {label}**\n\nТекущий токен: {token_display}",
        reply_markup=kb.as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data.startswith("adm_nb_set_"))
async def adm_nb_set(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    slot = int(call.data[len("adm_nb_set_"):])
    await state.update_data(nb_slot=slot)
    await call.message.edit_text(
        f"📋 **Слот {slot}** — введите токен бота от @BotFather\n"
        f"_(формат: `123456789:AAHxxxxxx`)_",
        reply_markup=back_kb(f"adm_nb_edit_{slot}").as_markup(),
        parse_mode="Markdown")
    await state.set_state(AdminPanelStates.adm_notify_bot_token)


@dp.message(AdminPanelStates.adm_notify_bot_token)
async def adm_nb_token_input(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    token = m.text.strip()
    d = await state.get_data()
    slot = d.get('nb_slot', 1)
    parts = token.split(":")
    if len(parts) != 2 or not parts[0].isdigit():
        return await m.answer("❌ Неверный формат. Пример: `123456789:AAHxxxxx`",
                              parse_mode="Markdown")
    try:
        test_b = Bot(token=token)
        bi = await test_b.get_me()
        label = f"@{bi.username}" if bi.username else f"Бот {slot}"
        await test_b.session.close()
    except Exception as e:
        await state.clear()
        return await m.answer(f"❌ Не удалось подключиться к боту: {e}")
    cur.execute('UPDATE notify_bots SET token=?, label=? WHERE slot=?', (token, label, slot))
    db.commit()
    await m.answer(f"✅ Бот-наблюдатель **{label}** добавлен в слот {slot}.",
                   parse_mode="Markdown")
    await state.clear()


@dp.callback_query(F.data.startswith("adm_nb_del_"))
async def adm_nb_del(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    slot = int(call.data[len("adm_nb_del_"):])
    cur.execute('UPDATE notify_bots SET token="", label=? WHERE slot=?', (f'Бот {slot}', slot))
    db.commit()
    await call.answer(f"✅ Слот {slot} очищен.", show_alert=True)
    await adm_notify_bots_menu(call, state)


async def adm_ban(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    try:
        args = command.args.split(maxsplit=2)
        uid = int(args[0])
        hours = int(args[1])
        reason = args[2] if len(args) > 2 else "Не указана"
        unban_time = int(time.time()) + (hours * 3600)

        cur.execute('UPDATE users SET banned_until = ?, ban_reason = ? WHERE user_id = ?', (unban_time, reason, uid))
        for (phone,) in db_fetchall('SELECT phone FROM accounts WHERE owner_id = ?', (uid,)):
            cur.execute(
                'UPDATE accounts SET owner_id = NULL, expires = 0, is_running = 0, notified_10m = 0 WHERE phone = ?',
                (phone,))
        db.commit()

        await message.answer(
            f"✅ Пользователь {uid} забанен на {hours} ч.\nПричина: {reason}\nВсе его активные номера возвращены в каталог.")
        try:
            await bot.send_message(uid,
                                   f"🚫 **Вы были заблокированы!**\n\nСрок: до {time.strftime('%d.%m.%Y %H:%M', time.localtime(unban_time))}\nПричина: {reason}\nВаши аренды отменены.",
                                   parse_mode="Markdown")
        except:
            pass
    except Exception:
        await message.answer("⚠️ Формат: `/ban ID ЧАСЫ ПРИЧИНА`\nПример: `/ban 123456789 24 Спам`",
                             parse_mode="Markdown")


@dp.message(Command("unnomber"))
async def adm_unnomber(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args: return await message.answer("⚠️ Формат: `/unnomber +79991234567`")

    phone = command.args.strip().replace(" ", "")
    res = db_fetchone('SELECT owner_id FROM accounts WHERE phone = ?', (phone,))
    if not res: return await message.answer(f"❌ Аккаунт `{phone}` не найден в базе.")

    owner_id = res[0]
    await refund_remaining_rent(phone, "досрочно снят администратором")
    cur.execute('UPDATE accounts SET owner_id = NULL, expires = 0, is_running = 0, notified_10m = 0 WHERE phone = ?',
                (phone,))
    db.commit()
    await message.answer(f"✅ Аренда номера `{phone}` досрочно завершена.", parse_mode="Markdown")
    if owner_id:
        try:
            await bot.send_message(owner_id, f"⚠️ Администратор досрочно завершил вашу аренду номера `{phone}`.",
                                   parse_mode="Markdown")
        except:
            pass


@dp.message(Command("blacklist"))
async def adm_blacklist(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args: return await message.answer("⚠️ Формат: `/blacklist слово`")
    word = command.args.strip().lower()
    try:
        cur.execute('INSERT INTO blacklist (word) VALUES (?)', (word,))
        db.commit()
        await message.answer(f"✅ Слово `{word}` успешно добавлено.")
    except sqlite3.IntegrityError:
        await message.answer(f"⚠️ Слово `{word}` уже присутствует.")


@dp.message(Command("stats"))
async def adm_stats(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args: return await message.answer("⚠️ Формат: `/stats ID`")
    try:
        uid = int(command.args.strip())
        bal = get_balance(uid)
        if bal is None: return await message.answer("❌ Пользователь не найден.")

        active_rows = db_fetchall('SELECT phone, expires FROM accounts WHERE owner_id = ? AND expires > ?',
                                  (uid, int(time.time())))
        active_list = "\n".join([f"• `{r[0]}` (до {time.strftime('%H:%M %d.%m', time.localtime(r[1]))})" for r in
                                 active_rows]) or "Нет активных"

        hist_rows = db_fetchall(
            'SELECT phone, duration, cost, date FROM rent_history WHERE user_id = ? ORDER BY id DESC LIMIT 5', (uid,))
        history_rent_list = "\n".join(
            [f"• `{h[0]}` | {h[1]} мин | ${h[2]} ({h[3]})" for h in hist_rows]) or "История пуста"

        ban_info = check_ban(uid)
        ban_text = f"🚫 **Бан:** до {time.strftime('%d.%m.%Y %H:%M', time.localtime(ban_info[0]))} ({ban_info[1]})\n\n" if ban_info else ""

        report = (f"👤 **Статистика пользователя `{uid}`**\n\n{ban_text}💳 **Баланс:** `${bal}`\n\n"
                  f"🔑 **Активная аренда:**\n{active_list}\n\n"
                  f"📜 **Последние аренды:**\n{history_rent_list}")
        await message.answer(report, parse_mode="Markdown")
    except:
        await message.answer("❌ Ошибка в ID.")


@dp.message(Command("givebal"))
async def adm_give(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    try:
        uid, amt = command.args.split()
        amt = float(amt.replace(",", "."))
        add_payment_history(int(uid), amt, "Admin Add")
        await message.answer(f"✅ Зачислено **${amt}** пользователю `{uid}`", parse_mode="Markdown")
    except:
        await message.answer("Ошибка. Формат: `/givebal ID СУММА`")


@dp.message(Command("delbal"))
async def adm_del_bal(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    try:
        uid, amt = command.args.split()
        uid, amt = int(uid), float(amt.replace(",", "."))
        cur.execute('UPDATE users SET balance = balance - ? WHERE user_id = ?', (amt, uid))
        db.commit()
        await message.answer(f"✅ Списано **${amt}** у пользователя `{uid}`", parse_mode="Markdown")
    except:
        await message.answer("Ошибка. Формат: `/delbal ID СУММА`")


@dp.message(Command("delacc"))
async def adm_del_acc(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args: return await message.answer("⚠️ Формат: `/delacc +7999...`")
    phone = command.args.strip().replace(" ", "")
    cur.execute('DELETE FROM accounts WHERE phone = ?', (phone,))
    db.commit()
    if os.path.exists(f"sessions/{phone}.session"):
        os.remove(f"sessions/{phone}.session")
    await message.answer(f"✅ Аккаунт `{phone}` удален.")


@dp.message(Command("setprice"))
async def adm_set_price(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    try:
        phone, price = command.args.split()
        price = float(price.replace(",", "."))
        if price < 0.001:
            return await message.answer("⚠️ Минимальная цена: **$0.001/мин**", parse_mode="Markdown")
        cur.execute('UPDATE accounts SET price_per_min = ? WHERE phone = ?', (price, phone))
        db.commit()
        await message.answer(f"✅ Цена для `{phone}` теперь **${price}/мин**", parse_mode="Markdown")
    except:
        await message.answer("Ошибка. Формат: `/setprice +7... 0.15`")


# --- КОМАНДА: УСТАНОВИТЬ ЦЕНУ В КЛОНЕ ---
# Формат: /setpriceclon @username_клона +7999... 0.05
@dp.message(Command("setpriceclon"))
async def adm_set_price_clon(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args:
        return await message.answer(
            "⚠️ Формат: `/setpriceclon @username_клона +7999... цена`\n"
            "Пример: `/setpriceclon @myclonebot +79991234567 0.05`\n\n"
            "Минимальная цена: **$0.001/мин**",
            parse_mode="Markdown")
    try:
        args = command.args.strip().split()
        if len(args) != 3:
            raise ValueError("Нужно 3 аргумента")
        clone_username = args[0].lstrip("@")
        phone = args[1].strip()
        price = float(args[2].replace(",", "."))

        if price < 0.001:
            return await message.answer(
                "⚠️ Минимальная цена: **$0.001/мин**", parse_mode="Markdown")

        # Ищем токен клона по username
        res = db_fetchone(
            'SELECT api_token, bot_id FROM clones WHERE bot_username=?',
            (clone_username,))
        if not res:
            return await message.answer(
                f"❌ Клон-бот `@{clone_username}` не найден в базе.",
                parse_mode="Markdown")

        clone_token, bot_id = res
        clone_bot_db = f"clone_{bot_id}.db"

        # Обновляем цену напрямую в БД клона
        if not os.path.exists(clone_bot_db):
            return await message.answer(
                f"❌ База данных клона `{clone_bot_db}` не найдена.\n"
                "Убедитесь, что клон-бот хотя бы раз запускался.",
                parse_mode="Markdown")

        clone_db = sqlite3.connect(clone_bot_db, check_same_thread=False)
        clone_db.execute('PRAGMA busy_timeout=3000')
        clone_cur = clone_db.cursor()
        clone_cur.execute(
            'UPDATE accounts SET price_per_min=? WHERE phone=?', (price, phone))
        clone_db.commit()
        rows_affected = clone_cur.rowcount
        clone_db.close()

        if rows_affected == 0:
            return await message.answer(
                f"❌ Номер `{phone}` не найден в клоне `@{clone_username}`.",
                parse_mode="Markdown")

        await message.answer(
            f"✅ Цена для `{phone}` в клоне `@{clone_username}` установлена: **${price}/мин**",
            parse_mode="Markdown")

        # Уведомляем владельца клона
        owner_res = db_fetchone('SELECT owner_id FROM clones WHERE bot_username=?', (clone_username,))
        if owner_res:
            try:
                clone_bot_obj = Bot(token=clone_token)
                await clone_bot_obj.send_message(
                    owner_res[0],
                    f"📢 **Администратор изменил цену аккаунта**\n\n"
                    f"📱 Номер: `{phone}`\n"
                    f"💰 Новая цена: **${price}/мин**",
                    parse_mode="Markdown")
                await clone_bot_obj.session.close()
            except Exception as e:
                logging.error(f"Не удалось уведомить владельца клона: {e}")

    except ValueError as e:
        await message.answer(
            f"⚠️ Ошибка: {e}\n\n"
            "Формат: `/setpriceclon @username +7999... 0.05`",
            parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


# --- НОВАЯ КОМАНДА: РЕДАКТИРОВАТЬ ЧАТЫ КАТАЛОГА ---
@dp.message(Command("redak"))
async def adm_redak(message: Message, command: CommandObject):
    """
    /redak +79991234567 https://t.me/chat1, https://t.me/chat2
    Редактирует список чатов, которые отображаются в каталоге при нажатии "Инфо".
    """
    if message.from_user.id != ADMIN_ID: return
    if not command.args:
        return await message.answer(
            "⚠️ Формат: `/redak +79991234567 чаты`\n\nПример:\n`/redak +79991234567 https://t.me/chat1, https://t.me/chat2`",
            parse_mode="Markdown")
    try:
        parts = command.args.split(maxsplit=1)
        phone = parts[0].strip()
        chats_text = parts[1].strip() if len(parts) > 1 else ""

        res = db_fetchone('SELECT phone FROM accounts WHERE phone = ?', (phone,))
        if not res:
            return await message.answer(f"❌ Аккаунт `{phone}` не найден.", parse_mode="Markdown")

        cur.execute('UPDATE accounts SET catalog_chats = ? WHERE phone = ?', (chats_text, phone))
        db.commit()
        await message.answer(
            f"✅ Список чатов для `{phone}` обновлён:\n`{chats_text}`",
            parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


# --- НОВАЯ КОМАНДА: НАПИСАТЬ ВСЕМ ПОЛЬЗОВАТЕЛЯМ ---
@dp.message(Command("all"))
async def adm_broadcast_all(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args:
        return await message.answer("⚠️ Формат: `/all ваше сообщение`", parse_mode="Markdown")

    text = command.args.strip()
    users = db_fetchall('SELECT user_id FROM users')
    sent = 0
    failed = 0
    for (uid,) in users:
        try:
            await bot.send_message(uid, f"📢 **Сообщение от администратора:**\n\n{text}", parse_mode="Markdown")
            sent += 1
        except:
            failed += 1
        await asyncio.sleep(0.05)  # Антиспам-пауза

    await message.answer(f"✅ Рассылка завершена.\nОтправлено: {sent}\nОшибок: {failed}")


# --- НОВАЯ КОМАНДА: НАПИСАТЬ КОНКРЕТНОМУ ПОЛЬЗОВАТЕЛЮ ---
# Форматы:
#   /pm ID сообщение                          — отправить в основной бот
#   /pm @username_клона ID сообщение          — отправить через клон-бот
@dp.message(Command("pm"))
async def adm_pm(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args:
        return await message.answer(
            "⚠️ Форматы:\n"
            "`/pm ID сообщение` — ответ через основной бот\n"
            "`/pm @username_клона ID сообщение` — ответ через клон-бот",
            parse_mode="Markdown")
    try:
        args = command.args.strip()

        # Формат: /pm @username_клона ID текст
        if args.startswith("@"):
            parts = args.split(maxsplit=2)
            clone_username = parts[0].lstrip("@")
            uid = int(parts[1])
            text = parts[2] if len(parts) > 2 else ""
            if not text:
                return await message.answer("⚠️ Сообщение не может быть пустым.")

            # Ищем токен клона по username
            res = db_fetchone(
                'SELECT api_token FROM clones WHERE bot_username=?', (clone_username,))
            if not res:
                return await message.answer(
                    f"❌ Клон-бот `@{clone_username}` не найден в базе.",
                    parse_mode="Markdown")

            clone_token = res[0]
            clone_bot_obj = Bot(token=clone_token)
            try:
                await clone_bot_obj.send_message(
                    uid,
                    f"📩 **Сообщение от администратора:**\n\n{text}",
                    parse_mode="Markdown")
                await message.answer(
                    f"✅ Ответ отправлен пользователю `{uid}` через `@{clone_username}`.",
                    parse_mode="Markdown")
            finally:
                await clone_bot_obj.session.close()

        # Формат: /pm ID текст
        else:
            parts = args.split(maxsplit=1)
            uid = int(parts[0])
            text = parts[1].strip() if len(parts) > 1 else ""
            if not text:
                return await message.answer("⚠️ Сообщение не может быть пустым.")
            await bot.send_message(
                uid,
                f"📩 **Сообщение от администратора:**\n\n{text}",
                parse_mode="Markdown")
            await message.answer(
                f"✅ Сообщение успешно отправлено пользователю `{uid}`.",
                parse_mode="Markdown")

    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")


# --- КОМАНДА ОТВЕТА ПОЛЬЗОВАТЕЛЯ АДМИНУ ---
@dp.message(Command("pma"))
async def user_reply_to_admin(message: Message, command: CommandObject):
    if not command.args:
        return await message.answer("⚠️ Формат: /pma ваше сообщение")
    text = command.args.strip()
    user = message.from_user
    user_info = f"ID: `{user.id}`"
    if user.username:
        user_info += f" | @{user.username}"
    if user.full_name:
        user_info += f" | {user.full_name}"
    try:
        # Отправляем ВСЕМ ADMIN_ID (можно расширить список)
        await bot.send_message(
            ADMIN_ID,
            f"📩 **Сообщение от пользователя (основной бот)**\n"
            f"{user_info}\n\n"
            f"💬 {text}\n\n"
            f"📤 Ответить: `/pm {user.id} ваш ответ`",
            parse_mode="Markdown"
        )
        await message.answer("✅ Ваше сообщение отправлено администратору.")
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить сообщение: {e}")


# --- ОСНОВНЫЕ ОБРАБОТЧИКИ ---
@dp.message(Command("start"))
@dp.callback_query(F.data == "to_main")
async def start_cmd(event: types.Message | types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = event.from_user.id

    if get_balance(user_id) is None:
        cur.execute('INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, ?)',
                    (user_id, 0.0))
        db.commit()
        try:
            await notify_admins(
                f"🆕 **Новый пользователь зарегистрирован!**\n"
                f"👤 ID: `{user_id}`\n"
                f"📛 Имя: {event.from_user.full_name}\n"
                f"🔗 @{event.from_user.username or '—'}")
        except Exception:
            pass

    caption = "👋 Главное меню. Выберите раздел:"
    if isinstance(event, Message):
        await send_panel_photo(
            event,
            photo=get_tab_image("main"),
            caption=caption,
            reply_markup=main_menu(user_id),
        )
        return
    await delete_tracked_panel(event.message.chat.id, user_id)
    try:
        await event.message.delete()
    except Exception:
        pass
    sent = await event.message.answer_photo(
        photo=get_tab_image("main"),
        caption=caption,
        reply_markup=main_menu(user_id),
        parse_mode="Markdown",
    )
    track_panel_message(user_id, sent.message_id)
    await event.answer()



@dp.message(F.text == "📱 ТгАккаунт")
async def tg_account_menu(message: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="🛒 Каталог на продажу", callback_data="tg_sale_catalog")
    kb.button(text="⬅️ Назад", callback_data="to_main")
    kb.adjust(1)

    await send_panel_photo(
        message,
        photo=get_tab_image("tgacc"),
        caption=(
            "📱 **ТгАккаунт**\n\n"
            "Здесь отображаются отдельные ТГ-аккаунты, доступные для продажи.\n"
            "Нажмите кнопку ниже, чтобы открыть каталог."
        ),
        reply_markup=kb.as_markup(),
        parse_mode="Markdown",
    )


@dp.message(F.text == "⭐ Tg Stars")
@dp.callback_query(F.data == "tg_stars_menu")
async def tg_stars_menu(event: Message | types.CallbackQuery, state: FSMContext):
    await state.clear()
    if isinstance(event, Message):
        await event.answer("ℹ️ Этот раздел больше недоступен.")
    else:
        await event.answer("ℹ️ Этот раздел больше недоступен.", show_alert=True)


@dp.callback_query(F.data.startswith("tgstars_qty_"))
async def tgstars_qty_pick(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer("ℹ️ Этот раздел больше недоступен.", show_alert=True)


@dp.message(States.tg_stars_custom_qty)
async def tgstars_custom_qty_input(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("ℹ️ Этот раздел больше недоступен.")


@dp.callback_query(F.data == "tgstars_target_self")
async def tgstars_target_self(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer("ℹ️ Этот раздел больше недоступен.", show_alert=True)


@dp.callback_query(F.data == "tgstars_target_other")
async def tgstars_target_other(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer("ℹ️ Этот раздел больше недоступен.", show_alert=True)


@dp.message(States.tg_stars_username)
async def tgstars_username_input(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("ℹ️ Этот раздел больше недоступен.")


def _extract_stars_balance_value(balance_obj) -> int:
    if balance_obj is None:
        return 0
    if isinstance(balance_obj, (int, float)):
        return int(balance_obj)
    # На некоторых сборках Telethon баланс может приходить как объект с полем amount.
    if hasattr(balance_obj, "amount"):
        try:
            return int(getattr(balance_obj, "amount") or 0)
        except Exception:
            return 0
    try:
        return int(balance_obj)
    except Exception:
        return 0


async def _get_account_stars_balance(phone: str) -> int | None:
    candidates = [
        os.path.join("sale_sessions", phone),
        os.path.join("sale_sessions", phone.replace("+", "")),
    ]
    last_err = None

    for session_base in candidates:
        if not (os.path.exists(session_base) or os.path.exists(session_base + ".session")):
            continue

        c = TelegramClient(session_base, API_ID, API_HASH)
        try:
            await c.connect()
            if not await c.is_user_authorized():
                continue

            # Вариант 1: peer = me (наиболее стабильный)
            try:
                me_peer = await c.get_input_entity("me")
                st = await c(GetStarsStatusRequest(peer=me_peer))
                return _extract_stars_balance_value(getattr(st, "balance", 0))
            except Exception as e:
                last_err = e

            # Вариант 2: peer = InputPeerSelf (fallback)
            try:
                st = await c(GetStarsStatusRequest(peer=InputPeerSelf()))
                return _extract_stars_balance_value(getattr(st, "balance", 0))
            except Exception as e:
                last_err = e
                continue
        except Exception as e:
            last_err = e
            continue
        finally:
            try:
                await c.disconnect()
            except Exception:
                pass

    if last_err:
        logging.warning(f"[tgstars] balance read failed for {phone}: {type(last_err).__name__}: {last_err}")
    return None


async def _pick_star_account_for_qty(qty: int) -> tuple[str | None, int | None]:
    rows = db_fetchall(
        "SELECT phone FROM tg_star_accounts WHERE is_active = 1 ORDER BY added_at DESC",
        (),
    )
    best_phone = None
    best_bal: int | None = None
    for (phone,) in rows:
        bal = await _get_account_stars_balance(phone)
        if bal is None:
            if best_phone is None:
                best_phone = phone  # fallback, если API баланса недоступен
            continue
        if best_bal is None or bal > best_bal:
            best_bal = bal
            best_phone = phone
        if bal >= qty:
            return phone, bal
    if best_phone and best_bal is None:
        # Не смогли прочитать баланс ни у одного активного аккаунта.
        # Возвращаем fallback-аккаунт, чтобы не блокировать заказ ложным "0 звезд".
        return best_phone, None
    return None, best_bal if best_bal is not None else 0


async def _process_tgstars_order(msg: Message, user_id: int, target_username: str, state: FSMContext):
    d = await state.get_data()
    qty = int(d.get("tg_stars_qty", 0))
    if qty <= 0:
        await state.clear()
        return await msg.answer("❌ Количество звёзд не выбрано. Откройте `⭐ Tg Stars` заново.", parse_mode="Markdown")

    packs = split_stars_to_packs(qty)
    if not packs:
        await state.clear()
        return await msg.answer("❌ Нельзя разбить это количество на подарки. Выберите другое число.")

    cost = round(qty * get_star_rate(), 2)
    bal = get_balance(user_id) or 0
    if bal < cost:
        await state.clear()
        return await msg.answer(f"❌ Недостаточно средств. Нужно **${cost}**", parse_mode="Markdown")

    active_rows = db_fetchall("SELECT phone FROM tg_star_accounts WHERE is_active = 1", ())
    if not active_rows:
        await state.clear()
        return await msg.answer("❌ Сейчас нет активных аккаунтов для выдачи Tg Stars.")

    acc_phone, stars_bal = await _pick_star_account_for_qty(qty)
    if not acc_phone:
        await state.clear()
        return await msg.answer(
            f"❌ Недостаточно звёзд на активных аккаунтах.\n"
            f"Нужно: **{qty}** ⭐\n"
            f"Максимум доступно сейчас: **{stars_bal}** ⭐",
            parse_mode="Markdown",
        )
    if stars_bal is None:
        append_daily_log(
            f"TG_STARS_BALANCE_WARN | account={acc_phone} | qty={qty} | balance_read=unknown | action=allow_order"
        )

    cur.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, user_id))
    cur.execute(
        "INSERT INTO tg_star_orders (user_id, target_username, stars_count, amount_usd, packs, account_phone, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending_send', ?)",
        (user_id, target_username, qty, cost, ",".join(map(str, packs)), acc_phone, int(time.time())),
    )
    db.commit()
    order_id = cur.lastrowid
    append_daily_log(
        f"SALE_TG_STARS | order_id={order_id} | buyer_id={user_id} | target={target_username} | stars={qty} | packs={'+'.join(map(str,packs))} | amount=${cost} | account={acc_phone}"
    )

    await state.clear()
    await msg.answer(
        f"✅ Заказ #{order_id} создан\n"
        f"Получатель: `{target_username}`\n"
        f"Звёзды: **{qty}**\n"
        f"Списано: **${cost}**\n"
        f"Выдача пакетами: `{'+'.join(map(str, packs))}`\n\n"
        "Заказ поставлен в авто-выдачу.",
        parse_mode="Markdown",
    )
@dp.callback_query(F.data == "tg_sale_catalog")
async def tg_sale_catalog(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    rows = db_fetchall(
        'SELECT id, phone, lot_name, price FROM tg_accounts WHERE is_sold = 0 ORDER BY id DESC LIMIT 50',
        (),
    )

    kb = InlineKeyboardBuilder()
    if not rows:
        kb.button(text="⬅️ Назад", callback_data="to_main")
        await send_panel_text(
            call,
            text="🛒 Каталог продаж пока пуст.",
            reply_markup=kb.as_markup(),
        )
        return

    for lot_id, phone, lot_name, price in rows:
        lot = lot_name.strip() if lot_name else "без названия"
        kb.button(text=f"🟢 {lot} | ${round(float(price or 0), 2)}", callback_data=f"tg_sale_view_{lot_id}")
    kb.button(text="⬅️ Назад", callback_data="to_main")
    kb.adjust(1)

    await send_panel_text(
        call,
        text="🛒 **Каталог ТГ-аккаунтов на продажу**\n\nВыберите лот:",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown",
    )

@dp.callback_query(F.data.startswith("tg_sale_view_"))
async def tg_sale_view(call: types.CallbackQuery):
    try:
        lot_id = int(call.data.split("_")[-1])
    except Exception:
        return await call.answer("❌ Неверный лот.", show_alert=True)

    res = db_fetchone(
        'SELECT phone, lot_name, price, sold_at FROM tg_accounts WHERE id = ? AND is_sold = 0',
        (lot_id,),
    )
    if not res:
        return await call.answer("❌ Лот не найден или уже продан.", show_alert=True)

    phone, lot_name, price, added_at = res
    lot = lot_name.strip() if lot_name else "без названия"
    added_txt = time.strftime("%d.%m.%Y %H:%M", time.localtime(int(added_at or time.time())))

    kb = InlineKeyboardBuilder()
    kb.button(text="🛍 Купить", callback_data=f"tg_sale_buy_{lot_id}")
    kb.button(text="👨‍💻 Связаться с поддержкой", url=SUPPORT_URL)
    kb.button(text="⬅️ Назад", callback_data="tg_sale_catalog")
    kb.adjust(1)

    await call.message.edit_text(
        f"🧾 **Лот #{lot_id}**\n\n"
        f"📱 Номер: `{phone}`\n"
        f"🏷 Название: **{lot}**\n"
        f"💵 Цена: **${round(float(price or 0), 2)}**\n"
        f"🕒 Добавлен: `{added_txt}`\n\n"
        "Для покупки напишите в поддержку.",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown",
    )


def _sale_bought_kb(lot_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="📁 tdata", callback_data=f"tg_sale_tdata_{lot_id}")
    kb.button(text="🔑 Код", callback_data=f"tg_sale_code_{lot_id}")
    kb.button(text="✅ Проверить отзыв", callback_data="review_check")
    kb.button(text="📝 Написать отзыв", url=REVIEW_CHAT_URL)
    kb.button(text="👨‍💻 Связаться с Поддержкой", url=SUPPORT_URL)
    kb.adjust(1)
    return kb.as_markup()


def _resolve_tdatafull_folder(phone: str) -> str | None:
    candidates = [
        os.path.join("tdatafull", phone),
        os.path.join("tdatafull", phone.replace("+", "")),
    ]
    for path in candidates:
        if os.path.isdir(path):
            return path
    return None


@dp.callback_query(F.data.startswith("tg_sale_buy_"))
async def tg_sale_buy(call: types.CallbackQuery):
    try:
        lot_id = int(call.data.split("_")[-1])
    except Exception:
        return await call.answer("❌ Неверный лот.", show_alert=True)

    row = db_fetchone(
        "SELECT phone, lot_name, price, is_sold FROM tg_accounts WHERE id = ?",
        (lot_id,),
    )
    if not row:
        return await call.answer("❌ Лот не найден.", show_alert=True)

    phone, lot_name, price, is_sold = row
    if is_sold:
        return await call.answer("❌ Лот уже продан.", show_alert=True)

    price_val = round(float(price or 0), 2)
    bal = get_balance(call.from_user.id) or 0
    if bal < price_val:
        return await call.answer(f"❌ Недостаточно средств. Нужно: ${price_val}", show_alert=True)

    cur.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (price_val, call.from_user.id))
    cur.execute(
        "UPDATE tg_accounts SET is_sold = 1, sold_to = ?, sold_at = ? WHERE id = ?",
        (call.from_user.id, int(time.time()), lot_id),
    )
    db.commit()
    append_daily_log(
        f"SALE_TG_ACCOUNT | order_lot_id={lot_id} | buyer_id={call.from_user.id} | phone={phone} | amount=${price_val}"
    )

    lot = lot_name.strip() if lot_name else "без названия"
    await call.message.edit_text(
        f"✅ **Покупка успешна**\n\n"
        f"🧾 Лот: **{lot}**\n"
        f"📱 Номер: `{phone}`\n"
        f"💵 Списано: **${price_val}**\n\n"
        f"Спасибо за покупку!\n\n"
        f"Будем рады отзыву в чате: {REVIEW_CHAT_URL}\n"
        f"Если в отзыве есть `@DutsiBot`, можно получить бонус **${REVIEW_BONUS}**.",
        reply_markup=_sale_bought_kb(lot_id),
        parse_mode="Markdown",
    )


@dp.callback_query(F.data.startswith("tg_sale_tdata_"))
async def tg_sale_tdata(call: types.CallbackQuery):
    try:
        lot_id = int(call.data.split("_")[-1])
    except Exception:
        return await call.answer("❌ Неверный лот.", show_alert=True)

    row = db_fetchone(
        "SELECT phone, sold_to FROM tg_accounts WHERE id = ? AND is_sold = 1",
        (lot_id,),
    )
    if not row:
        return await call.answer("❌ Лот не найден.", show_alert=True)
    phone, sold_to = row
    if sold_to != call.from_user.id and call.from_user.id != ADMIN_ID:
        return await call.answer("⛔ Доступ только покупателю.", show_alert=True)

    folder = _resolve_tdatafull_folder(phone)
    if not folder:
        return await call.answer("❌ Папка tdatafull для этого номера не найдена.", show_alert=True)

    os.makedirs("temp", exist_ok=True)
    zip_path = os.path.join("temp", f"tdata_{phone.replace('+', '')}.zip")
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(folder):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    arc = os.path.relpath(fpath, folder)
                    zf.write(fpath, arcname=arc)
        await call.message.answer_document(
            FSInputFile(zip_path),
            caption=f"📁 tdata для `{phone}`",
            parse_mode="Markdown",
        )
        await call.answer("Готово")
    except Exception as e:
        await call.answer(f"❌ Ошибка архивации: {e}", show_alert=True)


@dp.callback_query(F.data.startswith("tg_sale_code_"))
async def tg_sale_code(call: types.CallbackQuery):
    try:
        lot_id = int(call.data.split("_")[-1])
    except Exception:
        return await call.answer("❌ Неверный лот.", show_alert=True)

    row = db_fetchone(
        "SELECT phone, sold_to FROM tg_accounts WHERE id = ? AND is_sold = 1",
        (lot_id,),
    )
    if not row:
        return await call.answer("❌ Лот не найден.", show_alert=True)
    phone, sold_to = row
    if sold_to != call.from_user.id and call.from_user.id != ADMIN_ID:
        return await call.answer("⛔ Доступ только покупателю.", show_alert=True)

    session_path = os.path.join("sale_sessions", phone)
    reader_client = TelegramClient(session_path, API_ID, API_HASH)
    try:
        await reader_client.connect()
        if not await reader_client.is_user_authorized():
            return await call.answer("❌ Сессия не авторизована.", show_alert=True)

        req_dir = "sale_code_requests"
        os.makedirs(req_dir, exist_ok=True)
        req_client = TelegramClient(os.path.join(req_dir, f"{phone}_{call.from_user.id}"), API_ID, API_HASH)
        try:
            await req_client.connect()
            await req_client.send_code_request(phone)
        finally:
            try:
                await req_client.disconnect()
            except Exception:
                pass

        await asyncio.sleep(2)
        msgs = await reader_client.get_messages(777000, limit=8)
        login_code = None
        for msg in msgs:
            txt = (msg.message or "").strip()
            m = re.search(r"\b(\d{5,6})\b", txt)
            if m:
                login_code = m.group(1)
                break

        if not login_code:
            return await call.answer("❌ Не удалось прочитать код из чата Telegram. Повторите ещё раз.", show_alert=True)

        await call.message.answer(
            f"🔑 **Код для входа** в `{phone}`:\n\n`{login_code}`\n\n"
            "Это код из чата Telegram (777000).",
            parse_mode="Markdown",
        )
        await call.answer("Код отправлен")
    except Exception as e:
        await call.answer(f"❌ Не удалось получить код: {e}", show_alert=True)
    finally:
        try:
            await reader_client.disconnect()
        except Exception:
            pass

@dp.message(F.text == "❓ Помощь")
async def help_menu(message: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="1) Рассылка", callback_data="help_mailing")
    kb.button(text="2) Покупка аккаунтов", callback_data="help_tgacc")
    kb.button(text="3) Другой вопрос", callback_data="help_other")
    kb.button(text="⬅️ Назад", callback_data="to_main")
    kb.adjust(1)
    await send_panel_photo(
        message,
        photo=get_tab_image("help"),
        caption="❓ **Помощь**\n\nВыберите нужный раздел:",
        reply_markup=kb.as_markup(),
    )


@dp.callback_query(F.data.in_({"help_mailing", "help_tgacc", "help_other"}))
async def help_topics(call: types.CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад в Помощь", callback_data="help_back")
    kb.adjust(1)
    if call.data == "help_mailing":
        text = (
            "📣 **Рассылка**\n\n"
            "1. Откройте `📂 Каталог аккаунтов` и арендуйте номер.\n"
            "2. Перейдите в `🔑 Моя аренда`.\n"
            "3. Настройте текст/фото/чаты/интервал.\n"
            "4. Нажмите `🚀 ПУСК`."
        )
    elif call.data == "help_tgacc":
        text = (
            "🛒 **Покупка аккаунтов**\n\n"
            "1. Откройте `📱 ТгАккаунт`.\n"
            "2. Выберите лот в каталоге.\n"
            "3. Нажмите `Купить` и оплатите с баланса.\n"
            "4. После покупки получите `tdata` и `код`."
        )
    else:
        kb = InlineKeyboardBuilder()
        kb.button(text="👨‍💻 Написать в поддержку", url=SUPPORT_URL)
        kb.button(text="⬅️ Назад в Помощь", callback_data="help_back")
        kb.adjust(1)
        text = "❓ **Другой вопрос**\n\nНапишите в поддержку, поможем вручную."

    await call.message.edit_caption(caption=text, reply_markup=kb.as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data == "help_back")
async def help_back(call: types.CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.button(text="1) Рассылка", callback_data="help_mailing")
    kb.button(text="2) Покупка аккаунтов", callback_data="help_tgacc")
    kb.button(text="4) Другой вопрос", callback_data="help_other")
    kb.button(text="⬅️ Назад", callback_data="to_main")
    kb.adjust(1)
    await call.message.edit_caption(
        caption="❓ **Помощь**\n\nВыберите нужный раздел:",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown",
    )


@dp.message(F.text == "👨‍💻 Support")
async def support_info(message: Message):
    kb = InlineKeyboardBuilder()
    kb.button(text="Написать в Поддержку", url=SUPPORT_URL)
    kb.button(text="⬅️ Назад", callback_data="to_main")
    kb.adjust(1)
    await send_panel_photo(
        message,
        photo=get_tab_image("support"),
        caption="Связь с администрацией и поддержка:",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown",
    )


@dp.message(F.text == "📘 Правила")
@dp.callback_query(F.data == "to_rules")
async def rules_menu(event: Message | types.CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="👨‍💻 Поддержка", url=SUPPORT_URL)
    kb.button(text="⬅️ Назад", callback_data="to_main")
    kb.adjust(1)
    text = (
        "📘 **Правила использования сервиса**\n\n"
        "1. Арендованный аккаунт передаётся во временное пользование только на оплаченный срок.\n"
        "2. Пользователь несёт ответственность за содержание и частоту рассылки, а также за соблюдение правил Telegram.\n"
        "3. Возврат средств **не производится**, если во время аренды аккаунт получил спамблок, заморозку или блокировку.\n"
        "4. При выявлении злоупотреблений могут быть применены санкции: ограничение функций или бан аккаунта в сервисе сроком до **2 суток**.\n"
        "5. Запрещены мошеннические, вредоносные и нарушающие закон действия, а также обход ограничений платформы.\n"
        "6. Повторные нарушения могут привести к отказу в дальнейшем обслуживании без компенсации.\n"
        "7. Используя сервис, вы подтверждаете согласие с настоящими правилами и условиями работы бота."
    )
    await send_panel_photo(
        event,
        photo=get_tab_image("rules"),
        caption=text,
        reply_markup=kb.as_markup(),
        parse_mode="Markdown",
    )


@dp.message(F.text == "📝 Отзывы")
@dp.callback_query(F.data == "to_reviews")
async def reviews_menu(event: Message | types.CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardBuilder()
    kb.button(text="📝 Написать отзыв", url=REVIEW_CHAT_URL)
    kb.button(text="✅ Проверить отзыв", callback_data="review_check")
    kb.button(text="⬅️ Назад", callback_data="to_main")
    await send_panel_photo(
        event,
        photo=get_tab_image("reviews"),
        caption=(
            "📝 **Отзывы**\n\n"
            f"Оставьте отзыв в чате: {REVIEW_CHAT_URL}\n"
            f"Добавьте в текст `{REVIEW_TRIGGER}` и получите бонус **${REVIEW_BONUS}**."
        ),
        reply_markup=kb.adjust(1).as_markup(),
        parse_mode="Markdown",
    )


@dp.callback_query(F.data == "review_check")
async def review_check(call: types.CallbackQuery):
    row = db_fetchone("SELECT amount, rewarded_at FROM review_rewards WHERE user_id = ?", (call.from_user.id,))
    if row:
        amount, ts = row
        dt = time.strftime('%d.%m.%Y %H:%M', time.localtime(int(ts or 0))) if ts else "—"
        return await call.answer(f"✅ Бонус уже начислен: ${round(float(amount or 0),2)} ({dt})", show_alert=True)
    await call.answer(
        f"Пока не найден отзыв с {REVIEW_TRIGGER}. Напишите в {REVIEW_CHAT_URL} и нажмите снова.",
        show_alert=True,
    )


@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def review_listener(message: Message):
    chat_username = (message.chat.username or "").lower()
    if chat_username != REVIEW_CHAT_USERNAME:
        return
    if not message.from_user or message.from_user.is_bot:
        return
    text = (message.text or message.caption or "")
    if REVIEW_TRIGGER not in text.lower():
        return

    uid = message.from_user.id
    already = db_fetchone("SELECT 1 FROM review_rewards WHERE user_id = ?", (uid,))
    if already:
        return
    if get_balance(uid) is None:
        cur.execute('INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, ?)', (uid, 0.0))

    cur.execute(
        "INSERT OR REPLACE INTO review_rewards (user_id, amount, rewarded_at, review_text) VALUES (?, ?, ?, ?)",
        (uid, REVIEW_BONUS, int(time.time()), text[:500]),
    )
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (REVIEW_BONUS, uid))
    db.commit()

    try:
        await bot.send_message(
            uid,
            f"🎉 Спасибо за отзыв! На баланс начислено **${REVIEW_BONUS}**.",
            parse_mode="Markdown",
        )
    except Exception:
        pass


@dp.message(F.text == "👤 Профиль")
@dp.message(F.text == "💰 Баланс")
@dp.callback_query(F.data.in_({"to_balance", "to_profile"}))
async def profile_menu(event: Message | types.CallbackQuery, state: FSMContext):
    await state.clear()
    bal = get_balance(event.from_user.id)
    kb = InlineKeyboardBuilder()
    kb.button(text="🔌 @send", callback_data="topup_crypto")
    kb.button(text="🛒 История покупок", callback_data="profile_buy_history")
    kb.button(text="💳 История пополнений", callback_data="profile_topup_history")
    kb.button(text="⬅️ Назад", callback_data="to_main")
    await send_panel_photo(
        event,
        photo=get_tab_image("profile"),
        caption=f"👤 **Профиль**\n\n💳 Ваш баланс: **${bal}**",
        reply_markup=kb.adjust(1, 2, 1).as_markup(),
    )


@dp.callback_query(F.data == "profile_buy_history")
async def profile_buy_history(call: types.CallbackQuery):
    sale_rows = db_fetchall(
        "SELECT lot_name, phone, price, sold_at FROM tg_accounts WHERE sold_to = ? AND is_sold = 1 ORDER BY sold_at DESC LIMIT 20",
        (call.from_user.id,),
    )
    rent_rows = db_fetchall(
        "SELECT phone, cost, date FROM rent_history WHERE user_id = ? ORDER BY id DESC LIMIT 20",
        (call.from_user.id,),
    )

    items = []
    for lot_name, phone, price, sold_at in sale_rows:
        lot = lot_name.strip() if lot_name else "без названия"
        ts = int(sold_at or 0)
        items.append((ts, f"• 🛍 **Покупка**: **{lot}** | `{phone}` | ${round(float(price or 0),2)} | {time.strftime('%d.%m.%Y %H:%M', time.localtime(ts)) if ts else '—'}"))

    for phone, cost, date in rent_rows:
        try:
            ts = int(time.mktime(time.strptime(date, "%Y-%m-%d %H:%M:%S")))
        except Exception:
            ts = 0
        items.append((ts, f"• 🔑 **Аренда**: `{phone}` | ${round(float(cost or 0),2)} | {date}"))

    items.sort(key=lambda x: x[0], reverse=True)
    lines = [x[1] for x in items[:30]]

    if not lines:
        text = "🛒 **История покупок**\n\nПока пусто."
    else:
        text = "🛒 **История покупок**\n\n" + "\n".join(lines)
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data="to_profile")
    await call.message.edit_caption(caption=text, reply_markup=kb.as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data == "profile_topup_history")
async def profile_topup_history(call: types.CallbackQuery):
    rows = db_fetchall(
        "SELECT amount, method, date FROM payments WHERE user_id = ? ORDER BY id DESC LIMIT 20",
        (call.from_user.id,),
    )
    if not rows:
        text = "💳 **История пополнений**\n\nПока пусто."
    else:
        lines = [f"• ${round(float(a or 0),2)} | {m} | {d}" for a, m, d in rows]
        text = "💳 **История пополнений**\n\n" + "\n".join(lines)
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data="to_profile")
    await call.message.edit_caption(caption=text, reply_markup=kb.as_markup(), parse_mode="Markdown")


# --- КАТАЛОГ ---
@dp.message(F.text == "📂 Каталог аккаунтов")
@dp.callback_query(F.data == "catalog_inline")
async def catalog(event: Message | types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = event.from_user.id
    ban_info = check_ban(user_id)
    if ban_info:
        msg = f"🚫 Вы заблокированы до {time.strftime('%d.%m.%Y %H:%M', time.localtime(ban_info[0]))}.\nПричина: {ban_info[1]}\nДоступ в каталог закрыт."
        if isinstance(event, Message):
            await event.answer(msg)
        else:
            await event.answer(msg, show_alert=True)
        return

    # Показываем ВСЕ аккаунты — и свободные, и занятые
    rows = db_fetchall('SELECT phone, price_per_min, is_premium, owner_id, expires FROM accounts', ())
    all_items = [(phone, price, is_premium, owner_id, expires, '') for phone, price, is_premium, owner_id, expires in rows]

    # Если включён показ аккаунтов клонов — подгружаем из каждой клоновой БД
    if get_main_setting('show_clone_accounts') == '1':
        clone_rows = db_fetchall('SELECT bot_id, bot_username FROM clones WHERE is_running=1')
        for bot_id, bot_username in clone_rows:
            conn, ccur = get_clone_db(bot_id)
            if conn is None:
                continue
            try:
                ccur.execute('SELECT phone, price_per_min, is_premium, owner_id, expires FROM accounts')
                for row in ccur.fetchall():
                    phone, price, is_premium, owner_id, expires = row
                    tag = f"@{bot_username}" if bot_username else bot_id
                    all_items.append((phone, price, is_premium, owner_id, expires, tag))
            except Exception:
                pass
            finally:
                conn.close()

    kb = InlineKeyboardBuilder()
    now = int(time.time())
    for phone, price, is_premium, owner_id, expires, clone_tag in all_items:
        is_rented = owner_id is not None and expires is not None and expires > now
        tag_label = f" [{clone_tag}]" if clone_tag else ""
        if is_rented:
            time_left = format_time_left(expires)
            label = f"🔴 {'⭐ ' if is_premium else ''}📱 {phone}{tag_label} (${price}/мин) · ещё {time_left}"
        else:
            label = f"🟢 {'⭐ ' if is_premium else ''}📱 {phone}{tag_label} (${price}/мин)"
        # Для аккаунтов клонов передаём bot_id через callback
        cb = f"view_clone_{bot_id}_{phone}" if clone_tag else f"view_{phone}"
        kb.button(text=label, callback_data=cb)

    kb.adjust(1).row(InlineKeyboardButton(text="⬅️ Назад", callback_data="to_main"))

    caption = "📋 **Все номера в сервисе:**\n🟢 — свободен | 🔴 — занят"
    await send_panel_photo(
        event,
        photo=get_tab_image("catalog"),
        caption=caption,
        reply_markup=kb.as_markup(),
    )


# --- ПРОСМОТР НОМЕРА (Инфо + Аренда) ---
@dp.callback_query(F.data.startswith("view_"))
async def view_account(call: types.CallbackQuery, state: FSMContext):
    phone = call.data[5:]
    res = db_fetchone('SELECT phone, price_per_min, is_premium, owner_id, expires FROM accounts WHERE phone = ?',
                      (phone,))
    if not res:
        return await call.answer("❌ Аккаунт не найден.", show_alert=True)

    _, price, is_premium, owner_id, expires = res
    now = int(time.time())
    is_rented = owner_id is not None and expires is not None and expires > now

    status_icon = "🔴 Занят" if is_rented else "🟢 Свободен"
    premium_text = "⭐ Premium\n" if is_premium else ""
    time_left_text = f"\n⏳ Осталось: {format_time_left(expires)}" if is_rented else ""

    caption = (f"📱 **Номер:** `{phone}`\n"
               f"{premium_text}"
               f"💰 Цена: **${price}/мин**\n"
               f"🔘 Статус: {status_icon}{time_left_text}")

    kb = InlineKeyboardBuilder()
    kb.button(text="ℹ️ Инфо", callback_data=f"info_{phone}")
    kb.button(text="🔑 Аренда", callback_data=f"rent_{phone}")
    kb.button(text="⬅️ Назад", callback_data="catalog_inline")
    kb.adjust(2, 1)

    try:
        await call.message.edit_caption(caption=caption, reply_markup=kb.as_markup(), parse_mode="Markdown")
    except:
        await call.message.answer(caption, reply_markup=kb.as_markup(), parse_mode="Markdown")


# --- КНОПКА "ИНфО" — чаты из каталога ---
@dp.callback_query(F.data.startswith("info_"))
async def show_info(call: types.CallbackQuery):
    phone = call.data[5:]
    res = db_fetchone('SELECT catalog_chats FROM accounts WHERE phone = ?', (phone,))
    if not res:
        return await call.answer("❌ Аккаунт не найден.", show_alert=True)

    chats_raw = res[0] or ""
    chats_list = [c.strip() for c in chats_raw.split(',') if c.strip()]

    if chats_list:
        chats_text = "\n".join([f"• {c}" for c in chats_list])
        text = f"📋 **Чаты для рассылки номера** `{phone}`:\n\n{chats_text}"
    else:
        text = f"ℹ️ Для номера `{phone}` чаты ещё не добавлены.\n\nАдминистратор может добавить их командой:\n`/redak {phone} ссылки`"

    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=f"view_{phone}")

    await call.message.edit_caption(caption=text, reply_markup=kb.as_markup(), parse_mode="Markdown")


# --- КНОПКА "АРЕНДА" ---
@dp.callback_query(F.data.startswith("rent_"))
async def rent_init(call: types.CallbackQuery, state: FSMContext):
    ban_info = check_ban(call.from_user.id)
    if ban_info:
        return await call.answer("🚫 Вы заблокированы. Аренда недоступна.", show_alert=True)

    phone = call.data[5:]

    # Проверяем что номер свободен
    res = db_fetchone('SELECT owner_id, expires FROM accounts WHERE phone = ?', (phone,))
    if res and res[0] is not None and res[1] is not None and res[1] > int(time.time()):
        return await call.answer("❌ Этот номер уже арендован.", show_alert=True)

    await state.update_data(rent_phone=phone)
    await call.message.edit_caption(
        caption=f"⏳ Введите время аренды в минутах\n(От {MIN_RENT_TIME} до {MAX_RENT_TIME}):",
        reply_markup=back_kb(f"view_{phone}").as_markup())
    await state.set_state(States.waiting_for_rent_time)


@dp.message(States.waiting_for_rent_time)
async def rent_finish(m: Message, state: FSMContext):
    data = await state.get_data()
    try:
        mins = int(m.text)
        if mins < MIN_RENT_TIME or mins > MAX_RENT_TIME:
            return await m.answer(f"⚠️ Лимит: {MIN_RENT_TIME} - {MAX_RENT_TIME} минут.")

        res = db_fetchone('SELECT price_per_min FROM accounts WHERE phone = ?', (data['rent_phone'],))
        cost = round(mins * res[0], 2)
        if get_balance(m.from_user.id) < cost:
            return await m.answer("❌ Недостаточно средств.")

        cur.execute('UPDATE users SET balance = balance - ? WHERE user_id = ?', (cost, m.from_user.id))
        exp = int(time.time()) + (mins * 60)
        cur.execute('UPDATE accounts SET owner_id = ?, expires = ?, is_running = 0, notified_10m = 0 WHERE phone = ?',
                    (m.from_user.id, exp, data['rent_phone']))
        cur.execute('INSERT INTO rent_history (user_id, phone, duration, cost, date) VALUES (?, ?, ?, ?, ?)',
                    (m.from_user.id, data['rent_phone'], mins, cost, time.strftime('%Y-%m-%d %H:%M:%S')))
        db.commit()
        append_daily_log(
            f"SALE_RENT | user_id={m.from_user.id} | phone={data['rent_phone']} | mins={mins} | amount=${cost}"
        )
        await m.answer(f"✅ Аккаунт `{data['rent_phone']}` арендован на {mins} мин!\nСписано: **${cost}**",
                       parse_mode="Markdown")
        review_kb = InlineKeyboardBuilder()
        review_kb.button(text="📝 Написать отзыв", url=REVIEW_CHAT_URL)
        review_kb.button(text="✅ Проверить отзыв", callback_data="review_check")
        review_kb.adjust(1)
        await m.answer(
            f"🙏 Спасибо за аренду!\n"
            f"Оставьте отзыв в {REVIEW_CHAT_URL}.\n"
            f"Если в отзыве есть `{REVIEW_TRIGGER}`, можно получить бонус **${REVIEW_BONUS}** (1 раз).",
            reply_markup=review_kb.as_markup(),
            parse_mode="Markdown",
        )

        try:
            await notify_admins(
                f"🔔 **Новая аренда**\n"
                f"👤 Пользователь: `{m.from_user.id}`\n"
                f"📱 Номер: `{data['rent_phone']}`\n"
                f"⏱ Время: {mins} мин.\n"
                f"💰 Списано: **${cost}**")
        except Exception:
            pass
        await state.clear()
    except:
        await m.answer("Ошибка ввода. Введите целое число от 10 до 600.")


# --- УПРАВЛЕНИЕ АРЕНДОЙ ---
@dp.message(F.text == "🔑 Моя аренда")
@dp.callback_query(F.data == "to_my_rents")
async def my_rents(event: Message | types.CallbackQuery, state: FSMContext):
    await state.clear()
    rows = db_fetchall('SELECT phone, is_premium FROM accounts WHERE owner_id = ? AND expires > ?',
                       (event.from_user.id, int(time.time())))
    kb = InlineKeyboardBuilder()
    for r in rows:
        kb.button(text=f"{'⭐ ' if r[1] else ''}⚙️ {r[0]}", callback_data=f"manage_{r[0]}")
    kb.adjust(1).row(InlineKeyboardButton(text="⬅️ Назад", callback_data="to_main"))
    await send_panel_photo(
        event,
        photo=get_tab_image("myrent"),
        caption="🔧 Ваши активные номера:",
        reply_markup=kb.as_markup(),
    )


@dp.callback_query(F.data.startswith("manage_"))
async def manage_acc(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    p = call.data.split("_")[1]
    res = db_fetchone('SELECT is_running FROM accounts WHERE phone = ?', (p,))
    if not res:
        return await call.answer("❌ Номер больше не в вашей аренде", show_alert=True)

    kb = InlineKeyboardBuilder()
    kb.button(text="📝 Текст", callback_data=f"set_text_{p}")
    kb.button(text="🖼 Фото", callback_data=f"set_photo_{p}")
    kb.button(text="👥 Чаты", callback_data=f"set_chats_{p}")
    kb.button(text="⏳ Сек", callback_data=f"set_int_{p}")
    kb.button(text="🛑 СТОП" if res[0] else "🚀 ПУСК", callback_data=f"{'off' if res[0] else 'on'}_{p}")
    kb.button(text="⬅️ Назад", callback_data="to_my_rents")
    await call.message.edit_caption(caption=f"📱 `{p}`\nСтатус: {'🔥 РАБОТАЕТ' if res[0] else '💤 ПАУЗА'}",
                                    reply_markup=kb.adjust(2, 2, 1).as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data.startswith("early_end_warn_"))
async def early_end_warn(call: types.CallbackQuery):
    phone = call.data[len("early_end_warn_"):]
    info = get_rent_refund_info(phone, EARLY_RENT_REFUND_RATIO, call.from_user.id)
    if not info:
        return await call.answer("❌ Аренда уже недоступна.", show_alert=True)

    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, завершить", callback_data=f"early_end_confirm_{phone}")
    kb.button(text="⬅️ Назад", callback_data=f"manage_{phone}")
    kb.adjust(1)
    await call.message.edit_caption(
        caption=(
            f"⚠️ **Досрочное завершение аренды**\n\n"
            f"Номер: `{phone}`\n"
            f"Осталось: **{format_time_left(info['expires'])}**\n"
            f"Полный остаток: **${info['full_amount']}**\n"
            f"К возврату: **${info['refund_amount']}**\n\n"
            f"При досрочном завершении вернётся только **80%** от оставшегося времени."
        ),
        reply_markup=kb.as_markup(),
        parse_mode="Markdown",
    )


@dp.callback_query(F.data.startswith("early_end_confirm_"))
async def early_end_confirm(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    phone = call.data[len("early_end_confirm_"):]
    info = get_rent_refund_info(phone, EARLY_RENT_REFUND_RATIO, call.from_user.id)
    if not info:
        return await call.answer("❌ Аренда уже завершена.", show_alert=True)

    if info["refund_amount"] > 0:
        cur.execute(
            'UPDATE users SET balance = balance + ? WHERE user_id = ?',
            (info["refund_amount"], call.from_user.id),
        )

    cur.execute(
        'UPDATE accounts SET owner_id = NULL, expires = 0, is_running = 0, notified_10m = 0 WHERE phone = ?',
        (phone,),
    )
    db.commit()

    try:
        await notify_admins(
            f"⛔ **Аренда завершена пользователем**\n"
            f"👤 Пользователь: `{call.from_user.id}`\n"
            f"📱 Номер: `{phone}`\n"
            f"💰 Возврат: **${info['refund_amount']}** (80%)"
        )
    except Exception:
        pass

    await call.message.edit_caption(
        caption=(
            f"✅ Аренда `{phone}` завершена досрочно.\n"
            f"На баланс возвращено: **${info['refund_amount']}**"
        ),
        reply_markup=back_kb("to_my_rents").as_markup(),
        parse_mode="Markdown",
    )


# --- ОПЛАТА ---
@dp.callback_query(F.data.startswith("topup_"))
async def topup_init(call: types.CallbackQuery, state: FSMContext):
    if call.data == "topup_stars":
        await state.clear()
        return await call.answer("ℹ️ Этот способ больше недоступен.", show_alert=True)
    method = call.data.split("_")[1]
    await state.update_data(method=method)
    msg = "Введите количество Stars:" if method == 'stars' else "Введите сумму в USD для инвойса @send:"
    await call.message.edit_caption(caption=msg, reply_markup=back_kb("to_balance").as_markup())
    await state.set_state(States.top_up_amount)


@dp.message(States.top_up_amount)
async def create_pay(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        val = float(message.text.replace(",", "."))
        if val <= 0: raise ValueError
    except:
        return await message.answer("Пожалуйста, введите корректное число.")

    if data['method'] == 'stars':
        stars_count = int(val)
        usd_equiv = round(stars_count * get_star_rate(), 2)
        await message.answer_invoice(
            title="Пополнение баланса",
            description=f"Покупка {stars_count} ⭐ Stars → ${usd_equiv} на баланс",
            payload=f"paystars_{usd_equiv}",
            currency="XTR",
            prices=[LabeledPrice(label="Stars", amount=stars_count)],
        )
    elif crypto:
        invoice_kwargs = {
            'amount': val,
            'fiat': 'USD',
            'currency_type': CurrencyType.FIAT if CurrencyType else 'fiat',
            'accepted_assets': get_accepted_send_assets(),
            'description': f"Пополнение баланса на ${val}",
            'payload': f"send_topup_{message.from_user.id}_{val}",
        }
        try:
            inv = await crypto.create_invoice(**invoice_kwargs)
        except Exception:
            logging.exception("Failed to create @send invoice with accepted_assets")
            invoice_kwargs.pop('accepted_assets', None)
            try:
                inv = await crypto.create_invoice(**invoice_kwargs)
            except Exception:
                logging.exception("Failed to create @send invoice")
                await message.answer(
                    "❌ Не удалось создать инвойс `@send`. Попробуйте ещё раз или используйте Stars.",
                    parse_mode="Markdown",
                )
                return
        kb = InlineKeyboardBuilder()
        kb.button(text="Оплатить", url=inv.bot_invoice_url)
        kb.button(text="Проверить", callback_data=f"chk_{inv.invoice_id}_{val}")
        await message.answer(
            f"Инвойс `@send` на **${val}** создан.\n"
            f"Оплатить можно любой поддерживаемой криптовалютой из доступных в `@send`.",
            reply_markup=kb.adjust(1).as_markup(),
            parse_mode="Markdown",
        )
    await state.clear()


@dp.callback_query(F.data.startswith("chk_"))
async def check_crypto(call: types.CallbackQuery):
    _, iid, amt = call.data.split("_")
    inv = await crypto.get_invoices(invoice_ids=int(iid))
    if inv and inv.status == 'paid':
        add_payment_history(call.from_user.id, float(amt), "@send")
        await call.message.edit_text("✅ Оплата получена!")
        try:
            await notify_admins(
                f"💰 **Пополнение баланса**\n"
                f"👤 Пользователь: `{call.from_user.id}`\n"
                f"💵 Сумма: **${amt}**\n"
                f"💳 Метод: @send")
        except Exception:
            pass
    else:
        await call.answer("Не оплачено", show_alert=True)


@dp.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(q.id, ok=True)


@dp.message(F.successful_payment)
async def success_pay(m: Message):
    usd = float(m.successful_payment.invoice_payload.split("_")[1])
    add_payment_history(m.from_user.id, usd, "Stars")
    await m.answer(f"✅ Зачислено ${usd}")
    try:
        await notify_admins(
            f"💰 **Пополнение баланса**\n"
            f"👤 Пользователь: `{m.from_user.id}`\n"
            f"💵 Сумма: **${usd}**\n"
            f"⭐ Метод: Telegram Stars")
    except Exception:
        pass


# --- ТЕЛЕТОН И РАССЫЛКА ---

def _make_hint_and_kb(code_type_name: str, is_resend: bool = False):
    """Возвращает (текст подсказки, InlineKeyboardBuilder) по типу кода."""
    prefix = "📲 *Новый код отправлен*" if is_resend else "📲 *Код отправлен*"
    kb = InlineKeyboardBuilder()
    ctn = code_type_name.lower()
    if "app" in ctn:
        hint = (
            f"{prefix} *в Telegram*\n\n"
            "Код придёт как обычное сообщение от **Telegram** в другом клиенте под этим номером.\n\n"
            "📌 *Где искать:*\n"
            "• Откройте Telegram на телефоне — придёт уведомление\n"
            "• Или войдите через веб-версию ниже\n"
            "• Раздел **Избранное (Saved Messages)** — там будет сообщение с кодом"
        )
        kb.button(text="🌐 web.telegram.org (войти и найти код)", url="https://web.telegram.org/k/")
    elif "sms" in ctn:
        p = "Новый код" if is_resend else "Код"
        hint = f"📩 *{p}* отправлен по SMS на этот номер."
    elif "flash" in ctn or "missed" in ctn:
        p = "Новый код" if is_resend else "Код"
        hint = f"📞 *{p}* — последние цифры номера пропущенного звонка."
    elif "call" in ctn:
        p = "Новый код" if is_resend else "Код"
        hint = f"📞 *{p}* будет продиктован в голосовом звонке."
    elif "fragment" in ctn:
        hint = f"🔗 *Код* доступен на fragment.com для этого номера."
        kb.button(text="🔗 fragment.com", url="https://fragment.com/")
    else:
        hint = f"📨 *Код отправлен*. Проверьте Telegram или SMS на этом номере."
    return hint, kb


async def _disconnect_client(uid: int):
    """Безопасно отключает и удаляет клиента из active_clients."""
    entry = active_clients.pop(uid, None)
    if entry:
        c = entry["client"] if isinstance(entry, dict) else entry
        try:
            await c.disconnect()
        except Exception:
            pass


async def _finalize_star_source_account(m: Message, state: FSMContext, phone: str, uid: int):
    cur.execute(
        "INSERT OR REPLACE INTO tg_star_accounts (phone, is_active, added_at) VALUES (?, 1, ?)",
        (phone, int(time.time())),
    )
    db.commit()
    await _disconnect_client(uid)
    await state.clear()
    await m.answer(f"✅ Аккаунт `{phone}` добавлен в выдачу Tg Stars.", parse_mode="Markdown")


async def _request_code(
    m: Message,
    state: FSMContext,
    phone: str,
    from_panel: bool,
    session_dir: str = "sessions",
):
    """
    Единая функция запроса кода Telegram.
    Создаёт TelegramClient, подключается, отправляет запрос кода.
    При успехе — переводит в waiting_for_code.
    При уже авторизованной сессии — сразу к ask_premium_status.
    """
    uid = m.from_user.id
    await _disconnect_client(uid)

    os.makedirs(session_dir, exist_ok=True)

    c = TelegramClient(
        f"{session_dir}/{phone}",
        API_ID,
        API_HASH,
        receive_updates=False,
        device_model="Desktop",
        system_version="Windows 10",
        app_version="4.16.7",
        lang_code="ru",
        system_lang_code="ru-RU",
    )

    try:
        await c.connect()
        logging.info(f"[addacc] Подключились для {phone}, uid={uid}")
    except Exception as e:
        await m.answer(f"❌ Не удалось подключиться к Telegram: {e}")
        try:
            await c.disconnect()
        except Exception:
            pass
        return

    try:
        if await c.is_user_authorized():
            active_clients[uid] = {"client": c, "hash": None}
            await state.update_data(phone=phone, from_panel=from_panel, session_dir=session_dir)
            d = await state.get_data()
            if d.get("add_mode") == "star_source":
                await _finalize_star_source_account(m, state, phone, uid)
            else:
                await m.answer("✅ Аккаунт уже авторизован в сессии!")
                await ask_premium_status(m, state, phone)
            return

        sent = await c.send_code_request(phone)
        logging.info(f"[addacc] Код запрошен для {phone}, hash={sent.phone_code_hash[:6]}…")

        active_clients[uid] = {"client": c, "hash": sent.phone_code_hash}
        await state.update_data(
            phone=phone,
            from_panel=from_panel,
            code_hash=sent.phone_code_hash,
            session_dir=session_dir,
        )

        hint, kb = _make_hint_and_kb(type(sent.type).__name__.lower())
        await m.answer(
            f"{hint}\n\n✏️ Введите код (цифры слитно или через пробел):",
            parse_mode="Markdown",
            reply_markup=kb.as_markup() if kb.buttons else None,
        )
        await state.set_state(States.waiting_for_code)

    except FloodWaitError as e:
        await m.answer(f"⏳ Слишком много попыток. Подождите {e.seconds} сек и попробуйте снова.")
        await _disconnect_client(uid)
        await state.clear()
    except Exception as e:
        logging.error(f"[addacc] Ошибка запроса кода для {phone}: {e}")
        await m.answer(f"❌ Ошибка при запросе кода: {e}")
        await _disconnect_client(uid)
        await state.clear()

@dp.message(Command("addacc"))
async def add_acc(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    await m.answer("📱 Введите номер телефона (с кодом страны, например: +79991234567):")
    await state.update_data(from_panel=False, add_mode="rent")
    await state.set_state(States.waiting_for_phone)


@dp.message(States.waiting_for_phone)
async def h_phone(m: Message, state: FSMContext):
    d = await state.get_data()
    phone = m.text.strip().replace(" ", "").replace("-", "")
    if not phone.startswith("+"):
        phone = "+" + phone
    from_panel = d.get('from_panel', False)
    add_mode = d.get("add_mode", "rent")
    session_dir = "sale_sessions" if add_mode in ("sale", "star_source") else "sessions"
    await _request_code(m, state, phone, from_panel, session_dir=session_dir)


@dp.message(States.waiting_for_code)
async def h_code(m: Message, state: FSMContext):
    d = await state.get_data()
    uid = m.from_user.id
    code = m.text.strip().replace(" ", "").replace("-", "")
    add_mode = d.get("add_mode", "rent")

    entry = active_clients.get(uid)
    if not entry:
        await m.answer("❌ Сессия прервана. Введите номер телефона заново.")
        await state.set_state(States.waiting_for_phone)
        return

    c         = entry["client"] if isinstance(entry, dict) else entry
    code_hash = entry["hash"]   if isinstance(entry, dict) else d.get("code_hash")

    if not c.is_connected():
        try:
            await c.connect()
        except Exception as e:
            await m.answer(f"❌ Соединение разорвано: {e}\n\nВведите номер телефона заново.")
            await _disconnect_client(uid)
            await state.set_state(States.waiting_for_phone)
            return

    try:
        await c.sign_in(d['phone'], code, phone_code_hash=code_hash)
        # ✅ Успех без 2FA
        if add_mode == "star_source":
            await _finalize_star_source_account(m, state, d['phone'], uid)
        else:
            await ask_premium_status(m, state, d['phone'])

    except SessionPasswordNeededError:
        # Код принят, аккаунт требует 2FA-пароль.
        # Сохраняем клиент — он уже прошёл sign_in по коду, нужен тот же объект для пароля.
        active_clients[uid] = {"client": c, "hash": code_hash}
        await state.set_state(States.waiting_for_password)
        await m.answer(
            "🔐 *На аккаунте включена двухфакторная аутентификация.*\n\n"
            "Введите облачный пароль Telegram\n"
            "_(Настройки → Конфиденциальность → Двухфакторная аутентификация)_:",
            parse_mode="Markdown")

    except PhoneCodeInvalidError:
        await m.answer(
            "❌ *Неверный код.*\n\nПроверьте и введите снова:",
            parse_mode="Markdown")
        # Остаёмся в waiting_for_code

    except PhoneCodeExpiredError:
        phone = d.get('phone', '')
        try:
            if not c.is_connected():
                await c.connect()
            # Для star_source с 2FA иногда код выглядит "expired",
            # но аккаунт уже ждёт пароль 2FA.
            if add_mode == "star_source":
                try:
                    await c(functions.account.GetPasswordRequest())
                    active_clients[uid] = {"client": c, "hash": code_hash}
                    await state.set_state(States.waiting_for_password)
                    await m.answer(
                        "🔐 Код принят, на аккаунте включён 2FA.\n\n"
                        "Введите облачный пароль Telegram:",
                        parse_mode="Markdown",
                    )
                    return
                except Exception:
                    pass
            # ВАЖНО: сначала проверяем, не авторизован ли уже клиент.
            # Для аккаунтов с 2FA Telethon иногда выбрасывает PhoneCodeExpiredError
            # вместо SessionPasswordNeededError, когда код был принят, но нужен пароль.
            already_authed = await c.is_user_authorized()
            if already_authed:
                # Код был принят, аккаунт ждёт пароль 2FA
                active_clients[uid] = {"client": c, "hash": code_hash}
                await state.set_state(States.waiting_for_password)
                await m.answer(
                    "🔐 *На аккаунте включена двухфакторная аутентификация.*\n\n"
                    "Введите облачный пароль Telegram\n"
                    "_(Настройки → Конфиденциальность → Двухфакторная аутентификация)_:",
                    parse_mode="Markdown")
                return
            # Код действительно истёк — запрашиваем новый
            sent = await c.send_code_request(phone)
            active_clients[uid] = {"client": c, "hash": sent.phone_code_hash}
            await state.update_data(code_hash=sent.phone_code_hash)
            hint, hint_kb = _make_hint_and_kb(type(sent.type).__name__.lower(), is_resend=True)
            await m.answer(
                f"⚠️ Код истёк — отправлен новый.\n\n{hint}\n\n✏️ Введите новый код:",
                parse_mode="Markdown",
                reply_markup=hint_kb.as_markup() if hint_kb.buttons else None,
            )
            await state.set_state(States.waiting_for_code)
        except SessionPasswordNeededError:
            active_clients[uid] = {"client": c, "hash": code_hash}
            await state.set_state(States.waiting_for_password)
            await m.answer(
                "🔐 *На аккаунте включена двухфакторная аутентификация.*\n\n"
                "Введите облачный пароль Telegram\n"
                "_(Настройки → Конфиденциальность → Двухфакторная аутентификация)_:",
                parse_mode="Markdown")
        except FloodWaitError as e:
            await m.answer(f"⏳ Флуд-вейт {e.seconds} сек. Введите номер телефона заново.")
            await _disconnect_client(uid)
            await state.set_state(States.waiting_for_phone)
        except Exception as e:
            await m.answer(f"❌ Не удалось запросить новый код: {e}\n\nВведите номер телефона заново.")
            await _disconnect_client(uid)
            await state.set_state(States.waiting_for_phone)

    except FloodWaitError as e:
        await m.answer(f"⏳ Флуд-вейт {e.seconds} сек. Попробуйте позже.")
        await _disconnect_client(uid)
        await state.clear()

    except Exception as e:
        logging.error(f"[h_code] uid={uid} err={type(e).__name__}: {e}")
        await m.answer(f"❌ Ошибка входа ({type(e).__name__}): {e}\n\nВведите номер телефона заново.")
        await _disconnect_client(uid)
        await state.set_state(States.waiting_for_phone)


@dp.message(States.waiting_for_password)
async def h_2fa(m: Message, state: FSMContext):
    d = await state.get_data()
    uid = m.from_user.id
    password = m.text.strip()
    add_mode = d.get("add_mode", "rent")

    entry = active_clients.get(uid)
    if not entry:
        await m.answer("❌ Сессия прервана. Введите номер телефона заново.")
        await state.set_state(States.waiting_for_phone)
        return

    c = entry["client"] if isinstance(entry, dict) else entry

    if c is None or not c.is_connected():
        try:
            await c.connect()
        except Exception as e:
            await m.answer(f"❌ Соединение разорвано: {e}\n\nВведите номер телефона заново.")
            await _disconnect_client(uid)
            await state.set_state(States.waiting_for_phone)
            return

    try:
        await c.sign_in(password=password)
        # ✅ 2FA пройдена успешно
        if add_mode == "star_source":
            await _finalize_star_source_account(m, state, d['phone'], uid)
        else:
            await ask_premium_status(m, state, d['phone'])
    except Exception as e:
        err  = str(e).lower()
        ename = type(e).__name__.lower()
        is_wrong = any(k in err or k in ename
                       for k in ("password", "hash_invalid", "invalid", "wrong", "incorrect", "2fa"))
        if is_wrong:
            await m.answer(
                "❌ *Неверный пароль 2FA.*\n\n"
                "Попробуйте ещё раз.\n"
                "_Если забыли — сбросьте в Настройки Telegram → Конфиденциальность → Двухфакторная аутентификация._",
                parse_mode="Markdown")
        else:
            logging.error(f"[h_2fa] uid={uid} err={type(e).__name__}: {e}")
            await m.answer(
                f"❌ Ошибка 2FA ({type(e).__name__}): {e}\n\nПопробуйте ввести пароль ещё раз:")
        # Всегда остаёмся в waiting_for_password
        await state.set_state(States.waiting_for_password)

async def ask_premium_status(m: Message, state: FSMContext, phone: str):
    await state.update_data(phone=phone)
    kb = InlineKeyboardBuilder()
    kb.button(text="Да ⭐", callback_data="tgp_yes")
    kb.button(text="Нет", callback_data="tgp_no")
    await m.answer("Premium?", reply_markup=kb.adjust(2).as_markup())
    await state.set_state(States.waiting_for_tgp)


@dp.callback_query(States.waiting_for_tgp, F.data.in_(["tgp_yes", "tgp_no"]))
async def process_tgp(call: types.CallbackQuery, state: FSMContext):
    d = await state.get_data()
    phone = d['phone']
    uid = call.from_user.id
    add_mode = d.get("add_mode", "rent")
    is_premium = 1 if call.data == "tgp_yes" else 0
    if add_mode == "sale":
        session_dir = d.get("session_dir", "sale_sessions")
        session_name = f"{phone}.session"
        tdata_rel = f"tdata/{phone.replace('+','')}/"
        cur.execute(
            'INSERT OR REPLACE INTO tg_accounts '
            '(phone, lot_name, session_name, tdata_folder, password_2fa, price, is_sold, sold_to, sold_at) '
            'VALUES (?, COALESCE((SELECT lot_name FROM tg_accounts WHERE phone=?), ?), ?, ?, "", '
            'COALESCE((SELECT price FROM tg_accounts WHERE phone=?), 0.0), 0, NULL, ?)',
            (phone, phone, phone, session_name, tdata_rel, phone, int(time.time()))
        )
    elif add_mode == "star_source":
        cur.execute(
            "INSERT OR REPLACE INTO tg_star_accounts (phone, is_active, added_at) VALUES (?, 1, ?)",
            (phone, int(time.time())),
        )
    else:
        cur.execute('INSERT OR REPLACE INTO accounts (phone, is_running, is_premium) VALUES (?, 0, ?)',
                    (phone, is_premium))
    db.commit()

    # Отключаем клиент из памяти, сессия уже сохранена на диск
    await _disconnect_client(uid)

    # ── Экспорт tdata ────────────────────────────────────────────
    session_dir = d.get("session_dir", "sessions")
    session_path = os.path.join(session_dir, phone)
    tdata_root = "tdatafull" if add_mode == "sale" else "tdata"
    tdata_dir = await _export_tdata(session_path, phone, tdata_root=tdata_root)
    if tdata_dir:
        tdata_note = f"\n📁 tdata сохранена: `{tdata_root}/{phone.replace('+','')}/`"
    else:
        tdata_note = "\n⚠️ tdata не удалось создать (сессия ещё не сохранена на диск)"

    came_from_panel = d.get('from_panel', False)
    kb = InlineKeyboardBuilder()
    if came_from_panel:
        kb.button(text="⬅️ Вернуться в Админ панель", callback_data="adm_panel")
    else:
        kb.button(text="⬅️ В главное меню", callback_data="to_main")

    success_text = f"✅ Аккаунт `{phone}` добавлен."
    if add_mode == "sale":
        success_text += "\n\nИзмените лот командой:\n`/lots +79991234567, Название, 25.5`"
    elif add_mode == "star_source":
        success_text = f"✅ Аккаунт `{phone}` добавлен в выдачу Tg Stars."

    await call.message.edit_text(
        f"{success_text}{tdata_note}",
        reply_markup=kb.as_markup(),
        parse_mode="Markdown"
    )
    await state.clear()


# --- ВОЗВРАТ СРЕДСТВ ПРИ БЛОКИРОВКЕ АККАУНТА ---
async def refund_remaining_rent(phone: str, reason: str = "заморожен/заблокирован"):
    """Возвращает деньги за оставшееся время аренды пользователю."""
    res = db_fetchone(
        'SELECT owner_id, expires, price_per_min FROM accounts WHERE phone=? AND owner_id IS NOT NULL AND expires > ?',
        (phone, int(time.time())))
    if not res:
        return
    owner_id, expires, price_per_min = res
    now = int(time.time())
    remaining_seconds = max(0, expires - now)
    if remaining_seconds <= 0:
        return
    remaining_minutes = remaining_seconds / 60
    refund_amount = round(remaining_minutes * price_per_min, 2)
    if refund_amount <= 0:
        return
    # Возвращаем деньги пользователю
    cur.execute('UPDATE users SET balance = balance + ? WHERE user_id=?', (refund_amount, owner_id))
    # Освобождаем номер
    cur.execute('UPDATE accounts SET owner_id=NULL, expires=0, is_running=0, notified_10m=0 WHERE phone=?', (phone,))
    db.commit()
    # Уведомляем пользователя
    try:
        await bot.send_message(
            owner_id,
            f"⚠️ **Аккаунт `{phone}` был {reason}!**\n\n"
            f"Рассылка остановлена. Оставшееся время пересчитано.\n"
            f"💰 Возврат на баланс: **${refund_amount}**\n\n"
            f"Номер возвращён в каталог.",
            parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Не удалось уведомить {owner_id} о возврате: {e}")


# --- ОСНОВНОЙ ЦИКЛ РАССЫЛКИ ---
async def broadcast_loop(phone):
    client = TelegramClient(f"sessions/{phone}", API_ID, API_HASH)
    try:
        await client.connect()
        while True:
            res = db_fetchone(
                'SELECT is_running, text, interval, chats, expires, photo_id FROM accounts WHERE phone = ?',
                (phone,))

            if not res or not res[0] or int(time.time()) > res[4]:
                break

            interval = max(MIN_INTERVAL, res[2])
            chats = [c.strip() for c in res[3].split(',') if c.strip()]
            photo_bytes = None
            if res[5]:
                try:
                    f = await bot.get_file(res[5])
                    p_io = await bot.download_file(f.file_path)
                    photo_bytes = p_io.getvalue()
                except Exception as e:
                    logging.error(f"Не удалось загрузить фото для рассылки {phone}: {e}")

            for chat in chats:
                check = db_fetchone('SELECT is_running FROM accounts WHERE phone = ?', (phone,))
                if not check or not check[0]:
                    break
                try:
                    chat_ref, topic_id = extract_chat_and_topic(chat)
                    entity = await resolve_chat_entity(client, chat_ref)
                    await send_broadcast_payload(
                        client,
                        entity,
                        res[1],
                        topic_id=topic_id,
                        photo_bytes=photo_bytes,
                    )
                except (UserDeactivatedBanError, UserDeactivatedError, AuthKeyUnregisteredError) as e:
                    logging.warning(f"Аккаунт {phone} заблокирован/заморожен: {e}")
                    try:
                        await notify_account_issue(phone, classify_account_issue(e), e, chat_ref=chat)
                    except Exception:
                        pass
                    await refund_remaining_rent(phone, "заморожен или заблокирован Telegram")
                    return  # Выходим из цикла рассылки
                except (PeerFloodError, FloodWaitError) as e:
                    logging.warning(f"Ограничение рассылки {phone} в чате {chat}: {e}")
                    try:
                        await notify_account_issue(phone, classify_account_issue(e), e, chat_ref=chat)
                    except Exception:
                        pass
                except Exception as e:
                    logging.error(f"Broadcast error {chat}: {e}")
                    issue = classify_account_issue(e)
                    if issue != "техническая ошибка":
                        try:
                            await notify_account_issue(phone, issue, e, chat_ref=chat)
                        except Exception:
                            pass
                await asyncio.sleep(interval)

            await asyncio.sleep(10)
    finally:
        try:
            await client.disconnect()
        except: pass


@dp.callback_query(F.data.startswith(("on_", "off_")))
async def toggle_r(call: types.CallbackQuery, state: FSMContext):
    p = call.data.split("_")[1]
    on = 1 if "on" in call.data else 0
    cur.execute('UPDATE accounts SET is_running = ? WHERE phone = ?', (on, p))
    db.commit()

    if on:
        r = db_fetchone('SELECT text, photo_id, chats FROM accounts WHERE phone = ?', (p,))
        if r:
            chats_list = [c.strip() for c in (r[2] or "").split(",") if c.strip()]
            chats_links = "\n".join([f"• `{c}`" for c in chats_list]) if chats_list else "—"
            msg = (f"🚀 **Запуск рассылки!**\n📱 Номер: `{p}`\n"
                   f"👤 Владелец: `{call.from_user.id}`\n\n"
                   f"📝 Текст:\n{r[0]}\n\n👥 Чаты запуска:\n{chats_links}")
            try:
                await notify_admins(msg, photo_id=r[1] if r[1] else None)
            except:
                pass
        asyncio.create_task(broadcast_loop(p))

    await manage_acc(call, state)


@dp.callback_query(F.data.startswith("set_"))
async def set_param_init(call: types.CallbackQuery, state: FSMContext):
    param, p = call.data.split("_")[1], call.data.split("_")[2]
    await state.update_data(target=p)
    st_map = {"text": States.edit_text, "photo": States.edit_photo, "chats": States.edit_chats,
              "int": States.edit_interval}

    msg = ""
    if param == "chats":
        msg = "👥 **Настройка чатов/тем**\n\nОтправьте список ссылок через запятую.\n\n💡 **Пример (можно сразу в несколько тем):**\n`https://t.me/roblox_basee/16425957, https://t.me/roblox_basee/25539176`"
    elif param == "text":
        msg = "📝 **Настройка текста**\n\nОтправьте новый текст для рассылки:"
    elif param == "photo":
        msg = "🖼 **Настройка фото**\n\nОтправьте новую фотографию:"
    elif param == "int":
        msg = f"⏳ **Настройка интервала**\n\nОтправьте задержку в секундах (минимум {MIN_INTERVAL}, например: `{MIN_INTERVAL}`):"

    await call.message.edit_caption(caption=msg, reply_markup=back_kb(f"manage_{p}").as_markup(), parse_mode="Markdown")
    await state.set_state(st_map[param])


@dp.message(States.edit_text)
async def edit_t(m: Message, state: FSMContext):
    bad_word = contains_bad_words(m.text)
    if bad_word:
        return await m.answer(f"❌ Запрещенное слово: `{bad_word}`.", parse_mode="Markdown")

    d = await state.get_data()
    phone = d['target']
    old = db_fetchone('SELECT text FROM accounts WHERE phone = ?', (phone,))
    old_text = old[0] if old else ""

    cur.execute('UPDATE accounts SET text = ? WHERE phone = ?', (m.text, phone))
    db.commit()
    await m.answer("✅ Текст успешно обновлен!\nДля продолжения настройки откройте меню заново.")

    try:
        await notify_admins(
            f"✏️ **Текст рассылки изменён!**\n📱 Номер: `{phone}`\n"
            f"👤 Владелец: `{m.from_user.id}`\n\n"
            f"~~Старый~~:\n{old_text}\n\n**Новый**:\n{m.text}")
    except:
        pass
    await state.clear()


@dp.message(States.edit_photo)
async def edit_p(m: Message, state: FSMContext):
    d = await state.get_data()
    cur.execute('UPDATE accounts SET photo_id = ? WHERE phone = ?',
                (m.photo[-1].file_id if m.photo else None, d['target']))
    db.commit()
    await m.answer("✅ Фото успешно обновлено!")
    await state.clear()


@dp.message(States.edit_chats)
async def edit_c(m: Message, state: FSMContext):
    d = await state.get_data()
    cur.execute('UPDATE accounts SET chats = ? WHERE phone = ?', (m.text, d['target']))
    db.commit()
    await m.answer("✅ Список чатов/тем успешно обновлен!")
    await state.clear()


@dp.message(States.edit_interval)
async def edit_i(m: Message, state: FSMContext):
    if m.text.isdigit():
        val = int(m.text)
        if val < MIN_INTERVAL:
            return await m.answer(f"⚠️ Минимальный интервал — {MIN_INTERVAL} секунд.")
        d = await state.get_data()
        cur.execute('UPDATE accounts SET interval = ? WHERE phone = ?', (val, d['target']))
        db.commit()
        await m.answer(f"✅ Интервал успешно обновлен: {val} сек.")
        await state.clear()
    else:
        await m.answer("⚠️ Введите целое число.")


# ═══════════════════════════════════════════════════════════════
# КЛОН-БОТЫ: запуск процессов (админ / перезапуск)
# ═══════════════════════════════════════════════════════════════
import subprocess as _subprocess
from tdata_export import export_tdata as _export_tdata

_clone_processes: dict = {}


def launch_clone(api_token: str, owner_id: int, bot_id: str) -> bool:
    if bot_id in _clone_processes:
        if _clone_processes[bot_id].poll() is None:
            return True
    try:
        proc = _subprocess.Popen(
            ['python3', 'clone_bot.py', api_token, str(owner_id),
             str(ADMIN_ID), CRYPTO_PAY_TOKEN, str(API_ID), API_HASH, 'bot_data.db'],
            stdout=_subprocess.DEVNULL,
            stderr=_subprocess.DEVNULL,
            env={**os.environ, 'MAIN_BOT_TOKEN': API_TOKEN},
        )
        _clone_processes[bot_id] = proc
        return True
    except Exception as e:
        logging.error(f"Не удалось запустить клон {bot_id}: {e}")
        return False


def stop_clone(bot_id: str):
    proc = _clone_processes.pop(bot_id, None)
    if proc and proc.poll() is None:
        proc.terminate()


# ── Команда удаления клона администратором ───────────────────
@dp.message(Command("dellclonbot"))
async def adm_dellclonbot(message: Message, command: CommandObject):
    if message.from_user.id != ADMIN_ID: return
    if not command.args:
        return await message.answer(
            "⚠️ Формат: `/dellclonbot @username` или `/dellclonbot bot_id`",
            parse_mode="Markdown")

    arg = command.args.strip().lstrip("@")

    # Ищем по username или bot_id
    res = db_fetchone(
        'SELECT bot_id, api_token, owner_id, bot_username FROM clones WHERE bot_username=? OR bot_id=?',
        (arg, arg))
    if not res:
        return await message.answer(
            f"❌ Клон-бот `@{arg}` не найден в базе.",
            parse_mode="Markdown")

    bot_id, api_token, owner_id, bot_username = res
    uname = f"@{bot_username}" if bot_username else bot_id

    # Останавливаем процесс клона
    stop_clone(bot_id)

    # Удаляем из БД
    cur.execute('DELETE FROM clones WHERE bot_id=?', (bot_id,))
    cur.execute('DELETE FROM clone_withdraw_requests WHERE bot_id=?', (bot_id,))
    db.commit()

    await message.answer(
        f"✅ Клон-бот {uname} (`{bot_id}`) удалён из системы.\n"
        f"👤 Владелец: `{owner_id}`",
        parse_mode="Markdown")

    # Уведомляем владельца клона
    try:
        await bot.send_message(
            owner_id,
            f"⚠️ Ваш клон-бот {uname} был **удалён администратором**.",
            parse_mode="Markdown")
    except:
        pass


# ── Перезапуск клонов при старте ─────────────────────────────────
async def restart_running_clones():
    rows = db_fetchall('SELECT bot_id, api_token, owner_id FROM clones WHERE is_running=1')
    for bot_id, token, owner_id in rows:
        ok = launch_clone(token, owner_id, bot_id)
        if ok:
            logging.info(f"Клон {bot_id} перезапущен.")
        else:
            cur.execute('UPDATE clones SET is_running=0 WHERE bot_id=?', (bot_id,))
    db.commit()


async def check_expirations():
    """Фоновая задача: уведомляет об истечении аренды и освобождает аккаунты."""
    while True:
        now = int(time.time())
        # Уведомление за 10 минут до окончания
        rows = db_fetchall(
            'SELECT phone, owner_id FROM accounts '
            'WHERE owner_id IS NOT NULL AND expires > 0 '
            'AND expires - ? <= 600 AND notified_10m = 0',
            (now,))
        for phone, owner_id in rows:
            try:
                await bot.send_message(
                    owner_id,
                    f"⚠️ **Внимание!** До конца аренды `{phone}` менее 10 минут.",
                    parse_mode="Markdown")
            except Exception:
                pass
            cur.execute('UPDATE accounts SET notified_10m = 1 WHERE phone = ?', (phone,))
        db.commit()

        # Освобождение истёкших аренд
        expired = db_fetchall(
            'SELECT phone, owner_id FROM accounts '
            'WHERE owner_id IS NOT NULL AND expires > 0 AND expires <= ?',
            (now,))
        for phone, owner_id in expired:
            try:
                await bot.send_message(
                    owner_id,
                    f"🛑 Время аренды аккаунта `{phone}` подошло к концу. Сессия остановлена.",
                    parse_mode="Markdown")
            except Exception:
                pass
            cur.execute(
                'UPDATE accounts SET owner_id = NULL, expires = 0, '
                'is_running = 0, notified_10m = 0 WHERE phone = ?',
                (phone,))
        db.commit()
        await asyncio.sleep(60)


async def restore_active_broadcasts():
    """При рестарте — возобновляем рассылки, которые были активны до остановки бота."""
    now = int(time.time())
    rows = db_fetchall(
        'SELECT phone FROM accounts WHERE is_running=1 AND expires > ?', (now,))
    restored = 0
    for (phone,) in rows:
        session_file = f"sessions/{phone}.session"
        if os.path.exists(session_file):
            asyncio.create_task(broadcast_loop(phone))
            restored += 1
            logging.info(f"[restore] Рассылка для {phone} восстановлена.")
        else:
            cur.execute('UPDATE accounts SET is_running=0 WHERE phone=?', (phone,))
            logging.warning(f"[restore] Сессия {phone} не найдена — рассылка сброшена.")
    db.commit()
    if restored:
        logging.info(f"[restore] Восстановлено рассылок: {restored}")


async def process_tg_star_orders():
    while True:
        try:
            rows = db_fetchall(
                "SELECT id, user_id, target_username, stars_count, packs, account_phone "
                "FROM tg_star_orders WHERE status='pending_send' ORDER BY id ASC LIMIT 5",
                (),
            )
            for oid, uid, target, qty, packs, phone in rows:
                # В этой версии Telethon нет стабильного публичного метода прямой отправки Stars пользователю.
                # Поэтому помечаем заказ как manual_needed и уведомляем пользователя.
                cur.execute(
                    "UPDATE tg_star_orders SET status='manual_needed', error_text=?, completed_at=? WHERE id=?",
                    ("Auto gifting method is unavailable in current client API", int(time.time()), oid),
                )
                db.commit()
                append_daily_log(
                    f"TG_STARS_STATUS | order_id={oid} | user_id={uid} | status=manual_needed | target={target} | stars={qty} | account={phone}"
                )
                try:
                    await bot.send_message(
                        uid,
                        f"ℹ️ Заказ Tg Stars #{oid} требует ручной выдачи оператором.\n"
                        f"Получатель: `{target}`\n"
                        f"Звёзды: **{qty}**\n"
                        f"Пакеты: `{packs}`",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"[tgstars] worker error: {e}")
        await asyncio.sleep(10)


async def main():
    os.makedirs('sessions', exist_ok=True)
    os.makedirs('sale_sessions', exist_ok=True)
    os.makedirs('tdata', exist_ok=True)
    await restart_running_clones()
    await restore_active_broadcasts()
    asyncio.create_task(check_expirations())
    asyncio.create_task(process_tg_star_orders())
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
