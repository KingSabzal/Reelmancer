"""Compatibility shims applied before MoviePy is imported.

MoviePy 1.0.3 was written against Pillow 9. Pillow 10 removed the long-deprecated
constants, so on a machine without OpenCV installed MoviePy falls back to its Pillow
resizer and crashes with:

    AttributeError: module 'PIL.Image' has no attribute 'ANTIALIAS'

The crash only appears when OpenCV is absent, which is why it shows up on a clean
Windows install but not on machines that happen to have opencv-python.

This module restores the removed aliases on the Pillow module itself, so any library
that still references them keeps working. It must be imported before moviepy.
"""

from __future__ import annotations

import logging

LOGGER = logging.getLogger("compat")
if not LOGGER.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    LOGGER.addHandler(_h)
LOGGER.setLevel(logging.INFO)

_APPLIED = False

# Old Pillow constant -> new Resampling member.
_RESAMPLING_ALIASES = {
    "NEAREST": "NEAREST",
    "BOX": "BOX",
    "BILINEAR": "BILINEAR",
    "HAMMING": "HAMMING",
    "BICUBIC": "BICUBIC",
    "LANCZOS": "LANCZOS",
    "ANTIALIAS": "LANCZOS",  # ANTIALIAS was an alias for LANCZOS before Pillow 10
}

_OTHER_ALIASES = {
    "Transpose": ["FLIP_LEFT_RIGHT", "FLIP_TOP_BOTTOM", "ROTATE_90", "ROTATE_180",
                   "ROTATE_270", "TRANSPOSE", "TRANSVERSE"],
    "Transform": ["AFFINE", "EXTENT", "PERSPECTIVE", "QUAD", "MESH"],
    "Dither": ["NONE", "ORDERED", "RASTERIZE", "FLOYDSTEINBERG"],
    "Palette": ["WEB", "ADAPTIVE"],
    "Quantize": ["MEDIANCUT", "MAXCOVERAGE", "FASTOCTREE", "LIBIMAGEQUANT"],
}


def patch_pillow() -> bool:
    """Restore the Pillow constants removed in version 10. Returns True if patched."""
    global _APPLIED
    if _APPLIED:
        return True
    try:
        from PIL import Image
    except ImportError:
        return False

    patched_any = False

    resampling = getattr(Image, "Resampling", None)
    for legacy_name, modern_name in _RESAMPLING_ALIASES.items():
        if hasattr(Image, legacy_name):
            continue
        value = None
        if resampling is not None and hasattr(resampling, modern_name):
            value = getattr(resampling, modern_name)
        elif hasattr(Image, modern_name):
            value = getattr(Image, modern_name)
        if value is not None:
            setattr(Image, legacy_name, value)
            patched_any = True

    for enum_name, members in _OTHER_ALIASES.items():
        enum_class = getattr(Image, enum_name, None)
        if enum_class is None:
            continue
        for member in members:
            if not hasattr(Image, member) and hasattr(enum_class, member):
                setattr(Image, member, getattr(enum_class, member))
                patched_any = True

    _APPLIED = True
    if patched_any:
        try:
            import PIL

            LOGGER.info(
                "Applied Pillow %s compatibility shim (ANTIALIAS -> LANCZOS) for MoviePy 1.0.3.",
                PIL.__version__,
            )
        except Exception:  # noqa: BLE001
            pass
    return patched_any


def resizer_backend() -> str:
    """Return which resizing backend MoviePy will use: cv2, PIL or Scipy."""
    try:
        from moviepy.video.fx.resize import resizer

        return getattr(resizer, "origin", "unknown")
    except Exception:  # noqa: BLE001
        return "unavailable"


def apply_all() -> None:
    """Apply every compatibility shim. Safe to call repeatedly."""
    patch_pillow()


# Apply on import so a plain `import compat` is enough.
apply_all()
