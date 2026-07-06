"""Hash chain integrity tests (fyp.txt L5)."""

from hive.vault.hashchain import HashChain


def test_chain_verifies():
    chain = HashChain()
    chain.append({"event": "msg", "text": "a"}, ts=1.0)
    chain.append({"event": "hvi", "value": "123456"}, ts=2.0)
    assert chain.verify() is True


def test_tamper_breaks_chain():
    chain = HashChain()
    chain.append({"event": "msg", "text": "a"}, ts=1.0)
    chain.append({"event": "msg", "text": "b"}, ts=2.0)
    chain.entries[0].payload["text"] = "tampered"
    assert chain.verify() is False
