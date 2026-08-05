"""Controlled document assets: logos, marketing figures and fonts.

Two hard rules:

* the renderer never fetches a user-supplied URL; only files inside a
  configured asset repository may be referenced, by *name*, not by path;
* no proprietary font file is committed to this repository. Font paths are
  configuration, and an unavailable font degrades gracefully to the built-in
  Western font instead of failing document generation.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

LOGGER = logging.getLogger(__name__)

#: Where approved images may live. Defaults to a repository-local directory.
ASSET_ROOT_ENV = "DOCUMENT_ASSET_ROOT"
LOGO_ASSET_ENV = "DOCUMENT_LOGO_ASSET"
FONT_REGULAR_ENV = "DOCUMENT_FONT_REGULAR_PATH"
FONT_BOLD_ENV = "DOCUMENT_FONT_BOLD_PATH"
FONT_FAMILY_ENV = "DOCUMENT_FONT_FAMILY"

DEFAULT_ASSET_ROOT = Path("assets/documents")
ALLOWED_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".svg"})
ALLOWED_FONT_SUFFIXES = frozenset({".ttf", ".otf"})
MAX_ASSET_BYTES = 5 * 1024 * 1024

_ASSET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

#: Fallback family always available in the deterministic PDF engine.
FALLBACK_FONT_FAMILY = "Helvetica"


class AssetError(ValueError):
    """Raised when an asset reference is not acceptable."""


@dataclass(frozen=True)
class FontConfiguration:
    """Resolved font configuration. Never ships a font file itself."""

    family: str = FALLBACK_FONT_FAMILY
    regular_path: Path | None = None
    bold_path: Path | None = None
    fallback_used: bool = True
    fallback_reason: str = "No configured font; using the built-in font."

    @property
    def supports_non_western(self) -> bool:
        return self.regular_path is not None

    def describe(self) -> dict[str, object]:
        return {
            "family": self.family,
            "regular_configured": self.regular_path is not None,
            "bold_configured": self.bold_path is not None,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
        }


def asset_root(environment: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environment is None else environment
    raw = (values.get(ASSET_ROOT_ENV) or "").strip()
    root = Path(raw) if raw else DEFAULT_ASSET_ROOT
    return root.expanduser()


def resolve_asset(
    name: str | None,
    *,
    environment: Mapping[str, str] | None = None,
) -> Path | None:
    """Resolve an approved asset *name* to a file inside the asset root.

    ``name`` is a bare file name. Directory separators, parent references,
    absolute paths and URLs are rejected outright, so a caller can never make
    the renderer read an arbitrary file.
    """

    if not name:
        return None
    candidate = str(name).strip()
    if not _ASSET_NAME_RE.match(candidate):
        raise AssetError(
            "An asset reference must be a simple approved file name."
        )
    suffix = Path(candidate).suffix.casefold()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise AssetError(f"Unsupported asset type: {suffix or 'unknown'}")
    root = asset_root(environment)
    try:
        resolved_root = root.resolve()
        resolved = (resolved_root / candidate).resolve()
    except OSError as error:  # pragma: no cover - filesystem dependent
        raise AssetError("The asset repository is unavailable.") from error
    if resolved.parent != resolved_root:
        raise AssetError("Assets must live directly in the asset repository.")
    if not resolved.is_file():
        LOGGER.info("Approved asset is not present: %s", candidate)
        return None
    if resolved.stat().st_size > MAX_ASSET_BYTES:
        raise AssetError("The approved asset exceeds the permitted size.")
    return resolved


def resolve_logo(
    name: str | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> Path | None:
    """Resolve the branded logo placeholder, if one is configured."""

    values = os.environ if environment is None else environment
    reference = name or (values.get(LOGO_ASSET_ENV) or "").strip()
    if not reference:
        return None
    try:
        return resolve_asset(reference, environment=values)
    except AssetError as error:
        LOGGER.info("Configured logo cannot be used: %s", error)
        return None


def load_font_configuration(
    environment: Mapping[str, str] | None = None,
) -> FontConfiguration:
    """Read configurable font paths and degrade gracefully when absent."""

    values = os.environ if environment is None else environment
    family = (values.get(FONT_FAMILY_ENV) or "").strip() or FALLBACK_FONT_FAMILY
    regular = _font_path(values.get(FONT_REGULAR_ENV))
    if regular is None:
        return FontConfiguration(
            family=FALLBACK_FONT_FAMILY,
            fallback_used=True,
            fallback_reason=(
                "No embeddable font is configured; non-Western characters are "
                "rendered with the built-in font and may be substituted."
            ),
        )
    bold = _font_path(values.get(FONT_BOLD_ENV)) or regular
    return FontConfiguration(
        family=family,
        regular_path=regular,
        bold_path=bold,
        fallback_used=False,
        fallback_reason="",
    )


def _font_path(raw: str | None) -> Path | None:
    if not raw or not raw.strip():
        return None
    path = Path(raw.strip()).expanduser()
    if path.suffix.casefold() not in ALLOWED_FONT_SUFFIXES:
        LOGGER.info("Configured font has an unsupported type: %s", path.suffix)
        return None
    if not path.is_file():
        LOGGER.info("Configured font file is unavailable.")
        return None
    return path
