# main.py
import asyncio
import logging
from threading import Thread
from flask import Flask
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

import sqlite3
import os
from flask import Flask, request
import services.payments as payments_service
import config
import database as db
import services.subscriptions as subscriptions_service

# ---------------------------- Настройка логирования ----------------------------
logging.basicConfig(level=logging.INFO)

# ---------------------------- HTTP-сервер для Render ----------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "ChiefBot is running!"

def run_flask():
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

@app.route('/webhook/tochka', methods=['POST'])
def tochka_webhook():
    raw_body = request.get_data(as_text=True)
    webhook_data = payments_service.process_webhook(raw_body)

    if webhook_data:
        amount = webhook_data.get("amount", 0)
        purpose = webhook_data.get("purpose", "")
        payment_link_id = webhook_data.get("paymentLinkId", "")
        status = webhook_data.get("status", "")

        if status == "success" or status == "confirmed":
            db.update_payment_status(payment_link_id, "paid")
            
            # Находим платёж в БД
            payment = db.get_payment_by_link_id(payment_link_id)
            if payment:
                user_id = payment["user_id"]
                # Активируем подписку
                # Пока просто логируем
                print(f"✅ Платёж получен: {amount} ₽ — {purpose} от {user_id}")

    return "OK", 200
    
# ---------------------------- Инициализация бота и диспетчера ----------------------------
bot = Bot(token=config.BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# ---------------------------- Состояния FSM ----------------------------
class AddChannel(StatesGroup):
    waiting_for_forward = State()

class NewPost(StatesGroup):
    waiting_for_content = State()
    waiting_for_channel = State()

class PromoCreate(StatesGroup):
    waiting_for_code = State()
    waiting_for_plan = State()
    waiting_for_duration = State()
    waiting_for_uses = State()

# ---------------------------- Клавиатуры ----------------------------
def main_keyboard():
    """Главное меню бота."""
    buttons = [
        [KeyboardButton(text="➕ Добавить канал")],
        [KeyboardButton(text="📝 Новый пост")],
        [KeyboardButton(text="🎁 Промокод")],
        [KeyboardButton(text="💬 Сообщество админов")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# ---------------------------- Обработчик команды /start ----------------------------
@router.message(CommandStart())
async def cmd_start(message: Message):
    """Приветствие и добавление пользователя в БД."""
    user_id = str(message.from_user.id)
    username = message.from_user.username
    db.add_user(user_id, username)
    await message.answer(
        "👋 Привет! Я ChiefBot — помощник для администраторов Telegram-каналов.\n\n"
        "Выберите действие в меню ниже:",
        reply_markup=main_keyboard()
    )

# ---------------------------- Добавление канала ----------------------------
@router.message(F.text == "➕ Добавить канал")
async def add_channel_start(message: Message, state: FSMContext):
    """Начало процесса добавления канала."""
    await message.answer(
        "Чтобы добавить канал, выполните два шага:\n"
        "1. Добавьте меня в администраторы вашего канала (с правами на публикацию).\n"
        "2. Перешлите сюда любое сообщение из этого канала.\n\n"
        "Пересылайте сообщение прямо сейчас."
    )
    await state.set_state(AddChannel.waiting_for_forward)

@router.message(AddChannel.waiting_for_forward)
async def process_forwarded_message(message: Message, state: FSMContext):
    """Обработка пересланного сообщения из канала."""
    if not message.forward_from_chat or message.forward_from_chat.type != "channel":
        await message.answer("❌ Это не пересланное сообщение из канала. Попробуйте ещё раз.")
        return

    chat_id = str(message.forward_from_chat.id)
    channel_title = message.forward_from_chat.title
    channel_username = message.forward_from_chat.username
    user_id = str(message.from_user.id)

    try:
        member = await bot.get_chat_member(chat_id=int(chat_id), user_id=int(user_id))
        if member.status not in ("administrator", "creator"):
            await message.answer("❌ Вы не являетесь администратором этого канала.")
            await state.clear()
            return
    except Exception as e:
        await message.answer(f"❌ Не удалось проверить ваши права. Убедитесь, что бот добавлен в канал. Ошибка: {e}")
        await state.clear()
        return

    limits = db.check_limits(user_id)
    if limits["current_channels"] >= limits["allowed_channels"]:
        await message.answer(
            f"❌ Вы достигли лимита каналов для вашего тарифа ({limits['allowed_channels']}). "
            "Повысьте тариф, чтобы добавить больше каналов."
        )
        await state.clear()
        return

    db.add_channel(chat_id, user_id, channel_title, channel_username)
    db.verify_channel(chat_id)

    await message.answer(f"✅ Канал «{channel_title}» успешно подключён и верифицирован!")
    await state.clear()

# ---------------------------- Создание поста ----------------------------
@router.message(F.text == "📝 Новый пост")
async def new_post_start(message: Message, state: FSMContext):
    """Начало создания нового поста."""
    await message.answer(
        "Отправьте текст поста. Если нужно прикрепить фото, отправьте его вместе с текстом в одном сообщении.\n"
        "Для отмены нажмите /cancel."
    )
    await state.set_state(NewPost.waiting_for_content)

@router.message(NewPost.waiting_for_content)
async def process_post_content(message: Message, state: FSMContext):
    """Получение контента поста и выбор канала."""
    content = message.text or message.caption or ""
    media_type = None
    media_file_id = None

    if message.photo:
        media_type = "photo"
        media_file_id = message.photo[-1].file_id
    elif message.video:
        media_type = "video"
        media_file_id = message.video.file_id

    if not content and not media_file_id:
        await message.answer("❌ Пост пустой. Отправьте текст или фото.")
        return

    await state.update_data(content=content, media_type=media_type, media_file_id=media_file_id)

    channels = db.get_user_channels(str(message.from_user.id))
    if not channels:
        await message.answer("❌ У вас нет подключённых каналов. Сначала добавьте канал.")
        await state.clear()
        return

    if len(channels) == 1:
        await publish_post(message, state, channels[0]["id"])
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=ch["title"], callback_data=f"select_channel:{ch['id']}")]
            for ch in channels
        ])
        await message.answer("Выберите канал для публикации:", reply_markup=keyboard)
        await state.set_state(NewPost.waiting_for_channel)

async def publish_post(message: Message, state: FSMContext, channel_id: str):
    """Публикация поста в выбранный канал."""
    data = await state.get_data()
    content = data.get("content", "")
    media_type = data.get("media_type")
    media_file_id = data.get("media_file_id")

    try:
        if media_type == "photo":
            await bot.send_photo(chat_id=int(channel_id), photo=media_file_id, caption=content)
        elif media_type == "video":
            await bot.send_video(chat_id=int(channel_id), video=media_file_id, caption=content)
        else:
            await bot.send_message(chat_id=int(channel_id), text=content)

        db.add_post(channel_id, content, media_type, media_file_id, status="posted")
        await message.answer("✅ Пост успешно опубликован!")
    except Exception as e:
        await message.answer(f"❌ Ошибка при публикации: {e}")
    finally:
        await state.clear()

@router.callback_query(NewPost.waiting_for_channel, F.data.startswith("select_channel:"))
async def select_channel_callback(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора канала из инлайн-кнопок."""
    channel_id = callback.data.split(":")[1]
    await callback.answer()
    await publish_post(callback.message, state, channel_id)

# ---------------------------- Команда /cancel ----------------------------
@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    """Отмена текущего действия."""
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_keyboard())

@router.message(Command("my_channels"))
async def cmd_my_channels(message: Message):
    """Показывает подключённые каналы."""
    user_id = str(message.from_user.id)
    channels = db.get_user_channels(user_id)
    
    if not channels:
        await message.answer("У вас нет подключённых каналов.")
        return
    
    text = "Ваши каналы:\n\n"
    for ch in channels:
        status = "✅" if ch["verified"] else "❌"
        text += f"{status} {ch['title']} (id: {ch['id']})\n"
    
    await message.answer(text)

@router.message(Command("delete_channel"))
async def cmd_delete_channel(message: Message):
    """Удаляет канал."""
    user_id = str(message.from_user.id)
    channels = db.get_user_channels(user_id)
    
    if not channels:
        await message.answer("У вас нет каналов.")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=ch["title"], 
            callback_data=f"delete_channel:{ch['id']}"
        )]
        for ch in channels
    ])
    await message.answer("Выберите канал для удаления:", reply_markup=keyboard)


@router.callback_query(F.data.startswith("delete_channel:"))
async def delete_channel_callback(callback: CallbackQuery):
    channel_id = callback.data.split(":")[1]
    # Удаление из БД
    conn = sqlite3.connect(db.DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
    conn.commit()
    conn.close()
    
    await callback.message.answer("✅ Канал удалён.")
    await callback.answer()

# ---------------------------- Подписка на канал ----------------------------
@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message):
    """Команда для подписки на канал."""
    user_id = str(message.from_user.id)
    
    # Получаем каналы пользователя
    channels = db.get_user_channels(user_id)
    
    if not channels:
        await message.answer("❌ У вас нет подключённых каналов. Сначала добавьте канал.")
        return
    
    if len(channels) == 1:
        # Один канал — сразу создаём платёж
        channel_id = channels[0]["id"]
        payment_url = subscriptions_service.create_subscription_payment(user_id, channel_id)
        
        if payment_url:
            await message.answer(
                f"💳 Подписка на канал «{channels[0]['title']}»\n"
                f"Цена: 1 ₽\n"
                f"Длительность: 30 дней\n\n"
                f"Оплатите по ссылке:\n{payment_url}"
            )
        else:
            await message.answer("❌ Ошибка создания платежа.")
    else:
        # Несколько каналов — показываем список
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=ch["title"], 
                callback_data=f"subscribe_channel:{ch['id']}"
            )]
            for ch in channels
        ])
        await message.answer("Выберите канал для подписки:", reply_markup=keyboard)


@router.callback_query(F.data.startswith("subscribe_channel:"))
async def subscribe_channel_callback(callback: CallbackQuery):
    """Обработка выбора канала для подписки."""
    channel_id = callback.data.split(":")[1]
    user_id = str(callback.from_user.id)
    
    payment_url = subscriptions_service.create_subscription_payment(user_id, channel_id)
    
    if payment_url:
        await callback.message.answer(
            f"💳 Подписка на канал\n"
            f"Цена: 1 ₽\n"
            f"Длительность: 30 дней\n\n"
            f"Оплатите по ссылке:\n{payment_url}"
        )
    else:
        await callback.message.answer("❌ Ошибка создания платежа.")
    
    await callback.answer()

# ---------------------------- Промокоды ----------------------------
@router.message(F.text == "🎁 Промокод")
async def promo_info(message: Message):
    """Информация о промокодах."""
    await message.answer(
        "Для активации промокода отправьте команду:\n"
        "/promo <код>\n\n"
        "Например: /promo ABC123"
    )

@router.message(Command("promo"))
async def promo_command(message: Message, state: FSMContext):
    """Обработка команды /promo."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование:\n/promo <код> — активация промокода\n/promo create — создать промокод (только для админов)")
        return

    subcommand = args[1].strip()
    if subcommand == "create":
        if message.from_user.id not in config.ADMIN_IDS:
            await message.answer("⛔ У вас нет прав для создания промокодов.")
            return
        await message.answer("Введите код промокода (например, ABC123):")
        await state.set_state(PromoCreate.waiting_for_code)
    else:
        code = subcommand
        success, msg = db.activate_promocode(code, str(message.from_user.id))
        await message.answer(msg)

# ---------------------------- FSM для создания промокода ----------------------------
@router.message(PromoCreate.waiting_for_code)
async def promo_code_entered(message: Message, state: FSMContext):
    code = message.text.strip()
    if not code:
        await message.answer("Код не может быть пустым. Введите ещё раз.")
        return
    await state.update_data(code=code)
    await message.answer("Выберите тариф:\n- pro\n- premium")
    await state.set_state(PromoCreate.waiting_for_plan)

@router.message(PromoCreate.waiting_for_plan)
async def promo_plan_entered(message: Message, state: FSMContext):
    plan = message.text.strip().lower()
    if plan not in ("pro", "premium"):
        await message.answer("Некорректный тариф. Введите pro или premium.")
        return
    await state.update_data(plan=plan)
    await message.answer("Введите длительность подписки в днях (целое число):")
    await state.set_state(PromoCreate.waiting_for_duration)

@router.message(PromoCreate.waiting_for_duration)
async def promo_duration_entered(message: Message, state: FSMContext):
    try:
        duration = int(message.text.strip())
        if duration <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите положительное целое число дней.")
        return
    await state.update_data(duration_days=duration)
    await message.answer("Введите количество использований промокода (целое число):")
    await state.set_state(PromoCreate.waiting_for_uses)

@router.message(PromoCreate.waiting_for_uses)
async def promo_uses_entered(message: Message, state: FSMContext):
    try:
        uses = int(message.text.strip())
        if uses <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите положительное целое число.")
        return

    data = await state.get_data()
    db.add_promocode(
        code=data["code"],
        plan=data["plan"],
        duration_days=data["duration_days"],
        uses_left=uses,
        created_by=str(message.from_user.id)
    )
    await message.answer(f"✅ Промокод {data['code']} создан:\nТариф: {data['plan']}\nДней: {data['duration_days']}\nИспользований: {uses}")
    await state.clear()

# ---------------------------- Кнопка сообщества ----------------------------
@router.message(F.text == "💬 Сообщество админов")
async def community_button(message: Message):
    await message.answer(
        f"Присоединяйтесь к нашему сообществу администраторов Telegram-каналов:\n{config.COMMUNITY_CHAT_URL}"
    )

# ---------------------------- Запуск бота ----------------------------
async def main():
    db.init_db()
    # Запускаем Flask в отдельном потоке
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
