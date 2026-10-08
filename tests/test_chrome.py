"""Tests for `chrome/`: `pdf.py`'s command assembly and error paths, and the `/pdf/`, `/screenshot/` and `/scene/`
endpoints. Chrome is stubbed."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chrome import pdf, server
from chrome.server import app

URL = 'http://127.0.0.1:8765/artifacts/demo-abc123/'
# Shell for a fake Chrome that writes a PDF-looking file where `--print-to-pdf=` points, grumbling as it goes.
WRITES_PDF = r"""echo 'dbus: no bus' >&2; for a in "$@"; do case "$a" in --print-to-pdf=*) printf '%%PDF-1.4 fake' > "${a#--print-to-pdf=}";; esac; done"""
# What Chrome does when the page fails to load: says so on stderr and exits 0 without a file.
LOAD_FAILS = "echo 'Page load failed: net::ERR_SSL_PROTOCOL_ERROR' >&2; exit 0"
# A fake Chrome that writes a PNG-looking file where `--screenshot=` points and records its arguments beside it.
WRITES_PNG = (
    'for a in "$@"; do case "$a" in --screenshot=*) out="${a#--screenshot=}";; esac; done; '
    'printf "%s\\n" "$@" > "$out.args"; printf "\\211PNG fake" > "$out"'
)
# A fake Chrome that dumps a page holding a scene block, as a deck opened with `?scene` has, and the arguments
# after it as a comment, since a dump goes to stdout and leaves nothing else behind.
SCENE = '{"width": 1056, "height": 594, "pages": [{"index": 1, "blocks": []}]}'
DUMPS_SCENE = (
    f"""printf '<html><body><script type="application/json" id="artifact-scene">{SCENE}</script>"""
    """<!-- %s -->\\n' "$*"; echo 'dbus: no bus' >&2"""
)
DUMPS_PLAIN = "printf '<html><body><p>no scene here</p></body></html>\\n'"


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
    assert exc_info.value.stderr == ''


def test_container_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pdf, 'find_chrome', lambda: None)
    monkeypatch.setenv('CHROME_NO_SANDBOX', '1')
    with pytest.raises(
        pdf.ChromeError, match='--disable-gpu --no-sandbox --disable-dev-shm-usage --no-pdf-header-footer'
    ):
        pdf.print_to_pdf(URL, tmp_path / 'out.pdf')


def test_chrome_failure_reports_command_and_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_chrome(tmp_path, monkeypatch, 'echo boom >&2\nexit 3')
    with pytest.raises(pdf.ChromeError, match=r'Chrome exited with code 3:\n  .*--headless=new.*\nboom') as exc_info:
        pdf.print_to_pdf(URL, tmp_path / 'out.pdf')
    assert exc_info.value.stderr == 'boom'


def test_chrome_writing_nothing_is_an_error_with_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_chrome(tmp_path, monkeypatch, LOAD_FAILS)
    with pytest.raises(pdf.ChromeError) as exc_info:
        pdf.print_to_pdf(URL, tmp_path / 'out.pdf')
    message = str(exc_info.value)
    assert message.startswith('Chrome exited without writing a PDF:\n  ')
    assert message.endswith('\nPage load failed: net::ERR_SSL_PROTOCOL_ERROR')
    assert exc_info.value.stderr == 'Page load failed: net::ERR_SSL_PROTOCOL_ERROR'


def test_success_returns_path_and_stderr_and_creates_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_chrome(tmp_path, monkeypatch, WRITES_PDF)
    printed = pdf.print_to_pdf(URL, tmp_path / 'nested' / 'out.pdf')
    assert printed.path == (tmp_path / 'nested' / 'out.pdf').resolve()
    assert printed.path.read_bytes() == b'%PDF-1.4 fake'
    # Stderr is kept even when all went well.
    assert printed.stderr == 'dbus: no bus'


def test_screenshot_sizes_the_window_and_keeps_the_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_chrome(tmp_path, monkeypatch, WRITES_PNG)
    out = tmp_path / 'shots' / 'page.png'
    printed = pdf.screenshot(f'{URL}#3', out, 1600, 900)
    assert printed.path == out.resolve() and out.read_bytes().startswith(b'\x89PNG')
    args = (tmp_path / 'shots' / 'page.png.args').read_text().splitlines()
    assert '--window-size=1600,900' in args and '--hide-scrollbars' in args and f'--screenshot={out.resolve()}' in args
    # The page itself turns the hash into a page: it must reach Chrome untouched. No PDF flags.
    assert args[-1] == f'{URL}#3' and not any(a.startswith('--print-to-pdf') for a in args)


def test_screenshot_scale_is_the_device_pixel_ratio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_chrome(tmp_path, monkeypatch, WRITES_PNG)
    out = tmp_path / 'page.png'
    pdf.screenshot(URL, out, 1056, 594, scale=2)
    args = (tmp_path / 'page.png.args').read_text().splitlines()
    assert '--force-device-scale-factor=2' in args and '--window-size=1056,594' in args
    # The flag is left out at 1, Chrome's default.
    pdf.screenshot(URL, out, 1056, 594)
    assert not any(
        a.startswith('--force-device-scale-factor') for a in out.with_suffix('.png.args').read_text().split()
    )


def test_screenshot_writing_nothing_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_chrome(tmp_path, monkeypatch, LOAD_FAILS)
    with pytest.raises(pdf.ChromeError, match='without writing a screenshot') as exc_info:
        pdf.screenshot(URL, tmp_path / 'page.png', 800, 600)
    assert 'Page load failed' in exc_info.value.stderr


def test_dump_dom_returns_the_page_and_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_chrome(tmp_path, monkeypatch, DUMPS_SCENE)
    dumped = pdf.dump_dom(f'{URL}?scene')
    assert SCENE in dumped.html and dumped.stderr == 'dbus: no bus'
    # Chrome got the dump flag and the URL with its query, and no file flags.
    assert f'<!-- --headless=new --disable-gpu --dump-dom {URL}?scene -->' in dumped.html


def test_dump_dom_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_chrome(tmp_path, monkeypatch, LOAD_FAILS)
    with pytest.raises(pdf.ChromeError, match='Chrome exited without dumping the page') as exc_info:
        pdf.dump_dom(URL)
    assert exc_info.value.stderr == 'Page load failed: net::ERR_SSL_PROTOCOL_ERROR'
    fake_chrome(tmp_path, monkeypatch, 'echo boom >&2\nexit 3')
    with pytest.raises(pdf.ChromeError, match=r'Chrome exited with code 3'):
        pdf.dump_dom(URL)
    monkeypatch.setattr(pdf, 'find_chrome', lambda: None)
    with pytest.raises(pdf.ChromeError, match=f'(?s)not found.*--dump-dom {URL}'):
        pdf.dump_dom(URL)


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


def test_pdf_endpoint_when_the_page_fails_to_load(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_chrome(tmp_path, monkeypatch, LOAD_FAILS)
    response = client.post('/pdf/', json={'url': URL})
    assert response.status_code == 502
    detail = response.json()['detail']
    assert detail.startswith('Chrome exited without writing a PDF:')
    assert detail.endswith('Page load failed: net::ERR_SSL_PROTOCOL_ERROR')


def test_screenshot_endpoint_returns_the_png(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_chrome(tmp_path, monkeypatch, WRITES_PNG)
    response = client.post('/screenshot/', json={'url': f'{URL}#2', 'width': 1240, 'height': 1754})
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'image/png' and response.content.startswith(b'\x89PNG')
    # The size is bounded, and only http(s) pages are captured.
    assert client.post('/screenshot/', json={'url': URL, 'width': 10, 'height': 900}).status_code == 422
    assert client.post('/screenshot/', json={'url': 'file:///etc/passwd'}).status_code == 422


def test_screenshot_endpoint_reports_chrome_failure(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fake_chrome(tmp_path, monkeypatch, LOAD_FAILS)
    response = client.post('/screenshot/', json={'url': URL})
    assert response.status_code == 502 and 'Page load failed' in response.json()['detail']


def test_scene_endpoint_returns_the_scene(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_chrome(tmp_path, monkeypatch, DUMPS_SCENE)
    response = client.post('/scene/', json={'url': f'{URL}?scene'})
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'application/json'
    assert response.json() == {'width': 1056, 'height': 594, 'pages': [{'index': 1, 'blocks': []}]}
    assert client.post('/scene/', json={'url': 'file:///etc/passwd'}).status_code == 422


def test_scene_endpoint_without_a_scene_is_a_502(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_chrome(tmp_path, monkeypatch, DUMPS_PLAIN)
    response = client.post('/scene/', json={'url': URL})
    assert (
        response.status_code == 502
        and response.json()['detail'] == f'no scene in the page at {URL}: is it a deck opened with ?scene'
    )
    fake_chrome(tmp_path, monkeypatch, LOAD_FAILS)
    response = client.post('/scene/', json={'url': URL})
    assert response.status_code == 502 and 'Page load failed' in response.json()['detail']


def test_stderr_tail():
    assert server.stderr_tail('short') == 'short'
    long = 'x' * (server.STDERR_LIMIT + 10) + 'END'
    tail = server.stderr_tail(long)
    assert tail.startswith('...') and tail.endswith('END') and len(tail) == server.STDERR_LIMIT + 3
