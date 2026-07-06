"""Evidence Bundle PDF via ReportLab (fyp.txt L5, Forensic Notarization).

Compiles full chat log, sandbox screenshots, extracted HVIs, and the hash
chain into a PDF, plus a Section 90A certificate template, then seals it with
an RSA signature. Suitable for submission to MCMC / PDRM.
"""

from __future__ import annotations

from hive.state import SessionState
from hive.vault.hashchain import HashChain


def build_bundle(session: SessionState, chain: HashChain, out_path: str, key_path: str) -> str:
    """Render the evidence PDF, sign it, and return the output path.

    TODO(L5): ReportLab layout; embed screenshots; append hash-chain proof;
    generate s.90A certificate; sign via signer.sign_bytes.
    """
    raise NotImplementedError
