"""Pure functions: no database, no bucket."""

import pytest

from app.api.v1.files import MAX_OPEN_RANGE, disposition, parse_range
from app.core.config import Settings, origin_of
from app.services import kinds
from app.services.drive import clean_name, free_name
from app.services.errors import ServiceError


def settings(**overrides) -> Settings:
    base = {"jwt_secret": "x" * 32, "gcs_bucket": "b", "gcp_project_id": "p"}
    return Settings(_env_file=None, **{**base, **overrides})


class TestRanges:
    def test_no_header_is_whole_file(self):
        assert parse_range(None, 1000) is None

    def test_closed_range(self):
        assert parse_range("bytes=100-199", 1000) == (100, 199)

    def test_end_is_clamped_to_size(self):
        assert parse_range("bytes=900-5000", 1000) == (900, 999)

    def test_open_range_is_capped(self):
        size = MAX_OPEN_RANGE * 3
        assert parse_range("bytes=0-", size) == (0, MAX_OPEN_RANGE - 1)

    def test_suffix_range(self):
        assert parse_range("bytes=-10", 1000) == (990, 999)

    def test_past_the_end_is_unsatisfiable(self):
        with pytest.raises(ValueError):
            parse_range("bytes=1000-", 1000)

    def test_multiple_ranges_fall_back_to_whole_file(self):
        assert parse_range("bytes=0-1,5-6", 1000) is None


class TestNames:
    def test_free_name_keeps_extension_last(self):
        assert free_name("photo.jpg", {"photo.jpg"}, is_file=True) == "photo (1).jpg"

    def test_free_name_counts_on(self):
        assert free_name("photo (1).jpg", {"photo (1).jpg", "photo (2).jpg"}, is_file=True) == "photo (3).jpg"

    def test_free_name_is_case_insensitive(self):
        assert free_name("Photo.JPG", {"photo.jpg"}, is_file=True) == "Photo (1).JPG"

    def test_folders_have_no_extension(self):
        assert free_name("v1.2", {"v1.2"}, is_file=False) == "v1.2 (1)"

    def test_dotfile_keeps_its_name(self):
        assert free_name(".env", {".env"}, is_file=True) == ".env (1)"

    @pytest.mark.parametrize("bad", ["", "  ", ".", "..", "a/b", "x" * 256])
    def test_clean_name_rejects(self, bad):
        with pytest.raises(ServiceError):
            clean_name(bad)

    def test_clean_name_trims(self):
        assert clean_name("  Holiday  ") == "Holiday"

    def test_content_disposition_is_safe_for_any_name(self):
        header = disposition("attachment", 'naïve "quote".txt')
        assert header.startswith('attachment; filename="nave quote.txt"')
        assert "filename*=UTF-8''na%C3%AFve%20%22quote%22.txt" in header


class TestKinds:
    @pytest.mark.parametrize(
        ("name", "sent", "kind"),
        [
            ("IMG_0001.HEIC", "", "image"),
            ("clip.mov", "application/octet-stream", "video"),
            ("film.mkv", None, "video"),
            ("song.mp3", "audio/mpeg", "audio"),
            ("report.pdf", "", "pdf"),
            ("notes.md", "", "text"),
            ("backup.zip", "", "archive"),
            ("budget.xlsx", "", "document"),
            ("mystery.bin", "", "other"),
        ],
    )
    def test_kind(self, name, sent, kind):
        assert kinds.kind_for(name, kinds.content_type_for(name, sent)) == kind

    def test_active_content_never_inline(self):
        assert not kinds.safe_inline("text/html")
        assert not kinds.safe_inline("image/svg+xml")
        assert kinds.safe_inline("image/jpeg")


class TestOrigins:
    def test_origin_keeps_port_and_lowercases(self):
        assert origin_of("http://MyCloud.com:3002/drive") == "http://mycloud.com:3002"

    def test_default_port_dropped(self):
        assert origin_of("http://mycloud.com:80") == "http://mycloud.com"

    def test_allowed_origins_from_base_url(self):
        s = settings(
            web_base_url="http://mycloud.com:3002",
            web_host_port=3002,
            extra_origins="http://192.168.1.5:3002, ",
        )
        assert s.allowed_origins == [
            "http://mycloud.com:3002",
            "http://localhost:3002",
            "http://127.0.0.1:3002",
            "http://192.168.1.5:3002",
        ]

    def test_prefix_normalised(self):
        assert settings(gcs_prefix="/cloud").gcs_prefix == "cloud/"

    def test_missing_secret_is_reported(self):
        assert any("JWT_SECRET" in p for p in settings(jwt_secret="").problems())
