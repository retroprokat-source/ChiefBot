# services/ai_tools.py
import requests
import config

BASE_URL = config.BASE_URL


def generate_hashtags(post_text: str) -> list:
    """
    Генерирует 10-15 релевантных хештегов для поста.
    """
    prompt = (
        "Ты — SMM-специалист. Сгенерируй 10-15 релевантных хештегов "
        "для Telegram-поста. Только хештеги, без пояснений.\n\n"
        f"Текст поста:\n{post_text}"
    )
    
    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "model": "gemini-3.5-flash",
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 200,
    }
    
    try:
        response = requests.post(f"{BASE_URL}/chat/completions", headers=headers, json=payload, timeout=30)
        
        if response.status_code != 200:
            return []
        
        text = response.json()["choices"][0]["message"]["content"]
        hashtags = [word for word in text.split() if word.startswith("#")]
        return hashtags
    except Exception as e:
        return []


def generate_post_ideas(topic: str) -> str:
    """
    Генерирует 5 идей для постов на заданную тему.
    """
    prompt = (
        "Ты — контент-стратег. Предложи 5 идей для Telegram-постов "
        f"на тему: {topic}. Для каждой идеи — заголовок и 1-2 предложения."
    )
    
    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "model": "gemini-3.5-flash",
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 500,
    }
    
    try:
        response = requests.post(f"{BASE_URL}/chat/completions", headers=headers, json=payload, timeout=30)
        
        if response.status_code != 200:
            return ""
        
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return ""
