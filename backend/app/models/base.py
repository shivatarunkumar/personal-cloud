"""SQLAlchemy models mirror tables created by database/postgres/migrations.
They never create or alter tables; migrations are the only source of schema."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    # Every timestamp column is timestamptz; without this, SQLAlchemy maps
    # `datetime` to a naive TIMESTAMP and asyncpg rejects timezone-aware values.
    type_annotation_map = {datetime: DateTime(timezone=True)}


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, server_default=func.gen_random_uuid())


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
