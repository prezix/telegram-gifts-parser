import sqlite3
import json
import datetime
import re

# Открываем (или создаём) базу данных и создаём таблицы, если их ещё нет
conn = sqlite3.connect('gifts.db')
cursor = conn.cursor()

# Таблица для статичных данных о подарках (здесь храним только имя, можно расширять)
cursor.execute('''
CREATE TABLE IF NOT EXISTS gifts (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE,
    total_count INTEGER,
    base_star_cost REAL
)
''')

# Таблица для записей с ценами
cursor.execute('''
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

# Новая таблица для записей о продажах
cursor.execute('''
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER UNIQUE,
    gift_name TEXT,
    price_ton REAL,
    date TEXT
)
''')
conn.commit()

def get_text(item):
    """
    Универсальная функция для получения текстового значения из элемента,
    который может быть либо строкой, либо словарём с ключом "text".
    """
    if isinstance(item, dict):
        return item.get("text", "").strip()
    elif isinstance(item, str):
        return item.strip()
    return ""

def parse_message(msg):
    """
    Парсит сообщение с данными о подарке.
    
    Ожидается, что:
      - Имя подарка находится в первом элементе text.
      - Delta берётся из text[2] (первый токен).
      - Первая группа чисел в секции Floor (до маркера "Average") — это данные Tonnel.
      - Секция Average определяется поиском элемента, содержащего "Average".
    """
    if not isinstance(msg, dict) or msg.get("type") != "message":
        return None

    text = msg.get("text")
    if not text:
        return None
    if not isinstance(text, list):
        text = [text]

    # Минимальное число элементов для корректного парсинга (это можно настроить)
    if len(text) < 10:
        return None

    # Извлекаем имя подарка
    gift_name = get_text(text[0])
    if not gift_name:
        return None

    # Извлекаем delta из text[2] (берём первый токен)
    delta_raw = get_text(text[2]).split()[0]
    try:
        delta_value = float(delta_raw.replace(",", "."))
    except ValueError:
        return None

    # Ищем первую секцию "Average" в сообщении
    avg_marker = None
    for i, item in enumerate(text):
        if "Average" in get_text(item):
            avg_marker = i
            break
    if avg_marker is None:
        return None

    # Среди элементов от начала до маркера Average ищем 4 подряд идущих чисел для секции Floor
    floor_vals = []
    for i in range(0, avg_marker):
        val = get_text(text[i])
        match = re.search(r"[-+]?\d*[\.,]?\d+", val)
        if match:
            try:
                num = float(match.group(0).replace(",", "."))
                floor_vals.append(num)
            except ValueError:
                continue

    if len(floor_vals) < 4:
        return None

    # Предположим, что первые 4 найденных числа относятся к секции Floor (Tonnel, USD, Star, Rub)
    floor_ton, floor_usd, floor_star, floor_rub = floor_vals[:4]

    # Проверяем, что после маркера Average есть достаточное количество элементов для извлечения данных
    if len(text) < avg_marker + 10:
        return None
    try:
        average_ton = float(get_text(text[avg_marker + 3]).replace(",", "."))
        average_usd = float(get_text(text[avg_marker + 5]).replace(",", "."))
        average_star = float(get_text(text[avg_marker + 7]).replace(",", "."))
        average_rub = float(get_text(text[avg_marker + 9]).replace(",", "."))
    except ValueError:
        return None

    return {
        "gift_name": gift_name,
        "date": msg.get("date"),
        "delta_ton": delta_value,
        "floor_ton": floor_ton,
        "floor_usd": floor_usd,
        "floor_star": floor_star,
        "floor_rub": floor_rub,
        "average_ton": average_ton,
        "average_usd": average_usd,
        "average_star": average_star,
        "average_rub": average_rub
    }

def insert_gift(gift_name):
    """
    Вставляет имя подарка в таблицу gifts, если его там ещё нет.
    """
    if gift_name and gift_name.strip():
        try:
            cursor.execute("INSERT OR IGNORE INTO gifts (name) VALUES (?)", (gift_name.strip(),))
            conn.commit()
        except Exception as e:
            print("Ошибка при вставке подарка '{}': {}".format(gift_name, e))

def insert_price_data(data):
    """
    Вставляет данные в таблицу prices.
    Перед вставкой проверяем, есть ли уже запись с таким же gift_name и date.
    Если да — пропускаем вставку.
    """
    cursor.execute("SELECT id FROM prices WHERE gift_name = ? AND date = ?", (data["gift_name"], data["date"]))
    if cursor.fetchone() is not None:
        print("Данные для подарка '{}' с датой {} уже существуют. Пропускаем вставку.".format(data["gift_name"], data["date"]))
        return

    cursor.execute('''
    INSERT INTO prices (gift_name, date, delta_ton, floor_ton, floor_usd, floor_star, floor_rub, average_ton, average_usd, average_star, average_rub)
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
    conn.commit()

def parse_sale_message(msg):
    """
    Парсит сообщение о продаже подарка.
    
    Ожидаемый формат сообщения (пример):
      [
         "Gift Sold\n\n",
         {
           "type": "text_link",
           "text": "Perfume Bottle #1476",
           "href": "https://t.me/nft/PerfumeBottle-1476"
         },
         "\n\nPrice: 12.5 TON"
      ]
      
    Если сообщение удовлетворяет условиям (содержит "Gift Sold" и строку с "Price:"), 
    возвращает словарь с данными:
       - message_id
       - gift_name
       - price_ton
       - date
    В противном случае возвращает None.
    """
    if not isinstance(msg, dict) or msg.get("type") != "message":
        return None

    text = msg.get("text")
    if not text:
        return None
    if not isinstance(text, list):
        text = [text]

    # Проверяем, что сообщение содержит отметку о продаже
    if len(text) < 2:
        return None
    first_line = get_text(text[0])
    if "Gift Sold" not in first_line:
        return None

    # Извлекаем название подарка (предполагаем, что оно во втором элементе)
    gift_name = get_text(text[1])
    if not gift_name:
        return None

    # Ищем цену продажи в TON (возможно, в любом элементе массива)
    price_ton = None
    pattern = r"Price:\s*([-+]?\d*[\.,]?\d+)"
    for item in text:
        t = get_text(item)
        match = re.search(pattern, t)
        if match:
            try:
                price_ton = float(match.group(1).replace(",", "."))
            except ValueError:
                price_ton = None
            break

    if price_ton is None:
        return None

    return {
        "message_id": msg.get("id"),
        "gift_name": gift_name,
        "price_ton": price_ton,
        "date": msg.get("date")
    }

def insert_sale_data(data):
    """
    Вставляет данные о продаже в таблицу sales.
    Если запись с таким message_id уже существует, вставка не производится.
    """
    cursor.execute("SELECT id FROM sales WHERE message_id = ?", (data["message_id"],))
    if cursor.fetchone() is not None:
        print(f"Запись с message_id {data['message_id']} уже существует. Пропускаем.")
        return

    cursor.execute('''
    INSERT INTO sales (message_id, gift_name, price_ton, date)
    VALUES (?, ?, ?, ?)
    ''', (data["message_id"], data["gift_name"], data["price_ton"], data["date"]))
    conn.commit()

# Обработка сообщений с данными о подарках (из файла result.json)
try:
    with open('result.json', 'r', encoding='utf-8') as f:
        loaded = json.load(f)
except Exception as e:
    print("Ошибка загрузки result.json:", e)
    loaded = {}

if isinstance(loaded, dict) and "messages" in loaded:
    gift_messages = loaded["messages"]
elif isinstance(loaded, list):
    gift_messages = loaded
else:
    gift_messages = []

print("Найдено сообщений о подарках:", len(gift_messages))

for msg in gift_messages:
    parsed = parse_message(msg)
    if parsed is None:
        continue  # Пропускаем сообщения, не соответствующие ожидаемому формату
    # Вставляем или обновляем информацию о подарке
    insert_gift(parsed["gift_name"])
    print("Обрабатывается подарок:", parsed["gift_name"])
    # Добавляем запись с ценами, если такой ещё нет
    insert_price_data(parsed)

print("Парсинг сообщений о подарках завершён.\n")

# Обработка сообщений с данными о продажах (из файла sales.json)
try:
    with open('sales.json', 'r', encoding='utf-8') as f:
        loaded_sales = json.load(f)
except Exception as e:
    print("Ошибка загрузки sales.json:", e)
    loaded_sales = {}

if isinstance(loaded_sales, dict) and "messages" in loaded_sales:
    sale_messages = loaded_sales["messages"]
elif isinstance(loaded_sales, list):
    sale_messages = loaded_sales
else:
    sale_messages = []

print("Найдено сообщений о продажах:", len(sale_messages))

for msg in sale_messages:
    sale_data = parse_sale_message(msg)
    if sale_data is None:
        continue  # Пропускаем сообщения, не соответствующие формату продаж
    print("Обрабатывается продажа подарка:", sale_data["gift_name"], "по цене:", sale_data["price_ton"], "TON")
    insert_sale_data(sale_data)

print("Парсинг сообщений о продажах завершён.")

# Закрываем соединение с БД
conn.close()
