"""S7 encrypted session store tests (fyp.txt S7)."""

import pytest

from hive.security.session_store import load_session, save_session


def test_roundtrip(tmp_path):
    path = str(tmp_path / "user.session")
    secret = "1AbCdEf...telethon-string-session..."
    save_session(secret, path, "correct horse battery staple")
    assert load_session(path, "correct horse battery staple") == secret


def test_wrong_passphrase_fails(tmp_path):
    path = str(tmp_path / "user.session")
    save_session("secret", path, "right-pass")
    with pytest.raises(ValueError):
        load_session(path, "wrong-pass")


def test_ciphertext_is_not_plaintext(tmp_path):
    path = str(tmp_path / "user.session")
    save_session("PLAINTEXTMARKER", path, "pw")
    blob = (tmp_path / "user.session").read_bytes()
    assert b"PLAINTEXTMARKER" not in blob
    assert blob[:4] == b"HIVE"


def test_empty_passphrase_rejected(tmp_path):
    with pytest.raises(ValueError):
        save_session("secret", str(tmp_path / "s"), "")
