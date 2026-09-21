"""Image validation and WebP variants. Pure CPU work on bytes: no I/O, no database.

- The real format comes from the file header (Pillow), never from the declared MIME type.
- Pixel count is checked before decoding, so a small file that expands into a huge bitmap
  (decompression bomb) is refused cheaply.
- JPEGs are decoded at a reduced DCT scale (`draft`) when the largest variant is smaller than
  the source: a 24 MP photo decodes at ~6 MP, which keeps the worker's memory flat.
- Orientation from EXIF is applied, then all metadata except the ICC profile is dropped
  (EXIF can carry GPS coordinates of the seller's home).
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
ALLOWED_MIME_TYPES = frozenset(ALLOWED_FORMATS.values())
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 24_000_000
# Longest side of each variant; the first one is the stored "original".
VARIANT_WIDTHS = (2400, 1200, 600, 320)
WEBP_QUALITY = 82


class InvalidImageError(Exception):
    """The upload is not an image we accept. The message is safe to show to the tenant."""


@dataclass(frozen=True, slots=True)
class Rendition:
    name: str  # "orig" (largest, ≤2400 px), then "w1200", "w600", "w320"
    width: int
    height: int
    data: bytes


@dataclass(frozen=True, slots=True)
class ProcessedImage:
    source_format: str
    width: int  # of the largest rendition
    height: int
    checksum_sha256: str  # of the uploaded bytes
    renditions: list[Rendition]


def _fit(width: int, height: int, longest: int) -> tuple[int, int]:
    scale = min(1.0, longest / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def process_image(data: bytes) -> ProcessedImage:
    if not data:
        raise InvalidImageError("Arquivo vazio.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise InvalidImageError("Arquivo maior que 10 MB.")
    try:
        image = Image.open(io.BytesIO(data))
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidImageError("Arquivo não é uma imagem reconhecida.") from exc

    with image:
        if image.format not in ALLOWED_FORMATS:
            raise InvalidImageError("Formato não aceito; use JPEG, PNG ou WebP.")
        if image.width * image.height > MAX_PIXELS:
            raise InvalidImageError("Imagem com resolução acima de 24 megapixels.")
        source_format = str(image.format)
        icc_profile = image.info.get("icc_profile")
        if source_format == "JPEG":
            image.draft("RGB", _fit(image.width, image.height, VARIANT_WIDTHS[0]))
        try:
            image.seek(0)  # first frame of an animated PNG/WebP
            oriented = ImageOps.exif_transpose(image)
            has_alpha = oriented.mode in {"RGBA", "LA", "PA"} or (
                oriented.mode == "P" and "transparency" in oriented.info
            )
            base = oriented.convert("RGBA" if has_alpha else "RGB")
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise InvalidImageError("Imagem corrompida ou incompleta.") from exc

    renditions: list[Rendition] = []
    current = base
    for longest in VARIANT_WIDTHS:
        size = _fit(base.width, base.height, longest)
        if renditions and size[0] >= renditions[-1].width:
            continue  # source smaller than this step: the previous rendition already covers it
        if current.size != size:
            # Each step downsizes the previous one: cheaper than going from the source again.
            current = current.resize(size, Image.Resampling.LANCZOS)
        out = io.BytesIO()
        current.save(out, "WEBP", quality=WEBP_QUALITY, method=4, icc_profile=icc_profile)
        name = "orig" if not renditions else f"w{longest}"
        renditions.append(Rendition(name, size[0], size[1], out.getvalue()))

    largest = renditions[0]
    return ProcessedImage(
        source_format=source_format,
        width=largest.width,
        height=largest.height,
        checksum_sha256=hashlib.sha256(data).hexdigest(),
        renditions=renditions,
    )
