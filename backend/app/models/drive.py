import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Identity, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPrimaryKey


class Folder(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "folders"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"))
    name: Mapped[str]
    path: Mapped[str]
    # the marker object that makes the folder visible in the bucket (None until written)
    gcs_key: Mapped[str | None]
    trashed_at: Mapped[datetime | None]
    trash_root: Mapped[bool] = mapped_column(default=False)


class File(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "files"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    folder_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("folders.id", ondelete="CASCADE"))
    name: Mapped[str]
    gcs_key: Mapped[str]
    content_type: Mapped[str] = mapped_column(default="application/octet-stream")
    kind: Mapped[str] = mapped_column(default="other")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(default="uploading")
    upload_session_url: Mapped[str | None]
    upload_expires_at: Mapped[datetime | None]
    trashed_at: Mapped[datetime | None]
    trash_root: Mapped[bool] = mapped_column(default=False)
    last_opened_at: Mapped[datetime | None]


class FileEvent(Base):
    __tablename__ = "file_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    file_id: Mapped[uuid.UUID | None]
    folder_id: Mapped[uuid.UUID | None]
    action: Mapped[str]
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
