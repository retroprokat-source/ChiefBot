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
import services.ai_tools as ai_tools

# ---------------------------- Часовые пояса России ----------------------------
RUSSIA_TIMEZONES = {
    "Калининград": 2,
    "Москва": 3,
    "Самара": 4,
    "Екатеринбург": 5,
    "Омск": 6,
    "Новосибирск": 7,
    "Иркутск": 8,
    "Якутск": 9,
    "Владивосток": 10,
    "Магадан": 11,
    "Камчатка": 12
}

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
                    
                    try:
                        channel_info = db.get_channel_by_id(channel_id)
                        channel_title = channel_info["title"] if channel_info else channel_id
                        
                        import requests as r
                        
                        invite_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/createChatInviteLink"
                        invite_response = r.post(invite_url, json={
                            "chat_id": int(channel_id),
                            "member_limit": 1
                        })
                        
                        invite_link = None
                        if invite_response.status_code == 200:
                            invite_link = invite_response.json().get("result", {}).get("invite_link")
                        
                        message_text = f"✅ Оплата получена!\n\nПодписка на канал «{channel_title}» активирована.\nДействует до: {expires_at}"
                        
                        if invite_link:
                            message_text += f"\n\n🔗 Вступите в канал:\n{invite_link}"
                        
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

class TimezoneSetup(StatesGroup):
    waiting_for_timezone = State()

class AIGeneration(StatesGroup):
    waiting_for_hashtag_text = State()
    waiting_for_idea_topic = State()

class CustomPrice(StatesGroup):
    waiting_for_price = State()

class SubscriptionSettings(StatesGroup):
    waiting_for_channel = State()
    waiting_for_price = State()
    waiting_for_payment_link = State()
    waiting_for_instructions = State()
    

# ---------------------------- Клавиатуры ----------------------------
def admin_keyboard():
    """Меню администратора."""
    buttons = [
        [KeyboardButton(text="➕ Добавить канал")],
        [KeyboardButton(text="📝 Новый пост")],
        [KeyboardButton(text="📂 Черновики")],
        [KeyboardButton(text="👥 Подписчики")],
        [KeyboardButton(text="⚙️ Настройка подписки")],
        [KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="🎁 Промокод")],
        [KeyboardButton(text="✨ ИИ-хештеги"), KeyboardButton(text="💡 Идеи для постов")],
        [KeyboardButton(text="💬 Сообщество админов")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def subscriber_keyboard():
    """Меню подписчика."""
    buttons = [
        [KeyboardButton(text="💳 Подписаться")],
        [KeyboardButton(text="💬 Сообщество админов")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def role_selection_keyboard():
    """Клавиатура выбора роли."""
    buttons = [
        [InlineKeyboardButton(text="👨‍💼 Администратор", callback_data="role:admin")],
        [InlineKeyboardButton(text="👤 Подписчик", callback_data="role:subscriber")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def main_keyboard_for_user(user_id: str):
    """Возвращает клавиатуру в зависимости от роли и каналов."""
    channels = db.get_user_channels(user_id)
    role = db.get_user_role(user_id)
    
    if channels:
        # Есть каналы — полное админское меню + подписка
        return admin_keyboard()
    elif role == "admin":
        # Админ без каналов — меню с добавлением канала
        buttons = [
            [KeyboardButton(text="➕ Добавить канал")],
            [KeyboardButton(text="💬 Сообщество админов")]
        ]
        return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
    else:
        # Подписчик или роль не выбрана
        return subscriber_keyboard()


def timezone_keyboard():
    """Клавиатура выбора часового пояса."""
    buttons = []
    row = []
    for city, offset in RUSSIA_TIMEZONES.items():
        label = f"{city} UTC+{offset}"
        row.append(InlineKeyboardButton(text=label, callback_data=f"tz:{offset}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ---------------------------- Обработчик команды /start ----------------------------
@router.message(CommandStart())
async def cmd_start(message: Message):
    """Приветствие и добавление пользователя в БД."""
    user_id = str(message.from_user.id)
    username = message.from_user.username
    db.add_user(user_id, username)
    
    channels = db.get_user_channels(user_id)
    role = db.get_user_role(user_id)
    
    if channels:
        await message.answer(
            "👋 С возвращением!\n\n"
            "Вы подключены как администратор.\n"
            "Выберите действие:",
            reply_markup=admin_keyboard()
        )
    elif role == "admin":
        await message.answer(
            "👋 Привет, администратор!\n\n"
            "Чтобы начать, добавьте канал:\n"
            "1. Добавьте меня в администраторы канала\n"
            "2. Перешлите любое сообщение из канала\n\n"
            "Выберите действие:",
            reply_markup=main_keyboard_for_user(user_id)
        )
    elif role == "subscriber":
        await message.answer(
            "👋 Привет!\n\n"
            "Здесь вы можете получить доступ к закрытым каналам.\n"
            "Выберите канал для подписки:",
            reply_markup=subscriber_keyboard()
        )
    else:
        await message.answer(
            "👋 Привет! Я ChiefBot — помощник для администраторов Telegram-каналов.\n\n"
            "Я умею:\n"
            "• Публиковать посты (сейчас + отложенные)\n"
            "• Управлять платными подписками\n"
            "• Генерировать хештеги и идеи для постов\n"
            "• Вести статистику канала\n\n"
            "Кем вы являетесь?",
            reply_markup=role_selection_keyboard()
        )

@router.callback_query(F.data.startswith("role:"))
async def role_selected(callback: CallbackQuery):
    """Обработка выбора роли."""
    role = callback.data.split(":")[1]
    user_id = str(callback.from_user.id)
    db.set_user_role(user_id, role)
    
    if role == "admin":
        await callback.message.answer(
            "✅ Вы зарегистрированы как администратор.\n\n"
            "Чтобы начать, добавьте канал:\n"
            "1. Добавьте меня в администраторы канала (права: публикация)\n"
            "2. Перешлите сюда любое сообщение из канала\n\n"
            "Нажмите «➕ Добавить канал» для начала.",
            reply_markup=main_keyboard_for_user(user_id)
        )
    else:
        await callback.message.answer(
            "✅ Вы зарегистрированы как подписчик.\n\n"
            "Здесь вы можете получить доступ к закрытым каналам.\n"
            "Выберите канал для подписки:",
            reply_markup=subscriber_keyboard()
        )
    
    await callback.answer()
        
# ---------------------------- Черновики ----------------------------
@router.message(F.text == "📂 Черновики")
async def drafts_list(message: Message):
    """Список черновиков."""
    user_id = str(message.from_user.id)
    drafts = db.get_drafts(user_id)
    
    if not drafts:
        await message.answer("У вас нет черновиков.")
        return
    
    for draft in drafts:
        content_preview = draft["content"][:50] + "..." if len(draft["content"]) > 50 else draft["content"]
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📤 Опубликовать", callback_data=f"publish_draft:{draft['id']}"),
                InlineKeyboardButton(text="⏰ Запланировать", callback_data=f"schedule_draft:{draft['id']}")
            ],
            [
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_draft:{draft['id']}")
            ]
        ])
        
        await message.answer(
            f"📝 Черновик #{draft['id']}\n"
            f"Канал: {draft['channel_title']}\n"
            f"Текст: {content_preview}",
            reply_markup=keyboard
        )

@router.callback_query(F.data.startswith("publish_draft:"))
async def publish_draft_callback(callback: CallbackQuery):
    """Публикация черновика."""
    draft_id = int(callback.data.split(":")[1])
    draft = db.get_draft_by_id(draft_id)
    
    if not draft:
        await callback.message.answer("❌ Черновик не найден.")
        await callback.answer()
        return
    
    try:
        if draft["media_type"] == "photo":
            await bot.send_photo(
                chat_id=int(draft["channel_id"]),
                photo=draft["media_file_id"],
                caption=draft["content"]
            )
        elif draft["media_type"] == "video":
            await bot.send_video(
                chat_id=int(draft["channel_id"]),
                video=draft["media_file_id"],
                caption=draft["content"]
            )
        else:
            await bot.send_message(
                chat_id=int(draft["channel_id"]),
                text=draft["content"]
            )
        
        db.update_draft_status(draft_id, "posted")
        await callback.message.answer("✅ Черновик опубликован!")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка публикации: {e}")
    
    await callback.answer()

@router.callback_query(F.data.startswith("schedule_draft:"))
async def schedule_draft_callback(callback: CallbackQuery, state: FSMContext):
    """Планирование черновика."""
    draft_id = int(callback.data.split(":")[1])
    draft = db.get_draft_by_id(draft_id)
    
    if not draft:
        await callback.message.answer("❌ Черновик не найден.")
        await callback.answer()
        return
    
    # Сохраняем данные черновика в состояние
    await state.update_data(
        channel_id=draft["channel_id"],
        content=draft["content"],
        media_type=draft["media_type"],
        media_file_id=draft["media_file_id"]
    )
    
    # Удаляем черновик, так как он превращается в отложенный пост
    db.delete_draft(draft_id)
    
    user_id = str(callback.from_user.id)
    current_tz = db.get_user_timezone(user_id)
    
    if current_tz:
        city = None
        for c, offset in RUSSIA_TIMEZONES.items():
            if str(offset) == str(current_tz):
                city = c
                break
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Изменить пояс", callback_data="change_timezone")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_schedule")]
        ])
        
        await callback.message.answer(
            f"Ваш часовой пояс: {city} (UTC+{current_tz})\n\n"
            "Введите дату и время в формате:\n"
            "ДД.ММ.ГГГГ ЧЧ:ММ\n\n"
            "Например: 05.09.2026 15:30",
            reply_markup=keyboard
        )
        await state.set_state(NewPost.waiting_for_time)
    else:
        await state.update_data(planning_post=True)
        await callback.message.answer(
            "⚠️ Для планирования постов необходимо указать часовой пояс.\n\n"
            "Выберите ваш регион:",
            reply_markup=timezone_keyboard()
        )
        await state.set_state(TimezoneSetup.waiting_for_timezone)
    
    await callback.answer()


@router.callback_query(F.data.startswith("delete_draft:"))
async def delete_draft_callback(callback: CallbackQuery):
    """Удаление черновика."""
    draft_id = int(callback.data.split(":")[1])
    db.delete_draft(draft_id)
    await callback.message.answer("✅ Черновик удалён.")
    await callback.answer()

# ---------------------------- Подписчики ----------------------------
@router.message(F.text == "👥 Подписчики")
async def subscribers_button(message: Message):
    """Список заявок и активных подписчиков."""
    user_id = str(message.from_user.id)
    channels = db.get_user_channels(user_id)
    
    if not channels:
        await message.answer("❌ У вас нет каналов.")
        return
    
    if len(channels) == 1:
        await show_subscribers_menu(message, channels[0]["id"])
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=ch["title"], callback_data=f"subs_channel:{ch['id']}")]
            for ch in channels
        ])
        await message.answer("Выберите канал:", reply_markup=keyboard)


async def show_subscribers_menu(message: Message, channel_id: str):
    """Показывает меню подписчиков."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏳ Заявки", callback_data=f"subs_pending:{channel_id}")],
        [InlineKeyboardButton(text="✅ Активные", callback_data=f"subs_active:{channel_id}")],
        [InlineKeyboardButton(text="⏰ Истёкшие", callback_data=f"subs_expired:{channel_id}")]
    ])
    await message.answer("Выберите категорию:", reply_markup=keyboard)


@router.callback_query(F.data.startswith("subs_channel:"))
async def subs_channel_callback(callback: CallbackQuery):
    """Обработка выбора канала для подписчиков."""
    channel_id = callback.data.split(":")[1]
    await show_subscribers_menu(callback.message, channel_id)
    await callback.answer()


@router.callback_query(F.data.startswith("subs_pending:"))
async def subs_pending_callback(callback: CallbackQuery):
    """Список заявок на подписку."""
    channel_id = callback.data.split(":")[1]
    pending = db.get_pending_requests(channel_id)
    
    if not pending:
        await callback.message.answer("Нет заявок на подписку.")
        await callback.answer()
        return
    
    for req in pending:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Выдать доступ", callback_data=f"approve_sub:{req['id']}")],
            [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_sub:{req['id']}")]
        ])
        
        await callback.message.answer(
            f"🔔 Заявка #{req['id']}\n"
            f"Пользователь: @{req['username'] or 'без username'}\n"
            f"Канал: {req['channel_title']}\n"
            f"Дата: {req['created_at']}",
            reply_markup=keyboard
        )
    
    await callback.answer()


@router.callback_query(F.data.startswith("subs_active:"))
async def subs_active_callback(callback: CallbackQuery):
    """Список активных подписчиков."""
    channel_id = callback.data.split(":")[1]
    active = db.get_active_subscribers(channel_id)
    
    if not active:
        await callback.message.answer("Нет активных подписчиков.")
        await callback.answer()
        return
    
    for sub in active:
        await callback.message.answer(
            f"✅ Подписчик: {sub['user_id']}\n"
            f"Действует до: {sub['expires_at']}"
        )
    
    await callback.answer()


@router.callback_query(F.data.startswith("subs_expired:"))
async def subs_expired_callback(callback: CallbackQuery):
    """Список истёкших подписчиков."""
    channel_id = callback.data.split(":")[1]
    expired = db.get_expired_subscribers_by_channel(channel_id)
    
    if not expired:
        await callback.message.answer("Нет истёкших подписчиков.")
        await callback.answer()
        return
    
    for sub in expired:
        await callback.message.answer(
            f"⏰ Подписчик: {sub['user_id']}\n"
            f"Истекла: {sub['expires_at']}"
        )
    
    await callback.answer()


@router.callback_query(F.data.startswith("approve_sub:"))
async def approve_sub_callback(callback: CallbackQuery):
    """Выдача доступа."""
    request_id = int(callback.data.split(":")[1])
    result = db.approve_subscription_request(request_id)
    
    if result:
        channel_id = result["channel_id"]
        user_id = result["user_id"]
        
        # Добавляем в канал
        try:
            import requests as r
            invite_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/createChatInviteLink"
            invite_response = r.post(invite_url, json={
                "chat_id": int(channel_id),
                "member_limit": 1
            })
            
            invite_link = None
            if invite_response.status_code == 200:
                invite_link = invite_response.json().get("result", {}).get("invite_link")
            
            channel_info = db.get_channel_by_id(channel_id)
            channel_title = channel_info["title"] if channel_info else channel_id
            
            message_text = f"✅ Доступ в канал «{channel_title}» предоставлен!"
            if invite_link:
                message_text += f"\n\n🔗 Вступите по ссылке:\n{invite_link}"
            
            telegram_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage"
            r.post(telegram_url, json={
                "chat_id": int(user_id),
                "text": message_text
            })
            
            await callback.message.answer(f"✅ Доступ выдан пользователю {user_id}")
        except Exception as e:
            await callback.message.answer(f"❌ Ошибка выдачи доступа: {e}")
    else:
        await callback.message.answer("❌ Заявка не найдена.")
    
    await callback.answer()


@router.callback_query(F.data.startswith("reject_sub:"))
async def reject_sub_callback(callback: CallbackQuery):
    """Отклонение заявки."""
    request_id = int(callback.data.split(":")[1])
    db.reject_subscription_request(request_id)
    await callback.message.answer("✅ Заявка отклонена.")
    await callback.answer()

# ---------------------------- Настройка подписки ----------------------------
@router.message(F.text == "⚙️ Настройка подписки")
async def subscription_settings_start(message: Message, state: FSMContext):
    """Настройка подписки канала."""
    user_id = str(message.from_user.id)
    channels = db.get_user_channels(user_id)
    
    if not channels:
        await message.answer("❌ У вас нет каналов.")
        return
    
    if len(channels) == 1:
        await show_subscription_settings(message, channels[0]["id"])
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=ch["title"], callback_data=f"sub_settings_channel:{ch['id']}")]
            for ch in channels
        ])
        await message.answer("Выберите канал:", reply_markup=keyboard)


async def show_subscription_settings(message: Message, channel_id: str):
    """Показывает текущие настройки подписки."""
    settings = db.get_channel_subscription_settings(channel_id)
    channel_info = db.get_channel_by_id(channel_id)
    channel_title = channel_info["title"] if channel_info else channel_id
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Изменить цену", callback_data=f"set_sub_price:{channel_id}")],
        [InlineKeyboardButton(text="🔗 Изменить ссылку", callback_data=f"set_sub_link:{channel_id}")],
        [InlineKeyboardButton(text="📝 Изменить инструкцию", callback_data=f"set_sub_instr:{channel_id}")]
    ])
    
    await message.answer(
        f"⚙️ Настройки подписки для канала «{channel_title}»\n\n"
        f"💰 Цена: {settings['price'] or 'не указана'}\n"
        f"🔗 Ссылка: {settings['payment_link'] or 'не указана'}\n"
        f"📝 Инструкция: {settings['instructions'] or 'не указана'}",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("sub_settings_channel:"))
async def sub_settings_channel_callback(callback: CallbackQuery):
    """Выбор канала для настройки."""
    channel_id = callback.data.split(":")[1]
    await show_subscription_settings(callback.message, channel_id)
    await callback.answer()


@router.callback_query(F.data.startswith("set_sub_price:"))
async def set_sub_price_callback(callback: CallbackQuery, state: FSMContext):
    """Запрос новой цены."""
    channel_id = callback.data.split(":")[1]
    await state.update_data(channel_id=channel_id)
    await callback.message.answer("Введите цену подписки (например, 200):")
    await state.set_state(SubscriptionSettings.waiting_for_price)
    await callback.answer()


@router.callback_query(F.data.startswith("set_sub_link:"))
async def set_sub_link_callback(callback: CallbackQuery, state: FSMContext):
    """Запрос новой ссылки."""
    channel_id = callback.data.split(":")[1]
    await state.update_data(channel_id=channel_id)
    await callback.message.answer("Введите ссылку на оплату:")
    await state.set_state(SubscriptionSettings.waiting_for_payment_link)
    await callback.answer()


@router.callback_query(F.data.startswith("set_sub_instr:"))
async def set_sub_instr_callback(callback: CallbackQuery, state: FSMContext):
    """Запрос новой инструкции."""
    channel_id = callback.data.split(":")[1]
    await state.update_data(channel_id=channel_id)
    await callback.message.answer("Введите инструкцию для подписчика:")
    await state.set_state(SubscriptionSettings.waiting_for_instructions)
    await callback.answer()


@router.message(SubscriptionSettings.waiting_for_price)
async def sub_price_entered(message: Message, state: FSMContext):
    """Сохранение цены."""
    price = message.text.strip()
    data = await state.get_data()
    channel_id = data.get("channel_id")
    db.update_channel_subscription_settings(channel_id, price=price)
    await message.answer(f"✅ Цена подписки обновлена: {price} ₽")
    await state.clear()
    await show_subscription_settings(message, channel_id)


@router.message(SubscriptionSettings.waiting_for_payment_link)
async def sub_link_entered(message: Message, state: FSMContext):
    """Сохранение ссылки."""
    link = message.text.strip()
    data = await state.get_data()
    channel_id = data.get("channel_id")
    db.update_channel_subscription_settings(channel_id, payment_link=link)
    await message.answer("✅ Ссылка на оплату обновлена")
    await state.clear()
    await show_subscription_settings(message, channel_id)


@router.message(SubscriptionSettings.waiting_for_instructions)
async def sub_instructions_entered(message: Message, state: FSMContext):
    """Сохранение инструкции."""
    instructions = message.text.strip()
    data = await state.get_data()
    channel_id = data.get("channel_id")
    db.update_channel_subscription_settings(channel_id, instructions=instructions)
    await message.answer("✅ Инструкция обновлена")
    await state.clear()
    await show_subscription_settings(message, channel_id)

# ---------------------------- Статистика (кнопка) ----------------------------
@router.message(F.text == "📊 Статистика")
async def stats_button(message: Message):
    """Кнопка статистики."""
    await cmd_stats(message)

# ---------------------------- Добавление канала ----------------------------
@router.message(Command("add_channel"))
async def cmd_add_channel(message: Message, state: FSMContext):
    """Команда добавления канала."""
    await message.answer(
        "Чтобы добавить канал, выполните два шага:\n"
        "1. Добавьте меня в администраторы вашего канала (с правами на публикацию).\n"
        "2. Перешлите сюда любое сообщение из этого канала.\n\n"
        "Пересылайте сообщение прямо сейчас."
    )
    await state.set_state(AddChannel.waiting_for_forward)

@router.message(F.text == "➕ Добавить канал")
async def add_channel_start(message: Message, state: FSMContext):
    """Кнопка добавления канала."""
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
    db.set_user_role(user_id, "admin")

    await message.answer(
        f"✅ Канал «{channel_title}» успешно подключён и верифицирован!\n\n"
        "Теперь вам доступны все функции администратора.",
        reply_markup=admin_keyboard()
    )
    await state.clear()

# ---------------------------- Создание поста ----------------------------
@router.message(F.text == "📝 Новый пост")
async def new_post_start(message: Message, state: FSMContext):
    await message.answer(
        "Отправьте текст поста. Если нужно прикрепить фото, отправьте его вместе с текстом в одном сообщении.\n"
        "Для отмены нажмите /cancel."
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
            [InlineKeyboardButton(text="📤 Опубликовать сейчас", callback_data="publish_now")],
            [InlineKeyboardButton(text="⏰ Запланировать", callback_data="schedule_post")],
            [InlineKeyboardButton(text="💾 Сохранить в черновик", callback_data="save_draft")]
        ])
        await message.answer("Выберите действие:", reply_markup=keyboard)
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=ch["title"], callback_data=f"channel_selected:{ch['id']}")]
            for ch in channels
        ])
        await message.answer("Выберите канал:", reply_markup=keyboard)
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

@router.callback_query(F.data.startswith("channel_selected:"))
async def channel_selected_callback(callback: CallbackQuery, state: FSMContext):
    channel_id = callback.data.split(":")[1]
    await state.update_data(channel_id=channel_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Опубликовать сейчас", callback_data="publish_now")],
        [InlineKeyboardButton(text="⏰ Запланировать", callback_data="schedule_post")],
        [InlineKeyboardButton(text="💾 Сохранить в черновик", callback_data="save_draft")]
    ])
    await callback.message.answer("Выберите действие:", reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data == "publish_now")
async def publish_now_callback(callback: CallbackQuery, state: FSMContext):
    """Немедленная публикация."""
    data = await state.get_data()
    channel_id = data.get("channel_id")
    
    if not channel_id:
        await callback.message.answer("❌ Канал не выбран.")
        await callback.answer()
        return
    
    await callback.answer()
    await publish_post(callback.message, state, channel_id)

@router.callback_query(F.data == "save_draft")
async def save_draft_callback(callback: CallbackQuery, state: FSMContext):
    """Сохранение черновика."""
    data = await state.get_data()
    channel_id = data.get("channel_id")
    content = data.get("content", "")
    media_type = data.get("media_type")
    media_file_id = data.get("media_file_id")
    
    if not channel_id:
        await callback.message.answer("❌ Канал не выбран.")
        await callback.answer()
        return
    
    db.add_draft(channel_id, content, media_type, media_file_id)
    await callback.message.answer("✅ Пост сохранён в черновик!")
    await state.clear()
    await callback.answer()

@router.callback_query(F.data == "schedule_post")
async def schedule_post_callback(callback: CallbackQuery, state: FSMContext):
    """Запрос времени для отложенного постинга."""
    user_id = str(callback.from_user.id)
    current_tz = db.get_user_timezone(user_id)
    
    if current_tz:
        city = None
        for c, offset in RUSSIA_TIMEZONES.items():
            if str(offset) == str(current_tz):
                city = c
                break
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Изменить пояс", callback_data="change_timezone")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_schedule")]
        ])
        
        await callback.message.answer(
            f"Ваш часовой пояс: {city} (UTC+{current_tz})\n\n"
            "Введите дату и время в формате:\n"
            "ДД.ММ.ГГГГ ЧЧ:ММ\n\n"
            "Например: 05.09.2026 15:30",
            reply_markup=keyboard
        )
        await state.set_state(NewPost.waiting_for_time)
    else:
        await state.update_data(planning_post=True)
        await callback.message.answer(
            "⚠️ Для планирования постов необходимо указать часовой пояс.\n\n"
            "Выберите ваш регион:",
            reply_markup=timezone_keyboard()
        )
        await state.set_state(TimezoneSetup.waiting_for_timezone)
    
    await callback.answer()

@router.callback_query(F.data == "cancel_schedule")
async def cancel_schedule_callback(callback: CallbackQuery, state: FSMContext):
    """Отмена планирования."""
    await state.clear()
    await callback.message.answer("Планирование отменено.", reply_markup=main_keyboard_for_user(str(callback.from_user.id)))
    await callback.answer()

@router.callback_query(F.data == "change_timezone")
async def change_timezone_callback(callback: CallbackQuery, state: FSMContext):
    """Изменение часового пояса при планировании."""
    await state.update_data(planning_post=True)
    await callback.message.answer(
        "Выберите новый часовой пояс:",
        reply_markup=timezone_keyboard()
    )
    await state.set_state(TimezoneSetup.waiting_for_timezone)
    await callback.answer()

@router.callback_query(F.data.startswith("tz:"))
async def timezone_selected(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора часового пояса."""
    offset = callback.data.split(":")[1]
    user_id = str(callback.from_user.id)
    
    db.set_user_timezone(user_id, offset)
    
    city = None
    for c, off in RUSSIA_TIMEZONES.items():
        if str(off) == str(offset):
            city = c
            break
    
    await callback.message.answer(f"✅ Часовой пояс установлен: {city} (UTC+{offset})")
    await callback.answer()
    
    data = await state.get_data()
    if data.get("planning_post"):
        await callback.message.answer(
            "Теперь введите дату и время для отложенного поста:\n"
            "ДД.ММ.ГГГГ ЧЧ:ММ\n\n"
            "Например: 05.09.2026 15:30"
        )
        await state.set_state(NewPost.waiting_for_time)
    else:
        await state.clear()

@router.message(NewPost.waiting_for_time, Command("cancel"))
async def cancel_schedule_time(message: Message, state: FSMContext):
    """Отмена планирования из состояния ожидания времени."""
    await state.clear()
    await message.answer("Планирование отменено.", reply_markup=main_keyboard_for_user(str(message.from_user.id)))


@router.message(NewPost.waiting_for_time)
async def process_schedule_time(message: Message, state: FSMContext):
    """Обработка времени для отложенного постинга."""
    from datetime import datetime, timedelta

    # Если нажата кнопка меню — сбрасываем состояние и переходим
    if message.text in ("➕ Добавить канал", "📝 Новый пост", "📂 Черновики", "👥 Подписчики", "⚙️ Настройка подписки", "💳 Подписаться", "🎁 Промокод", "✨ ИИ-хештеги", "💡 Идеи для постов", "💬 Сообщество админов"):
        await state.clear()
        # Перенаправляем на нужный обработчик
        if message.text == "➕ Добавить канал":
            await add_channel_start(message, state)
        elif message.text == "📝 Новый пост":
            await new_post_start(message, state)
        elif message.text == "📂 Черновики":
            await drafts_list(message)
        elif message.text == "👥 Подписчики":
            await subscribers_button(message)
        elif message.text == "⚙️ Настройка подписки":
            await subscription_settings_start(message, state)
        elif message.text == "💳 Подписаться":
            await subscribe_button(message)
        elif message.text == "🎁 Промокод":
            await promo_info(message)
        elif message.text == "✨ ИИ-хештеги":
            await ai_hashtags_start(message, state)
        elif message.text == "💡 Идеи для постов":
            await ai_ideas_start(message, state)
        elif message.text == "💬 Сообщество админов":
            await community_button(message)
        return
    
    try:
        scheduled_dt = datetime.strptime(message.text.strip(), "%d.%m.%Y %H:%M")
        
        user_id = str(message.from_user.id)
        user_tz_offset = db.get_user_timezone(user_id)
        
        if user_tz_offset is None:
            await message.answer("❌ Часовой пояс не установлен. Используйте /set_timezone")
            await state.clear()
            return
        
        utc_dt = scheduled_dt - timedelta(hours=int(user_tz_offset))
        scheduled_at_utc = utc_dt.strftime("%Y-%m-%d %H:%M:%S")
        
        data = await state.get_data()
        channel_id = data.get("channel_id")
        content = data.get("content", "")
        media_type = data.get("media_type")
        media_file_id = data.get("media_file_id")
        
        db.add_scheduled_post(channel_id, content, media_type, media_file_id, scheduled_at_utc)
        
        await scheduler_service.schedule_post(
            channel_id=channel_id,
            content=content,
            media_type=media_type,
            media_file_id=media_file_id,
            scheduled_at=utc_dt
        )
        
        city = None
        for c, offset in RUSSIA_TIMEZONES.items():
            if str(offset) == str(user_tz_offset):
                city = c
                break
        
        await message.answer(
            f"✅ Пост запланирован:\n"
            f"📅 {message.text.strip()} ({city}, UTC+{user_tz_offset})\n"
            f"🌍 {utc_dt.strftime('%d.%m.%Y %H:%M')} (UTC)"
        )
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Неверный формат. Используйте: ДД.ММ.ГГГГ ЧЧ:ММ")

# ---------------------------- Команда /cancel ----------------------------
@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_keyboard_for_user(str(message.from_user.id)))

# ---------------------------- Часовой пояс ----------------------------
@router.message(Command("set_timezone"))
async def cmd_set_timezone(message: Message, state: FSMContext):
    """Команда смены часового пояса."""
    user_id = str(message.from_user.id)
    current_tz = db.get_user_timezone(user_id)
    
    if current_tz:
        city = None
        for c, offset in RUSSIA_TIMEZONES.items():
            if str(offset) == str(current_tz):
                city = c
                break
        
        current_label = f"{city} (UTC+{current_tz})" if city else f"UTC+{current_tz}"
        await message.answer(
            f"Ваш текущий часовой пояс: {current_label}\n\n"
            "Выберите новый пояс:",
            reply_markup=timezone_keyboard()
        )
    else:
        await message.answer(
            "Выберите ваш часовой пояс:",
            reply_markup=timezone_keyboard()
        )
    await state.set_state(TimezoneSetup.waiting_for_timezone)
    await state.update_data(planning_post=False)

# ---------------------------- Статистика ----------------------------
@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Базовая статистика по каналам."""
    user_id = str(message.from_user.id)
    channels = db.get_user_channels(user_id)
    
    if not channels:
        await message.answer("❌ У вас нет подключённых каналов.")
        return
    
    for ch in channels:
        channel_id = ch["id"]
        active_subs = db.get_active_subscribers(channel_id)
        expired_subs = db.get_expired_subscribers_by_channel(channel_id)
        pending_subs = db.get_pending_requests(channel_id)
        
        # Считаем посты
        conn = sqlite3.connect(db.DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM posts WHERE channel_id = ? AND status = 'posted'", (channel_id,))
        posts_count = cur.fetchone()[0]
        conn.close()
        
        await message.answer(
            f"📊 Статистика канала «{ch['title']}»\n\n"
            f"📝 Постов опубликовано: {posts_count}\n"
            f"✅ Активных подписчиков: {len(active_subs)}\n"
            f"⏰ Истёкших: {len(expired_subs)}\n"
            f"⏳ Заявок: {len(pending_subs)}"
        )

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

# ---------------------------- Подписка (для подписчика) ----------------------------
@router.message(F.text == "💳 Подписаться")
async def subscribe_button(message: Message):
    """Кнопка Подписаться для подписчика."""
    channels = db.get_all_channels_for_subscribe()
    
    if not channels:
        await message.answer(
            "😔 Пока нет доступных каналов для подписки.\n"
            "Загляните позже!"
        )
        return
    
    if len(channels) == 1:
        await show_subscription_info(message, channels[0])
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=ch["title"], callback_data=f"sub_info:{ch['id']}")]
            for ch in channels
        ])
        await message.answer(
            "🔒 Доступные каналы для подписки:\n\n"
            "Выберите канал, чтобы увидеть условия.",
            reply_markup=keyboard
        )


async def show_subscription_info(message: Message, channel):
    """Показывает информацию о подписке."""
    price = channel.get("price") or "не указана"
    payment_link = channel.get("payment_link") or "не указана"
    instructions = channel.get("instructions") or "Оплатите и нажмите «Я оплатил»."
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"i_paid:{channel['id']}")]
    ])
    
    await message.answer(
        f"💳 Подписка на канал «{channel['title']}»\n\n"
        f"💰 Стоимость: {price}\n"
        f"🔗 Оплата: {payment_link}\n\n"
        f"📝 Как получить доступ:\n{instructions}\n\n"
        "После оплаты нажмите «Я оплатил» — администратор проверит и выдаст доступ.",
        reply_markup=keyboard
    )


@router.callback_query(F.data.startswith("sub_info:"))
async def sub_info_callback(callback: CallbackQuery):
    """Показывает информацию о канале."""
    channel_id = callback.data.split(":")[1]
    channels = db.get_all_channels_for_subscribe()
    channel = next((ch for ch in channels if ch["id"] == channel_id), None)
    
    if channel:
        await show_subscription_info(callback.message, channel)
    
    await callback.answer()


@router.callback_query(F.data.startswith("i_paid:"))
async def i_paid_callback(callback: CallbackQuery):
    """Подписчик нажал «Я оплатил»."""
    channel_id = callback.data.split(":")[1]
    user_id = str(callback.from_user.id)
    username = callback.from_user.username
    
    # Сохраняем заявку
    db.add_subscription_request(channel_id, user_id, username)
    
    await callback.message.answer(
        "✅ Заявка отправлена администратору.\n"
        "После проверки оплаты вам придёт уведомление с доступом."
    )
    
    # Уведомляем админа
    channel_info = db.get_channel_by_id(channel_id)
    owner_id = channel_info["owner_id"] if channel_info else None
    channel_title = channel_info["title"] if channel_info else channel_id
    
    if owner_id:
        try:
            telegram_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage"
            import requests as r
            r.post(telegram_url, json={
                "chat_id": int(owner_id),
                "text": (
                    f"🔔 Заявка на подписку\n\n"
                    f"Пользователь: @{username or 'без username'}\n"
                    f"Канал: «{channel_title}»\n\n"
                    f"Проверьте оплату и выдайте доступ в разделе «👥 Подписчики»."
                )
            })
        except Exception as e:
            logging.error(f"❌ Ошибка уведомления админа: {e}")
    
    await callback.answer()

# ---------------------------- Подписка на канал ----------------------------
@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message):
    """Команда подписки для админа (просмотр)."""
    user_id = str(message.from_user.id)
    channels = db.get_user_channels(user_id)
    
    if not channels:
        await message.answer("❌ У вас нет подключённых каналов. Сначала добавьте канал.")
        return
    
    # Показываем информацию о подписках на каналы
    for ch in channels:
        settings = db.get_channel_subscription_settings(ch["id"])
        await message.answer(
            f"Канал: {ch['title']}\n"
            f"Цена: {settings['price'] or 'не указана'}\n"
            f"Ссылка: {settings['payment_link'] or 'не указана'}"
        )

@router.callback_query(F.data.startswith("subscribe_channel:"))
async def subscribe_channel_callback(callback: CallbackQuery):
    channel_id = callback.data.split(":")[1]
    user_id = str(callback.from_user.id)
    
    channel_info = db.get_channel_by_id(channel_id)
    channel_title = channel_info["title"] if channel_info else channel_id
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 ₽", callback_data=f"sub_price:{channel_id}:1"),
         InlineKeyboardButton(text="5 ₽", callback_data=f"sub_price:{channel_id}:5")],
        [InlineKeyboardButton(text="10 ₽", callback_data=f"sub_price:{channel_id}:10"),
         InlineKeyboardButton(text="50 ₽", callback_data=f"sub_price:{channel_id}:50")],
        [InlineKeyboardButton(text="100 ₽", callback_data=f"sub_price:{channel_id}:100")],
        [InlineKeyboardButton(text="✏️ Своя цена", callback_data=f"custom_price:{channel_id}")]
    ])
    
    await callback.message.answer(
        f"💳 Подписка на канал «{channel_title}»\n\n"
        "Выберите цену подписки:",
        reply_markup=keyboard
    )
    
    await callback.answer()

@router.callback_query(F.data.startswith("sub_price:"))
async def sub_price_callback(callback: CallbackQuery):
    """Обработка выбора цены подписки."""
    parts = callback.data.split(":")
    channel_id = parts[1]
    price = parts[2]
    user_id = str(callback.from_user.id)
    
    channel_info = db.get_channel_by_id(channel_id)
    channel_title = channel_info["title"] if channel_info else channel_id
    
    payment_url = subscriptions_service.create_subscription_payment(user_id, channel_id, price)
    
    if payment_url:
        await callback.message.answer(
            f"💳 Подписка на канал «{channel_title}»\n"
            f"Цена: {price} ₽\n"
            f"Длительность: 30 дней\n\n"
            f"Оплатите по ссылке:\n{payment_url}"
        )
    else:
        await callback.message.answer("❌ Ошибка создания платежа.")
    
    await callback.answer()

@router.callback_query(F.data.startswith("custom_price:"))
async def custom_price_callback(callback: CallbackQuery, state: FSMContext):
    """Запрос своей цены."""
    channel_id = callback.data.split(":")[1]
    await state.update_data(channel_id=channel_id)
    await callback.message.answer(
        "Введите свою цену подписки в рублях (целое число, минимум 1 ₽):"
    )
    await state.set_state(CustomPrice.waiting_for_price)
    await callback.answer()


@router.message(CustomPrice.waiting_for_price)
async def custom_price_entered(message: Message, state: FSMContext):
    """Обработка своей цены."""
    try:
        price = int(message.text.strip())
        if price < 1:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое число больше 0.")
        return
    
    data = await state.get_data()
    channel_id = data.get("channel_id")
    user_id = str(message.from_user.id)
    
    channel_info = db.get_channel_by_id(channel_id)
    channel_title = channel_info["title"] if channel_info else channel_id
    
    payment_url = subscriptions_service.create_subscription_payment(user_id, channel_id, str(price))
    
    if payment_url:
        await message.answer(
            f"💳 Подписка на канал «{channel_title}»\n"
            f"Цена: {price} ₽\n"
            f"Длительность: 30 дней\n\n"
            f"Оплатите по ссылке:\n{payment_url}"
        )
    else:
        await message.answer("❌ Ошибка создания платежа.")
    
    await state.clear()

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

# ---------------------------- ИИ-генерация ----------------------------
@router.message(F.text == "✨ ИИ-хештеги")
async def ai_hashtags_start(message: Message, state: FSMContext):
    """Начало генерации хештегов."""
    user_id = str(message.from_user.id)
    
    # Проверяем лимит
    user = db.get_user(user_id)
    plan = user.get("plan", "free") if user else "free"
    
    if plan == "free":
        used = db.get_ai_generations_today(user_id)
        if used >= 5:
            await message.answer("❌ Вы исчерпали лимит ИИ-генераций на сегодня (5/день для Free).\nПовысьте тариф для безлимита.")
            return
    
    await message.answer(
        "Отправьте текст поста, для которого нужно сгенерировать хештеги:"
    )
    await state.set_state(AIGeneration.waiting_for_hashtag_text)


@router.message(AIGeneration.waiting_for_hashtag_text)
async def ai_hashtags_generate(message: Message, state: FSMContext):
    """Генерация хештегов."""
    post_text = message.text.strip()
    
    if not post_text:
        await message.answer("❌ Текст пустой. Отправьте текст поста.")
        return
    
    await message.answer("⏳ Генерирую хештеги...")
    
    hashtags = ai_tools.generate_hashtags(post_text)
    
    if hashtags:
        db.increment_ai_generations(str(message.from_user.id))
        result = " ".join(hashtags)
        await message.answer(f"✅ Ваши хештеги:\n\n{result}")
    else:
        await message.answer("❌ Ошибка генерации. Попробуйте позже.")
    
    await state.clear()


@router.message(F.text == "💡 Идеи для постов")
async def ai_ideas_start(message: Message, state: FSMContext):
    """Начало генерации идей."""
    user_id = str(message.from_user.id)
    
    # Проверяем лимит
    user = db.get_user(user_id)
    plan = user.get("plan", "free") if user else "free"
    
    if plan == "free":
        used = db.get_ai_generations_today(user_id)
        if used >= 5:
            await message.answer("❌ Вы исчерпали лимит ИИ-генераций на сегодня (5/день для Free).\nПовысьте тариф для безлимита.")
            return
    
    await message.answer(
        "На какую тему сгенерировать идеи для постов?\n"
        "Например: криптовалюта, спорт, мода"
    )
    await state.set_state(AIGeneration.waiting_for_idea_topic)


@router.message(AIGeneration.waiting_for_idea_topic)
async def ai_ideas_generate(message: Message, state: FSMContext):
    """Генерация идей для постов."""
    topic = message.text.strip()
    
    if not topic:
        await message.answer("❌ Тема пустая. Введите тему.")
        return
    
    await message.answer("⏳ Генерирую идеи...")
    
    ideas = ai_tools.generate_post_ideas(topic)
    
    if ideas:
        db.increment_ai_generations(str(message.from_user.id))
        await message.answer(f"✅ Идеи для постов на тему «{topic}»:\n\n{ideas}")
    else:
        await message.answer("❌ Ошибка генерации. Попробуйте позже.")
    
    await state.clear()

# ---------------------------- Запуск бота ----------------------------
async def main():
    db.init_db()
    db.migrate()
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    asyncio.create_task(scheduler_service.run_scheduler())
    
    await dp.start_polling(bot)
    
if __name__ == "__main__":
    asyncio.run(main())
