"""Pure-function tests for `domains.masterdata.images.thumbnails`.

We test:
    1. The happy path produces 3 distinct JPEG byte blobs of the
       expected shape.
    2. Aspect ratio is preserved (no squashing of landscape vs portrait).
    3. EXIF rotation is honored (the canonical iPhone-photo footgun).
    4. Alpha channels are flattened against a white background (don't
       silently turn semi-transparent PNGs black).
    5. Unsupported formats raise a typed exception with a user-facing
       message — the message is the only thing the user ever sees.
    6. Output sizes are bounded by the requested max side, not the
       arbitrary other side.

Test images are constructed programmatically with Pillow rather than
committed as fixtures so the suite stays self-contained and the inputs
are deterministic across machines.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from domains.masterdata.images.thumbnails import (
    ImageProcessingError,
    ProcessedImageBundle,
    UnsupportedImageFormatError,
    generate_thumbnails,
)


# ─── Fixtures ─────────────────────────────────────────────────


def _synth_jpeg(size: tuple[int, int] = (1200, 800), color: tuple[int, int, int] = (200, 100, 50)) -> bytes:
    """A solid-colored JPEG of the requested pixel dimensions."""
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _synth_png(size: tuple[int, int] = (600, 400)) -> bytes:
    img = Image.new("RGB", size, (50, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _synth_png_with_alpha(size: tuple[int, int] = (300, 300)) -> bytes:
    """Semi-transparent PNG to verify the alpha-flatten path."""
    img = Image.new("RGBA", size, (255, 0, 0, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _synth_jpeg_with_exif_rotation() -> bytes:
    """JPEG marked Orientation=6 (rotate 270 CCW). Decodes physically as
    landscape (200x100) but renders as portrait (100x200) once EXIF is
    applied — same trick iPhones pull.
    """
    img = Image.new("RGB", (200, 100), (10, 10, 10))
    # Bake an EXIF tag for orientation = 6.
    exif = img.getexif()
    exif[0x0112] = 6  # Orientation
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


# ─── Happy path ───────────────────────────────────────────────


def test_generate_thumbnails_returns_three_blobs_for_a_jpeg():
    """Baseline contract: feed it a JPEG, get back the bundle with three
    non-empty JPEG byte blobs."""
    out = generate_thumbnails(_synth_jpeg())
    assert isinstance(out, ProcessedImageBundle)
    for blob in (out.full, out.medium, out.thumb):
        assert isinstance(blob, bytes)
        assert len(blob) > 0
        # JPEG magic bytes (start-of-image marker).
        assert blob[:2] == b"\xff\xd8", "expected JPEG SOI marker"


def test_thumb_is_smaller_than_medium_is_smaller_than_full():
    """File-size hierarchy isn't 100% guaranteed for tiny inputs (JPEG
    overhead can dominate), but for a real 1200x800 JPEG the bundle
    should respect intuition."""
    out = generate_thumbnails(_synth_jpeg(size=(1200, 800)))
    assert len(out.thumb) < len(out.medium) < len(out.full), (
        f"size order broken: thumb={len(out.thumb)} medium={len(out.medium)} "
        f"full={len(out.full)}"
    )


def test_thumb_dimensions_capped_at_thumb_px():
    """`generate_thumbnails(thumb_px=80)` must produce a thumb whose
    longest side ≤ 80 px. Aspect ratio preserved means the *other* side
    is proportionally smaller."""
    out = generate_thumbnails(_synth_jpeg(size=(1200, 800)), thumb_px=80)
    thumb_img = Image.open(io.BytesIO(out.thumb))
    assert max(thumb_img.size) == 80
    # 1200x800 → 80x53 (round). Aspect ratio check within rounding tolerance.
    assert abs(thumb_img.size[0] / thumb_img.size[1] - 1200 / 800) < 0.1


def test_medium_dimensions_capped_at_medium_px():
    out = generate_thumbnails(_synth_jpeg(size=(2000, 1000)), medium_px=400)
    med_img = Image.open(io.BytesIO(out.medium))
    assert max(med_img.size) == 400
    assert med_img.size == (400, 200)


def test_portrait_image_thumb_is_taller_than_wide():
    """Aspect ratio preservation for portrait input — 800x1200 should
    produce 53x80 thumb, not the other way around."""
    out = generate_thumbnails(_synth_jpeg(size=(800, 1200)), thumb_px=80)
    w, h = Image.open(io.BytesIO(out.thumb)).size
    assert h > w, f"portrait thumb should stay portrait, got {w}x{h}"
    assert h == 80


# ─── EXIF rotation (iPhone footgun) ────────────────────────────


def test_exif_rotation_is_applied():
    """A 200x100 JPEG with Orientation=6 should land at 100x200 in the
    output (the rotated form). If `exif_transpose` weren't called, we'd
    get 200x100. This is THE test that catches the iPhone bug."""
    out = generate_thumbnails(_synth_jpeg_with_exif_rotation())
    w, h = Image.open(io.BytesIO(out.full)).size
    # Orientation=6 = 90° CW physical→logical, so 200x100 → 100x200.
    assert (w, h) == (100, 200), (
        f"EXIF rotation not applied: expected 100x200, got {w}x{h}"
    )


# ─── Format coverage ──────────────────────────────────────────


def test_png_input_works():
    """PNG should be accepted and re-encoded as JPEG."""
    out = generate_thumbnails(_synth_png())
    # All outputs are JPEG regardless of input format.
    assert out.full[:2] == b"\xff\xd8"


def test_png_with_alpha_flattened_against_white():
    """Alpha channel must be composited against white. Without that
    handling, semi-transparent PNG → black-backed JPEG, which is ugly
    and surprises users.

    Source pixel is RGBA(255, 0, 0, 128). Over white the math is:
        out = src * (alpha/255) + bg * (1 - alpha/255)
    Channel-by-channel:
        R: 255*0.5 + 255*0.5 = 255  (stays high)
        G: 0*0.5   + 255*0.5 ≈ 127  ← the diagnostic channel
        B: 0*0.5   + 255*0.5 ≈ 127
    If alpha is dropped and the RGB pixel is taken as-is (the bug we're
    catching), G would land at 0 → image looks black-and-red. We assert
    G is the mid-grey we expect."""
    out = generate_thumbnails(_synth_png_with_alpha())
    img = Image.open(io.BytesIO(out.full))
    _r, g, _b = img.getpixel((10, 10))
    # 50% blend of (G=0) and (G=255) ≈ 127. JPEG quant + chroma sub-
    # sampling can drift; bound loosely either way.
    assert 100 < g < 155, (
        f"alpha not flattened over white: pixel G={g} (expected ~127); "
        "if G≈0 the alpha channel was dropped instead of composited."
    )


# ─── Error paths ──────────────────────────────────────────────


def test_unsupported_format_raises_user_friendly_error():
    """Garbage bytes that aren't an image should raise a typed
    exception whose message mentions JPG/PNG/WebP and HEIC by name."""
    with pytest.raises(UnsupportedImageFormatError) as exc:
        generate_thumbnails(b"this is not an image at all, just text bytes")
    msg = str(exc.value)
    assert "JPG" in msg or "PNG" in msg, (
        "error message should guide user toward supported formats"
    )
    assert "HEIC" in msg, "error should mention HEIC (the common iPhone case)"


def test_empty_bytes_raises():
    with pytest.raises(ImageProcessingError):
        generate_thumbnails(b"")


def test_truncated_jpeg_raises_processing_error():
    """A JPEG header with the body chopped off should fail cleanly, not
    crash inside Pillow."""
    full = _synth_jpeg()
    truncated = full[:50]  # not enough to be a valid image
    with pytest.raises(ImageProcessingError):
        generate_thumbnails(truncated)
