import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

MATCHMAKING_CATEGORY_ID = int(os.getenv("MATCHMAKING_CATEGORY_ID", "0"))

MIN_RATING = 2.0
MAX_RATING = 12.0
RATING_STEP = 1.0

MAX_ACTIVE_REQUESTS_PER_USER = 1  # Максимум активных заявок на пользователя
CHANNEL_INACTIVE_TIMEOUT = 3600   # Удаление неактивных каналов через 1 час (в секундах)
REQUEST_TIMEOUT = 1800            # Таймаут заявки в очереди - 30 минут (в секундах)

def get_ratings():
    ratings = []
    current = MIN_RATING
    while current <= MAX_RATING:
        ratings.append(current)
        current += RATING_STEP
    return ratings

RATINGS = get_ratings()