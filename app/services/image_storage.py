"""
Scan image persistence.

Uploaded scan photos are saved through a small backend abstraction so the call
sites (``/extract``) never care *where* bytes physically live. Today the only
active backend is :class:`LocalImageStorage` (a Docker-mounted volume); a
self-hosted S3-compatible store (e.g. MinIO) can be added later as another
backend without touching callers.

Images are sanitized on save: EXIF is stripped (privacy — phone photos carry GPS
and device metadata) and the image is downscaled and re-encoded to a bounded JPEG
so on-disk size stays predictable. Keys are date-partitioned with a UUID, e.g.
``2026/06/03/2f1c....jpg`` and stored in ``app.product_scan_summary.image_path``.
"""

from __future__ import annotations

import io
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import settings

logger = logging.getLogger(__name__)


_EXT_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/heic": ".heic",
    "image/heif": ".heif",
}


def _date_prefix() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}/{now.month:02d}/{now.day:02d}"


class ImageStorage(ABC):
    """Backend-agnostic interface for persisting and retrieving scan images."""

    @abstractmethod
    def save(self, image_bytes: bytes, content_type: str | None) -> str:
        """Persist image bytes and return the storage key (relative, POSIX-style)."""

    @abstractmethod
    def resolve(self, key: str) -> Path | None:
        """Return a readable filesystem path for ``key``, or None if unavailable/invalid."""

    def url_for(self, key: str) -> str:
        """Public URL the mobile app can use to fetch the image (relative path by default)."""
        base = settings.image_public_base_url.rstrip("/")
        return f"{base}/{key.lstrip('/')}"


class LocalImageStorage(ImageStorage):
    """Stores images on a local directory (mounted as a Docker named volume in prod)."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = (base_dir or settings.image_storage_dir).resolve()

    def _sanitize_to_jpeg(self, image_bytes: bytes) -> bytes | None:
        """Strip EXIF, fix orientation, downscale, and re-encode to JPEG.

        Returns None when the payload is not a decodable image (caller falls back
        to storing the raw bytes so nothing is silently dropped).
        """
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                img = ImageOps.exif_transpose(img)  # honor orientation before dropping EXIF
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                max_dim = max(64, int(settings.image_storage_max_dim))
                img.thumbnail((max_dim, max_dim), Image.LANCZOS)
                out = io.BytesIO()
                # A fresh save without exif= produces a metadata-stripped file.
                img.save(
                    out,
                    format="JPEG",
                    quality=int(settings.image_storage_jpeg_quality),
                    optimize=True,
                )
                return out.getvalue()
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            logger.warning("image_storage: could not decode image for sanitize: %s", exc)
            return None

    def save(self, image_bytes: bytes, content_type: str | None) -> str:
        if not image_bytes:
            raise ValueError("Empty image payload")

        sanitized = self._sanitize_to_jpeg(image_bytes)
        if sanitized is not None:
            data, ext = sanitized, ".jpg"
        else:
            # Keep the original bytes so failed/edge-case scans are still captured.
            data = image_bytes
            ext = _EXT_BY_CONTENT_TYPE.get((content_type or "").lower(), ".bin")

        key = f"{_date_prefix()}/{uuid.uuid4().hex}{ext}"
        target = self.base_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return key

    def resolve(self, key: str) -> Path | None:
        if not key:
            return None
        target = (self.base_dir / key).resolve()
        # Path-traversal guard: resolved path must stay inside base_dir.
        if target != self.base_dir and self.base_dir not in target.parents:
            logger.warning("image_storage: rejected out-of-root key=%r", key)
            return None
        return target


def _build_storage() -> ImageStorage:
    backend = (settings.image_storage_backend or "local").strip().lower()
    if backend != "local":
        logger.warning(
            "image_storage: backend %r not implemented; falling back to local", backend
        )
    return LocalImageStorage()


@lru_cache(maxsize=1)
def get_image_storage() -> ImageStorage:
    return _build_storage()
