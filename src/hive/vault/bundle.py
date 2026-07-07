"""Evidence Bundle PDF via ReportLab (fyp.txt L5, Forensic Notarization).

Compiles the full chat log, extracted HVIs, sandbox results, verdict + signal
trail, and the SHA-256 hash chain into a PDF, prepends a Section 90A
certificate, then seals the PDF bytes with an RSA-PSS signature written to a
`.sig` sidecar. Suitable for submission to MCMC / PDRM.

Admissibility (fyp.txt L5): the s.90A certificate must be completed/signed by a
person responsible for the computer; timestamps should be anchored to a trusted
time source. This module produces the artefact and certificate template — it
does not by itself guarantee judicial weight.

`reportlab` is lazy-imported so it is only required where a bundle is built.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from hive.logging_setup import get_logger
from hive.state import SessionState
from hive.vault.hashchain import HashChain
from hive.vault.signer import sign_bytes

log = get_logger(__name__)


def s90a_certificate(session: SessionState, operator_name: str) -> str:
    """Return the Section 90A certificate text for the operator to sign."""
    now = datetime.now(timezone.utc).isoformat()
    return (
        "SECTION 90A CERTIFICATE (Evidence Act 1950)\n"
        f"I, {operator_name or '________________'}, am responsible for the "
        "operation of the computer system (HIVE) that produced this document. "
        "The document was produced by the computer in the course of its ordinary "
        "use. To the best of my knowledge the computer was operating properly, "
        "and the information is derived from data supplied in the ordinary course "
        "of its activities.\n"
        f"Session peer id: {session.peer_id}\n"
        f"Certificate generated (UTC): {now}\n"
        "Signature: ______________________   Date: ____________\n"
    )


def build_bundle(
    session: SessionState,
    chain: HashChain,
    out_path: str,
    key_path: str,
    operator_name: str = "",
) -> str:
    """Render the evidence PDF, sign it, and return the output path.

    Writes `out_path` and `out_path + '.sig'` (the detached RSA signature).
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
    from reportlab.lib import colors

    styles = getSampleStyleSheet()
    story = []

    def h(text: str) -> None:
        story.append(Paragraph(text, styles["Heading2"]))

    def p(text: str) -> None:
        story.append(Paragraph(text, styles["Normal"]))

    story.append(Paragraph("HIVE Evidence Bundle", styles["Title"]))
    p(f"Verdict: <b>{session.verdict}</b> (score {session.verdict_score:.3f})")
    p(f"Turns: {session.turn_count} &nbsp; Peer: {session.peer_id} &nbsp; Persona: {session.persona}")
    story.append(Spacer(1, 12))

    h("Section 90A Certificate")
    for line in s90a_certificate(session, operator_name).splitlines():
        p(line or "&nbsp;")
    story.append(Spacer(1, 12))

    h("Conversation transcript")
    for m in session.messages:
        who = "SCAMMER" if m.role == "stranger" else "AGENT" if m.role == "agent" else "SYS"
        p(f"<b>{who}:</b> {(m.text or '').replace('<', '&lt;')}")
    story.append(Spacer(1, 12))

    if session.hvis:
        h("Extracted High-Value Indicators")
        rows = [["Kind", "Value", "Conf", "Msg"]] + [
            [x.kind, x.value, f"{x.confidence:.2f}", str(x.source_msg_id)] for x in session.hvis
        ]
        _table(story, rows, Table, TableStyle, colors)
        story.append(Spacer(1, 12))

    if session.sandbox_results:
        h("Forensic sandbox results")
        for r in session.sandbox_results:
            p(f"{r.get('url','')} → {r.get('final_url','')} "
              f"[{r.get('verdict_signal','')}], IP {r.get('dest_ip','')}, "
              f"cloaking={r.get('cloaking_suspected')}")
        story.append(Spacer(1, 12))

    h("SHA-256 hash chain")
    rows = [["#", "Entry hash (sha256)", "Prev hash"]] + [
        [str(e.index), e.entry_hash[:24] + "…", (e.prev_hash[:16] + "…")] for e in chain.entries
    ]
    _table(story, rows, Table, TableStyle, colors)
    p(f"Chain integrity: <b>{'VALID' if chain.verify() else 'BROKEN'}</b> "
      f"({len(chain.entries)} entries)")

    SimpleDocTemplate(out_path, pagesize=A4).build(story)

    with open(out_path, "rb") as fh:
        pdf_bytes = fh.read()
    digest = hashlib.sha256(pdf_bytes).hexdigest()
    signature = sign_bytes(pdf_bytes, key_path)
    sig_path = out_path + ".sig"
    with open(sig_path, "wb") as fh:
        fh.write(signature)

    log.info("L5 bundle: wrote %s (%d bytes, sha256=%s) + %s", out_path, len(pdf_bytes), digest[:12], sig_path)
    return out_path


def _table(story, rows, Table, TableStyle, colors) -> None:
    t = Table(rows, hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(t)
