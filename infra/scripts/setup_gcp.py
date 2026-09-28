"""Prepare the bucket for Personal Cloud. Idempotent: run it as often as you like.

    make setup-gcp

- checks the bucket exists and is reachable with your credentials
- turns on uniform bucket-level access and enforces public access prevention, so no
  object can ever be made public
- sets CORS so browsers on your addresses (WEB_BASE_URL, localhost, EXTRA_ORIGINS) can
  upload straight to the bucket

It never creates or deletes a bucket and never touches objects.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from google.api_core import exceptions as gexc  # noqa: E402
from google.auth.exceptions import DefaultCredentialsError  # noqa: E402
from google.cloud import storage  # noqa: E402

import app.adapters.storage  # noqa: E402,F401  (quiets the gcloud-login warning)
from app.core.config import get_settings  # noqa: E402


def main() -> int:
    settings = get_settings()
    if not settings.gcs_bucket or not settings.gcp_project_id:
        print("[setup-gcp] set GCP_PROJECT_ID and GCS_BUCKET in .env first", file=sys.stderr)
        return 1
    print(f"[setup-gcp] bucket gs://{settings.gcs_bucket} in project {settings.gcp_project_id}")
    try:
        client = storage.Client(project=settings.gcp_project_id)
        bucket = client.get_bucket(settings.gcs_bucket, timeout=15)
    except DefaultCredentialsError:
        print(
            "  FAIL  no Google credentials on this machine\n        → gcloud auth application-default login",
            file=sys.stderr,
        )
        return 1
    except gexc.NotFound:
        print(
            f"  FAIL  bucket {settings.gcs_bucket} does not exist\n"
            f"        → create it (README step 3) or fix GCS_BUCKET in .env",
            file=sys.stderr,
        )
        return 1
    except gexc.Forbidden as exc:
        print(
            f"  FAIL  no access to the bucket: {exc.message}\n"
            "        → your account needs Storage Admin on the bucket for setup",
            file=sys.stderr,
        )
        return 1

    changed = []
    iam = bucket.iam_configuration
    if not iam.uniform_bucket_level_access_enabled:
        iam.uniform_bucket_level_access_enabled = True
        changed.append("uniform bucket-level access on")
    if iam.public_access_prevention != "enforced":
        iam.public_access_prevention = "enforced"
        changed.append("public access prevention enforced")

    origins = settings.allowed_origins
    cors = [
        {
            "origin": origins,
            "method": ["PUT", "POST", "GET", "HEAD", "OPTIONS"],
            "responseHeader": ["Content-Type", "Content-Range", "Range", "x-goog-resumable", "Location"],
            "maxAgeSeconds": 3600,
        }
    ]
    if bucket.cors != cors:
        bucket.cors = cors
        changed.append("CORS updated")

    if changed:
        try:
            bucket.patch(timeout=30)
        except gexc.Forbidden as exc:
            print(
                f"  FAIL  could not update the bucket: {exc.message}\n"
                "        → your account needs Storage Admin (storage.buckets.update) on it",
                file=sys.stderr,
            )
            return 1
    print("  ok    private: uniform access on, public access prevention enforced")
    print(f"  ok    browser uploads allowed from: {', '.join(origins)}")
    print(f"  ok    location {bucket.location}, storage class {bucket.storage_class}")
    print(f"[setup-gcp] {'; '.join(changed) if changed else 'already set up, nothing changed'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
