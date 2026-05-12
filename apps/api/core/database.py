"""异步数据库连接（databases 库，兼容 PostgreSQL asyncpg 驱动）"""
import databases
from core.config import settings

database = databases.Database(settings.database_url)


async def connect():
    await database.connect()


async def disconnect():
    await database.disconnect()
