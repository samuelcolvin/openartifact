"""Tests for `backend/pdf.py`. Chrome itself is not run; the command assembly and error paths are."""

from __future__ import annotations

from pathlib import Path

import pdf
import pytest

from build import BuildError

URL = 'http://127.0.0.1:8000/artifacts/demo-abc123/'


def test_missing_chrome_reports_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pdf, 'find_chrome', lambda: None)
    with pytest.raises(BuildError) as exc_info:
        pdf.print_to_pdf(URL, tmp_path / 'out.pdf')
    message = str(exc_info.value)
    assert message.startswith('Chrome / Chromium not found')
    # No paper flags: each artifact type's stylesheet sets `@page`, which Chrome honours.
    assert '--paper-' not in message
    assert message.endswith(f' --print-to-pdf={tmp_path / "out.pdf"} {URL}')


def test_chrome_failure_reports_command_and_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake = tmp_path / 'chrome'
    fake.write_text('#!/bin/sh\necho boom >&2\nexit 3\n')
    fake.chmod(0o755)
    monkeypatch.setattr(pdf, 'find_chrome', lambda: str(fake))
    with pytest.raises(BuildError, match=r'Chrome exited with code 3:\n  .*--headless=new.*\nboom'):
        pdf.print_to_pdf(URL, tmp_path / 'out.pdf')


def test_success_returns_resolved_path_and_creates_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake = tmp_path / 'chrome'
    fake.write_text('#!/bin/sh\nexit 0\n')
    fake.chmod(0o755)
    monkeypatch.setattr(pdf, 'find_chrome', lambda: str(fake))
    out = pdf.print_to_pdf(URL, tmp_path / 'nested' / 'out.pdf')
    assert out == (tmp_path / 'nested' / 'out.pdf').resolve()
    assert out.parent.is_dir()


def test_shell_quote():
    assert pdf.shell_quote('--headless=new') == '--headless=new'
    assert pdf.shell_quote("it's here") == "'it'\\''s here'"
