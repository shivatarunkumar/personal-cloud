import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Sort = Literal["name", "modified", "size"]
Order = Literal["asc", "desc"]


class FolderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    path: str
    created_at: datetime
    updated_at: datetime
    trashed_at: datetime | None = None
    item_count: int | None = None


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    folder_id: uuid.UUID | None
    name: str
    content_type: str
    kind: str
    size_bytes: int
    created_at: datetime
    updated_at: datetime
    trashed_at: datetime | None = None
    # where it lives, for search / recent / photos results ("" = My files)
    folder_path: str | None = None


class Crumb(BaseModel):
    id: uuid.UUID | None
    name: str


class ListingOut(BaseModel):
    folder: FolderOut | None
    breadcrumbs: list[Crumb]
    folders: list[FolderOut]
    files: list[FileOut]


class FolderCreate(BaseModel):
    parent_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=255)


class EnsureFolders(BaseModel):
    parent_id: uuid.UUID | None = None
    paths: list[str] = Field(max_length=5000)


class EnsureFoldersOut(BaseModel):
    folders: dict[str, uuid.UUID]


class Rename(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class Selection(BaseModel):
    file_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5000)
    folder_ids: list[uuid.UUID] = Field(default_factory=list, max_length=5000)


class Transfer(Selection):
    target_folder_id: uuid.UUID | None = None


class Outcome(BaseModel):
    files: int
    folders: int
    renamed: list[str] = Field(default_factory=list)


class UsageOut(BaseModel):
    used_bytes: int
    quota_bytes: int | None
    trash_bytes: int
    by_kind: dict[str, dict[str, int]]


class UploadStart(BaseModel):
    folder_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=0)
    content_type: str | None = Field(default=None, max_length=255)
    # a name already taken: keep both ("photo (1).jpg"), replace (old one to the trash), or fail
    on_conflict: Literal["rename", "replace", "error"] = "rename"


class UploadOut(BaseModel):
    file: FileOut
    upload_url: str | None
    chunk_size: int


class ConflictItem(BaseModel):
    folder_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=255)


class ConflictCheck(BaseModel):
    items: list[ConflictItem] = Field(max_length=20000)


class Conflict(BaseModel):
    folder_id: uuid.UUID | None
    name: str
    # "file": can be replaced; "folder": a folder has that name, so only skip or keep both
    type: Literal["file", "folder"]
    file_id: uuid.UUID | None
    size_bytes: int | None
    updated_at: datetime
