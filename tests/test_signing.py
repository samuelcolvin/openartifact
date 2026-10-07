"""Tests for `backend/signing.py`: purpose-keyed, expiring tokens."""

from __future__ import annotations

import pytest

import signing

A = b'purpose a'
B = b'purpose b'


@pytest.fixture(autouse=True)
def secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('OPENARTIFACT_SECRET_KEY', 'test secret')


def test_round_trip_and_expiry():
    token = signing.token(A, 'x', 'y', expires=1_000)
    assert signing.verify(A, token, 'x', 'y', now=999) == 1_000
    with pytest.raises(signing.SignatureError, match='expired'):
        signing.verify(A, token, 'x', 'y', now=1_001)


def test_parts_purpose_and_tampering_matter():
    token = signing.token(A, 'x', 'y', expires=1_000)
    with pytest.raises(signing.SignatureError, match='does not match'):
        signing.verify(A, token, 'x', 'z', now=0)
    with pytest.raises(signing.SignatureError, match='does not match'):
        signing.verify(B, token, 'x', 'y', now=0)
    _, _, sig = token.partition('.')
    with pytest.raises(signing.SignatureError, match='does not match'):
        signing.verify(A, f'2000.{sig}', 'x', 'y', now=0)
    # Joining parts differently must not collide: ('x\ny',) is not ('x', 'y').
    with pytest.raises(signing.SignatureError, match='does not match'):
        signing.verify(A, token, 'x\ny', now=0)
    with pytest.raises(signing.SignatureError, match='malformed'):
        signing.verify(A, 'junk', 'x', 'y', now=0)
    with pytest.raises(signing.SignatureError, match='malformed'):
        signing.verify(A, '', 'x', 'y', now=0)
