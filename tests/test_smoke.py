from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402
from converter import convert_file_to_pdf, engine_status, merge_pdfs  # noqa: E402
from easyofd import OFD  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402


def main() -> None:
    status = engine_status()
    assert status["available"], status

    temp_dir = ROOT / "data" / "smoke-test"
    temp_dir.mkdir(parents=True, exist_ok=True)

    sample_image = temp_dir / "sample.png"
    image_pdf = temp_dir / "sample-image.pdf"
    image_a4_pdf = temp_dir / "sample-image-a4.pdf"
    sample_ofd = temp_dir / "sample.ofd"
    ofd_pdf = temp_dir / "sample-ofd.pdf"
    merged_pdf = temp_dir / "merged.pdf"

    image = Image.new("RGB", (640, 360), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((32, 32, 608, 328), outline=(11, 122, 104), width=4)
    draw.text((64, 150), "OFD and image batch conversion smoke test", fill=(0, 0, 0))
    image.save(sample_image)

    image_result = convert_file_to_pdf(sample_image, image_pdf)
    assert image_pdf.exists()
    assert image_pdf.read_bytes().startswith(b"%PDF")
    assert image_result.pdf_bytes > 0

    image_a4_result = convert_file_to_pdf(sample_image, image_a4_pdf, paper_size="a4")
    assert image_a4_pdf.exists()
    assert image_a4_result.pdf_bytes > 0
    with fitz.open(image_a4_pdf) as doc:
        rect = doc[0].rect
        assert round(rect.width) == 595
        assert round(rect.height) == 842

    shutil.rmtree(ROOT / "test", ignore_errors=True)
    sample_ofd.write_bytes(OFD().jpg2ofd([image]))
    ofd_result = convert_file_to_pdf(sample_ofd, ofd_pdf, paper_size="a4")
    assert ofd_pdf.exists()
    assert ofd_pdf.read_bytes().startswith(b"%PDF")
    assert ofd_result.pdf_bytes > 0

    merged_result = merge_pdfs([image_a4_pdf, ofd_pdf], merged_pdf)
    assert merged_pdf.exists()
    assert merged_pdf.read_bytes().startswith(b"%PDF")
    assert merged_result.pages == 2

    print(
        {
            "ok": True,
            "imagePdfBytes": image_result.pdf_bytes,
            "imageA4Pages": image_a4_result.pages,
            "ofdPdfBytes": ofd_result.pdf_bytes,
            "ofdPages": ofd_result.pages,
            "mergedPages": merged_result.pages,
        }
    )


if __name__ == "__main__":
    main()
