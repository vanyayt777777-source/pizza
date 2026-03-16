import os
import asyncio
import logging
import random
from datetime import datetime, timedelta
import sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
import aiohttp
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Токен бота из переменных окружения
CRYPTOBOT_TOKEN = "549010:AAppnlCnLcg0vq9FR5CKDE8vpatHDV5FYvT"  # Ваш токен Crypto Bot
ADMIN_ID = 7973988177  # Ваш Telegram ID

# Проверяем наличие токена бота
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения!")

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Состояния FSM
class OrderStates(StatesGroup):
    waiting_for_link = State()

class MailingStates(StatesGroup):
    waiting_for_message = State()

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('vest_pizza.db')
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            subscription_expiry DATE,
            joined_date DATE,
            is_admin INTEGER DEFAULT 0
        )
    ''')
    
    # Таблица заказов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            link TEXT,
            status TEXT,
            created_at DATETIME,
            completed_at DATETIME,
            success_count INTEGER,
            fail_count INTEGER
        )
    ''')
    
    # Таблица транзакций
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            status TEXT,
            payment_id TEXT,
            created_at DATETIME
        )
    ''')
    
    conn.commit()
    conn.close()

# Функции для работы с БД
def get_user(user_id: int):
    conn = sqlite3.connect('vest_pizza.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def create_user(user_id: int, username: str, first_name: str, last_name: str):
    conn = sqlite3.connect('vest_pizza.db')
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO users (user_id, username, first_name, last_name, joined_date)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, username, first_name, last_name, datetime.now().date()))
    conn.commit()
    conn.close()

def has_subscription(user_id: int) -> bool:
    user = get_user(user_id)
    if user and user[4]:  # subscription_expiry
        expiry_date = datetime.strptime(user[4], '%Y-%m-%d').date()
        return expiry_date >= datetime.now().date()
    return False

def add_subscription(user_id: int, days: int = 36500):  # ~100 лет = навсегда
    conn = sqlite3.connect('vest_pizza.db')
    cursor = conn.cursor()
    expiry_date = datetime.now().date() + timedelta(days=days)
    cursor.execute("UPDATE users SET subscription_expiry = ? WHERE user_id = ?", 
                  (expiry_date, user_id))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect('vest_pizza.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()
    return [user[0] for user in users]

def get_statistics():
    conn = sqlite3.connect('vest_pizza.db')
    cursor = conn.cursor()
    
    # Общее количество пользователей
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    
    # Пользователи с подпиской
    cursor.execute("SELECT COUNT(*) FROM users WHERE subscription_expiry >= date('now')")
    subscribed_users = cursor.fetchone()[0]
    
    # Количество заказов
    cursor.execute("SELECT COUNT(*) FROM orders")
    total_orders = cursor.fetchone()[0]
    
    # Заказы за сегодня
    cursor.execute("SELECT COUNT(*) FROM orders WHERE date(created_at) = date('now')")
    today_orders = cursor.fetchone()[0]
    
    conn.close()
    
    return {
        "total_users": total_users,
        "subscribed_users": subscribed_users,
        "total_orders": total_orders,
        "today_orders": today_orders
    }

def save_order(user_id: int, link: str, success: int, fail: int):
    conn = sqlite3.connect('vest_pizza.db')
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO orders (user_id, link, status, created_at, completed_at, success_count, fail_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user_id, link, "completed", datetime.now(), datetime.now(), success, fail))
    conn.commit()
    conn.close()

def save_transaction(user_id: int, amount: float, payment_id: str):
    conn = sqlite3.connect('vest_pizza.db')
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO transactions (user_id, amount, status, payment_id, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, amount, "pending", payment_id, datetime.now()))
    conn.commit()
    conn.close()

def update_transaction_status(payment_id: str, status: str):
    conn = sqlite3.connect('vest_pizza.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE transactions SET status = ? WHERE payment_id = ?", (status, payment_id))
    conn.commit()
    conn.close()

def get_pending_transaction(user_id: int):
    conn = sqlite3.connect('vest_pizza.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM transactions 
        WHERE user_id = ? AND status = 'pending' 
        ORDER BY created_at DESC LIMIT 1
    """, (user_id,))
    transaction = cursor.fetchone()
    conn.close()
    return transaction

# Клавиатуры
def main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="🍕 Заказать пиццу", callback_data="order_pizza"))
    builder.add(InlineKeyboardButton(text="👤 Профиль", callback_data="profile"))
    builder.add(InlineKeyboardButton(text="⭐ Подписка", callback_data="subscription"))
    builder.adjust(1)
    return builder.as_markup()

def subscription_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="💳 Купить подписку навсегда - 2$", callback_data="buy_subscription"))
    builder.add(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return builder.as_markup()

def admin_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"))
    builder.add(InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_mailing"))
    builder.add(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    builder.adjust(1)
    return builder.as_markup()

def back_to_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main"))
    return builder.as_markup()

# Функция для создания счета в Crypto Bot
async def create_crypto_invoice(user_id: int, amount: float = 2):
    url = "https://pay.crypt.bot/api/createInvoice"
    
    # Используем правильный формат для Crypto Bot API
    payload = {
        "asset": "USDT",  # Валюта счета
        "amount": str(amount),  # Сумма как строка
        "description": "Vest Pizza - Подписка навсегда",
        "payload": str(user_id),  # ID пользователя для идентификации
        "expires_in": 3600  # Счет действителен 1 час
    }
    
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN,
        "Content-Type": "application/json"
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            logger.info(f"Creating invoice with payload: {payload}")
            async with session.post(url, json=payload, headers=headers) as response:
                response_text = await response.text()
                logger.info(f"Response status: {response.status}")
                logger.info(f"Response body: {response_text}")
                
                if response.status == 200:
                    data = await response.json()
                    if data.get("ok"):
                        return data["result"]
                    else:
                        logger.error(f"Crypto Bot API error: {data}")
                        return None
                else:
                    logger.error(f"HTTP error {response.status}: {response_text}")
                    return None
        except Exception as e:
            logger.error(f"Exception in create_crypto_invoice: {e}")
            return None

# Функция для проверки статуса счета
async def check_invoice_status(invoice_id: str):
    url = f"https://pay.crypt.bot/api/getInvoices"
    
    params = {
        "invoice_ids": invoice_id
    }
    
    headers = {
        "Crypto-Pay-API-Token": CRYPTOBOT_TOKEN
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params=params, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("ok") and data.get("result") and data["result"].get("items"):
                        return data["result"]["items"][0]
                return None
        except Exception as e:
            logger.error(f"Error checking invoice: {e}")
            return None

# Обработчики команд
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user = message.from_user
    create_user(user.id, user.username, user.first_name, user.last_name)
    
    # Проверяем, является ли пользователь админом
    is_admin = user.id == ADMIN_ID
    
    welcome_text = (
        f"🍕 Добро пожаловать в Vest Pizza!\n\n"
        f"Здесь вы можете заказать самую вкусную пиццу!\n"
        f"Для заказа необходима подписка."
    )
    
    if is_admin:
        welcome_text += "\n\n👑 У вас есть права администратора."
    
    await message.answer(welcome_text, reply_markup=main_menu_keyboard())

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "👨‍💼 Панель администратора",
            reply_markup=admin_keyboard()
        )
    else:
        await message.answer("❌ У вас нет прав администратора.")

# Обработчики callback'ов
@dp.callback_query(F.data == "order_pizza")
async def process_order_pizza(callback: CallbackQuery, state: FSMContext):
    if not has_subscription(callback.from_user.id):
        await callback.message.edit_text(
            "❌ Для заказа пиццы необходимо оформить подписку!\n"
            "Перейдите в раздел ⭐ Подписка",
            reply_markup=main_menu_keyboard()
        )
        await callback.answer()
        return
    
    await callback.message.edit_text(
        "🍕 Отправьте ссылку на Telegram аккаунт в формате:\n"
        "https://t.me/username\n\n"
        "Или нажмите кнопку отмена:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_main")]
        ])
    )
    await state.set_state(OrderStates.waiting_for_link)
    await callback.answer()

@dp.message(OrderStates.waiting_for_link)
async def process_link(message: types.Message, state: FSMContext):
    link = message.text.strip()
    
    # Проверка формата ссылки
    if not link.startswith("https://t.me/"):
        await message.answer(
            "❌ Неверный формат ссылки. Отправьте ссылку вида:\n"
            "https://t.me/username"
        )
        return
    
    await message.answer(
        "✅ Ссылка принята!\n"
        "⏳ Обработка заказа... Это займет 30-60 секунд."
    )
    
    # Имитация обработки
    await asyncio.sleep(random.randint(30, 60))
    
    # Генерация случайных результатов
    success = random.randint(1200, 1800)
    fail = 2
    
    # Сохраняем заказ
    save_order(message.from_user.id, link, success, fail)
    
    await message.answer(
        f"✅ Заказ обработан!\n"
        f"📊 Результат: {success} отправок успешно, {fail} неудачно.\n"
        f"🍕 Пицца будет доставлена в течение суток!\n\n"
        f"Спасибо за заказ в Vest Pizza!",
        reply_markup=back_to_main_keyboard()
    )
    
    await state.clear()

@dp.callback_query(F.data == "profile")
async def process_profile(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    
    if user and user[4]:  # subscription_expiry
        expiry = user[4]
        status = "✅ Активна" if datetime.strptime(expiry, '%Y-%m-%d').date() >= datetime.now().date() else "❌ Истекла"
    else:
        expiry = "Нет"
        status = "❌ Нет подписки"
    
    # Получаем количество заказов
    conn = sqlite3.connect('vest_pizza.db')
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM orders WHERE user_id = ?", (callback.from_user.id,))
    orders_count = cursor.fetchone()[0]
    conn.close()
    
    await callback.message.edit_text(
        f"👤 Профиль пользователя\n\n"
        f"ID: {callback.from_user.id}\n"
        f"Имя: {callback.from_user.first_name}\n"
        f"Username: @{callback.from_user.username}\n\n"
        f"⭐ Подписка: {status}\n"
        f"📅 Действует до: {expiry if expiry != 'Нет' else '—'}\n"
        f"🍕 Заказов: {orders_count}",
        reply_markup=back_to_main_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "subscription")
async def process_subscription(callback: CallbackQuery):
    await callback.message.edit_text(
        "⭐ Vest Pizza Premium\n\n"
        "С подпиской вы можете:\n"
        "✅ Заказывать пиццу без ограничений\n"
        "✅ Приоритетная доставка\n"
        "✅ Специальные предложения\n\n"
        "💰 Стоимость: 2$ навсегда!\n\n"
        "Оплата через Crypto Bot (USDT)",
        reply_markup=subscription_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "buy_subscription")
async def process_buy_subscription(callback: CallbackQuery):
    await callback.message.edit_text(
        "⏳ Создание счета для оплаты...",
        reply_markup=None
    )
    
    # Создаем счет в Crypto Bot
    invoice = await create_crypto_invoice(callback.from_user.id, 2)
    
    if invoice and invoice.get("pay_url"):
        # Сохраняем транзакцию
        save_transaction(callback.from_user.id, 2, invoice["invoice_id"])
        
        # Отправляем пользователю ссылку на оплату
        await callback.message.edit_text(
            f"💳 Оплата подписки\n\n"
            f"Сумма: 2 USDT\n"
            f"Счет создан: {invoice['invoice_id']}\n\n"
            f"Для оплаты нажмите кнопку ниже:\n"
            f"После оплаты нажмите '✅ Я оплатил' для проверки",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Оплатить в Crypto Bot", url=invoice["pay_url"])],
                [InlineKeyboardButton(text="✅ Я оплатил", callback_data="check_payment")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="subscription")]
            ])
        )
    else:
        await callback.message.edit_text(
            "❌ Не удалось создать счет для оплаты.\n"
            "Пожалуйста, попробуйте позже или обратитесь к администратору.",
            reply_markup=back_to_main_keyboard()
        )
    
    await callback.answer()

@dp.callback_query(F.data == "check_payment")
async def process_check_payment(callback: CallbackQuery):
    # Получаем последнюю pending транзакцию
    transaction = get_pending_transaction(callback.from_user.id)
    
    if not transaction:
        await callback.answer("Активных платежей не найдено", show_alert=True)
        return
    
    # Проверяем статус через API
    invoice = await check_invoice_status(transaction[4])  # payment_id
    
    if invoice:
        logger.info(f"Invoice status: {invoice.get('status')}")
        
        if invoice.get("status") == "paid":
            # Активируем подписку
            add_subscription(callback.from_user.id)
            
            # Обновляем статус транзакции
            update_transaction_status(transaction[4], "completed")
            
            await callback.message.edit_text(
                "✅ Оплата прошла успешно!\n"
                "⭐ Подписка активирована навсегда!\n"
                "Теперь вы можете заказывать пиццу.",
                reply_markup=main_menu_keyboard()
            )
        else:
            status_text = {
                "active": "ожидает оплаты",
                "expired": "истек"
            }.get(invoice.get("status"), "неизвестный статус")
            
            await callback.answer(f"Платеж еще не оплачен (статус: {status_text})", show_alert=True)
    else:
        await callback.answer("Не удалось проверить статус платежа", show_alert=True)
    
    await callback.answer()

@dp.callback_query(F.data == "admin_stats")
async def process_admin_stats(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    stats = get_statistics()
    
    await callback.message.edit_text(
        f"📊 Статистика бота\n\n"
        f"👥 Всего пользователей: {stats['total_users']}\n"
        f"⭐ С подпиской: {stats['subscribed_users']}\n"
        f"🍕 Всего заказов: {stats['total_orders']}\n"
        f"📅 Заказов сегодня: {stats['today_orders']}\n",
        reply_markup=admin_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_mailing")
async def process_admin_mailing(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        "📢 Отправьте сообщение для рассылки всем пользователям:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin")]
        ])
    )
    await state.set_state(MailingStates.waiting_for_message)
    await callback.answer()

@dp.message(MailingStates.waiting_for_message)
async def process_mailing_message(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Нет доступа")
        await state.clear()
        return
    
    users = get_all_users()
    success_count = 0
    fail_count = 0
    
    status_msg = await message.answer(f"📢 Начинаю рассылку {len(users)} пользователям...")
    
    for user_id in users:
        try:
            await bot.send_message(
                user_id,
                f"📢 Рассылка от администратора:\n\n{message.text}"
            )
            success_count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            fail_count += 1
            logger.error(f"Failed to send message to {user_id}: {e}")
    
    await status_msg.edit_text(
        f"✅ Рассылка завершена!\n"
        f"📊 Успешно отправлено: {success_count}\n"
        f"❌ Не удалось отправить: {fail_count}"
    )
    await state.clear()

@dp.callback_query(F.data == "admin")
async def process_admin(callback: CallbackQuery):
    if callback.from_user.id == ADMIN_ID:
        await callback.message.edit_text(
            "👨‍💼 Панель администратора",
            reply_markup=admin_keyboard()
        )
    await callback.answer()

@dp.callback_query(F.data == "back_to_main")
async def process_back_to_main(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🍕 Главное меню",
        reply_markup=main_menu_keyboard()
    )
    await callback.answer()

# Запуск бота
async def main():
    # Инициализируем базу данных
    init_db()
    
    # Проверяем подключение к Crypto Bot API
    logger.info("Проверка подключения к Crypto Bot API...")
    test_invoice = await create_crypto_invoice(ADMIN_ID, 0.01)
    if test_invoice:
        logger.info("✅ Подключение к Crypto Bot API успешно")
    else:
        logger.warning("⚠️ Не удалось подключиться к Crypto Bot API. Проверьте токен.")
    
    # Запускаем бота
    logger.info("Бот запущен...")
    logger.info(f"Admin ID: {ADMIN_ID}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
