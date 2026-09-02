# services/scheduler.py
import logging
import asyncio
from datetime import datetime
import database as db
import config


async def check_expired_subscriptions():
    """Проверяет истёкшие подписки и кикает подписчиков."""
    expired = db.get_expired_subscribers()
    
    if not expired:
        logging.info("Нет истёкших подписок")
        return
    
    for sub in expired:
        channel_id = sub["channel_id"]
        user_id = sub["user_id"]
        
        try:
            # Кикаем из канала
            import requests as r
            url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/banChatMember"
            response = r.post(url, json={
                "chat_id": int(channel_id),
                "user_id": int(user_id)
            })
            
            if response.status_code == 200:
                # Разбаниваем, чтобы мог вернуться
                unban_url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/unbanChatMember"
                r.post(unban_url, json={
                    "chat_id": int(channel_id),
                    "user_id": int(user_id)
                })
                
                db.mark_subscriber_expired(channel_id, user_id)
                logging.info(f"✅ Пользователь {user_id} кикнут из {channel_id}")
            else:
                logging.error(f"❌ Ошибка кика {user_id}: {response.text}")
        except Exception as e:
            logging.error(f"❌ Ошибка: {e}")


async def run_scheduler():
    """Запускает периодическую проверку."""
    while True:
        await check_expired_subscriptions()
        await asyncio.sleep(3600)  # Проверка каждый час

async def schedule_post(channel_id: str, content: str, media_type: str, media_file_id: str, scheduled_at):
    """Планирует пост на указанное время."""
    from datetime import datetime
    
    delay = (scheduled_at - datetime.now()).total_seconds()
    
    if delay < 0:
        delay = 1
    
    logging.info(f"⏰ Пост запланирован через {delay} секунд в канал {channel_id}")
    
    async def publish():
        await asyncio.sleep(delay)
        try:
            import requests as r
            
            if media_type == "photo":
                url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendPhoto"
                data = {"chat_id": int(channel_id), "photo": media_file_id, "caption": content}
            elif media_type == "video":
                url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendVideo"
                data = {"chat_id": int(channel_id), "video": media_file_id, "caption": content}
            else:
                url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage"
                data = {"chat_id": int(channel_id), "text": content}
            
            response = r.post(url, json=data)
            if response.status_code == 200:
                logging.info(f"✅ Отложенный пост опубликован в {channel_id}")
            else:
                logging.error(f"❌ Ошибка публикации: {response.text[:300]}")
        except Exception as e:
            logging.error(f"❌ Ошибка: {e}")
    
    asyncio.create_task(publish())
