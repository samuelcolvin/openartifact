"""Tests for `backend/upload.py`: path and size validation, and the signed token. No database needed."""

from __future__ import annotations

import uuid

import pytest
import upload

ARTIFACT = uuid.UUID('12345678-1234-5678-1234-567812345678')


@pytest.mark.parametrize('path', ['main.md', 'assets/logo.png', 'components/Card.html', 'assets/a b/é.png', 'x'])
def test_validate_path_accepts_relative_paths(path: str):
    assert upload.validate_path(path) == path


@pytest.mark.parametrize(
    'path, message',
    [
        ('', 'path is empty'),
        ('/etc/passwd', 'relative to the artifact directory'),
        ('a\\b.txt', 'relative to the artifact directory'),
        ('assets/../main.md', 'empty, . and .. segments'),
        ('./main.md', 'empty, . and .. segments'),
        ('assets//logo.png', 'empty, . and .. segments'),
        ('assets/', 'empty, . and .. segments'),
        ('.git/config', '.git entries'),
        ('assets/.gitignore', '.git entries'),
        ('dist/index.html', 'build output'),
        ('a\nb', 'control characters'),
    ],
)
def test_validate_path_rejects(path: str, message: str):
    with pytest.raises(upload.UploadError, match=message):
        upload.validate_path(path)


def test_validate_size():
    assert upload.validate_size('a', 0) == 0
    assert upload.validate_size('a', upload.MAX_UPLOAD_SIZE) == upload.MAX_UPLOAD_SIZE
    with pytest.raises(upload.UploadError, match="'a': size must not be negative"):
        upload.validate_size('a', -1)
    with pytest.raises(upload.UploadError, match=r"'a': 10,485,761 bytes is over the 10,485,760 byte limit"):
        upload.validate_size('a', upload.MAX_UPLOAD_SIZE + 1)


def test_token_round_trip():
    token = upload.make_token(ARTIFACT, 'assets/logo.png', 1234, expires=2_000_000_000)
    assert token.startswith('2000000000.')
    upload.verify_token(token, ARTIFACT, 'assets/logo.png', 1234)
    # Any one value changed, and the signature no longer matches.
    for artifact, path, size in [
        (uuid.uuid4(), 'assets/logo.png', 1234),
        (ARTIFACT, 'assets/logo.svg', 1234),
        (ARTIFACT, 'assets/logo.png', 1235),
    ]:
        with pytest.raises(upload.UploadError, match='does not match'):
            upload.verify_token(token, artifact, path, size)
    # Nor can the expiry be moved: it is signed too.
    _, _, signature = token.partition('.')
    with pytest.raises(upload.UploadError, match='does not match'):
        upload.verify_token(f'3000000000.{signature}', ARTIFACT, 'assets/logo.png', 1234)


def test_token_expires():
    token = upload.make_token(ARTIFACT, 'main.md', 10, expires=1_000)
    upload.verify_token(token, ARTIFACT, 'main.md', 10, now=999)
    with pytest.raises(upload.UploadError, match='expired'):
        upload.verify_token(token, ARTIFACT, 'main.md', 10, now=1_001)


@pytest.mark.parametrize('token', ['', 'nope', 'abc.def', '.sig', '123'])
def test_malformed_tokens(token: str):
    with pytest.raises(upload.UploadError, match='malformed|does not match'):
        upload.verify_token(token, ARTIFACT, 'main.md', 10)


def test_secret_key_prefers_the_secret_over_the_dev_token(monkeypatch: pytest.MonkeyPatch):
    with_dev_token = upload.make_token(ARTIFACT, 'main.md', 10, expires=1_000)
    monkeypatch.setenv('OPENARTIFACT_SECRET_KEY', 'another key')
    with_secret = upload.make_token(ARTIFACT, 'main.md', 10, expires=1_000)
    assert with_secret != with_dev_token
    with pytest.raises(upload.UploadError, match='does not match'):
        upload.verify_token(with_dev_token, ARTIFACT, 'main.md', 10, now=0)
    monkeypatch.delenv('OPENARTIFACT_SECRET_KEY')
    monkeypatch.delenv('OPENARTIFACT_DEV_TOKEN')
    with pytest.raises(RuntimeError, match='OPENARTIFACT_SECRET_KEY or OPENARTIFACT_DEV_TOKEN is required'):
        upload.make_token(ARTIFACT, 'main.md', 10, expires=1_000)
