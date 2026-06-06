"""Floor-plan image intake: validate + normalise uploads to a safe raster PNG.

Everything (PNG/JPG, or page 1 of a PDF) is normalised to a single PNG with
known pixel dimensions, so the front-end can position markers as a percentage
of the image regardless of the original format.

Safety limits (configurable):
  - FLOOR_PLAN_MAX_UPLOAD_BYTES  — reject oversized uploads up front
  - FLOOR_PLAN_MAX_DIMENSION     — downscale the longest edge
  - FLOOR_PLAN_PDF_DPI           — PDF rasterisation DPI (page 1 only)

PDFs only ever rasterise the FIRST page — a 300-page engineering set can't
exhaust memory.
"""
from __future__ import annotations

import io
import logging
import os
import uuid

from flask import current_app

logger = logging.getLogger(__name__)


class FloorPlanError(ValueError):
    """Raised for any user-facing validation failure during upload."""


def _ext(filename: str) -> str:
    return (os.path.splitext(filename or "")[1].lstrip(".") or "").lower()


def _storage_dir() -> str:
    path = current_app.config["FLOOR_PLAN_DIR"]
    os.makedirs(path, exist_ok=True)
    return path


def _downscale_if_needed(image, max_dim: int):
    """Return a copy scaled so its longest edge <= max_dim (Pillow Image)."""
    width, height = image.size
    longest = max(width, height)
    if longest <= max_dim:
        return image
    scale = max_dim / float(longest)
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    logger.info("[FLOOR PLAN] downscaling %sx%s -> %sx%s", width, height, *new_size)
    return image.resize(new_size)


def _render_pdf_first_page(data: bytes, dpi: int, max_dim: int):
    """Rasterise ONLY page 1 of a PDF to a Pillow Image."""
    import fitz  # PyMuPDF
    from PIL import Image

    doc = fitz.open(stream=data, filetype="pdf")
    try:
        if doc.page_count < 1:
            raise FloorPlanError("PDF has no pages.")
        page = doc.load_page(0)  # first page only
        # Cap the zoom so a huge page at high DPI can't produce a giant bitmap.
        zoom = dpi / 72.0
        rect = page.rect
        longest_pt = max(rect.width, rect.height) or 1.0
        max_zoom = max_dim / longest_pt
        if zoom > max_zoom:
            zoom = max_zoom
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        return Image.open(io.BytesIO(pix.tobytes("png")))
    finally:
        doc.close()


def normalise_upload(file_storage, max_bytes=None, max_dim=None, pdf_dpi=None):
    """Validate + normalise an uploaded plan to a stored PNG.

    Returns dict: {image_filename, mime_type, image_width, image_height,
    original_filename}.  Raises FloorPlanError on any validation failure.
    """
    from PIL import Image

    cfg = current_app.config
    max_bytes = max_bytes or cfg["FLOOR_PLAN_MAX_UPLOAD_BYTES"]
    max_dim = max_dim or cfg["FLOOR_PLAN_MAX_DIMENSION"]
    pdf_dpi = pdf_dpi or cfg["FLOOR_PLAN_PDF_DPI"]
    allowed = cfg["FLOOR_PLAN_ALLOWED_EXTENSIONS"]

    original_filename = file_storage.filename or "plan"
    ext = _ext(original_filename)
    if ext not in allowed:
        raise FloorPlanError(
            f"Unsupported file type '.{ext}'. Allowed: {', '.join(sorted(allowed))}."
        )

    data = file_storage.read()
    if not data:
        raise FloorPlanError("Uploaded file is empty.")
    if len(data) > max_bytes:
        mb = max_bytes / (1024 * 1024)
        raise FloorPlanError(f"File too large. Maximum size is {mb:.0f} MB.")

    is_pdf = ext == "pdf" or data[:5] == b"%PDF-"
    try:
        if is_pdf:
            image = _render_pdf_first_page(data, dpi=pdf_dpi, max_dim=max_dim)
        else:
            image = Image.open(io.BytesIO(data))
            image.load()  # force decode now so a corrupt image fails here
    except FloorPlanError:
        raise
    except Exception as exc:
        logger.warning("[FLOOR PLAN] could not decode upload: %s", exc)
        raise FloorPlanError("Could not read the image. Please upload a valid PNG, JPG, or PDF.")

    # Flatten to RGB (drop alpha/palette) so the stored PNG renders predictably.
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")
    image = _downscale_if_needed(image, max_dim)
    width, height = image.size

    filename = f"{uuid.uuid4().hex}.png"
    dest = os.path.join(_storage_dir(), filename)
    tmp = f"{dest}.tmp"
    image.save(tmp, format="PNG", optimize=True)
    os.replace(tmp, dest)

    return {
        "image_filename": filename,
        "mime_type": "image/png",
        "image_width": width,
        "image_height": height,
        "original_filename": original_filename[:255],
    }


def image_path(image_filename: str) -> str:
    """Absolute path of a stored plan image (used by the authenticated route)."""
    return os.path.join(current_app.config["FLOOR_PLAN_DIR"], image_filename)


def delete_image(image_filename: str) -> None:
    """Best-effort removal of a stored plan image."""
    if not image_filename:
        return
    try:
        path = image_path(image_filename)
        if os.path.exists(path):
            os.remove(path)
    except Exception as exc:
        logger.warning("[FLOOR PLAN] could not delete image %s: %s", image_filename, exc)
