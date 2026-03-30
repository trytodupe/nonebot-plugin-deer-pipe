from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nonebot_plugin_uninfo import Session


from .constants import DATABASE_PATH, DATABASE_URL
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
import sqlite3
import asyncio
from collections import defaultdict
from sqlalchemy import select
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import Field, Index, SQLModel, col, delete, func, update
from uuid import UUID, uuid4


# ORM models
class User(SQLModel, table=True):
    __table_args__ = (
        Index("ix_user_id", "adapter", "scope", "user_id", unique=True),
    )

    uuid: UUID = Field(primary_key=True, default_factory=uuid4)
    adapter: str
    scope: str
    user_id: str
    can_be_helped: bool = True
    no_deer_until: datetime | None = None


class DeerRecord(SQLModel, table=True):
    __table_args__ = (Index("ix_deerrecord_id", "user_uuid", "month", "day"),)

    uuid: UUID = Field(primary_key=True, default_factory=uuid4)
    user_uuid: UUID = Field(foreign_key="user.uuid")
    month: int = Field(index=True)
    day: int
    count: int = 1


# Initialize database engin
_engine = create_async_engine(DATABASE_URL)
_initialized = False
_init_lock: asyncio.Lock | None = None


def _get_current_db_path() -> Path:
    return Path(DATABASE_PATH)


def _get_previous_db_path() -> Path:
    return _get_current_db_path().with_name("userdata-v3.db")


def _migrate_previous_database():
    current_path = _get_current_db_path()
    previous_path = _get_previous_db_path()
    temp_path = current_path.with_name(f"{current_path.name}.migrating")

    if current_path.exists() or not previous_path.exists():
        return

    if temp_path.exists():
        temp_path.unlink()

    # Create the new schema before copying rows.
    sync_engine = create_engine(f"sqlite:///{temp_path}")
    try:
        SQLModel.metadata.create_all(sync_engine)
    finally:
        sync_engine.dispose()

    user_rows: list[tuple[str, str, str, str]] = []
    record_rows: list[tuple[str, int, int, int]] = []

    with sqlite3.connect(previous_path) as old_db:
        old_db.row_factory = sqlite3.Row
        user_rows = [
            (
                row["uuid"],
                row["adapter"],
                row["scope"],
                row["user_id"],
            )
            for row in old_db.execute(
                "SELECT uuid, adapter, scope, user_id FROM user ORDER BY rowid"
            )
        ]
        record_rows = [
            (
                row["user_uuid"],
                row["month"],
                row["day"],
                row["count"],
            )
            for row in old_db.execute(
                "SELECT user_uuid, month, day, count FROM deerrecord ORDER BY rowid"
            )
        ]

    user_uuid_map: dict[str, str] = {}
    merged_users: dict[tuple[str, str, str], str] = {}
    merged_records: dict[tuple[str, int, int], int] = defaultdict(int)

    for user_uuid, adapter, scope, user_id in user_rows:
        key = (adapter, scope, user_id)
        canonical_uuid = merged_users.setdefault(key, user_uuid)
        user_uuid_map[user_uuid] = canonical_uuid

    for user_uuid, month, day, count in record_rows:
        canonical_uuid = user_uuid_map.get(user_uuid)
        if canonical_uuid is None:
            continue
        merged_records[(canonical_uuid, month, day)] += count

    with sqlite3.connect(temp_path) as new_db:
        new_db.execute("PRAGMA foreign_keys = OFF")
        for (adapter, scope, user_id), user_uuid in merged_users.items():
            new_db.execute(
                """
                INSERT INTO user (
                    uuid, adapter, scope, user_id, can_be_helped, no_deer_until
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_uuid, adapter, scope, user_id, 1, None),
            )

        for (user_uuid, month, day), count in merged_records.items():
            new_db.execute(
                """
                INSERT INTO deerrecord (uuid, user_uuid, month, day, count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(uuid4()), user_uuid, month, day, count),
            )

        new_db.commit()

    temp_path.replace(current_path)


@asynccontextmanager
async def _get_session():
    # Initialize engine
    global _initialized, _init_lock
    if _init_lock is None:
        _init_lock = asyncio.Lock()
    if not _initialized:
        async with _init_lock:
            if not _initialized:
                _migrate_previous_database()
                async with _engine.begin() as conn:
                    await conn.run_sync(SQLModel.metadata.create_all)
                _initialized = True

    # Create session
    async with AsyncSession(_engine) as session:
        yield session


async def _get_records(db: AsyncSession, now: datetime, user: User):
    # Fetch records
    result = (
        await db.execute(
            select(col(DeerRecord.day), col(DeerRecord.count))
            .where(col(DeerRecord.user_uuid) == user.uuid)
            .where(col(DeerRecord.month) == now.month)
        )
    ).all()

    # Return map
    return {i.tuple()[0]: i.tuple()[1] for i in result}


async def cleanup():
    """Cleanup expired data"""
    async with _get_session() as db:
        now = datetime.now()

        # Find expired deer data
        res1 = (
            await db.execute(
                select(col(DeerRecord.user_uuid))
                .distinct()
                .where(col(DeerRecord.month) != now.month)
            )
        ).all()
        set1 = {i.tuple()[0] for i in res1}

        # Cleanup expired deer data
        await db.execute(delete(DeerRecord).where(col(DeerRecord.month) != now.month))

        # Find active users from deer data
        res2 = (await db.execute(select(col(DeerRecord.user_uuid)).distinct())).all()
        set2 = {i.tuple()[0] for i in res2}

        # Cleanup inactive users
        set3 = set1 - set2
        await db.execute(delete(User).where(col(User.uuid).in_(set3)))

        # Commit trascation
        await db.commit()


async def get_user(session: Session, user_id: str):
    """
    Get user

    :param session: Uninfo session
    :param user_id: User ID
    :return: User
    """
    async with _get_session() as db:
        # Fetch user
        user = await db.scalar(
            select(User)
            .where(col(User.adapter) == session.adapter)
            .where(col(User.scope) == session.scope)
            .where(col(User.user_id) == user_id)
        )

        # If user not exists
        if user is None:
            # Insert new user
            user = User(
                adapter=session.adapter,
                scope=session.scope,
                user_id=user_id,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)

        # Return user
        return user


async def update_user(user: User):
    """
    Update user fields

    :param user: User
    """
    async with _get_session() as db:
        db.add(user)
        await db.commit()


async def get_records(now: datetime, user: User):
    """
    Get user deer record map

    :param now: Current time
    :param user: User
    :return: dict[day of month, count]
    """
    async with _get_session() as db:
        return await _get_records(db, now, user)


async def check_in(now: datetime, user: User, day: int | None = None):
    """
    Check in

    :param now: Current time
    :param user: User
    :param day: Past day of current month
    :return: tuple[is success, dict[day of month, count]]
    """
    async with _get_session() as db:
        # Get deer records
        records = await _get_records(db, now, user)

        # If check today && today is checked
        if day is None and now.day in records:
            records[now.day] += 1
            await db.execute(
                update(DeerRecord)
                .where(col(DeerRecord.user_uuid) == user.uuid)
                .where(col(DeerRecord.month) == now.month)
                .where(col(DeerRecord.day) == now.day)
                .values(count=records[now.day])
            )
            await db.commit()
            return (True, records)

        # If check past && past is checked
        elif day is not None and day in records:
            return (False, records)

        # If check today && today is not checked || check past and past is not checked
        else:
            records[day or now.day] = 1
            db.add(DeerRecord(user_uuid=user.uuid, month=now.month, day=day or now.day))
            await db.commit()
            return (True, records)


async def check_out(now: datetime, user: User):
    """
    Check out (count -1)

    :param now: Current time
    :param user: User
    :return: dict[day of month, count]
    """
    async with _get_session() as db:
        # Get deer records
        records = await _get_records(db, now, user)

        # If today is checked
        if now.day in records:
            records[now.day] -= 1
            await db.execute(
                update(DeerRecord)
                .where(col(DeerRecord.user_uuid) == user.uuid)
                .where(col(DeerRecord.month) == now.month)
                .where(col(DeerRecord.day) == now.day)
                .values(count=records[now.day])
            )

        # If today is not checked
        else:
            records[now.day] = -1
            db.add(DeerRecord(user_uuid=user.uuid, month=now.month, day=now.day, count=-1))

        await db.commit()
        return records


async def get_rank(_session: Session, now: datetime):
    """
    Get rank

    :param session: Uninfo session
    :param now: Current time
    :return: list[tuple[user ID, count]]
    """
    async with _get_session() as db:
        # Fetch rank of top 5
        res = (
            await db.execute(
                select(func.sum(DeerRecord.count), col(User.user_id))
                .join(User)
                .where(col(User.adapter) == _session.adapter)
                .where(col(User.scope) == _session.scope)
                .where(col(DeerRecord.month) == now.month)
                .group_by(col(DeerRecord.user_uuid))
                .order_by(func.sum(DeerRecord.count).desc())
                .limit(5)
            )
        ).all()

        # Return rank
        return [(i.tuple()[1], i.tuple()[0]) for i in res]
