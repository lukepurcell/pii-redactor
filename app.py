"""
PII Redactor — demo web app.

Routes
  GET  /                       the single-page UI
  GET  /api/session            whether the current request is authenticated
  POST /api/unlock             set a session cookie from REDACTOR_API_KEY
  GET  /api/detectors          predefined PII types (grouped, for the UI)
  GET  /api/dropbox/status     whether a Dropbox token is configured
  GET  /api/dropbox/files      list PDFs in a Dropbox folder
  POST /api/redact             run redaction; returns audit report + preview URLs
  GET  /jobs/<job>/<file>      serve a job's outputs (redacted PDF, JSON, PNGs)

Source of the PDF can be: an uploaded file, the bundled sample, or a Dropbox
path. When a Dropbox path is used and `upload_back` is set, the redacted copy is
written back to Dropbox as "<name> (redacted).pdf".
"""

from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import time
import uuid

from flask import Flask, jsonify, request, send_from_directory, render_template, session

from pii_engine import PREDEFINED_DETECTORS
from pii_engine.engine import RuleConfig
from pii_engine.redactor import (
    DEFAULT_PAD, DEFAULT_VERTICAL_SHIFT_MM,
    DEFAULT_TOP_EXTRA_MM, DEFAULT_BOTTOM_EXTRA_MM, VALID_MODES,
    redact_pdf, render_highlight_images, render_pdf_images,
)
from dropbox_client import DropboxClient

APP_DIR = os.path.dirname(os.path.abspath(__file__))
WORK_DIR = os.path.join(APP_DIR, "_work")
SAMPLE = os.path.join(APP_DIR, "samples", "sample_pii.pdf")
os.makedirs(WORK_DIR, exist_ok=True)

JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")
JOB_FILE_RE = re.compile(r"^(redacted\.pdf|report\.json|(before|after)_\d+\.png)$")
PDF_MAGIC = b"%PDF"
MIN_DPI, MAX_DPI = 72, 300
MAX_MM_SHIFT = 20.0
MAX_PAD = 20

_EPHEMERAL_API_KEY = uuid.uuid4().hex

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB
app.secret_key = (
    os.environ.get("FLASK_SECRET_KEY")
    or os.environ.get("REDACTOR_API_KEY")
    or _EPHEMERAL_API_KEY
)
dropbox_client = DropboxClient()

PUBLIC_ENDPOINTS = {"index", "api_session", "api_unlock"}


def _api_key() -> str:
    return os.environ.get("REDACTOR_API_KEY") or _EPHEMERAL_API_KEY


def _job_ttl_seconds() -> int:
    try:
        return max(60, int(os.environ.get("REDACTOR_JOB_TTL", "3600")))
    except ValueError:
        return 3600


def _keys_match(provided: str | None, expected: str) -> bool:
    if not provided or not expected:
        return False
    if len(provided) != len(expected):
        return False
    return hmac.compare_digest(provided, expected)


def _authorized() -> bool:
    expected = _api_key()
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and _keys_match(auth[7:].strip(), expected):
        return True
    header_key = request.headers.get("X-API-Key")
    if _keys_match(header_key, expected):
        return True
    return session.get("auth") is True


def _sweep_jobs() -> None:
    ttl = _job_ttl_seconds()
    now = time.time()
    try:
        names = os.listdir(WORK_DIR)
    except OSError:
        return
    for name in names:
        path = os.path.join(WORK_DIR, name)
        if not os.path.isdir(path):
            continue
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            continue
        if age > ttl:
            shutil.rmtree(path, ignore_errors=True)


def _client_error(message: str, status: int = 400):
    return jsonify({"error": message}), status


@app.before_request
def _gate():
    _sweep_jobs()
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if _authorized():
        return None
    return _client_error("Unauthorized", 401)


# --------------------------------------------------------------------------- #
#  UI + metadata
# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/session")
def api_session():
    return jsonify({"authenticated": _authorized()})


@app.post("/api/unlock")
def api_unlock():
    payload = request.get_json(silent=True) or {}
    provided = (
        payload.get("api_key")
        or request.form.get("api_key")
        or ""
    ).strip()
    if not _keys_match(provided, _api_key()):
        return _client_error("Invalid API key", 401)
    session["auth"] = True
    return jsonify({"ok": True})


@app.get("/api/detectors")
def api_detectors():
    grouped: dict[str, list] = {}
    for d in PREDEFINED_DETECTORS:
        grouped.setdefault(d.category, []).append({
            "key": d.key, "label": d.label, "severity": d.severity,
            "default_on": d.default_on, "description": d.description,
        })
    return jsonify(grouped)


@app.get("/api/dropbox/status")
def api_dropbox_status():
    return jsonify({"enabled": dropbox_client.enabled})


@app.get("/api/dropbox/files")
def api_dropbox_files():
    if not dropbox_client.enabled:
        return _client_error("Dropbox not configured")
    folder = request.args.get("folder", "")
    try:
        files = [f.__dict__ for f in dropbox_client.list_pdfs(folder)]
        return jsonify({"files": files})
    except Exception:
        return _client_error("Could not list Dropbox files", 500)


# --------------------------------------------------------------------------- #
#  Redaction
# --------------------------------------------------------------------------- #
def _parse_config() -> RuleConfig:
    raw = request.form.get("config_json", "{}")
    cfg = json.loads(raw)
    return RuleConfig(
        enabled_keys=cfg.get("enabled_keys", []),
        custom_rules=cfg.get("custom_rules", []),
    )


def _looks_like_pdf(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == PDF_MAGIC
    except OSError:
        return False


def _resolve_input(job_dir: str) -> tuple[str, str | None]:
    """Return (local_pdf_path, dropbox_source_path_or_None)."""
    in_path = os.path.join(job_dir, "input.pdf")

    if request.form.get("use_sample") == "true":
        shutil.copy(SAMPLE, in_path)
        return in_path, None

    if "file" in request.files and request.files["file"].filename:
        request.files["file"].save(in_path)
        if not _looks_like_pdf(in_path):
            raise ValueError("Uploaded file is not a PDF.")
        return in_path, None

    dbx_path = request.form.get("dropbox_path", "").strip()
    if dbx_path:
        if not dropbox_client.enabled:
            raise ValueError("Dropbox path supplied but Dropbox is not configured.")
        dropbox_client.download(dbx_path, in_path)
        if not _looks_like_pdf(in_path):
            raise ValueError("Dropbox file is not a PDF.")
        return in_path, dbx_path

    raise ValueError("No input: upload a file, use the sample, or give a Dropbox path.")


def _parse_dpi() -> int:
    try:
        dpi = int(request.form.get("dpi", 200))
    except (TypeError, ValueError):
        raise ValueError("dpi must be an integer")
    if dpi < MIN_DPI or dpi > MAX_DPI:
        raise ValueError(f"dpi must be between {MIN_DPI} and {MAX_DPI}")
    return dpi


def _parse_mode() -> str:
    mode = (request.form.get("mode") or "surgical").strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {', '.join(VALID_MODES)}")
    return mode


def _bounded_float(name: str, default: float) -> float:
    try:
        value = float(request.form.get(name, default))
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number")
    if abs(value) > MAX_MM_SHIFT:
        raise ValueError(f"{name} must be between -{MAX_MM_SHIFT} and {MAX_MM_SHIFT}")
    return value


@app.post("/api/redact")
def api_redact():
    job = uuid.uuid4().hex[:12]
    job_dir = os.path.join(WORK_DIR, job)
    os.makedirs(job_dir, exist_ok=True)

    try:
        config = _parse_config()
        dpi = _parse_dpi()
        mode = _parse_mode()
        pad = int(request.form.get("pad", DEFAULT_PAD))
        if pad < 0 or pad > MAX_PAD:
            raise ValueError(f"pad must be between 0 and {MAX_PAD}")
        vertical_shift_mm = _bounded_float("vertical_shift_mm", DEFAULT_VERTICAL_SHIFT_MM)
        top_extra_mm = _bounded_float("top_extra_mm", DEFAULT_TOP_EXTRA_MM)
        bottom_extra_mm = _bounded_float("bottom_extra_mm", DEFAULT_BOTTOM_EXTRA_MM)
        in_path, dbx_path = _resolve_input(job_dir)
    except (ValueError, json.JSONDecodeError) as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        return _client_error(str(e))
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        return _client_error("Could not read input", 500)

    out_path = os.path.join(job_dir, "redacted.pdf")

    try:
        report = redact_pdf(
            in_path, out_path, config, dpi=dpi, pad=pad, mode=mode,
            vertical_shift_mm=vertical_shift_mm,
            top_extra_mm=top_extra_mm,
            bottom_extra_mm=bottom_extra_mm,
        )
    except Exception:
        return _client_error("Redaction failed", 500)

    with open(os.path.join(job_dir, "report.json"), "w") as f:
        json.dump(report, f, indent=2)

    pages = []
    try:
        before = render_highlight_images(
            in_path, config, dpi=130, pad=pad,
            vertical_shift_mm=vertical_shift_mm,
            top_extra_mm=top_extra_mm,
            bottom_extra_mm=bottom_extra_mm,
        )
        after = render_pdf_images(out_path, dpi=130)
        for i, (b, a) in enumerate(zip(before, after)):
            if i >= 8:
                break
            b.save(os.path.join(job_dir, f"before_{i}.png"))
            a.save(os.path.join(job_dir, f"after_{i}.png"))
            pages.append({
                "before": f"/jobs/{job}/before_{i}.png",
                "after": f"/jobs/{job}/after_{i}.png",
            })
    except Exception:
        report.setdefault("warnings", []).append("Preview render failed")

    dropbox_result = None
    if dbx_path and request.form.get("upload_back") == "true":
        try:
            target = DropboxClient.redacted_path(dbx_path)
            dropbox_result = {"uploaded_to": dropbox_client.upload(out_path, target)}
        except Exception:
            dropbox_result = {"error": "Dropbox upload failed"}

    return jsonify({
        "job": job,
        "report": report,
        "pages": pages,
        "downloads": {
            "pdf": f"/jobs/{job}/redacted.pdf",
            "report": f"/jobs/{job}/report.json",
        },
        "dropbox": dropbox_result,
    })


@app.get("/jobs/<job>/<path:fname>")
def jobs(job, fname):
    if not JOB_ID_RE.fullmatch(job) or not JOB_FILE_RE.fullmatch(fname):
        return _client_error("Not found", 404)
    directory = os.path.join(WORK_DIR, job)
    if not os.path.isdir(directory):
        return _client_error("Not found", 404)
    return send_from_directory(directory, fname)


if __name__ == "__main__":
    host = os.environ.get("REDACTOR_BIND", "127.0.0.1")
    port = int(os.environ.get("REDACTOR_PORT", "8080"))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in {"1", "true", "yes"}
    if not os.environ.get("REDACTOR_API_KEY"):
        print(f"REDACTOR_API_KEY not set; using ephemeral key: {_EPHEMERAL_API_KEY}")
    print(f"PII Redactor running on http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)
