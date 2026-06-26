from __future__ import annotations

import cgi
import json
import mimetypes
import os
import re
import shutil
import sys
import time
import uuid
import zipfile
from datetime import datetime
from email.utils import formatdate
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlparse

from converter import (
    ConversionError,
    ConverterUnavailable,
    SUPPORTED_EXTENSIONS,
    convert_file_to_pdf,
    engine_status,
    merge_pdfs,
    normalize_paper_size,
)


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = APP_DIR / "data" / "jobs"
MAX_REQUEST_MB = int(os.environ.get("OFD_MAX_REQUEST_MB", "500"))
MAX_REQUEST_BYTES = MAX_REQUEST_MB * 1024 * 1024
JOB_ID_RE = re.compile(r"^[a-f0-9]{32}$")
FILE_ID_RE = re.compile(r"^[a-f0-9]{32}$|^all$|^merged$")


class BatchPdfHandler(BaseHTTPRequestHandler):
    server_version = "OfdImagePdfBatcher/1.1"
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/health":
                self.send_json(HTTPStatus.OK, {"ok": True, "converter": engine_status()})
            elif path.startswith("/api/jobs/"):
                self.handle_job_state(path)
            elif path.startswith("/download/"):
                self.handle_download(path)
            else:
                self.serve_static(path)
        except HttpError as exc:
            self.send_json(exc.status, {"ok": False, "error": exc.message})
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/convert":
            self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "api_not_found"})
            return

        content_length = int(self.headers.get("Content-Length", "0") or 0)
        if content_length <= 0:
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "empty_upload"})
            return
        if content_length > MAX_REQUEST_BYTES:
            self.send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"ok": False, "error": f"upload_too_large:{MAX_REQUEST_MB}mb"},
            )
            return

        try:
            self.send_json(HTTPStatus.OK, self.handle_convert(content_length))
        except HttpError as exc:
            self.send_json(exc.status, {"ok": False, "error": exc.message})
        except Exception as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    def handle_convert(self, content_length: int) -> dict:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise HttpError(HTTPStatus.BAD_REQUEST, "multipart_required")

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
                "CONTENT_LENGTH": str(content_length),
            },
            keep_blank_values=False,
        )
        fields = _extract_file_fields(form)
        if not fields:
            raise HttpError(HTTPStatus.BAD_REQUEST, "no_files_selected")

        paper_size = normalize_paper_size(form.getfirst("paperSize", "original"))
        merge_enabled = _bool_value(form.getfirst("merge", "false"))

        job_id = uuid.uuid4().hex
        job_dir = DATA_DIR / job_id
        upload_dir = job_dir / "uploads"
        result_dir = job_dir / "results"
        upload_dir.mkdir(parents=True, exist_ok=True)
        result_dir.mkdir(parents=True, exist_ok=True)

        state = {
            "jobId": job_id,
            "createdAt": datetime.now().isoformat(timespec="seconds"),
            "options": {"merge": merge_enabled, "paperSize": paper_size},
            "files": [],
            "merged": None,
            "summary": {"total": 0, "success": 0, "failed": 0},
            "zipUrl": f"/download/{job_id}/all",
        }

        for field in fields:
            item = self.process_file(field, upload_dir, result_dir, job_id, paper_size)
            state["files"].append(item)

        success = sum(1 for item in state["files"] if item["status"] == "success")
        failed = len(state["files"]) - success
        state["summary"] = {"total": len(state["files"]), "success": success, "failed": failed}

        if merge_enabled:
            state["merged"] = _create_merged_result(job_id, result_dir, state["files"])

        _write_archive(result_dir, state["files"], state.get("merged"))
        _write_json(job_dir / "state.json", state)
        return {"ok": True, **state}

    def process_file(
        self,
        field: cgi.FieldStorage,
        upload_dir: Path,
        result_dir: Path,
        job_id: str,
        paper_size: str,
    ) -> dict:
        original_name = _client_filename(field.filename or "unnamed")
        suffix = Path(original_name).suffix.lower()
        file_id = uuid.uuid4().hex
        pdf_name = _pdf_name(original_name, file_id)
        input_path = upload_dir / f"{file_id}{suffix or '.bin'}"
        output_path = result_dir / f"{file_id}.pdf"
        item = {
            "id": file_id,
            "originalName": original_name,
            "pdfName": pdf_name,
            "sourceType": suffix.lstrip(".") or "unknown",
            "paperSize": paper_size,
            "status": "failed",
            "inputBytes": 0,
            "outputBytes": 0,
            "pages": None,
            "durationMs": None,
            "error": "",
            "downloadUrl": "",
        }

        if suffix not in SUPPORTED_EXTENSIONS:
            item["error"] = "unsupported_file_type"
            return item

        try:
            with input_path.open("wb") as target:
                shutil.copyfileobj(field.file, target)
            item["inputBytes"] = input_path.stat().st_size
            started = time.perf_counter()
            result = convert_file_to_pdf(input_path, output_path, paper_size=paper_size)
            item.update(
                {
                    "sourceType": result.kind,
                    "paperSize": result.paper_size,
                    "status": "success",
                    "outputBytes": result.pdf_bytes,
                    "pages": result.pages,
                    "durationMs": round((time.perf_counter() - started) * 1000),
                    "downloadUrl": f"/download/{job_id}/{file_id}",
                }
            )
        except ConverterUnavailable as exc:
            item["error"] = str(exc)
        except ConversionError as exc:
            item["error"] = str(exc)
        except Exception as exc:
            item["error"] = str(exc)

        return item

    def handle_job_state(self, path: str) -> None:
        parts = PurePosixPath(path).parts
        if len(parts) != 4:
            raise HttpError(HTTPStatus.NOT_FOUND, "job_not_found")
        state = _load_state(parts[-1])
        self.send_json(HTTPStatus.OK, {"ok": True, **state})

    def handle_download(self, path: str) -> None:
        parts = PurePosixPath(path).parts
        if len(parts) != 4:
            raise HttpError(HTTPStatus.NOT_FOUND, "invalid_download_url")
        _, _, job_id, file_key = parts
        if not JOB_ID_RE.match(job_id) or not FILE_ID_RE.match(file_key):
            raise HttpError(HTTPStatus.BAD_REQUEST, "invalid_download_url")

        job_dir = DATA_DIR / job_id
        result_dir = job_dir / "results"
        state = _load_state(job_id)

        if file_key == "all":
            target = result_dir / "converted-pdfs.zip"
            if not target.exists():
                _write_archive(result_dir, state["files"], state.get("merged"))
            self.send_file(target, "converted-pdfs.zip", "application/zip")
            return

        if file_key == "merged":
            merged = state.get("merged") or {}
            if merged.get("status") != "success":
                raise HttpError(HTTPStatus.NOT_FOUND, "merged_pdf_not_found")
            self.send_file(result_dir / "merged.pdf", merged.get("pdfName", "merged.pdf"), "application/pdf")
            return

        item = next((entry for entry in state["files"] if entry["id"] == file_key), None)
        if not item or item.get("status") != "success":
            raise HttpError(HTTPStatus.NOT_FOUND, "pdf_not_found")
        self.send_file(result_dir / f"{file_key}.pdf", item["pdfName"], "application/pdf")

    def serve_static(self, path: str) -> None:
        target = STATIC_DIR / "index.html" if path in ("", "/") else STATIC_DIR.joinpath(
            *[part for part in PurePosixPath(path.lstrip("/")).parts if part not in ("", "..")]
        )
        try:
            target.resolve().relative_to(STATIC_DIR.resolve())
        except ValueError as exc:
            raise HttpError(HTTPStatus.FORBIDDEN, "forbidden") from exc
        if not target.exists() or not target.is_file():
            raise HttpError(HTTPStatus.NOT_FOUND, "file_not_found")
        content_type = _with_charset(mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_file(target, target.name, content_type, inline=True)

    def send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, filename: str, content_type: str, inline: bool = False) -> None:
        if not path.exists() or not path.is_file():
            raise HttpError(HTTPStatus.NOT_FOUND, "file_not_found")
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Last-Modified", formatdate(path.stat().st_mtime, usegmt=True))
        if not inline:
            self.send_header("Content-Disposition", _content_disposition(filename))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))


class HttpError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _extract_file_fields(form: cgi.FieldStorage) -> list[cgi.FieldStorage]:
    return [field for field in (form.list or []) if getattr(field, "filename", None)]


def _bool_value(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _create_merged_result(job_id: str, result_dir: Path, files: list[dict]) -> dict:
    merged = {
        "status": "failed",
        "pdfName": "merged.pdf",
        "outputBytes": 0,
        "pages": None,
        "error": "",
        "downloadUrl": "",
    }
    try:
        pdf_paths = [result_dir / f"{item['id']}.pdf" for item in files if item.get("status") == "success"]
        result = merge_pdfs(pdf_paths, result_dir / "merged.pdf")
        merged.update(
            {
                "status": "success",
                "outputBytes": result.pdf_bytes,
                "pages": result.pages,
                "downloadUrl": f"/download/{job_id}/merged",
            }
        )
    except (ConverterUnavailable, ConversionError) as exc:
        merged["error"] = str(exc)
    except Exception as exc:
        merged["error"] = str(exc)
    return merged


def _client_filename(name: str) -> str:
    normalized = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    return normalized or "unnamed"


def _pdf_name(original_name: str, file_id: str) -> str:
    stem = Path(original_name).stem.strip() or file_id[:8]
    cleaned = re.sub(r"[\r\n\t<>:\"/\\|?*]+", "_", stem).strip(" ._")
    return f"{cleaned or file_id[:8]}.pdf"


def _unique_archive_name(name: str, used: set[str]) -> str:
    base = Path(name).stem
    suffix = Path(name).suffix or ".pdf"
    candidate = f"{base}{suffix}"
    index = 2
    while candidate in used:
        candidate = f"{base} ({index}){suffix}"
        index += 1
    used.add(candidate)
    return candidate


def _write_archive(result_dir: Path, files: list[dict], merged: dict | None = None) -> None:
    result_dir.mkdir(parents=True, exist_ok=True)
    archive_path = result_dir / "converted-pdfs.zip"
    used: set[str] = set()
    failures: list[str] = []
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in files:
            if item.get("status") == "success":
                pdf_path = result_dir / f"{item['id']}.pdf"
                if pdf_path.exists():
                    archive.write(pdf_path, _unique_archive_name(item["pdfName"], used))
            else:
                failures.append(f"{item.get('originalName', '')}: {item.get('error', 'conversion_failed')}")
        if merged and merged.get("status") == "success":
            archive.write(result_dir / "merged.pdf", _unique_archive_name(merged.get("pdfName", "merged.pdf"), used))
        elif merged and merged.get("status") == "failed":
            failures.append(f"merged.pdf: {merged.get('error', 'merge_failed')}")
        if failures:
            archive.writestr("errors.txt", "\n".join(failures))


def _load_state(job_id: str) -> dict:
    if not JOB_ID_RE.match(job_id):
        raise HttpError(HTTPStatus.BAD_REQUEST, "invalid_job_id")
    state_path = DATA_DIR / job_id / "state.json"
    if not state_path.exists():
        raise HttpError(HTTPStatus.NOT_FOUND, "job_not_found")
    return json.loads(state_path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _content_disposition(filename: str) -> str:
    fallback = re.sub(r"[^A-Za-z0-9_. -]+", "_", filename) or "download.pdf"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


def _with_charset(content_type: str) -> str:
    if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
        return f"{content_type}; charset=utf-8"
    return content_type


def main() -> None:
    host = os.environ.get("OFD_HOST", "127.0.0.1")
    port = int(os.environ.get("OFD_PORT", "8765"))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), BatchPdfHandler)
    print(f"OFD and image batch PDF converter: http://{host}:{port}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
