import nest_asyncio
nest_asyncio.apply()
import matplotlib.dates as mdates

import asyncio
import aiosqlite
import logging
import io
from datetime import datetime, timedelta
import matplotlib
matplotlib.use('Agg')  # Неинтерактивный backend

import numpy as np
import matplotlib.pyplot as plt
from sklearn.linear_model import LinearRegression, RANSACRegressor
from telegram import InputMediaPhoto
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# Для экспоненциального сглаживания (Holt)
from statsmodels.tsa.holtwinters import ExponentialSmoothing

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

gift_db = None
user_db = None

# Пример списка подарков (названия должны соответствовать записям в таблице gifts)
GIFT_LIST = [
    "Precious Peach", "Spiced Wine", "Perfume Bottle", "Magic Potion",
    "Evil Eye", "Sharp Tongue", "Scared Cat", "Trapped Heart",
    "Skull Flower", "Homemade Cake", "Santa Hat", "Kissed Frog",
    "Spy Agaric", "Vintage Cigar", "Signet Ring", "Plush Pepe",
    "Eternal Rose", "Durov's Cap", "Berry Box", "Hex Pot",
    "Jelly Bunny", "Lunar Snake", "Party Sparkler", "Witch Hat",
    "Jester Hat", "Desk Calendar", "Snow Mittens", "Cookie Heart",
    "Jingle Bells", "Hanging Star", "Love Candle", "Mad Pumpkin",
    "Voodoo Doll", "B-Day Candle", "Bunny Muffin", "Hypno Lollipop",
    "Crystal Ball", "Eternal Candle", "Flying Broom", "Lol Pop",
    "Ginger Cookie", "Star Notepad", "Love Potion", "Toy Bear",
    "Diamond Ring","Loot Bag"
]

# Словарь для rate limiting (user_id: last_command_time)
user_last_command = {}
COMMAND_COOLDOWN = 2

def rate_limit(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        now = datetime.now()
        last_time = user_last_command.get(user_id)
        if last_time and (now - last_time).total_seconds() < COMMAND_COOLDOWN:
            await update.message.reply_text("Пожалуйста, не спамьте команды.")
            return
        user_last_command[user_id] = now
        return await func(update, context)
    return wrapper

def parse_date(date_str: str) -> datetime:
    try:
        return datetime.strptime(date_str, "%Y.%m.%d - %H:%M:%S")
    except Exception as e:
        logger.error(f"Ошибка при обработке даты {date_str}: {e}")
        raise e

# Инициализация базы данных подарков
async def init_gift_db():
    global gift_db
    gift_db = await aiosqlite.connect('gifts.db')
    await gift_db.execute("PRAGMA foreign_keys = ON;")
    await gift_db.commit()

# Инициализация базы данных пользователей
async def init_user_db():
    global user_db
    user_db = await aiosqlite.connect('users.db')
    await user_db.execute("PRAGMA foreign_keys = ON;")
    await user_db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            chat_id INTEGER,
            join_date TEXT,
            command_count INTEGER DEFAULT 0
        );
    """)
    await user_db.commit()

async def register_user(update: Update):
    user = update.effective_user
    chat_id = update.effective_chat.id
    join_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with user_db.execute("SELECT user_id FROM users WHERE user_id = ?", (user.id,)) as cursor:
        exists = await cursor.fetchone()
    if not exists:
        await user_db.execute(
            "INSERT INTO users (user_id, username, chat_id, join_date, command_count) VALUES (?, ?, ?, ?, ?)",
            (user.id, user.username, chat_id, join_date, 1)
        )
    else:
        await user_db.execute(
            "UPDATE users SET command_count = command_count + 1 WHERE user_id = ?",
            (user.id,)
        )
    await user_db.commit()

@rate_limit
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    await update.message.reply_text(
        "Привет! Я бот для анализа подарков (цены в TON).\n\n"
        "Доступные команды:\n"
        "/gifts – выбор подарка с инлайн-кнопками\n"
        "/gift <название> – информация о подарке\n"
        "/forecast <название> – прогноз цены (TON)\n"
        "/detailed <название> – подробный анализ подарка\n"
        "/myprofile – информация о пользователе\n"
        "/help – помощь"
    )

@rate_limit
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    await update.message.reply_text(
        "Доступные команды:\n"
        "/start – Запуск бота\n"
        "/gift <название> – Информация о подарке\n"
        "/forecast <название> – Прогноз цены (TON)\n"
        "/detailed <название> – Подробный анализ подарка\n"
        "/gifts – Выбор подарка с инлайн-кнопками\n"
        "/myprofile – Информация о пользователе\n"
        "/help – Помощь"
    )

@rate_limit
async def myprofile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    user = update.effective_user
    async with user_db.execute("SELECT username, join_date, command_count FROM users WHERE user_id = ?", (user.id,)) as cursor:
        record = await cursor.fetchone()
    if record:
        username, join_date, command_count = record
        text = (f"👤 <b>Мой профиль</b>\n"
                f"Username: {username}\n"
                f"Дата регистрации: {join_date}\n"
                f"Всего команд использовано: {command_count}")
    else:
        text = "Информация о пользователе не найдена."
    await update.message.reply_html(text)

@rate_limit
async def gift_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Укажите название подарка. Пример: /gift Perfume Bottle")
        return
    gift_name = " ".join(context.args)
    await register_user(update)

    # Получаем базовую инфу о подарке
    async with gift_db.execute("SELECT id, name, total_count FROM gifts WHERE name = ?", (gift_name,)) as cursor:
        gift = await cursor.fetchone()
    if not gift:
        await update.message.reply_text(f"Подарок '{gift_name}' не найден.")
        return
    gift_id, name, total_count = gift

    text = (f"📦 <b>Информация о подарке:</b>\n"
            f"ID: {gift_id}\n"
            f"Название: {name}\n"
            f"Общее количество: {total_count}\n")

    # Пример анализа delta_ton
    async with gift_db.execute("SELECT delta_ton FROM prices WHERE gift_name = ? ORDER BY date ASC", (gift_name,)) as cursor:
        rows = await cursor.fetchall()
    if rows:
        delta_values = [r[0] for r in rows if r[0] is not None]
        if delta_values:
            avg_delta = sum(delta_values)/len(delta_values)
            trend = "растут" if avg_delta > 0 else "падают" if avg_delta < 0 else "стабильны"
            text += (f"\n📊 <b>Анализ цен (TON):</b>\n"
                     f"Среднее изменение (delta_ton): {avg_delta:.4f}\n"
                     f"Тренд: цены {trend}.")
        else:
            text += "\nНет валидных данных delta_ton."
    else:
        text += "\nИнформация о ценах отсутствует."

    await update.message.reply_html(text)

@rate_limit
async def forecast_prices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Подсказка пользователю. Реальный прогноз идёт через inline-кнопки /gifts.
    """
    await register_user(update)
    if not context.args:
        await update.message.reply_text("Укажите название подарка. Пример: /forecast Perfume Bottle")
        return
    gift_name = " ".join(context.args)
    await update.message.reply_text(
        "Используйте /gifts для выбора подарка с инлайн-кнопками.\n"
        "Или выберите подарок из списка ниже."
    )

@rate_limit
async def detailed_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Аналогичная подсказка, так как детальный анализ вызывается через inline-кнопки.
    """
    await register_user(update)
    if not context.args:
        await update.message.reply_text("Укажите название подарка. Пример: /detailed Perfume Bottle")
        return
    gift_name = " ".join(context.args)
    await update.message.reply_text(
        "Используйте /gifts для выбора подарка с инлайн-кнопками.\n"
        "Или выберите подарок из списка ниже."
    )

async def get_gift_info_text(gift_name: str) -> str:
    async with gift_db.execute("SELECT id, name, total_count FROM gifts WHERE name = ?", (gift_name,)) as cursor:
        gift = await cursor.fetchone()
    if not gift:
        return f"Подарок '{gift_name}' не найден."
    gift_id, name, total_count = gift

    text = (f"📦 <b>Информация о подарке:</b>\n"
            f"ID: {gift_id}\n"
            f"Название: {name}\n"
            f"Общее количество: {total_count}\n")

    # Анализ delta_ton
    async with gift_db.execute("SELECT delta_ton FROM prices WHERE gift_name = ? ORDER BY date ASC", (gift_name,)) as cursor:
        rows = await cursor.fetchall()
    if rows:
        deltas = [r[0] for r in rows if r[0] is not None]
        if deltas:
            avg_d = sum(deltas)/len(deltas)
            trend = "растут" if avg_d > 0 else "падают" if avg_d < 0 else "стабильны"
            text += (f"\n📊 <b>Анализ (TON):</b>\n"
                     f"Среднее изменение (delta_ton): {avg_d:.4f}\n"
                     f"Тренд: {trend}")
        else:
            text += "\nНет валидных данных delta_ton."
    else:
        text += "\nИнформация о ценах отсутствует."

    return text

def build_sub_buttons(gift_name: str) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("Прогноз (TON)", callback_data=f"forecast:{gift_name}"),
            InlineKeyboardButton("Детальный анализ", callback_data=f"detailed:{gift_name}")
        ],
        [
            InlineKeyboardButton("Вернуться", callback_data="list")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def display_gift_info(gift_name: str, query) -> None:
    text = await get_gift_info_text(gift_name)
    markup = build_sub_buttons(gift_name)
    if query.message.text:
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=markup)
    else:
        await query.edit_message_caption(caption=text, parse_mode='HTML', reply_markup=markup)

async def forecast_inline_otc(gift_name: str, query) -> None:
    """
    Прогноз цены (TON) для OTC-рынка:
      - Использует данные из таблиц prices (поле floor_ton) и sales (поле price_ton),
      - Строит три модели: RANSAC, обычная линейная регрессия и Holt (экспоненциальное сглаживание).
      - Итоговый прогноз = среднее значений всех моделей.
    """
    combined_data = []

    # 1) Извлекаем данные из таблицы prices (floor_ton)
    async with gift_db.execute("""
        SELECT date, floor_ton
        FROM prices
        WHERE gift_name = ? AND floor_ton IS NOT NULL
        ORDER BY date ASC
    """, (gift_name,)) as cursor:
        price_rows = await cursor.fetchall()
    for date_str, price_ton in price_rows:
        try:
            d = parse_date(date_str)
            combined_data.append((d, price_ton))
        except Exception:
            continue

    # 2) Извлекаем данные из таблицы sales (price_ton)
    async with gift_db.execute("""
        SELECT date, price_ton
        FROM sales
        WHERE gift_name LIKE ?
        ORDER BY date ASC
    """, (f"{gift_name}%",)) as cursor:
        sales_rows = await cursor.fetchall()
    for date_str, price_ton in sales_rows:
        try:
            d = parse_date(date_str)
            combined_data.append((d, price_ton))
        except Exception:
            continue

    if not combined_data or len(combined_data) < 2:
        await query.edit_message_text("Недостаточно данных (TON) для анализа данного подарка.")
        return

    # 3) Сортируем объединённый ряд по дате
    combined_data.sort(key=lambda x: x[0])
    dates = [item[0] for item in combined_data]
    prices = [item[1] for item in combined_data]

    # Преобразуем даты в числовой формат
    X = np.array([d.toordinal() for d in dates]).reshape(-1, 1)
    y = np.array(prices)

    # Весовая функция для свежести данных (больше веса – последним данным)
    alpha = 0.1
    last_date_ord = dates[-1].toordinal()
    weights = np.exp(-alpha * (last_date_ord - X.flatten()))

    # Модель 1: RANSAC (устойчивая регрессия)
    ransac = RANSACRegressor(estimator=LinearRegression(), max_trials=100, min_samples=0.6)
    ransac.fit(X, y, sample_weight=weights)

    future_date = dates[-1] + timedelta(days=1)
    future_day_ord = np.array([[future_date.toordinal()]])
    ransac_forecast = ransac.predict(future_day_ord)[0]
    ransac_forecast = max(ransac_forecast, 0)  # цена не может быть отрицательной

    # Модель 2: обычная линейная регрессия
    lin_model = LinearRegression()
    lin_model.fit(X, y, sample_weight=weights)
    lin_future = lin_model.predict(future_day_ord)[0]
    lin_future = max(lin_future, 0)

    # Модель 3: Holt (экспоненциальное сглаживание)
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        holt_model = ExponentialSmoothing(y, trend="add", damped_trend=True, seasonal=None)
        holt_fit = holt_model.fit(optimized=True)
        holt_forecast = holt_fit.forecast(1)[0]
        holt_forecast = max(holt_forecast, 0)
    except Exception as e:
        logger.error(f"Holt model error: {e}")
        holt_forecast = lin_future

    # Итоговый прогноз (среднее значение)
    final_forecast = (ransac_forecast + lin_future + holt_forecast) / 3.0

    # --- Построение графика ---
    import matplotlib.dates as mdates
    plt.figure(figsize=(12, 6))
    
    # Фактические цены с прозрачностью
    plt.scatter(dates, y, color='blue', alpha=0.8, s=60, label="Фактические цены (TON)")
    
    # Линейная регрессия
    plt.plot(dates, lin_model.predict(X), 'g--', linewidth=1.5, label="Лин. регрессия")
    
    # RANSAC регрессия
    plt.plot(dates, ransac.predict(X), 'r--', linewidth=1.5, label="RANSAC регрессия")
    
    # Holt сглаживание (если доступно)
    try:
        plt.plot(dates, holt_fit.fittedvalues, 'm--', linewidth=1.5, label="Holt сглаживание")
    except:
        pass

    # Прогнозные точки
    plt.scatter(future_date, ransac_forecast, color='red', s=100, label=f"RANSAC прогноз ({ransac_forecast:.2f})")
    plt.scatter(future_date, lin_future, color='green', s=100, label=f"Лин. прогноз ({lin_future:.2f})")
    plt.scatter(future_date, holt_forecast, color='magenta', s=100, label=f"Holt прогноз ({holt_forecast:.2f})")
    plt.scatter(future_date, final_forecast, color='black', s=120, label=f"Итоговый прогноз ({final_forecast:.2f})")
    
    # Форматирование оси X как даты
    ax = plt.gca()
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
    plt.xticks(rotation=45)
    
    plt.ylim(bottom=0)
    plt.xlabel("Дата")
    plt.ylabel("Цена (TON)")
    plt.title(f"OTC-прогноз (TON) для подарка: {gift_name}")
    plt.grid(True, linestyle=':')
    plt.legend()
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()

    text = (
        f"🔮 <b>OTC-прогноз (TON) для подарка: {gift_name}</b>\n"
        f"Дата прогноза: {future_date.strftime('%Y-%m-%d')}\n\n"
        f"Использованы данные из таблиц prices (floor_ton) и sales (price_ton).\n"
        f"Модели прогнозирования:\n"
        f"  • RANSAC: {ransac_forecast:.2f} TON\n"
        f"  • Линейная регрессия: {lin_future:.2f} TON\n"
        f"  • Holt сглаживание: {holt_forecast:.2f} TON\n\n"
        f"Итоговый прогноз (среднее): <b>{final_forecast:.2f} TON</b>"
    )

    markup = InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data=f"gift:{gift_name}")]])
    await query.edit_message_media(
        media=InputMediaPhoto(media=buf, caption=text, parse_mode='HTML'),
        reply_markup=markup
    )


# --- ДЕТАЛЬНЫЙ АНАЛИЗ (пример) ---
async def detailed_inline(gift_name: str, query) -> None:
    # Получаем базовую информацию о подарке
    async with gift_db.execute("SELECT id, name, total_count FROM gifts WHERE name = ?", (gift_name,)) as cursor:
        gift = await cursor.fetchone()
    if not gift:
        await query.edit_message_text(f"Подарок '{gift_name}' не найден.")
        return
    gift_id, name, total_count = gift

    combined_data = []

    # 1) Извлекаем данные из таблицы prices (поле floor_ton)
    async with gift_db.execute("""
        SELECT date, floor_ton
        FROM prices
        WHERE gift_name = ? AND floor_ton IS NOT NULL
        ORDER BY date ASC
    """, (gift_name,)) as cursor:
        price_rows = await cursor.fetchall()
    for date_str, floor_ton in price_rows:
        try:
            d = parse_date(date_str)
            combined_data.append((d, floor_ton))
        except Exception:
            continue

    # 2) Извлекаем данные из таблицы sales (поле price_ton)
    async with gift_db.execute("""
        SELECT date, price_ton
        FROM sales
        WHERE gift_name LIKE ?
        ORDER BY date ASC
    """, (f"{gift_name}%",)) as cursor:
        sales_rows = await cursor.fetchall()
    for date_str, price_ton in sales_rows:
        try:
            d = parse_date(date_str)
            combined_data.append((d, price_ton))
        except Exception:
            continue

    if not combined_data or len(combined_data) < 2:
        await query.edit_message_text("Недостаточно данных (TON) для детального анализа.")
        return

    # Сортируем объединённый ряд по дате
    combined_data.sort(key=lambda x: x[0])
    dates = [item[0] for item in combined_data]
    ton_prices = [item[1] for item in combined_data]

    # Вычисляем статистические показатели
    import statistics
    mean_price = statistics.mean(ton_prices)
    min_price = min(ton_prices)
    max_price = max(ton_prices)
    std_price = statistics.stdev(ton_prices) if len(ton_prices) > 1 else 0

    # Строим модель линейной регрессии для прогноза
    X = np.array([d.toordinal() for d in dates]).reshape(-1, 1)
    y = np.array(ton_prices)
    lin_model = LinearRegression()
    lin_model.fit(X, y)
    future_date = dates[-1] + timedelta(days=1)
    forecast_lin = lin_model.predict([[future_date.toordinal()]])[0]

    # Формируем текстовый отчет
    analysis_text = (
        f"📊 <b>Детальный анализ (TON):</b>\n"
        f"Подарок: {name}\n"
        f"Общее количество: {total_count}\n\n"
        f"Статистика по цене (TON):\n"
        f"  • Средняя: {mean_price:.2f}\n"
        f"  • Мин: {min_price:.2f}, Макс: {max_price:.2f}\n"
        f"  • Стандартное отклонение: {std_price:.2f}\n"
        f"Линейный прогноз на {future_date.strftime('%Y-%m-%d')}: {forecast_lin:.2f} TON\n"
    )

    # Построение графика
    import matplotlib.dates as mdates
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(dates, ton_prices, 'bo-', label="Фактические цены (TON)")
    y_lin_pred = lin_model.predict(X)
    ax.plot(dates, y_lin_pred, 'r--', linewidth=1.5, label="Линейная регрессия")
    ax.scatter(future_date, forecast_lin, color='green', s=100, label=f"Прогноз ({forecast_lin:.2f} TON)")

    # Форматируем ось X как даты
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
    plt.xticks(rotation=45)
    ax.set_ylim(bottom=0)

    ax.set_xlabel("Дата")
    ax.set_ylabel("Цена (TON)")
    ax.set_title(f"Детальный анализ (TON) для {name}")
    ax.grid(True, linestyle=':')
    ax.legend()
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()

    markup = InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data=f"gift:{gift_name}")]])
    await query.edit_message_media(
        media=InputMediaPhoto(media=buf, caption=analysis_text, parse_mode='HTML'),
        reply_markup=markup
    )


# --- ОБРАБОТЧИК CALLBACK ---
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data
    await query.answer()
    if data.startswith("gift:"):
        gift_name = data.split(":", 1)[1]
        await display_gift_info(gift_name, query)
    elif data.startswith("forecast:"):
        gift_name = data.split(":", 1)[1]
        await forecast_inline_otc(gift_name, query)
    elif data.startswith("detailed:"):
        gift_name = data.split(":", 1)[1]
        await detailed_inline(gift_name, query)
    elif data == "list":
        await query.message.delete()
        keyboard = []
        row = []
        for gift in GIFT_LIST:
            row.append(InlineKeyboardButton(gift, callback_data=f"gift:{gift}"))
            if len(row) == 3:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=query.message.chat_id, text="Выберите подарок:", reply_markup=markup)
    else:
        await query.edit_message_text("Неизвестная команда.")

@rate_limit
async def list_gifts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await register_user(update)
    keyboard = []
    row = []
    for gift in GIFT_LIST:
        row.append(InlineKeyboardButton(gift, callback_data=f"gift:{gift}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите подарок:", reply_markup=markup)

async def main() -> None:
    await init_gift_db()
    await init_user_db()
    application = ApplicationBuilder().token("BOT-TOKEN").build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("gifts", list_gifts_command))
    application.add_handler(CommandHandler("gift", gift_info))
    application.add_handler(CommandHandler("forecast", forecast_prices))
    application.add_handler(CommandHandler("detailed", detailed_analysis))
    application.add_handler(CommandHandler("myprofile", myprofile))
    application.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Bot started")
    await application.run_polling(close_loop=False)

if __name__ == '__main__':
    asyncio.run(main())
