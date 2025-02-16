import asyncio
import re
from telethon import TelegramClient, events
import aiosqlite

# ----------------------- Настройки Telethon -----------------------
# Замените на свои данные:
api_id =         # например, 123456
api_hash = ""   # например, "abcdef123456..."
session_name = "my_session"  # имя файла сессии

# Имена чатов/каналов (username или ID) для подписки
SALES_CHANNEL = "GiftNotification"           # группа/канал о продажах
FLOOR_CHANNEL = "GiftChangesFloorPrices"       # канал с обновлениями цен подарков

# ----------------------- Функция форматирования даты -----------------------
def format_date(dt):
    """
    Форматирует объект datetime в строку вида: 2025.01.13 - 03:13:19
    """
    return dt.strftime("%Y.%m.%d - %H:%M:%S") if dt else ""

# ----------------------- Инициализация БД -----------------------
DB_FILE = 'gifts.db'
db = None  # Глобальная переменная для подключения к БД


import re

def remove_markdown(text: str) -> str:
    """
    Убираем часть Markdown-разметки:
      - двойные звёздочки **...**
      - двойные подчёркивания __...__
      - а также [текст](ссылка) превращаем в просто текст
    """
    # 1) Заменяем **...** на просто ...
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    # 2) Заменяем __...__ на просто ...
    text = re.sub(r'__(.*?)__', r'\1', text)
    # 3) Заменяем [текст](ссылка) на просто текст
    text = re.sub(r'\[(.*?)\]\(https?:\/\/[^\)]+\)', r'\1', text)
    return text


async def init_db():
    global db
    db = await aiosqlite.connect(DB_FILE)
    await db.execute('''
        CREATE TABLE IF NOT EXISTS gifts (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE,
            total_count INTEGER,
            base_star_cost REAL
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gift_name TEXT,
            date TEXT,
            delta_ton REAL,
            floor_ton REAL,
            floor_usd REAL,
            floor_star REAL,
            floor_rub REAL,
            average_ton REAL,
            average_usd REAL,
            average_star REAL,
            average_rub REAL
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER UNIQUE,
            gift_name TEXT,
            price_ton REAL,
            date TEXT
        )
    ''')
    await db.commit()
    print("База данных и таблицы инициализированы.")

# ----------------------- Функции парсинга -----------------------
def parse_sale_message(message):
    """
    Парсит сообщение о продаже подарка.
    Ожидаемый формат:
    
    Gift Sold 
    
    Vintage Cigar #17369 (https://t.me/nft/VintageCigar-1476)
    
    Price: 5.5 TON
    """
    text = message.text
    if not text:
        return None
    # Разбиваем сообщение на строки и отбрасываем пустые
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or "Gift Sold" not in lines[0]:
        return None
    if len(lines) < 3:
        return None

    # Извлекаем название подарка из второй строки
    gift_line = lines[1]
    m = re.match(r"(.+?)\s*\(", gift_line)
    gift_name = m.group(1).strip() if m else gift_line.strip()

    # Ищем строку с ценой
    price_ton = None
    for line in lines:
        if "Price:" in line:
            m_price = re.search(r"Price:\s*([-+]?\d*[\.,]?\d+)", line)
            if m_price:
                try:
                    price_ton = float(m_price.group(1).replace(",", "."))
                except ValueError:
                    price_ton = None
                break

    if price_ton is None:
        return None

    return {
        "message_id": message.id,
        "gift_name": gift_name,
        "price_ton": price_ton,
        "date": format_date(message.date)
    }

def parse_floor_message(message):
    raw_text = message.text
    if not raw_text:
        return None

    # Сначала убираем часть markdown
    text = remove_markdown(raw_text)

    # Теперь text выглядит примерно так:
    # "Flying Broom +0.01 TON 📈
    #   Floor Tonnel: 0,69 TON ≈ 2,58 USD ≈ 172 ⭐️ ≈ 235 ₽
    #   ...
    #   Average Tonnel: 0,71 TON ≈ 2,64 USD ≈ 176 ⭐️ ≈ 241 ₽"

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None

    # --- 1) Парсим первую строку вида: "Flying Broom +0.01 TON"
    #     (раньше у нас было "Flying Broom (https://...)", теперь ссылки нет)
    first_line = lines[0]
    # Можно адаптировать регулярку под "Название +0.01 TON":
    m = re.match(r'^(.+?)\s+([-+]?\d*[.,]?\d+)\s*TON', first_line)
    if not m:
        return None

    gift_name = m.group(1).strip()
    delta_str = m.group(2).replace(',', '.')
    try:
        delta_ton = float(delta_str)
    except ValueError:
        return None

    # --- 2) Ищем строки Floor Tonnel: ... и Average Tonnel: ...
    floor_ton = floor_usd = floor_star = floor_rub = None
    average_ton = average_usd = average_star = average_rub = None

    # Пример шаблона для "X TON ≈ Y USD ≈ Z ⭐️ ≈ W ₽"
    pattern_tonnel = r'([-+]?\d*[.,]?\d+)\s*TON\s*≈\s*([-+]?\d*[.,]?\d+)\s*USD\s*≈\s*([-+]?\d*[.,]?\d+)\s*\S*\s*≈\s*([-+]?\d*[.,]?\d+)'

    for line in lines[1:]:
        if line.startswith("Floor Tonnel"):
            m_floor = re.search(pattern_tonnel, line)
            if m_floor:
                floor_ton  = float(m_floor.group(1).replace(',', '.').replace(' ', ''))
                floor_usd  = float(m_floor.group(2).replace(',', '.').replace(' ', ''))
                floor_star = float(m_floor.group(3).replace(',', '.').replace(' ', ''))
                floor_rub  = float(m_floor.group(4).replace(',', '.').replace(' ', ''))

        elif line.startswith("Average Tonnel"):
            m_avg = re.search(pattern_tonnel, line)
            if m_avg:
                average_ton  = float(m_avg.group(1).replace(',', '.').replace(' ', ''))
                average_usd  = float(m_avg.group(2).replace(',', '.').replace(' ', ''))
                average_star = float(m_avg.group(3).replace(',', '.').replace(' ', ''))
                average_rub  = float(m_avg.group(4).replace(',', '.').replace(' ', ''))

    return {
        "gift_name": gift_name,
        "delta_ton": delta_ton,
        "floor_ton": floor_ton,
        "floor_usd": floor_usd,
        "floor_star": floor_star,
        "floor_rub": floor_rub,
        "average_ton": average_ton,
        "average_usd": average_usd,
        "average_star": average_star,
        "average_rub": average_rub,
        "date": format_date(message.date),
    }


# ----------------------- Функции работы с БД -----------------------
async def insert_gift(gift_name):
    if gift_name and gift_name.strip():
        try:
            await db.execute("INSERT OR IGNORE INTO gifts (name) VALUES (?)", (gift_name.strip(),))
            await db.commit()
        except Exception as e:
            print(f"Ошибка при вставке подарка '{gift_name}': {e}")

async def insert_price_data(data):
    async with db.execute("SELECT id FROM prices WHERE gift_name = ? AND date = ?", (data["gift_name"], data["date"])) as cursor:
        row = await cursor.fetchone()
        if row is not None:
            print(f"Данные для подарка '{data['gift_name']}' с датой {data['date']} уже существуют. Пропускаем вставку.")
            return

    await db.execute('''
        INSERT INTO prices (
            gift_name, date, delta_ton, floor_ton, floor_usd,
            floor_star, floor_rub, average_ton, average_usd, average_star, average_rub
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data["gift_name"],
        data["date"],
        data["delta_ton"],
        data["floor_ton"],
        data["floor_usd"],
        data["floor_star"],
        data["floor_rub"],
        data["average_ton"],
        data["average_usd"],
        data["average_star"],
        data["average_rub"]
    ))
    await db.commit()

async def insert_sale_data(data):
    async with db.execute("SELECT id FROM sales WHERE message_id = ?", (data["message_id"],)) as cursor:
        row = await cursor.fetchone()
        if row is not None:
            print(f"Запись с message_id {data['message_id']} уже существует. Пропускаем.")
            return

    await db.execute('''
        INSERT INTO sales (message_id, gift_name, price_ton, date)
        VALUES (?, ?, ?, ?)
    ''', (data["message_id"], data["gift_name"], data["price_ton"], data["date"]))
    await db.commit()

# ----------------------- Основная логика с Telethon -----------------------
async def main():
    # Инициализируем базу данных
    await init_db()

    # Создаём клиент Telethon и подключаемся
    client = TelegramClient(session_name, api_id, api_hash)
    await client.start()
    print("Телеграм-клиент запущен. Ожидаем новые сообщения...")

    # Обработчик сообщений о продажах
    @client.on(events.NewMessage(chats=SALES_CHANNEL))
    async def handler_sales(event):
        sale_data = parse_sale_message(event.message)
        if sale_data:
            print(f"Обрабатывается продажа подарка: {sale_data['gift_name']} по цене: {sale_data['price_ton']} TON")
            await insert_sale_data(sale_data)

    # Обработчик сообщений с обновлением цен (Gift Floor Prices)
    @client.on(events.NewMessage(chats=FLOOR_CHANNEL))
    async def handler_floor(event):
        # Смотрим сырое сообщение
        print("New floor message:", event.message.text)

        floor_data = parse_floor_message(event.message)
        if floor_data:
            print(f"Обновление цены: {floor_data}")
            # Допустим, записываем в БД
            await insert_gift(floor_data["gift_name"])
            await insert_price_data(floor_data)
        else:
            print("Сообщение не распознано парсером.")


    # Запускаем клиент до отключения
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
