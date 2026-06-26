from __future__ import annotations

import io
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
SUPPORTED_EXTENSIONS = {".ofd", *IMAGE_EXTENSIONS}
PAPER_SIZES = {
    "original": None,
    "a4": (595.28, 841.89),
    "a4_landscape": (841.89, 595.28),
    "a3": (841.89, 1190.55),
    "a3_landscape": (1190.55, 841.89),
    "letter": (612.0, 792.0),
    "letter_landscape": (792.0, 612.0),
}


class ConverterUnavailable(RuntimeError):
    pass


class ConversionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConversionResult:
    pdf_bytes: int
    pages: int | None
    kind: str
    paper_size: str


def engine_status() -> dict:
    status = {
        "ofd": _module_status("easyofd", "easyofd"),
        "image": _module_status("PIL", "Pillow"),
        "pdf": _module_status("fitz", "PyMuPDF"),
    }
    return {
        "available": all(item["available"] for item in status.values()),
        "engines": status,
        "supportedExtensions": sorted(SUPPORTED_EXTENSIONS),
        "paperSizes": list(PAPER_SIZES.keys()),
    }


def convert_file_to_pdf(input_path: Path, output_path: Path, paper_size: str = "original") -> ConversionResult:
    paper_size = normalize_paper_size(paper_size)
    suffix = input_path.suffix.lower()
    if suffix == ".ofd":
        result = convert_ofd_to_pdf(input_path, output_path)
    elif suffix in IMAGE_EXTENSIONS:
        result = convert_image_to_pdf(input_path, output_path)
    else:
        raise ConversionError("unsupported_file_type")

    if paper_size != "original":
        resize_pdf_to_paper(output_path, paper_size)
        result = ConversionResult(
            pdf_bytes=output_path.stat().st_size,
            pages=_count_pdf_pages(output_path),
            kind=result.kind,
            paper_size=paper_size,
        )
    return result


def normalize_paper_size(value: str | None) -> str:
    key = (value or "original").strip().lower()
    if key not in PAPER_SIZES:
        raise ConversionError("unsupported_paper_size")
    return key


def convert_ofd_to_pdf(input_path: Path, output_path: Path) -> ConversionResult:
    input_path = Path(input_path)
    output_path = Path(output_path)

    _ensure_readable_file(input_path)
    if not zipfile.is_zipfile(input_path):
        raise ConversionError("invalid_ofd_container")

    try:
        from easyofd import OFD  # type: ignore
        from loguru import logger  # type: ignore
    except Exception as exc:
        raise ConverterUnavailable("easyofd_missing") from exc

    try:
        logger.disable("easyofd")
    except Exception:
        pass

    log_buffer = io.StringIO()
    try:
        ofd_bytes = input_path.read_bytes()
        with redirect_stdout(log_buffer), redirect_stderr(log_buffer):
            reader = OFD()
            reader.read(ofd_bytes, fmt="binary")
            pdf_bytes = reader.to_pdf()
            reader.del_data()
    except Exception as exc:
        detail = _tail(log_buffer.getvalue())
        message = str(exc) or exc.__class__.__name__
        if detail:
            message = f"{message}; {detail}"
        raise ConversionError(message) from exc

    if not isinstance(pdf_bytes, (bytes, bytearray)) or len(pdf_bytes) == 0:
        raise ConversionError("empty_pdf_output")
    if not bytes(pdf_bytes).startswith(b"%PDF"):
        raise ConversionError("invalid_pdf_output")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(bytes(pdf_bytes))
    return ConversionResult(
        pdf_bytes=output_path.stat().st_size,
        pages=_count_pdf_pages(output_path),
        kind="ofd",
        paper_size="original",
    )


def convert_image_to_pdf(input_path: Path, output_path: Path) -> ConversionResult:
    input_path = Path(input_path)
    output_path = Path(output_path)
    _ensure_readable_file(input_path)

    try:
        from PIL import Image, ImageSequence  # type: ignore
    except Exception as exc:
        raise ConverterUnavailable("pillow_missing") from exc

    try:
        with Image.open(input_path) as image:
            frames = [_rgb_frame(frame) for frame in ImageSequence.Iterator(image)]
            if not frames:
                raise ConversionError("image_has_no_frames")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            first, rest = frames[0], frames[1:]
            first.save(output_path, "PDF", save_all=bool(rest), append_images=rest, resolution=100.0)
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(str(exc) or exc.__class__.__name__) from exc

    return ConversionResult(
        pdf_bytes=output_path.stat().st_size,
        pages=_count_pdf_pages(output_path),
        kind="image",
        paper_size="original",
    )


def resize_pdf_to_paper(pdf_path: Path, paper_size: str) -> None:
    paper_size = normalize_paper_size(paper_size)
    dimensions = PAPER_SIZES[paper_size]
    if dimensions is None:
        return

    try:
        import fitz  # type: ignore
    except Exception as exc:
        raise ConverterUnavailable("pymupdf_missing") from exc

    width, height = dimensions
    temp_path = pdf_path.with_suffix(".resized.tmp.pdf")
    source = None
    target = None
    try:
        source = fitz.open(pdf_path)
        target = fitz.open()
        for page in source:
            page_rect = page.rect
            if page_rect.width <= 0 or page_rect.height <= 0:
                continue
            scale = min(width / page_rect.width, height / page_rect.height)
            draw_width = page_rect.width * scale
            draw_height = page_rect.height * scale
            x0 = (width - draw_width) / 2
            y0 = (height - draw_height) / 2
            dest = fitz.Rect(x0, y0, x0 + draw_width, y0 + draw_height)
            new_page = target.new_page(width=width, height=height)
            new_page.show_pdf_page(dest, source, page.number)
        if target.page_count == 0:
            raise ConversionError("pdf_has_no_pages")
        target.save(temp_path, deflate=True, garbage=4)
        target.close()
        target = None
        source.close()
        source = None
        temp_path.replace(pdf_path)
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(str(exc) or exc.__class__.__name__) from exc
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def merge_pdfs(pdf_paths: list[Path], output_path: Path) -> ConversionResult:
    existing = [Path(path) for path in pdf_paths if Path(path).exists()]
    if not existing:
        raise ConversionError("no_successful_pdfs_to_merge")

    try:
        import fitz  # type: ignore
    except Exception as exc:
        raise ConverterUnavailable("pymupdf_missing") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged = fitz.open()
    try:
        for pdf_path in existing:
            with fitz.open(pdf_path) as doc:
                merged.insert_pdf(doc)
        if merged.page_count == 0:
            raise ConversionError("merged_pdf_has_no_pages")
        merged.save(output_path, deflate=True, garbage=4)
    finally:
        merged.close()

    return ConversionResult(
        pdf_bytes=output_path.stat().st_size,
        pages=_count_pdf_pages(output_path),
        kind="merged",
        paper_size="original",
    )


def _rgb_frame(image):
    from PIL import Image  # type: ignore

    frame = image.copy()
    if frame.mode in ("RGBA", "LA") or (frame.mode == "P" and "transparency" in frame.info):
        background = Image.new("RGB", frame.size, "white")
        alpha = frame.convert("RGBA").split()[-1]
        background.paste(frame.convert("RGB"), mask=alpha)
        return background
    if frame.mode != "RGB":
        return frame.convert("RGB")
    return frame


def _ensure_readable_file(path: Path) -> None:
    if not path.exists():
        raise ConversionError("file_not_found")
    if not path.is_file():
        raise ConversionError("not_a_file")
    if path.stat().st_size == 0:
        raise ConversionError("empty_file")


def _module_status(module_name: str, label: str) -> dict:
    try:
        module = __import__(module_name)
        version = getattr(module, "__version__", "available")
        return {"available": True, "engine": label, "version": version}
    except Exception as exc:
        return {"available": False, "engine": label, "error": str(exc)}


def _count_pdf_pages(pdf_path: Path) -> int | None:
    try:
        import fitz  # type: ignore

        with fitz.open(pdf_path) as doc:
            return doc.page_count
    except Exception:
        return None


def _tail(text: str, limit: int = 300) -> str:
    clean = " ".join(line.strip() for line in text.splitlines() if line.strip())
    if len(clean) <= limit:
        return clean
    return clean[-limit:]
