"""Product image processing (R5 2026-06-22).

Sub-package for image upload concerns: thumbnail generation, format
normalization, size validation. Kept inside `domains.masterdata` so the
boundary "anything touching Product images" lives in one place.
"""

from domains.masterdata.images.thumbnails import (
    ImageProcessingError,
    UnsupportedImageFormatError,
    generate_thumbnails,
)

__all__ = [
    "ImageProcessingError",
    "UnsupportedImageFormatError",
    "generate_thumbnails",
]
