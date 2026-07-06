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

# ─── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@host:5432/dbname")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))  # куди надсилати алерти

TRANSIT_BA = "cll20px0d0264y8uw4tehcs06"
CHECK_INTERVAL = 10 * 60  # 10 хвилин у секундах

# Порогове відхилення — алерт якщо баланс змінився більш ніж на N
# 0 = алерт при будь-якій зміні (навіть 1 рупія)
ALERT_THRESHOLD = 0
# ──────────────────────────────────────────────────────────────────────────────

# Поточний відомий баланс (зберігається в пам'яті)
current_balance: Decimal | None = None


async def get_balance(pool: asyncpg.Pool) -> Decimal:
    """Отримує поточний баланс транзитного рахунку."""
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
    """Задача яка виконується кожні 10 хвилин."""
    global current_balance

    pool: asyncpg.Pool = context.bot_data.get("db_pool")
    if pool is None:
        logger.error("Пул БД недоступний, пропускаємо перевірку")
        return

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
        # Перший запуск — просто запам'ятовуємо
        current_balance = new_balance
        logger.info(f"Початковий баланс: {current_balance:,.2f}")
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
        logger.info(f"Алерт: {current_balance} → {new_balance} (diff: {diff})")
        current_balance = new_balance
    else:
        logger.info(f"Баланс без змін: {new_balance:,.2f}")


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/balance — показати поточний баланс прямо зараз."""
    pool: asyncpg.Pool = context.bot_data.get("db_pool")
    if pool is None:
        await update.message.reply_text("❌ БД недоступна. Перевір підключення.")
        return
    try:
        balance = await get_balance(pool)
        await update.message.reply_text(
            f"💰 Поточний баланс транзиту:\n<b>{balance:,.2f}</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка: {e}")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — привітання."""
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        f"👋 Привіт! Я моніторю баланс транзиту.\n\n"
        f"Твій Chat ID: <code>{chat_id}</code>\n\n"
        f"Команди:\n"
        f"/balance — показати поточний баланс\n"
        f"/status — статус моніторингу",
        parse_mode="HTML",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status — показати статус бота."""
    global current_balance
    balance_text = f"{current_balance:,.2f}" if current_balance is not None else "ще не перевірявся"
    await update.message.reply_text(
        f"🟢 Бот активний\n"
        f"⏱ Інтервал перевірки: 10 хв\n"
        f"💰 Останній відомий баланс: <b>{balance_text}</b>",
        parse_mode="HTML",
    )


async def post_init(application: Application) -> None:
    """Підключення до БД після ініціалізації."""
    try:
        pool = await asyncpg.create_pool(
            DATABASE_URL, min_size=1, max_size=3,
            command_timeout=30, timeout=30,
        )
        application.bot_data["db_pool"] = pool
        logger.info("Підключено до БД")
    except Exception as e:
        logger.error(f"Не вдалося підключитися до БД: {e}")
        application.bot_data["db_pool"] = None


async def post_shutdown(application: Application) -> None:
    """Закриття пулу підключень."""
    pool: asyncpg.Pool = application.bot_data.get("db_pool")
    if pool:
        await pool.close()
        logger.info("З'єднання з БД закрито")


def main() -> None:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("status", cmd_status))

    # Запускаємо перевірку кожні 10 хвилин
    app.job_queue.run_repeating(
        check_balance_job,
        interval=CHECK_INTERVAL,
        first=5,  # перша перевірка через 5 секунд після старту
    )

    logger.info("Бот запущено")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
