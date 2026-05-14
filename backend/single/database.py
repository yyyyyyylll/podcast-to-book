from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from core.config import settings


engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG)
async_session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


def _add_missing_columns(conn):
    """给已有表安全地添加缺失的列（create_all 不处理这种情况）。"""
    import sqlalchemy as sa
    inspector = sa.inspect(conn)

    _MIGRATIONS = [
        ("tasks", "name_aliases", "JSON"),
        ("users", "is_internal", "BOOLEAN DEFAULT FALSE"),
        ("tasks", "edit_count", "INTEGER DEFAULT 0"),
        ("tasks", "custom_full_cover_url", "TEXT DEFAULT ''"),
        ("tasks", "custom_back_cover_url", "TEXT DEFAULT ''"),
        ("tasks", "original_cover_url", "TEXT DEFAULT ''"),
        # 积分阶梯 + 权益码体系
        ("tasks", "unlock_level", "VARCHAR(20) DEFAULT 'full_text'"),
        ("users", "invited_by", "VARCHAR(36)"),
        ("users", "invited_by_code", "VARCHAR(20)"),
        ("redemption_codes", "code_type", "VARCHAR(4) DEFAULT ''"),
        ("redemption_codes", "channel", "VARCHAR(4) DEFAULT ''"),
        ("redemption_codes", "batch_name", "VARCHAR(100) DEFAULT ''"),
        ("redemption_codes", "custom_tag", "VARCHAR(10) DEFAULT ''"),
        ("redemption_codes", "creator_user_id", "VARCHAR(36)"),
        ("redemption_records", "user_phone", "VARCHAR(20) DEFAULT ''"),
        ("redemption_records", "code_type", "VARCHAR(4) DEFAULT ''"),
        ("redemption_records", "channel", "VARCHAR(4) DEFAULT ''"),
        ("redemption_records", "inviter_user_id", "VARCHAR(36)"),
        ("ratings", "contact_ok", "BOOLEAN DEFAULT FALSE"),
        ("ratings", "nps_score", "INTEGER DEFAULT 0"),
        ("users", "guide_completed", "BOOLEAN DEFAULT FALSE"),
    ]

    for table, column, col_type in _MIGRATIONS:
        if not inspector.has_table(table):
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column not in existing:
            conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            print(f"[database] 已添加列: {table}.{column} ({col_type})")
            if table == "users" and column == "guide_completed":
                conn.execute(sa.text("UPDATE users SET guide_completed = TRUE"))
                print("[database] 已将所有现有用户的 guide_completed 设为 TRUE")


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
