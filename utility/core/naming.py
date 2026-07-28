"""Human-readable, filesystem-safe file names built from the YouTube upload title.

Rendered videos used to be stored under their internal UUID, which made the gallery
folder unreadable. Every exported file is now named after the title that will actually
be used when the video is uploaded to YouTube, with a hyphen between every word.

    "Why Cats Purr: 5 Reasons!"  ->  "Why-Cats-Purr-5-Reasons.mp4"

The slug keeps the original capitalisation, drops punctuation and emoji, and stays
within the limits of every common filesystem (Windows, macOS, Linux).
"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import Optional

# Windows caps a single path component at 255 characters, but long names are also
# painful to work with, so the slug itself is kept well below that.
MAX_SLUG_LENGTH = 120

# Reserved device names on Windows: a file called "CON.mp4" cannot be created.
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def slugify_title(title: str, fallback: str = "video") -> str:
    """Turn an upload title into a hyphen-separated, filesystem-safe file name stem.

    Accents are folded to their ASCII base letter, emoji and punctuation are dropped,
    and every remaining run of word characters is joined with a single hyphen.
    """
    text = unicodedata.normalize("NFKD", str(title or ""))
    # Drop combining marks so that "Cafe\u0301" becomes "Cafe" rather than "Caf".
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # Keep ASCII letters and digits only; everything else becomes a separator.
    words = re.findall(r"[A-Za-z0-9]+", text)
    slug = "-".join(words)

    if len(slug) > MAX_SLUG_LENGTH:
        slug = slug[:MAX_SLUG_LENGTH]
        # Never end on a half word: cut back to the last complete one.
        if "-" in slug:
            slug = slug.rsplit("-", 1)[0]

    slug = slug.strip("-")
    if not slug:
        slug = fallback
    if slug.upper() in _WINDOWS_RESERVED:
        slug = f"{slug}-video"
    return slug


def unique_path(directory: str, stem: str, extension: str) -> str:
    """Return a free path inside *directory*, adding "-2", "-3", ... on collision."""
    extension = extension if extension.startswith(".") else f".{extension}"
    candidate = os.path.join(directory, f"{stem}{extension}")
    if not os.path.exists(candidate):
        return candidate
    counter = 2
    while True:
        candidate = os.path.join(directory, f"{stem}-{counter}{extension}")
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def output_stem(youtube_title: str, topic: str = "", video_id: Optional[str] = None) -> str:
    """Pick the best available source for the file name stem.

    The YouTube package title wins. If the language model returned nothing usable the
    topic is tried next, and the internal id is the last resort so a render is never
    lost just because it could not be named.
    """
    for candidate in (youtube_title, topic):
        slug = slugify_title(candidate, fallback="")
        if slug:
            return slug
    return slugify_title(video_id or "", fallback="video")
