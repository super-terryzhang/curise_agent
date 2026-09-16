"""Pure image-processing utilities for product image uploads.

All functions take bytes in, return bytes out. No I/O, no DB, no
storage backend. Keeps the upload pipeline composable and easy to unit
test — the storage / DB orchestration lives in `_products_service.py`.

Design notes (long-term):
    - We DON'T support HEIC server-side. Adding `pillow-heif` would
      bring in libheif and put us in the murky HEVC patent zone. The
      frontend converts HEIC → JPEG client-side with `heic2any`
      (Phase 2); MVP rejects HEIC with a friendly error so the user
      knows what to do.
    - We DO call `ImageOps.exif_transpose` on every input. iPhone
      photos have the EXIF orientation set to "rotated 90°"; without
      this fix, every iPhone photo looks sideways in the UI.
    - We always re-encode to JPEG. Reasoning: JPEG is universally
      browser-supported, smaller than PNG for photo-style content,
      and gives us a single output format to test. PNG with
      transparency would matter if Felix imported product line-art
      (rare); revisit if needed.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

# Sentinel sizes chosen so:
#   THUMB_PX (80)  fits a table cell (32 → 2x retina for sharpness).
#   MEDIUM_PX (400) fits a 320 CSS-pixel gallery tile + retina headroom.
# Customizable per call but defaults reflect product decisions in the
# R5 design doc, not arbitrary numbers.
THUMB_PX = 80
MEDIUM_PX = 400


class ImageProcessingError(Exception):
    """Wraps everything that can go wrong when decoding/resizing.

    Service layer catches this and translates to a 400 BadRequest. Don't
    expose Pillow internals to the HTTP boundary.
    """


class UnsupportedImageFormatError(ImageProcessingError):
    """The bytes don't look like an image we know how to handle.

    Most common: HEIC from iPhones. The error message guides the user
    toward the right action ("convert to JPG first") rather than the
    technically accurate ("we don't link libheif").
    """


@dataclass(frozen=True)
class ProcessedImageBundle:
    """Three byte blobs ready to upload to storage."""

    full: bytes
    thumb: bytes
    medium: bytes


def generate_thumbnails(
    content: bytes,
    *,
    thumb_px: int = THUMB_PX,
    medium_px: int = MEDIUM_PX,
    quality_full: int = 92,
    quality_medium: int = 88,
    quality_thumb: int = 82,
) -> ProcessedImageBundle:
    """Decode `content`, return the three resized JPEG blobs.

    Sizing semantics: both dimensions are bounded so portrait + landscape
    images both fit in an `{N}x{N}` box without distortion. A 1200x800
    image → medium 400x267, thumb 80x53. This matches `<img object-fit:
    cover />` rendering on the frontend.

    Quality defaults tuned with `cwebp -mt -size <target>` style
    intuition: 92 for the canonical we'll show in lightbox, 88 for
    gallery thumbnails (a notch lower buys ~25% size cut, invisible at
    400px), 82 for the 80px cell thumbnail (any aliasing is dominated
    by the small render size).
    """
    # Pillow's `Image.open` is lazy — it only reads the header. Wrap
    # the rest in our own exception hierarchy so the service layer
    # doesn't have to know PIL exists.
    try:
        img = Image.open(io.BytesIO(content))
        img.load()  # Force decode now to surface format errors early.
    except UnidentifiedImageError as exc:
        # This is the path HEIC takes when libheif isn't installed.
        # We could try to sniff the magic bytes for FTYP/heic to give
        # an even more specific hint, but the generic message covers
        # all unsupported formats (TIFF subtypes, RAW, etc.).
        raise UnsupportedImageFormatError(
            "无法识别的图片格式。请使用 JPG / PNG / WebP；"
            "iPhone 拍的 HEIC 请先用截图工具或在线工具转 JPG。"
        ) from exc
    except Exception as exc:  # corrupted file, broken header, etc.
        raise ImageProcessingError(f"图片解析失败：{exc}") from exc

    # EXIF rotation: iPhone photos arrive with `Orientation=6` (rotated
    # CCW). Without this fix Felix's phone-uploaded shots show up
    # sideways in the gallery. `exif_transpose` is a no-op for images
    # whose pixels are already in the right orientation.
    img = ImageOps.exif_transpose(img)

    # JPEG can't store alpha or palette. Convert to RGB so the encoder
    # doesn't drop bands silently or fail on palette images (gifs).
    if img.mode not in ("RGB", "L"):
        # White background composite for alpha → opaque conversion.
        # Without this, semi-transparent PNGs end up black-backed.
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            background = Image.new("RGB", img.size, (255, 255, 255))
            rgba = img.convert("RGBA")
            background.paste(rgba, mask=rgba.split()[-1])
            img = background
        else:
            img = img.convert("RGB")

    return ProcessedImageBundle(
        full=_encode_jpeg(img, quality=quality_full),
        medium=_encode_jpeg(_thumb_copy(img, medium_px), quality=quality_medium),
        thumb=_encode_jpeg(_thumb_copy(img, thumb_px), quality=quality_thumb),
    )


def _thumb_copy(img: Image.Image, max_side: int) -> Image.Image:
    """Return an `img.copy()` scaled so the longest side is `max_side`.

    Uses `.thumbnail()` which preserves aspect ratio. `Image.LANCZOS` is
    the standard high-quality downsampling filter; `BICUBIC` would be
    faster but Felix's product photos warrant the quality.
    """
    copy = img.copy()
    copy.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return copy


def _encode_jpeg(img: Image.Image, *, quality: int) -> bytes:
    """Encode to JPEG with `optimize=True` (extra Huffman pass).

    `progressive=True` would help perceived load time over slow links
    by letting the image render in multiple passes; left off for MVP
    because most of our users are on office wifi and progressive adds
    ~5% file size for that benefit.
    """
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()
