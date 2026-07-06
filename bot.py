import asyncio
import logging
import os
from decimal import Decimal

import asyncpg
from telegram import Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@host:5432/dbname")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))

TRANSIT_BA = "cll20px0d0264y8uw4tehcs06"
CHECK_INTERVAL = 10 * 60

ALERT_THRESHOLD = 0

current_balance: Decimal | None = None


async def get_balance(pool: asyncpg.Pool) -> Decimal:
    row = await pool.fetchrow(
        """
        SELECT COALESCE(SUM(amount), 0) AS balance
        FROM "Transaction"
        WHERE "bankAccount" = $1
        """,
        TRANSIT_BA,
    )
    return Decimal(str(row["balance"]))


async def check_balance_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    global current_balance
    pool: asyncpg.Pool = context.bot_data["db_pool"]
    try:
        new_balance = await get_balance(pool)
    except Exception as e:
        logger.error(f"Помилка отримання балансу: {e}")
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=f"⚠️ Помилка підключення до БД:\n<code>{e}</code>",
            parse_mode="HTML",
        )
        return
    if current_balance is None:
        current_balance = new_balance
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"✅ Бот запущено. Моніторинг балансу транзиту розпочато.\n"
                f"💰 Поточний баланс: <b>{current_balance:,.2f}</b>"
            ),
            parse_mode="HTML",
        )
        return
    diff = new_balance - current_balance
    if abs(diff) > ALERT_THRESHOLD:
        direction = "📈 збільшився" if diff > 0 else "📉 зменшився"
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=(
                f"🔔 <b>Баланс транзиту змінився!</b>\n\n"
                f"{direction} на <b>{abs(diff):,.2f}</b>\n\n"
                f"Було:  <b>{current_balance:,.2f}</b>\n"
                f"Стало: <b>{new_balance:,.2f}</b>"
            ),
            parse_mode="HTML",
        )
        current_balance = new_balance
    else:
        logger.info(f"Баланс без змін: {new_balance:,.2f}")


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pool: asyncpg.Pool = context.bot_data["db_pool"]
    try:
        balance = await get_balance(pool)
        await update.message.reply_text(
            f"💰 Поточний баланс транзиту:\n<b>{balance:,.2f}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка: {e}")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat
