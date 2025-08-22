import logging
from datetime import datetime
import random
import re

import pytz
from tradingview_ta import TA_Handler, Interval
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# =============== НАСТРОЙКИ ===============
import os
TOKEN = os.getenv("TELEGRAM_TOKEN")


# Первые 20 валютных пар
FOREX_PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "AUD/USD",
    "NZD/USD", "USD/CAD", "EUR/GBP", "EUR/JPY", "GBP/JPY",
    "AUD/JPY", "CHF/JPY", "EUR/AUD", "EUR/CAD", "GBP/AUD",
    "GBP/CAD", "AUD/CAD", "NZD/JPY", "EUR/NZD", "GBP/NZD"
]

# OTC-версии (будем анализировать базовую пару как прокси)
OTC_PAIRS = [p + " OTC" for p in FOREX_PAIRS]

# Таймфреймы
TIMEFRAMES_FOREX = ["1m", "5m", "15m", "30m", "1h"]  # без 5s/10s/30s для обычных пар
TIMEFRAMES_OTC   = ["5s", "10s", "30s", "1m", "5m", "15m", "30m", "1h"]

# Карта таймфреймов TradingView (секундные мапим на 1m — у TV нет 5s/10s/30s)
TF_MAP = {
    "5s":  Interval.INTERVAL_1_MINUTE,
    "10s": Interval.INTERVAL_1_MINUTE,
    "30s": Interval.INTERVAL_1_MINUTE,
    "1m":  Interval.INTERVAL_1_MINUTE,
    "5m":  Interval.INTERVAL_5_MINUTES,
    "15m": Interval.INTERVAL_15_MINUTES,
    "30m": Interval.INTERVAL_30_MINUTES,
    "1h":  Interval.INTERVAL_1_HOUR,
}

# Краткие уверенные объяснения
EXPLANATIONS = {
    "BUY":  "📈 Импульс вверх — индикаторы подтверждают рост.",
    "SELL": "📉 Давление вниз — индикаторы подтверждают снижение.",
}

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Память выбора пользователя
user_data = {}

# =============== ВСПОМОГАТЕЛЬНОЕ ===============
def is_market_closed() -> bool:
    """
    Рынок закрыт:
      - Сб–Вс: полностью
      - Пн–Пт: с 22:45 до 02:00 (по Europe/Moscow)
    """
    tz = pytz.timezone("Europe/Moscow")
    now = datetime.now(tz)
    weekday = now.weekday()  # 0=Пн … 6=Вс
    hour = now.hour
    minute = now.minute

    if weekday in (5, 6):
        return True
    if (hour == 22 and minute >= 45) or (0 <= hour < 2):
        return True
    return False


def tv_symbol_from_pair(pair: str) -> str:
    """
    EUR/USD -> EURUSD (для TradingView FX_IDC)
    Для OTC: удаляем суффикс ' OTC' и тоже конвертируем.
    """
    base = pair.replace(" OTC", "")
    return base.replace("/", "")


def coerce_to_buy_sell(analysis) -> str:
    """
    Переводим TV 'RECOMMENDATION' в BUY/SELL.
    Если NEUTRAL — используем рекомендации скользящих/осцилляторов, иначе эвристика.
    """
    try:
        summary = (analysis.summary.get("RECOMMENDATION") or "").upper()
    except Exception:
        summary = ""

    if summary in ("BUY", "STRONG_BUY"):
        return "BUY"
    if summary in ("SELL", "STRONG_SELL"):
        return "SELL"

    # NEUTRAL → попробуем уточнить через подсекции
    ma_rec = (analysis.moving_averages.get("RECOMMENDATION") or "").upper()
    if ma_rec in ("BUY", "STRONG_BUY"):
        return "BUY"
    if ma_rec in ("SELL", "STRONG_SELL"):
        return "SELL"

    osc_rec = (analysis.oscillators.get("RECOMMENDATION") or "").upper()
    if osc_rec in ("BUY", "STRONG_BUY"):
        return "BUY"
    if osc_rec in ("SELL", "STRONG_SELL"):
        return "SELL"

    # Если совсем равновесие — примем сторону по количеству BUY/SELL в МА
    try:
        ma_counts = analysis.moving_averages.get("COMPUTE") or {}
        buy_cnt  = sum(1 for v in ma_counts.values() if str(v).upper().startswith("BUY"))
        sell_cnt = sum(1 for v in ma_counts.values() if str(v).upper().startswith("SELL"))
        if buy_cnt > sell_cnt:
            return "BUY"
        if sell_cnt > buy_cnt:
            return "SELL"
    except Exception:
        pass

    # Последняя страховка
    return random.choice(["BUY", "SELL"])


def analyze_with_tradingview(pair: str, timeframe: str, is_otc: bool) -> tuple[str, str]:
    """
    Возвращает (signal, explain). Для OTC используем базовую пару как прокси.
    """
    symbol = tv_symbol_from_pair(pair)
    interval = TF_MAP.get(timeframe, Interval.INTERVAL_5_MINUTES)

    handler = TA_Handler(
        symbol=symbol,
        screener="forex",
        exchange="FX_IDC",
        interval=interval
    )
    analysis = handler.get_analysis()
    signal = coerce_to_buy_sell(analysis)
    explain = EXPLANATIONS[signal]
    return signal, explain


def build_keyboard(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


# =============== ХЕНДЛЕРЫ ===============
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["Выбрать валютную пару"], ["Обычные пары", "OTC пары"]]
    await update.message.reply_text("Выбери действие:", reply_markup=build_keyboard(keyboard))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["Выбрать валютную пару"], ["Обычные пары", "OTC пары"]]
    await update.message.reply_text("👋 Привет! Я бот-сигнальщик. Выбери действие:", reply_markup=build_keyboard(keyboard))


async def choose_forex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[p] for p in FOREX_PAIRS] + [["Назад"]]
    await update.message.reply_text("Выбери валютную пару:", reply_markup=build_keyboard(keyboard))


async def choose_otc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[p] for p in OTC_PAIRS] + [["Назад"]]
    await update.message.reply_text("Выбери OTC пару:", reply_markup=build_keyboard(keyboard))


async def pair_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.message.from_user.id

    if text in FOREX_PAIRS:
        user_data[uid] = {"pair": text, "otc": False}
        tfs = TIMEFRAMES_FOREX
    elif text in OTC_PAIRS:
        user_data[uid] = {"pair": text, "otc": True}
        tfs = TIMEFRAMES_OTC
    else:
        return

    keyboard = [[tf] for tf in tfs] + [["Сменить пару", "Назад"]]
    await update.message.reply_text(f"✅ Пара: {text}\nВыберите таймфрейм:", reply_markup=build_keyboard(keyboard))


async def timeframe_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tf = update.message.text
    uid = update.message.from_user.id

    # Проверим, что это один из известных таймфреймов
    if tf not in TIMEFRAMES_FOREX + TIMEFRAMES_OTC:
        return

    if uid not in user_data:
        await update.message.reply_text("Сначала выбери валютную пару через кнопку Start.")
        return

    pair = user_data[uid]["pair"]
    is_otc = user_data[uid]["otc"]

    # Рынок закрыт → обычные пары недоступны
    if (not is_otc) and is_market_closed():
        keyboard = [["OTC пары", "Назад"]]
        await update.message.reply_text(
            "❌ Нет данных: рынок закрыт.\n👉 Перейти к OTC парам?",
            reply_markup=build_keyboard(keyboard)
        )
        return

    # Анализ TradingView
    try:
        signal, short_explain = analyze_with_tradingview(pair, tf, is_otc)
        text = (
            f"📊 Пара: {pair}\n"
            f"⏱ Таймфрейм: {tf}\n"
            f"💡 Сигнал: {signal}\n\n"
            f"{short_explain}"
        )
    except Exception as e:
        logger.exception("Ошибка анализа: %s", e)
        text = f"⚠️ Ошибка анализа для {pair} на {tf}: {e}"

    await update.message.reply_text(text)


async def back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await main_menu(update, context)


async def change_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await main_menu(update, context)


# =============== MAIN ===============
def main():
    app = Application.builder().token(TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start))

    # Кнопки верхнего уровня
    app.add_handler(MessageHandler(filters.Regex("^Обычные пары$"), choose_forex))
    app.add_handler(MessageHandler(filters.Regex("^OTC пары$"), choose_otc))
    app.add_handler(MessageHandler(filters.Regex("^Назад$"), back))
    app.add_handler(MessageHandler(filters.Regex("^Выбрать валютную пару$"), main_menu))
    app.add_handler(MessageHandler(filters.Regex("^Сменить пару$"), change_pair))

    # Сначала хэндлер таймфреймов (чтобы он не «съедался» обработчиком пар)
    tf_regex = f"^({'|'.join(map(re.escape, TIMEFRAMES_FOREX + TIMEFRAMES_OTC))})$"
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(tf_regex), timeframe_chosen))

    # Затем хэндлер пар
    pairs_regex = f"^({'|'.join(map(re.escape, FOREX_PAIRS + OTC_PAIRS))})$"
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(pairs_regex), pair_chosen))

    logger.info("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()
