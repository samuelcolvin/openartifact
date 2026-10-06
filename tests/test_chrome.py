"""Tests for `chrome/`: `pdf.py`'s command assembly and error paths, and the `/pdf/` endpoint. Chrome is stubbed."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chrome import pdf
from chrome.server import app

URL = 'http://127.0.0.1:8765/artifacts/demo-abc123/'
# Shell for a fake Chrome that writes a PDF-looking file where `--print-to-pdf=` points.
WRITES_PDF = r"""for a in "$@"; do case "$a" in --print-to-pdf=*) printf '%%PDF-1.4 fake' > "${a#--print-to-pdf=}";; esac; done"""


def fake_chrome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> Path:
    """Install a shell script as the Chrome `pdf.py` finds."""
    fake = tmp_path / 'chrome'
    fake.write_text(f'#!/bin/sh\n{script}\n')
    fake.chmod(0o755)
    monkeypatch.setattr(pdf, 'find_chrome', lambda: str(fake))
    return fake


# --- pdf.py ------------------------------------------------------------------


def test_missing_chrome_reports_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pdf, 'find_chrome', lambda: None)
    monkeypatch.delenv('CHROME_NO_SANDBOX', raising=False)
    with pytest.raises(pdf.ChromeError) as exc_info:
        pdf.print_to_pdf(URL, tmp_path / 'out.pdf')
    message = str(exc_info.value)
    assert message.startswith('Chrome / Chromium not found')
    # No paper flags: each artifact type's stylesheet sets `@page`, which Chrome honours.
    assert '--paper-' not in message
    assert '--no-sandbox' not in message
    assert message.endswith(f' --print-to-pdf={tmp_path / "out.pdf"} {URL}')


def test_container_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pdf, 'find_chrome', lambda: None)
    monkeypatch.setenv('CHROME_NO_SANDBOX', '1')
    with pytest.raises(
        pdf.ChromeError, match='--disable-gpu --no-sandbox --disable-dev-shm-usage --no-pdf-header-footer'
    ):
        pdf.print_to_pdf(URL, tmp_path / 'out.pdf')


def test_chrome_failure_reports_command_and_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_chrome(tmp_path, monkeypatch, 'echo boom >&2\nexit 3')
    with pytest.raises(pdf.ChromeError, match=r'Chrome exited with code 3:\n  .*--headless=new.*\nboom'):
        pdf.print_to_pdf(URL, tmp_path / 'out.pdf')


def test_success_returns_resolved_path_and_creates_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_chrome(tmp_path, monkeypatch, 'exit 0')
    out = pdf.print_to_pdf(URL, tmp_path / 'nested' / 'out.pdf')
    assert out == (tmp_path / 'nested' / 'out.pdf').resolve()
    assert out.parent.is_dir()


def test_shell_quote():
    assert pdf.shell_quote('--headless=new') == '--headless=new'
    assert pdf.shell_quote("it's here") == "'it'\\''s here'"


# --- the endpoint ------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health(client: TestClient):
    assert client.get('/health/').json() == {'status': 'ok'}


def test_pdf_endpoint_returns_the_file(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_chrome(tmp_path, monkeypatch, WRITES_PDF)
    response = client.post('/pdf/', json={'url': URL})
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'application/pdf'
    assert response.content == b'%PDF-1.4 fake'


def test_pdf_endpoint_prints_only_http_urls(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_chrome(tmp_path, monkeypatch, WRITES_PDF)
    for url in ('file:///etc/passwd', 'chrome://version', 'artifacts/x/'):
        response = client.post('/pdf/', json={'url': url})
        assert response.status_code == 422, url
    assert client.post('/pdf/', json={}).status_code == 422


def test_pdf_endpoint_reports_chrome_failure(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_chrome(tmp_path, monkeypatch, 'echo boom >&2\nexit 3')
    response = client.post('/pdf/', json={'url': URL})
    assert response.status_code == 502
    assert response.json()['detail'].startswith('Chrome exited with code 3:')


def test_pdf_endpoint_when_chrome_writes_nothing(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_chrome(tmp_path, monkeypatch, 'exit 0')
    response = client.post('/pdf/', json={'url': URL})
    assert response.status_code == 502
    assert response.json()['detail'] == 'Chrome exited without writing a PDF'
