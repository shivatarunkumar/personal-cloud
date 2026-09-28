"""The API against the real database, with an in-memory bucket.

Needs PostgreSQL set up by `make db-init`; skipped when it isn't reachable. Every test
creates its own accounts and deletes them afterwards, so it's safe on a database that has
real data in it. No request reaches Google Cloud.
"""

from __future__ import annotations

import secrets
import uuid

import httpx
import pytest
from sqlalchemy import delete, text

from app.adapters import storage as storage_module
from app.core.db import get_engine, get_sessionmaker
from app.main import app
from app.models.user import User


class MemoryStorage:
    """Stands in for GcsStorage: objects live in a dict."""

    bucket_name = "memory"

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.sessions: dict[str, str] = {}

    def bucket_exists(self) -> bool:
        return True

    def resumable_session(self, key, content_type, size, origin, metadata):
        url = f"memory://{uuid.uuid4()}"
        self.sessions[url] = key
        return url

    def write_empty(self, key, content_type, metadata):
        self.objects[key] = b""

    def put(self, url: str, data: bytes) -> None:
        """What the browser does with the session URL."""
        self.objects[self.sessions[url]] = data

    def stat(self, key):
        return len(self.objects[key]) if key in self.objects else None

    def read_range(self, key, start, end):
        yield self.objects[key][start : end + 1]

    def copy(self, source_key, target_key, metadata):
        self.objects[target_key] = self.objects[source_key]

    def delete(self, key):
        self.objects.pop(key, None)

    def delete_prefix(self, prefix):
        doomed = [k for k in self.objects if k.startswith(prefix)]
        for k in doomed:
            del self.objects[k]
        return len(doomed)


@pytest.fixture(scope="session")
async def database():
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1 FROM files LIMIT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not ready ({type(exc).__name__}); run make db-init")
    yield
    await get_engine().dispose()


@pytest.fixture
def bucket(monkeypatch):
    memory = MemoryStorage()
    monkeypatch.setattr(storage_module, "get_storage", lambda: memory)
    for module in (
        "app.services.drive",
        "app.services.uploads",
        "app.api.v1.files",
        "app.api.v1.account",
        "app.api.v1.health",
    ):
        monkeypatch.setattr(f"{module}.get_storage", lambda: memory)
    return memory


@pytest.fixture
async def people(database, bucket):
    """Two signed-in accounts; removed at the end."""
    tag = secrets.token_hex(4)
    clients = []
    for who in ("alice", "bob"):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1")
        res = await client.post(
            "/auth/register",
            json={
                "email": f"{who}{tag}@example.com",
                "username": f"{who}{tag}",
                "display_name": who.title(),
                "password": "correct-horse-battery",
            },
        )
        assert res.status_code == 201, res.text
        clients.append(client)
    yield clients
    for client in clients:
        await client.aclose()
    async with get_sessionmaker()() as session:
        await session.execute(delete(User).where(User.username.in_([f"alice{tag}", f"bob{tag}"])))
        await session.commit()


async def upload(client, bucket, name, data, folder_id=None):
    res = await client.post("/uploads", json={"folder_id": folder_id, "name": name, "size": len(data)})
    assert res.status_code == 201, res.text
    body = res.json()
    if body["upload_url"]:
        bucket.put(body["upload_url"], data)
        res = await client.post(f"/uploads/{body['file']['id']}/complete")
        assert res.status_code == 200, res.text
    return body["file"]["id"]


async def test_upload_list_and_stream(people, bucket):
    alice, _ = people
    folder = (await alice.post("/drive/folders", json={"name": "Trip"})).json()
    data = secrets.token_bytes(5000)
    fid = await upload(alice, bucket, "clip.mp4", data, folder["id"])

    listing = (await alice.get(f"/drive/folders/{folder['id']}")).json()
    assert [f["name"] for f in listing["files"]] == ["clip.mp4"]
    assert listing["files"][0]["kind"] == "video"

    res = await alice.get(f"/files/{fid}/content", headers={"range": "bytes=10-19"})
    assert res.status_code == 206 and res.content == data[10:20]
    assert res.headers["content-range"] == "bytes 10-19/5000"
    assert (await alice.get("/drive/usage")).json()["used_bytes"] == 5000


async def test_incomplete_upload_is_refused(people, bucket):
    alice, _ = people
    res = await alice.post("/uploads", json={"name": "big.bin", "size": 100})
    body = res.json()
    bucket.put(body["upload_url"], b"only half")
    res = await alice.post(f"/uploads/{body['file']['id']}/complete")
    assert res.status_code == 409
    # an unfinished upload is not listed
    assert (await alice.get("/drive/folders/root")).json()["files"] == []


async def test_other_people_see_nothing(people, bucket):
    alice, bob = people
    folder = (await alice.post("/drive/folders", json={"name": "Private"})).json()
    fid = await upload(alice, bucket, "secret.txt", b"mine", folder["id"])
    assert (await bob.get(f"/files/{fid}/content")).status_code == 404
    assert (await bob.get(f"/files/{fid}/download")).status_code == 404
    assert (await bob.get(f"/drive/folders/{folder['id']}")).status_code == 404
    assert (await bob.patch(f"/drive/files/{fid}", json={"name": "x"})).status_code == 404
    assert (await bob.post("/drive/trash", json={"file_ids": [fid]})).status_code == 404
    assert (
        await bob.post("/uploads", json={"folder_id": folder["id"], "name": "a", "size": 1})
    ).status_code == 404
    assert (await bob.get("/drive/search", params={"q": "secret"})).json()["files"] == []


async def test_trash_restore_and_delete_forever(people, bucket):
    alice, _ = people
    a = (await alice.post("/drive/folders", json={"name": "A"})).json()
    b = (await alice.post("/drive/folders", json={"parent_id": a["id"], "name": "B"})).json()
    await upload(alice, bucket, "x.txt", b"12345", b["id"])

    await alice.post("/drive/trash", json={"folder_ids": [a["id"]]})
    assert (await alice.get(f"/drive/folders/{b['id']}")).status_code == 404
    trash = (await alice.get("/drive/trash")).json()
    assert [f["name"] for f in trash["folders"]] == ["A"] and trash["files"] == []

    # a new "A" takes the name while the old one is in the trash; restoring renames it
    await alice.post("/drive/folders", json={"name": "A"})
    out = (await alice.post("/drive/restore", json={"folder_ids": [a["id"]]})).json()
    assert out["renamed"] == ["A → A (1)"]
    assert (await alice.get(f"/drive/folders/{b['id']}")).json()["folder"]["path"] == "A (1)/B"

    await alice.post("/drive/trash", json={"folder_ids": [a["id"]]})
    out = (await alice.post("/drive/trash/empty")).json()
    assert out["files"] == 1
    assert not any(k.endswith("x.txt") for k in bucket.objects)
    assert (await alice.get("/drive/usage")).json()["used_bytes"] == 0


async def test_move_rules_and_names(people, bucket):
    alice, _ = people
    a = (await alice.post("/drive/folders", json={"name": "A"})).json()
    b = (await alice.post("/drive/folders", json={"parent_id": a["id"], "name": "B"})).json()
    res = await alice.post("/drive/move", json={"folder_ids": [a["id"]], "target_folder_id": b["id"]})
    assert res.status_code == 400  # not into itself

    await upload(alice, bucket, "doc.txt", b"1")
    await upload(alice, bucket, "doc.txt", b"2", a["id"])
    root_file = next(f for f in (await alice.get("/drive/folders/root")).json()["files"])
    out = (
        await alice.post("/drive/move", json={"file_ids": [root_file["id"]], "target_folder_id": a["id"]})
    ).json()
    assert out["renamed"] == ["doc.txt → doc (1).txt"]
    assert (await alice.post("/drive/folders", json={"name": "a"})).status_code == 409


async def test_signup_can_be_switched_off(database, bucket, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "allow_signup", False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test/api/v1"
    ) as client:
        res = await client.post(
            "/auth/register",
            json={
                "email": "nobody@example.com",
                "username": "nobody",
                "display_name": "N",
                "password": "x" * 12,
            },
        )
    assert res.status_code == 403


async def test_bucket_uses_readable_paths(people, bucket):
    alice, _ = people
    me = (await alice.get("/auth/me")).json()["username"]
    trip = (await alice.post("/drive/folders", json={"name": "Trip"})).json()
    fid = await upload(alice, bucket, "beach.jpg", b"sand", trip["id"])
    assert f"{me}/Trip/beach.jpg" in bucket.objects
    assert f"{me}/Trip/" in bucket.objects  # the folder shows in the bucket too

    await alice.patch(f"/drive/folders/{trip['id']}", json={"name": "Holiday"})
    assert f"{me}/Holiday/beach.jpg" in bucket.objects
    assert f"{me}/Trip/beach.jpg" not in bucket.objects and f"{me}/Trip/" not in bucket.objects

    await alice.post("/drive/move", json={"file_ids": [fid], "target_folder_id": None})
    assert f"{me}/beach.jpg" in bucket.objects

    # trash moves it out of the way, so the name is free for a new upload
    await alice.post("/drive/trash", json={"file_ids": [fid]})
    assert f"{me}/beach.jpg" not in bucket.objects
    assert any(k.startswith(f"_trash/{me}/") and k.endswith("/beach.jpg") for k in bucket.objects)
    await upload(alice, bucket, "beach.jpg", b"new sand")
    assert bucket.objects[f"{me}/beach.jpg"] == b"new sand"

    # restoring the old one next to the new one gives it a number
    out = (await alice.post("/drive/restore", json={"file_ids": [fid]})).json()
    assert out["renamed"] == ["beach.jpg → beach (1).jpg"]
    assert bucket.objects[f"{me}/beach (1).jpg"] == b"sand"
    assert not any(k.startswith("_trash/") for k in bucket.objects)

    # copy lands under its own name
    await alice.post("/drive/copy", json={"folder_ids": [trip["id"]], "target_folder_id": None})
    assert f"{me}/Holiday (1)/" in bucket.objects


async def test_trashed_and_live_folders_with_the_same_name_stay_apart(people, bucket):
    alice, _ = people
    old = (await alice.post("/drive/folders", json={"name": "A"})).json()
    await upload(alice, bucket, "old.txt", b"old", old["id"])
    await alice.post("/drive/trash", json={"folder_ids": [old["id"]]})

    new = (await alice.post("/drive/folders", json={"name": "A"})).json()
    await upload(alice, bucket, "new.txt", b"new", new["id"])
    await alice.patch(f"/drive/folders/{new['id']}", json={"name": "B"})
    await alice.post("/drive/trash/empty")

    listing = (await alice.get(f"/drive/folders/{new['id']}")).json()
    assert listing["folder"]["path"] == "B"
    assert [f["name"] for f in listing["files"]] == ["new.txt"]
    assert (await alice.get(f"/files/{listing['files'][0]['id']}/content")).content == b"new"


async def test_conflict_check_and_replace(people, bucket):
    alice, bob = people
    me = (await alice.get("/auth/me")).json()["username"]
    docs = (await alice.post("/drive/folders", json={"name": "Docs"})).json()
    await upload(alice, bucket, "cv.pdf", b"version 1", docs["id"])
    await alice.post("/drive/folders", json={"parent_id": docs["id"], "name": "Scans"})

    res = await alice.post(
        "/uploads/conflicts",
        json={
            "items": [
                {"folder_id": docs["id"], "name": "CV.pdf"},  # same name, other case: a clash
                {"folder_id": docs["id"], "name": "scans"},  # a folder holds the name
                {"folder_id": docs["id"], "name": "new.pdf"},  # free
                {"folder_id": None, "name": "cv.pdf"},  # other folder: free
            ]
        },
    )
    found = {(c["name"], c["type"]) for c in res.json()}
    assert found == {("cv.pdf", "file"), ("Scans", "folder")}
    assert next(c for c in res.json() if c["type"] == "file")["size_bytes"] == 9
    # someone else's names never show up
    assert (
        await bob.post("/uploads/conflicts", json={"items": [{"folder_id": docs["id"], "name": "cv.pdf"}]})
    ).json() == []

    # replace: the new version takes the name, the old one waits in the trash
    res = await alice.post(
        "/uploads", json={"folder_id": docs["id"], "name": "cv.pdf", "size": 9, "on_conflict": "replace"}
    )
    body = res.json()
    assert body["file"]["name"] == "cv.pdf"
    bucket.put(body["upload_url"], b"version 2")
    await alice.post(f"/uploads/{body['file']['id']}/complete")
    assert bucket.objects[f"{me}/Docs/cv.pdf"] == b"version 2"
    trashed = [k for k in bucket.objects if k.startswith(f"_trash/{me}/")]
    assert len(trashed) == 1 and bucket.objects[trashed[0]] == b"version 1"
    files = (await alice.get(f"/drive/folders/{docs['id']}")).json()["files"]
    assert [f["name"] for f in files] == ["cv.pdf"]
    assert [f["name"] for f in (await alice.get("/drive/trash")).json()["files"]] == ["cv.pdf"]
