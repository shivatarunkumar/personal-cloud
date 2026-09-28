"""Google Cloud Storage: the only place file bytes live.

Every call here is blocking (the google-cloud-storage client is synchronous); services run
them in a thread. Nothing in this module is called while listing folders: bytes are only
read when someone opens, plays or downloads a file.
"""

from __future__ import annotations

import logging
import time
import warnings
from collections.abc import Iterator
from functools import lru_cache

from google.cloud import storage

from app.core.config import Settings, get_settings

log = logging.getLogger("cloud.storage")

# Using your own gcloud login is the intended setup here, so the library's warning about
# "end user credentials" is noise on every command.
warnings.filterwarnings("ignore", message="Your application has authenticated using end user credentials")

DOWNLOAD_CHUNK_BYTES = 1024 * 1024


class StorageNotConfigured(RuntimeError):
    pass


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


class GcsStorage:
    def __init__(self, settings: Settings) -> None:
        if not settings.gcs_bucket:
            raise StorageNotConfigured("GCS_BUCKET is not set in .env")
        self._client = storage.Client(project=settings.gcp_project_id or None)
        self.bucket_name = settings.gcs_bucket

    @property
    def bucket(self) -> storage.Bucket:
        return self._client.bucket(self.bucket_name)

    def bucket_exists(self) -> bool:
        started = time.perf_counter()
        found = self._client.lookup_bucket(self.bucket_name, timeout=5) is not None
        log.debug("  gcs lookup bucket %s: %s (%dms)", self.bucket_name, found, _ms(started))
        return found

    def resumable_session(
        self, key: str, content_type: str, size: int, origin: str, metadata: dict[str, str]
    ) -> str:
        """Start a resumable upload for one object and return its session URL.

        The browser PUTs the file to that URL in chunks, so file bytes never pass through
        the API. The URL carries its own authorisation, only writes this one object, and
        answers CORS for `origin`, the page that will send the chunks.
        """
        started = time.perf_counter()
        blob = self.bucket.blob(key)
        blob.metadata = metadata
        url = blob.create_resumable_upload_session(
            content_type=content_type, size=size, origin=origin, timeout=30
        )
        log.debug("  gcs resumable session %s (%d bytes, origin %s) in %dms", key, size, origin, _ms(started))
        return url

    def write_empty(self, key: str, content_type: str, metadata: dict[str, str]) -> None:
        """A zero-byte file: nothing for the browser to send, so it is written here."""
        blob = self.bucket.blob(key)
        blob.metadata = metadata
        blob.upload_from_string(b"", content_type=content_type, timeout=15)

    def stat(self, key: str) -> int | None:
        """The object's size, or None when it doesn't exist (yet)."""
        started = time.perf_counter()
        blob = self.bucket.get_blob(key, timeout=15)
        log.debug("  gcs stat %s: %s (%dms)", key, blob.size if blob else "missing", _ms(started))
        return None if blob is None else (blob.size or 0)

    def read_range(self, key: str, start: int, end: int) -> Iterator[bytes]:
        """Yield bytes [start, end] in 1 MB pieces: only what the browser asked for."""
        log.debug("  gcs read %s bytes %d-%d", key, start, end)
        blob = self.bucket.blob(key)
        position = start
        while position <= end:
            last = min(position + DOWNLOAD_CHUNK_BYTES - 1, end)
            chunk = blob.download_as_bytes(start=position, end=last, raw_download=True, timeout=60)
            if not chunk:
                return
            yield chunk
            position += len(chunk)

    def copy(self, source_key: str, target_key: str, metadata: dict[str, str]) -> None:
        """Server-side copy: the bytes never leave Google."""
        started = time.perf_counter()
        source = self.bucket.blob(source_key)
        target = self.bucket.blob(target_key)
        target.metadata = metadata
        token, _, _ = target.rewrite(source, timeout=120)
        while token is not None:  # big objects are copied in several calls
            token, _, _ = target.rewrite(source, token=token, timeout=120)
        log.debug("  gcs copy %s → %s in %dms", source_key, target_key, _ms(started))

    def delete(self, key: str) -> None:
        try:
            self.bucket.blob(key).delete(timeout=30)
            log.debug("  gcs delete %s", key)
        except Exception as exc:  # already gone is fine
            if getattr(exc, "code", None) == 404:
                log.debug("  gcs delete %s: already gone", key)
                return
            raise

    def delete_prefix(self, prefix: str) -> int:
        """Delete every object under a prefix (a whole account). Returns how many."""
        deleted = 0
        batch: list[storage.Blob] = []
        for blob in self._client.list_blobs(self.bucket_name, prefix=prefix):
            batch.append(blob)
            if len(batch) == 100:
                self.bucket.delete_blobs(batch, on_error=lambda _blob: None)
                deleted += len(batch)
                batch = []
        if batch:
            self.bucket.delete_blobs(batch, on_error=lambda _blob: None)
            deleted += len(batch)
        log.info("deleted %d object(s) under %s", deleted, prefix)
        return deleted

    def list_objects(self, prefix: str) -> Iterator[storage.Blob]:
        """Object listings (names and metadata, never bytes), for reindexing."""
        yield from self._client.list_blobs(self.bucket_name, prefix=prefix)

    def probe(self, key: str) -> None:
        """Write, read back and delete a tiny object: proves credentials and permissions."""
        blob = self.bucket.blob(key)
        blob.upload_from_string(b"personal-cloud probe", content_type="text/plain", timeout=15)
        blob.download_as_bytes(timeout=15)
        blob.delete(timeout=15)


@lru_cache
def get_storage() -> GcsStorage:
    return GcsStorage(get_settings())
