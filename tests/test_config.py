"""Tests for `backend/config.py`: the environment-driven settings and their URL handling."""

from __future__ import annotations

import pytest

import config


def test_chrome_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv('OPENARTIFACT_CHROME_URL', raising=False)
    assert config.chrome_url() is None
    monkeypatch.setenv('OPENARTIFACT_CHROME_URL', 'http://chrome:8766/')
    assert config.chrome_url() == 'http://chrome:8766'
    # Render's blueprint passes a private service's address as a bare host:port.
    monkeypatch.setenv('OPENARTIFACT_CHROME_URL', 'openartifact-chrome:8766')
    assert config.chrome_url() == 'http://openartifact-chrome:8766'
    monkeypatch.setenv('OPENARTIFACT_CHROME_URL', 'https://chrome.example.com')
    assert config.chrome_url() == 'https://chrome.example.com'


def test_internal_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('OPENARTIFACT_BASE_URL', 'https://openartifact.dev/')
    monkeypatch.delenv('OPENARTIFACT_INTERNAL_URL', raising=False)
    assert config.internal_url() == 'https://openartifact.dev'
    monkeypatch.setenv('OPENARTIFACT_INTERNAL_URL', 'openartifact:8765')
    assert config.internal_url() == 'http://openartifact:8765'
    monkeypatch.setenv('OPENARTIFACT_INTERNAL_URL', 'http://openartifact.internal:8765/')
    assert config.internal_url() == 'http://openartifact.internal:8765'
