# main.py
import asyncio
import json
import logging
import sqlite3
import os
from threading import Thread
from flask import Flask, request
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

import config
import database as db
import services.payments as payments_service
import services.subscriptions as subscriptions_service
import services.scheduler as scheduler_service

# ---------------------------- Настройка логирования ----------------------------
logging.basicConfig(level=logging.INFO)

# ---------------------------- Инициализация бота и диспетчера ----------------------------
bot = Bot(token=config.BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# ---------------------------- HTTP-сервер для Render ----------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "ChiefBot is running!"

def run_flask():
    port = int(os.getenv("PORT", 10000))
    payments_service.setup_webhook()  # ← регистрация вебхука при запуске
    app.run(host="0.0.0.0", port=port)
    
@app.route('/webhook/tochka', methods=['GET', 'POST'])
def tochka_webhook():
    if request.method == 'GET':
        return "OK", 200
    
    raw_body = request.get_data(as_text=True)
    logging.info(f"🔔 Вебхук получен: {raw_body[:1000]}")
    
    webhook_data = payments_service.process_webhook(raw_body)
    logging.info(f"📦 Декодированные данные: {json.dumps(webhook_data, ensure_ascii=False)[:1000]}")

    if webhook_data:
        amount = webhook_data.get("amount", 0)
        purpose = webhook_data.get("purpose", "")
        payment_link_id = webhook_data.get("paymentLinkId", "")
        status = webhook_data.get("status", "")
        payment_status = webhook_data.get("paymentStatus", "")

        logging.info(f"Статус: {status}, paymentStatus: {payment_status}, paymentLinkId: {payment_link_id}")

        if status in ("success", "confirmed", "paid", "APPROVED", "approved") or payment_status in ("success", "confirmed", "paid", "APPROVED", "approved", "SUCCESS", "CONFIRMED", "PAID"):
            db.update_payment_status(payment_link_id, "paid")
            
            payment = db.get_payment_by_link_id(payment_link_id)
            if payment:
                user_id = payment["user_id"]
                channel_id = payment["channel_id"]
                
                if channel_id:
                    expires_at = subscriptions_service.activate_subscription(user_id, channel_id)
                    logging.info(f"✅ Подписка активирована для user={user_id}, channel={channel_id}")
                    
                    # Отправляем уведомление и создаём пригласительную ссылку
                    try:
                        channel_info = db.get_channel_by_id(channel_id)
                        channel_title = channel_info["title"] if channel_info else channel_id
                        
                        import requests as r
                        
                        # Создаём пригласительную ссылку
                        invite_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/createChatInviteLink"
                        invite_response = r.post(invite_url, json={
                            "chat_id": int(channel_id),
                            "member_limit": 1
                        })
                        
                        invite_link = None
                        if invite_response.status_code == 200:
                            invite_link = invite_response.json().get("result", {}).get("invite_link")
                        
                        # Формируем текст сообщения
                        message_text = f"✅ Оплата получена!\n\nПодписка на канал «{channel_title}» активирована.\nДействует до: {expires_at}"
                        
                        if invite_link:
                            message_text += f"\n\n🔗 Вступите в канал:\n{invite_link}"
                        
                        # Отправляем сообщение
                        telegram_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage"
                        r.post(telegram_url, json={
                            "chat_id": int(user_id),
                            "text": message_text
                        })
                        logging.info(f"✅ Уведомление отправлено пользователю {user_id}")
                    except Exception as e:
                        logging.error(f"❌ Ошибка отправки уведомления: {e}")
                else:
                    logging.error(f"❌ Нет channel_id в платеже {payment_link_id}")
            else:
                logging.error(f"❌ Платёж не найден: {payment_link_id}")
        else:
            logging.warning(f"⚠️ Неизвестный статус: {status}, paymentStatus: {payment_status}")

    return "OK", 200

# ---------------------------- Состояния FSM ----------------------------
class AddChannel(StatesGroup):
    waiting_for_forward = State()

class NewPost(StatesGroup):
    waiting_for_content = State()
    waiting_for_channel = State()
    waiting_for_time = State()

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
        [KeyboardButton(text="💳 Подписка")],
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

# ---------------------------- Кнопка Подписка ----------------------------
@router.message(F.text == "💳 Подписка")
async def subscribe_button(message: Message):
    """Обработка кнопки Подписка."""
    await cmd_subscribe(message)

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
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Опубликовать сейчас", callback_data="post_now")],
        [InlineKeyboardButton(text="⏰ Запланировать", callback_data="post_schedule")]
    ])
    await message.answer(
        "Отправьте текст поста. Если нужно прикрепить фото, отправьте его вместе с текстом в одном сообщении.\n"
        "Для отмены нажмите /cancel.",
        reply_markup=keyboard
    )
    await state.set_state(NewPost.waiting_for_content)

@router.message(NewPost.waiting_for_content)
async def process_post_content(message: Message, state: FSMContext):
    """Получение контента поста."""
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
        await state.update_data(channel_id=channels[0]["id"])
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 Опубликовать сейчас", callback_data="post_now")],
            [InlineKeyboardButton(text="⏰ Запланировать", callback_data="post_schedule")]
        ])
        await message.answer("Выберите действие:", reply_markup=keyboard)
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
    channel_id = callback.data.split(":")[1]
    await callback.answer()
    await publish_post(callback.message, state, channel_id)

@router.callback_query(F.data == "post_now")
async def post_now_callback(callback: CallbackQuery, state: FSMContext):
    """Немедленная публикация."""
    data = await state.get_data()
    channel_id = data.get("channel_id")
    
    if not channel_id:
        await callback.message.answer("❌ Канал не выбран.")
        await callback.answer()
        return
    
    await callback.answer()
    await publish_post(callback.message, state, channel_id)


@router.callback_query(F.data == "post_schedule")
async def post_schedule_callback(callback: CallbackQuery, state: FSMContext):
    """Запрос времени для отложенного постинга."""
    await callback.message.answer(
        "Введите дату и время в формате:\n"
        "ДД.ММ.ГГГГ ЧЧ:ММ\n\n"
        "Например: 05.09.2026 15:30"
    )
    await state.set_state(NewPost.waiting_for_time)
    await callback.answer()


@router.message(NewPost.waiting_for_time)
async def process_schedule_time(message: Message, state: FSMContext):
    """Обработка времени для отложенного постинга."""
    from datetime import datetime
    import services.scheduler as scheduler_service
    
    try:
        scheduled_dt = datetime.strptime(message.text.strip(), "%d.%m.%Y %H:%M")
        scheduled_at = scheduled_dt.strftime("%Y-%m-%d %H:%M:%S")
        
        data = await state.get_data()
        channel_id = data.get("channel_id")
        content = data.get("content", "")
        media_type = data.get("media_type")
        media_file_id = data.get("media_file_id")
        
        # Сохраняем пост в БД со статусом scheduled
        db.add_scheduled_post(channel_id, content, media_type, media_file_id, scheduled_at)
        
        # Добавляем задачу в планировщик
        await scheduler_service.schedule_post(
            channel_id=channel_id,
            content=content,
            media_type=media_type,
            media_file_id=media_file_id,
            scheduled_at=scheduled_dt
        )
        
        await message.answer(f"✅ Пост запланирован на {message.text.strip()}")
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Неверный формат. Используйте: ДД.ММ.ГГГГ ЧЧ:ММ")

# ---------------------------- Команда /cancel ----------------------------
@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_keyboard())

# ---------------------------- Мои каналы ----------------------------
@router.message(Command("my_channels"))
async def cmd_my_channels(message: Message):
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

# ---------------------------- Удаление канала ----------------------------
@router.message(Command("delete_channel"))
async def cmd_delete_channel(message: Message):
    user_id = str(message.from_user.id)
    channels = db.get_user_channels(user_id)
    
    if not channels:
        await message.answer("У вас нет каналов.")
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=ch["title"], callback_data=f"delete_channel:{ch['id']}")]
        for ch in channels
    ])
    await message.answer("Выберите канал для удаления:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("delete_channel:"))
async def delete_channel_callback(callback: CallbackQuery):
    channel_id = callback.data.split(":")[1]
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
    user_id = str(message.from_user.id)
    channels = db.get_user_channels(user_id)
    
    if not channels:
        await message.answer("❌ У вас нет подключённых каналов. Сначала добавьте канал.")
        return
    
    if len(channels) == 1:
        channel_id = channels[0]["id"]
        channel_title = channels[0]["title"]
        
        # Проверяем, есть ли уже активная подписка
        existing = db.get_user_subscription(user_id, channel_id)
        if existing:
            await message.answer(
                f"✅ У вас уже есть активная подписка на канал «{channel_title}».\n"
                f"Действует до: {existing}"
            )
            return
        
        payment_url = subscriptions_service.create_subscription_payment(user_id, channel_id)
        
        if payment_url:
            await message.answer(
                f"💳 Подписка на канал «{channel_title}»\n"
                f"Цена: 1 ₽\n"
                f"Длительность: 30 дней\n\n"
                f"Оплатите по ссылке:\n{payment_url}"
            )
        else:
            await message.answer("❌ Ошибка создания платежа.")
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=ch["title"], callback_data=f"subscribe_channel:{ch['id']}")]
            for ch in channels
        ])
        await message.answer("Выберите канал для подписки:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("subscribe_channel:"))
async def subscribe_channel_callback(callback: CallbackQuery):
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
    await message.answer(
        "Для активации промокода отправьте команду:\n"
        "/promo <код>\n\n"
        "Например: /promo ABC123"
    )

@router.message(Command("promo"))
async def promo_command(message: Message, state: FSMContext):
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
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Запускаем планировщик
    asyncio.create_task(scheduler_service.run_scheduler())
    
    await dp.start_polling(bot)
    
if __name__ == "__main__":
    asyncio.run(main())
