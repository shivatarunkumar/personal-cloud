"""Check Google Cloud without starting the API: one line per check, and the fix.

    make check-gcp                  # includes a write/read/delete probe object
    make check-gcp ARGS=--no-write  # read-only checks

Exits non-zero when anything fails.
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from google.api_core import exceptions as gexc  # noqa: E402
from google.auth.exceptions import DefaultCredentialsError, RefreshError  # noqa: E402

import app.adapters.storage  # noqa: E402,F401  (quiets the gcloud-login warning)
from app.core.config import get_settings  # noqa: E402

failures = 0


def report(ok: bool | None, name: str, detail: str, fix: str = "") -> None:
    global failures
    label = {True: "ok  ", False: "FAIL", None: "warn"}[ok]
    print(f"  {label}  {name:<12} {detail}")
    if ok is False:
        failures += 1
    if fix and ok is not True:
        print(f"  {'':<18}→ {fix}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-write", action="store_true", help="skip the write/delete probe")
    args = parser.parse_args()
    settings = get_settings()
    print("[check-gcp] checking configuration, credentials and the bucket\n")

    if not settings.gcp_project_id or not settings.gcs_bucket:
        report(False, "config", "GCP_PROJECT_ID or GCS_BUCKET is empty", "fill them in .env")
        return 1
    report(
        True,
        "config",
        f"project={settings.gcp_project_id} bucket={settings.gcs_bucket}"
        + (f" prefix={settings.gcs_prefix}" if settings.gcs_prefix else ""),
    )

    try:
        socket.create_connection(("storage.googleapis.com", 443), timeout=5).close()
        report(True, "network", "reached storage.googleapis.com:443")
    except OSError as exc:
        report(
            False,
            "network",
            f"cannot reach storage.googleapis.com: {exc}",
            "check the internet connection / proxy",
        )
        return 1

    try:
        import google.auth

        credentials, project = google.auth.default()
        who = getattr(credentials, "service_account_email", None) or "your gcloud login"
        report(True, "credentials", f"{who}" + (f" (default project {project})" if project else ""))
    except DefaultCredentialsError:
        report(
            False,
            "credentials",
            "no Application Default Credentials",
            "gcloud auth application-default login",
        )
        return 1

    from app.adapters.storage import get_storage

    storage = get_storage()
    try:
        bucket = storage._client.get_bucket(settings.gcs_bucket, timeout=15)
    except RefreshError:
        report(False, "bucket", "credentials expired", "gcloud auth application-default login")
        return 1
    except gexc.NotFound:
        report(False, "bucket", f"{settings.gcs_bucket} does not exist", "create it (README step 3)")
        return 1
    except gexc.Forbidden as exc:
        report(
            False, "bucket", f"access denied: {exc.message}", "grant your account Storage Object Admin on it"
        )
        return 1
    report(True, "bucket", f"{bucket.location} {bucket.storage_class}")

    iam = bucket.iam_configuration
    private = iam.uniform_bucket_level_access_enabled and iam.public_access_prevention == "enforced"
    report(
        True if private else None,
        "private",
        "public access prevention enforced" if private else "public access prevention is not enforced",
        "make setup-gcp",
    )

    origins = set(settings.allowed_origins)
    configured = {o for rule in (bucket.cors or []) for o in rule.get("origin", [])}
    missing = sorted(origins - configured)
    report(
        True if not missing else None,
        "cors",
        "uploads allowed from all your addresses"
        if not missing
        else f"not allowed yet: {', '.join(missing)}",
        "make setup-gcp",
    )

    if args.no_write:
        report(None, "write", "skipped (--no-write)")
    else:
        try:
            storage.probe(f"{settings.gcs_prefix}_probe/check-gcp.txt")
            report(True, "write", "wrote, read and deleted a probe object")
        except gexc.Forbidden as exc:
            report(
                False,
                "write",
                f"denied: {exc.message}",
                "grant your account Storage Object Admin on the bucket",
            )

    print()
    print("[check-gcp] all good" if not failures else f"[check-gcp] {failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
