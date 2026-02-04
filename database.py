import aiosqlite
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
import config

DATABASE_FILE = "matchmaking.db"


class Database:
    def __init__(self):
        self.db_file = DATABASE_FILE
        self._lock = asyncio.Lock()
    
    async def init(self):
        """Инициализация базы данных"""
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS search_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    user_rating REAL NOT NULL,
                    desired_rating REAL NOT NULL,
                    channel_id INTEGER,
                    status TEXT DEFAULT 'waiting',
                    partner_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    matched_at TIMESTAMP,
                    UNIQUE(user_id, guild_id, status)
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS private_channels (
                    channel_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    user1_id INTEGER NOT NULL,
                    user2_id INTEGER,
                    request_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_requests_status 
                ON search_requests(status, desired_rating, user_rating)
            """)
            
            await db.commit()
    
    async def get_active_requests_count(self, user_id: int, guild_id: int) -> int:
        """Получить количество активных заявок пользователя"""
        async with aiosqlite.connect(self.db_file) as db:
            cursor = await db.execute("""
                SELECT COUNT(*) FROM search_requests 
                WHERE user_id = ? AND guild_id = ? AND status = 'waiting'
            """, (user_id, guild_id))
            result = await cursor.fetchone()
            return result[0] if result else 0
    
    async def create_request(
        self, 
        user_id: int, 
        guild_id: int, 
        user_rating: float, 
        desired_rating: float,
        channel_id: int
    ) -> int:
        """Создать новую заявку на поиск"""
        async with self._lock:
            async with aiosqlite.connect(self.db_file) as db:
                cursor = await db.execute("""
                    INSERT INTO search_requests 
                    (user_id, guild_id, user_rating, desired_rating, channel_id, status)
                    VALUES (?, ?, ?, ?, ?, 'waiting')
                """, (user_id, guild_id, user_rating, desired_rating, channel_id))
                await db.commit()
                return cursor.lastrowid
    
    async def find_match(
        self, 
        user_id: int, 
        guild_id: int, 
        user_rating: float, 
        desired_rating: float
    ) -> Optional[dict]:
        """Найти подходящую заявку для матча"""
        async with aiosqlite.connect(self.db_file) as db:
            db.row_factory = aiosqlite.Row
            
            cursor = await db.execute("""
                SELECT * FROM search_requests 
                WHERE guild_id = ? 
                AND user_id != ?
                AND status = 'waiting'
                AND user_rating = ?
                AND desired_rating = ?
                ORDER BY created_at ASC
                LIMIT 1
            """, (guild_id, user_id, desired_rating, user_rating))
            
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None
    
    async def complete_match(
        self, 
        request_id: int, 
        partner_id: int
    ):
        """Завершить матч - обновить статус заявки"""
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("""
                UPDATE search_requests 
                SET status = 'matched', partner_id = ?, matched_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (partner_id, request_id))
            await db.commit()
    
    async def cancel_request(self, user_id: int, guild_id: int) -> Optional[int]:
        """Отменить заявку пользователя, вернуть ID канала"""
        async with aiosqlite.connect(self.db_file) as db:
            cursor = await db.execute("""
                SELECT channel_id FROM search_requests 
                WHERE user_id = ? AND guild_id = ? AND status = 'waiting'
            """, (user_id, guild_id))
            row = await cursor.fetchone()
            channel_id = row[0] if row else None
            
            await db.execute("""
                UPDATE search_requests 
                SET status = 'cancelled'
                WHERE user_id = ? AND guild_id = ? AND status = 'waiting'
            """, (user_id, guild_id))
            await db.commit()
            
            return channel_id
    
    async def add_private_channel(
        self, 
        channel_id: int, 
        guild_id: int, 
        user1_id: int, 
        request_id: int
    ):
        """Добавить приватный канал в базу"""
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("""
                INSERT OR REPLACE INTO private_channels 
                (channel_id, guild_id, user1_id, request_id)
                VALUES (?, ?, ?, ?)
            """, (channel_id, guild_id, user1_id, request_id))
            await db.commit()
    
    async def update_channel_partner(self, channel_id: int, user2_id: int):
        """Добавить второго пользователя в канал"""
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("""
                UPDATE private_channels 
                SET user2_id = ?, last_activity = CURRENT_TIMESTAMP
                WHERE channel_id = ?
            """, (user2_id, channel_id))
            await db.commit()
    
    async def update_channel_activity(self, channel_id: int):
        """Обновить время последней активности канала"""
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("""
                UPDATE private_channels 
                SET last_activity = CURRENT_TIMESTAMP
                WHERE channel_id = ?
            """, (channel_id,))
            await db.commit()
    
    async def get_inactive_channels(self, timeout_seconds: int) -> List[int]:
        """Получить список неактивных каналов"""
        async with aiosqlite.connect(self.db_file) as db:
            threshold = datetime.now() - timedelta(seconds=timeout_seconds)
            cursor = await db.execute("""
                SELECT channel_id FROM private_channels 
                WHERE last_activity < ?
            """, (threshold.isoformat(),))
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
    
    async def remove_channel(self, channel_id: int):
        """Удалить канал из базы"""
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute(
                "DELETE FROM private_channels WHERE channel_id = ?", 
                (channel_id,)
            )
            await db.commit()
    
    async def get_expired_requests(self, timeout_seconds: int) -> List[dict]:
        """Получить устаревшие заявки"""
        async with aiosqlite.connect(self.db_file) as db:
            db.row_factory = aiosqlite.Row
            threshold = datetime.now() - timedelta(seconds=timeout_seconds)
            cursor = await db.execute("""
                SELECT * FROM search_requests 
                WHERE status = 'waiting' AND created_at < ?
            """, (threshold.isoformat(),))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def expire_request(self, request_id: int):
        """Отметить заявку как просроченную"""
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("""
                UPDATE search_requests 
                SET status = 'expired'
                WHERE id = ?
            """, (request_id,))
            await db.commit()
    
    async def get_user_request(self, user_id: int, guild_id: int) -> Optional[dict]:
        """Получить активную заявку пользователя"""
        async with aiosqlite.connect(self.db_file) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT * FROM search_requests 
                WHERE user_id = ? AND guild_id = ? AND status = 'waiting'
            """, (user_id, guild_id))
            row = await cursor.fetchone()
            return dict(row) if row else None

db = Database()