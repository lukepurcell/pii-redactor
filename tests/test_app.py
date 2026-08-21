import io
from pathlib import Path

import pytest


def test_unauthenticated_api_is_401(client):
    res = client.get("/api/detectors")
    assert res.status_code == 401


def test_session_is_public(client):
    res = client.get("/api/session")
    assert res.status_code == 200
    assert res.get_json()["authenticated"] is False


def test_unlock_sets_session(client):
    bad = client.post("/api/unlock", json={"api_key": "nope"})
    assert bad.status_code == 401
    ok = client.post("/api/unlock", json={"api_key": "test-secret-key"})
    assert ok.status_code == 200
    res = client.get("/api/detectors")
    assert res.status_code == 200


def test_bearer_auth_detectors(client, auth_headers):
    res = client.get("/api/detectors", headers=auth_headers)
    assert res.status_code == 200
    assert "Financial" in res.get_json()


def test_job_path_rejects_bad_ids(client, auth_headers):
    res = client.get("/jobs/../app.py", headers=auth_headers)
    assert res.status_code in (400, 404)
    res = client.get("/jobs/not-a-job-id/redacted.pdf", headers=auth_headers)
    assert res.status_code == 404
    res = client.get("/jobs/" + "a" * 12 + "/secret.txt", headers=auth_headers)
    assert res.status_code == 404


def test_dpi_cap(client, auth_headers):
    res = client.post(
        "/api/redact",
        data={"use_sample": "true", "dpi": "9999", "config_json": "{}"},
        headers=auth_headers,
    )
    assert res.status_code == 400
    assert "dpi" in res.get_json()["error"]


def test_redact_sample_requires_auth_and_returns_report(client, auth_headers):
    sample = Path(__file__).parent.parent / "samples" / "sample_pii.pdf"
    if not sample.exists():
        pytest.skip("Sample PDF not found")
    res = client.post(
        "/api/redact",
        data={
            "use_sample": "true",
            "dpi": "150",
            "mode": "flatten",
            "config_json": '{"enabled_keys":["email"],"custom_rules":[]}',
        },
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["report"]["verification"]["banner"] in {"flatten_ok", "fail"}
    pdf = client.get(body["downloads"]["pdf"], headers=auth_headers)
    assert pdf.status_code == 200


def test_non_pdf_upload_rejected(client, auth_headers):
    res = client.post(
        "/api/redact",
        data={
            "config_json": "{}",
            "file": (io.BytesIO(b"not a pdf"), "note.txt"),
        },
        headers=auth_headers,
        content_type="multipart/form-data",
    )
    assert res.status_code == 400
    assert "PDF" in res.get_json()["error"]
