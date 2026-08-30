"""Branded, Unicode-safe forensic evidence bundle generation."""

from __future__ import annotations

import hashlib
import html
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hive.logging_setup import get_logger
from hive.reporting import reporting_guidance
from hive.state import SessionState
from hive.vault.hashchain import HashChain
from hive.vault.package import build_evidence_package, evidence_package_path
from hive.vault.signer import sign_bytes

log = get_logger(__name__)
_FONT_PAIR: tuple[str, str] | None = None
_EMOJI_FONT: str | None = None
_EMOJI_FONT_READY = False
_EMOJI_SEQUENCE = re.compile(
    r"([\u2600-\u27BF\U0001F000-\U0001FAFF]"
    r"(?:[\uFE0E\uFE0F\U0001F3FB-\U0001F3FF]|"
    r"\u200D[\u2600-\u27BF\U0001F000-\U0001FAFF]"
    r"[\uFE0E\uFE0F\U0001F3FB-\U0001F3FF]*)*)"
)

_BRAND_INK = "#20242A"
_BRAND_GOLD = "#E5A321"
_BRAND_PAPER = "#F6F4EF"
_BRAND_MUTED = "#667085"
_BRAND_LINE = "#D9D5CB"


def s90a_certificate(session: SessionState, operator_name: str) -> str:
    """Return the Section 90A certificate text for the operator to sign."""
    now = datetime.now(UTC).isoformat()
    return (
        "SECTION 90A CERTIFICATE (Evidence Act 1950)\n"
        f"I, {operator_name or '________________'}, am responsible for the "
        "operation of the computer system (HIVE) that produced this document. "
        "The document was produced by the computer in the course of its ordinary "
        "use. To the best of my knowledge the computer was operating properly, "
        "and the information is derived from data supplied in the ordinary course "
        "of its activities.\n"
        f"Session peer id: {session.peer_id}\n"
        f"Observed display name: {session.peer_display_name or 'Unavailable'}\n"
        f"Observed username: {session.peer_username or 'Unavailable'}\n"
        f"Certificate generated (UTC): {now}\n"
        "Signature: ______________________   Date: ____________\n"
    )


def _xml(value: object) -> str:
    rendered = "" if value is None else str(value)
    return html.escape(rendered).replace("\n", "<br/>")


def _emoji_font() -> str | None:
    """Register an optional monochrome emoji fallback for evidence reports."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFError, TTFont

    global _EMOJI_FONT, _EMOJI_FONT_READY
    if _EMOJI_FONT_READY:
        return _EMOJI_FONT
    _EMOJI_FONT_READY = True
    candidates = [
        os.getenv("HIVE_REPORT_FONT_EMOJI", ""),
        r"C:\Windows\Fonts\seguiemj.ttf",
        "/usr/share/fonts/truetype/noto/NotoEmoji-Regular.ttf",
        "/usr/share/fonts/truetype/ancient-scripts/Symbola_hint.ttf",
    ]
    for index, path in enumerate(candidates):
        if not path or not Path(path).is_file():
            continue
        name = f"HIVEEmoji{index}"
        try:
            pdfmetrics.registerFont(TTFont(name, path))
        except (OSError, TTFError) as exc:
            log.warning("report emoji font rejected: path=%s error=%s", path, exc)
            continue
        _EMOJI_FONT = name
        return name
    log.warning("no report emoji font available; using explicit Unicode labels")
    return None


def _message_xml(value: object, emoji_font: str | None) -> str:
    """Escape transcript text and render emoji without missing-glyph squares."""

    def render_line(line: str) -> str:
        parts: list[str] = []
        cursor = 0
        for match in _EMOJI_SEQUENCE.finditer(line):
            parts.append(html.escape(line[cursor : match.start()]))
            emoji = match.group(0)
            if emoji_font:
                parts.append(f'<font name="{emoji_font}">{html.escape(emoji)}</font>')
            else:
                codepoints = " ".join(
                    f"U+{ord(character):04X}"
                    for character in emoji
                    if character not in {"\u200d", "\ufe0e", "\ufe0f"}
                )
                parts.append(f"[emoji {codepoints}]")
            cursor = match.end()
        parts.append(html.escape(line[cursor:]))
        return "".join(parts)

    return "<br/>".join(render_line(line) for line in str(value or "").split("\n"))


def _timestamp(value: float | None) -> str:
    if not value:
        return "Time unavailable"
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _font_pair() -> tuple[str, str]:
    """Register an embedded Unicode font, preferring CJK-capable local families."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFError, TTFont

    global _FONT_PAIR
    if _FONT_PAIR is not None:
        return _FONT_PAIR

    candidates = [
        (
            os.getenv("HIVE_REPORT_FONT_REGULAR", ""),
            os.getenv("HIVE_REPORT_FONT_BOLD", ""),
        ),
        (
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        ),
        (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"),
        (r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simhei.ttf"),
    ]
    for index, (regular, bold) in enumerate(candidates):
        if not regular or not Path(regular).is_file():
            continue
        bold_path = bold if bold and Path(bold).is_file() else regular
        normal_name = f"HIVEText{index}"
        bold_name = f"HIVETextBold{index}"
        try:
            pdfmetrics.registerFont(TTFont(normal_name, regular, subfontIndex=0))
            pdfmetrics.registerFont(TTFont(bold_name, bold_path, subfontIndex=0))
        except (OSError, TTFError) as exc:
            log.warning("report font rejected: path=%s error=%s", regular, exc)
            continue
        widths = getattr(pdfmetrics.getFont(normal_name).face, "charWidths", {})
        if ord("A") not in widths or ord("中") not in widths:
            log.warning("report font lacks Latin/CJK coverage: path=%s", regular)
            continue
        pdfmetrics.registerFontFamily(
            normal_name,
            normal=normal_name,
            bold=bold_name,
            italic=normal_name,
            boldItalic=bold_name,
        )
        _FONT_PAIR = (normal_name, bold_name)
        return _FONT_PAIR

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    _FONT_PAIR = ("STSong-Light", "STSong-Light")
    return _FONT_PAIR


def _styles(font: str, bold_font: str) -> dict[str, Any]:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    sample = getSampleStyleSheet()
    base = ParagraphStyle(
        "HIVEBody",
        parent=sample["BodyText"],
        fontName=font,
        fontSize=8.8,
        leading=13,
        textColor=colors.HexColor(_BRAND_INK),
        spaceAfter=6,
        wordWrap="CJK",
    )
    return {
        "body": base,
        "small": ParagraphStyle(
            "HIVESmall",
            parent=base,
            fontSize=7.2,
            leading=10,
            textColor=colors.HexColor(_BRAND_MUTED),
        ),
        "kicker": ParagraphStyle(
            "HIVEKicker",
            parent=base,
            fontName=bold_font,
            fontSize=7.5,
            leading=10,
            textColor=colors.HexColor(_BRAND_GOLD),
            spaceAfter=7,
        ),
        "title": ParagraphStyle(
            "HIVETitle",
            parent=base,
            fontName=bold_font,
            fontSize=25,
            leading=29,
            textColor=colors.HexColor(_BRAND_INK),
            spaceAfter=8,
        ),
        "deck": ParagraphStyle(
            "HIVEDeck",
            parent=base,
            fontSize=10.5,
            leading=15,
            textColor=colors.HexColor(_BRAND_MUTED),
            spaceAfter=16,
        ),
        "section": ParagraphStyle(
            "HIVESection",
            parent=base,
            fontName=bold_font,
            fontSize=13,
            leading=16,
            textColor=colors.HexColor(_BRAND_INK),
            spaceBefore=8,
            spaceAfter=8,
            keepWithNext=True,
        ),
        "label": ParagraphStyle(
            "HIVELabel",
            parent=base,
            fontName=bold_font,
            fontSize=7,
            leading=9,
            textColor=colors.HexColor(_BRAND_MUTED),
            alignment=TA_LEFT,
            spaceAfter=2,
        ),
        "metric": ParagraphStyle(
            "HIVEMetric",
            parent=base,
            fontName=bold_font,
            fontSize=13,
            leading=16,
            spaceAfter=0,
        ),
        "table_header": ParagraphStyle(
            "HIVETableHeader",
            parent=base,
            fontName=bold_font,
            fontSize=7,
            leading=9,
            textColor=colors.white,
            spaceAfter=0,
        ),
        "table": ParagraphStyle(
            "HIVETable",
            parent=base,
            fontSize=7.5,
            leading=10,
            spaceAfter=0,
        ),
        "message": ParagraphStyle(
            "HIVEMessage",
            parent=base,
            fontSize=9,
            leading=13,
            spaceAfter=0,
        ),
    }


def _section(story: list[Any], title: str, styles: dict[str, Any]) -> None:
    from reportlab.platypus import Paragraph

    story.append(Paragraph(_xml(title), styles["section"]))


def _data_table(
    rows: list[list[Any]],
    widths: list[float],
    *,
    repeat_header: bool = True,
) -> Any:
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    table = Table(rows, colWidths=widths, repeatRows=1 if repeat_header else 0, hAlign="LEFT")
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(_BRAND_INK)),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(_BRAND_LINE)),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor(_BRAND_LINE)),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for row in range(1, len(rows)):
        if row % 2 == 0:
            commands.append(
                ("BACKGROUND", (0, row), (-1, row), colors.HexColor(_BRAND_PAPER))
            )
    table.setStyle(TableStyle(commands))
    return table


def _format_bytes(value: int | None) -> str:
    size = max(0, int(value or 0))
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} bytes"


def _embedded_image_card(message: Any, width: float, styles: dict[str, Any]) -> Any | None:
    """Build a bounded evidence preview for a captured inbound image."""
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image, KeepTogether, Paragraph, Table, TableStyle

    path = Path(str(message.media_path or ""))
    media_kind = str(message.media_kind or "").lower()
    media_mime = str(message.media_mime or "").lower()
    if not path.is_file() or not (
        media_kind in {"image", "photo", "qr"} or media_mime.startswith("image/")
    ):
        return None
    try:
        image_width, image_height = ImageReader(str(path)).getSize()
        if image_width <= 0 or image_height <= 0:
            raise ValueError("image dimensions are unavailable")
        max_image_width = min(width, 120 * mm)
        max_image_height = 95 * mm
        scale = min(max_image_width / image_width, max_image_height / image_height, 1.0)
        preview = Image(
            str(path),
            width=image_width * scale,
            height=image_height * scale,
        )
    except Exception as exc:  # noqa: BLE001 - corrupt evidence must not abort sealing
        log.warning("evidence image preview skipped: path=%s error=%s", path, exc)
        return None
    preview.hAlign = "LEFT"
    digest = str(message.media_sha256 or "")
    caption = (
        f"<b>Received image</b> | {_xml(message.media_name or path.name)} | "
        f"{_xml(message.media_mime or 'image')} | {_xml(_format_bytes(message.media_size))}"
    )
    if digest:
        caption += f" | SHA-256 {_xml(digest)}"
    card = Table(
        [[preview], [Paragraph(caption, styles["small"])]],
        colWidths=[width],
        hAlign="LEFT",
    )
    card.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(_BRAND_PAPER)),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(_BRAND_LINE)),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return KeepTogether([card])


def _sandbox_finding_xml(index: int, result: dict[str, Any]) -> str:
    """Render a complete, non-blank sandbox finding for the evidence report."""
    redirects = list(result.get("redirect_chain") or [])
    blocked = list(result.get("blocked_requests") or [])
    status = int(result.get("http_status") or 0)
    certificate_age = result.get("certificate_age_days")
    screenshot_path = Path(str(result.get("screenshot_path") or ""))
    screenshot = "Captured" if screenshot_path.is_file() else "Not captured"
    if result.get("screenshot_error"):
        screenshot = f"Unavailable - {result['screenshot_error']}"
    values = [
        ("Finding", f"{index}: {result.get('verdict_signal') or 'unclassified'}"),
        ("Submitted URL", result.get("url") or "Unavailable"),
        ("Final URL", result.get("final_url") or "Unavailable"),
        ("HTTP status", status if status else "Unavailable"),
        ("Destination IP", result.get("dest_ip") or "Unavailable"),
        ("Access state", result.get("access_state") or "unknown"),
        ("Fetcher", result.get("fetcher") or "unknown"),
        ("Redirects", " -> ".join(str(item) for item in redirects) or "None observed"),
        ("Blocked requests", len(blocked)),
        (
            "Certificate age",
            f"{certificate_age} days" if certificate_age is not None else "Unavailable",
        ),
        ("Runtime", f"{int(result.get('runtime_ms') or 0)} ms"),
        ("Challenge", result.get("challenge_provider") or "None detected"),
        ("Cloaking suspected", "Yes" if result.get("cloaking_suspected") else "No"),
        ("Screenshot", screenshot),
    ]
    if result.get("certificate_error"):
        values.append(("Certificate error", result["certificate_error"]))
    if result.get("error"):
        values.append(("Error detail", result["error"]))
    return "<br/>".join(f"<b>{_xml(label)}:</b> {_xml(value)}" for label, value in values)


def build_bundle(
    session: SessionState,
    chain: HashChain,
    out_path: str,
    key_path: str,
    operator_name: str = "",
) -> str:
    """Render, brand, sign, and return a Unicode-safe evidence PDF."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    signature_target = Path(str(target) + ".sig")
    signature_temporary = Path(str(temporary) + ".sig")
    package_target = evidence_package_path(target)

    font, bold_font = _font_pair()
    emoji_font = _emoji_font()
    styles = _styles(font, bold_font)
    generated = datetime.now(UTC)
    document_id = f"HIVE-{session.peer_id}-{generated:%Y%m%d%H%M%S}"
    logo_path = Path(__file__).parents[1] / "webpanel" / "resources" / "logo.png"

    doc = SimpleDocTemplate(
        str(temporary),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=28 * mm,
        bottomMargin=18 * mm,
        title=f"HIVE Intelligence Report - Peer {session.peer_id}",
        author="HIVE - Honeypot for Intelligence, Verdict & Evidence",
        subject="Signed anti-scam intelligence and evidence report",
    )

    def page_chrome(canvas: Any, current_doc: Any) -> None:
        width, height = A4
        canvas.saveState()
        canvas.setFillColor(colors.HexColor(_BRAND_INK))
        canvas.rect(0, height - 20 * mm, width, 20 * mm, fill=1, stroke=0)
        if logo_path.is_file():
            canvas.drawImage(
                str(logo_path),
                18 * mm,
                height - 16.5 * mm,
                width=11 * mm,
                height=11 * mm,
                preserveAspectRatio=True,
                mask="auto",
            )
        canvas.setFillColor(colors.white)
        canvas.setFont(bold_font, 12)
        canvas.drawString(32 * mm, height - 10.5 * mm, "HIVE")
        canvas.setFont(font, 7)
        canvas.setFillColor(colors.HexColor("#CDD0D5"))
        canvas.drawString(32 * mm, height - 14.5 * mm, "INTELLIGENCE & EVIDENCE")
        canvas.setFont(bold_font, 6.5)
        canvas.setFillColor(colors.HexColor(_BRAND_GOLD))
        canvas.drawRightString(width - 18 * mm, height - 11.5 * mm, "RESTRICTED")

        canvas.setStrokeColor(colors.HexColor(_BRAND_LINE))
        canvas.line(18 * mm, 12 * mm, width - 18 * mm, 12 * mm)
        canvas.setFillColor(colors.HexColor(_BRAND_MUTED))
        canvas.setFont(font, 6.5)
        canvas.drawString(18 * mm, 8 * mm, document_id)
        canvas.drawRightString(width - 18 * mm, 8 * mm, f"PAGE {current_doc.page}")
        canvas.restoreState()

    story: list[Any] = []
    story.append(Paragraph("SIGNED CASE FILE", styles["kicker"]))
    story.append(Paragraph(f"Intelligence report: peer {_xml(session.peer_id)}", styles["title"]))
    story.append(
        Paragraph(
            "A structured record of the engagement, extracted indicators, "
            "sandbox findings, and cryptographic evidence integrity.",
            styles["deck"],
        )
    )

    metric_values = [
        ("VERDICT", session.verdict.replace("_", " ").upper()),
        ("CONFIDENCE", f"{session.verdict_score:.1%}"),
        ("INBOUND MESSAGES", str(session.turn_count)),
        ("INDICATORS", str(len(session.hvis))),
    ]
    metric_cells = [
        [Paragraph(_xml(label), styles["label"]), Paragraph(_xml(value), styles["metric"])]
        for label, value in metric_values
    ]
    metrics = Table([metric_cells], colWidths=[doc.width / 4] * 4)
    metrics.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(_BRAND_PAPER)),
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor(_BRAND_LINE)),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor(_BRAND_LINE)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(metrics)
    story.append(Spacer(1, 8 * mm))

    _section(story, "Case overview", styles)
    overview = [
        [
            Paragraph("FIELD", styles["table_header"]),
            Paragraph("RECORDED VALUE", styles["table_header"]),
        ],
        [Paragraph("Peer ID", styles["table"]), Paragraph(_xml(session.peer_id), styles["table"])],
        [
            Paragraph("Observed display name", styles["table"]),
            Paragraph(_xml(session.peer_display_name or "Unavailable"), styles["table"]),
        ],
        [
            Paragraph("Observed username", styles["table"]),
            Paragraph(_xml(session.peer_username or "Unavailable"), styles["table"]),
        ],
        [
            Paragraph("Identity observed", styles["table"]),
            Paragraph(_xml(_timestamp(session.identity_observed_ts)), styles["table"]),
        ],
        [Paragraph("Persona", styles["table"]), Paragraph(_xml(session.persona), styles["table"])],
        [
            Paragraph("Session started", styles["table"]),
            Paragraph(_xml(_timestamp(session.started_ts)), styles["table"]),
        ],
        [Paragraph("Document ID", styles["table"]), Paragraph(document_id, styles["table"])],
        [
            Paragraph("Generated", styles["table"]),
            Paragraph(generated.strftime("%Y-%m-%d %H:%M:%S UTC"), styles["table"]),
        ],
    ]
    story.append(_data_table(overview, [42 * mm, doc.width - 42 * mm]))
    story.append(Spacer(1, 6 * mm))

    _section(story, "Section 90A certificate", styles)
    certificate = "<br/>".join(
        _xml(line) if line else "&nbsp;"
        for line in s90a_certificate(session, operator_name).splitlines()
    )
    cert_table = Table([[Paragraph(certificate, styles["body"])]], colWidths=[doc.width])
    cert_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF8E6")),
                ("BOX", (0, 0), (-1, -1), 0.7, colors.HexColor(_BRAND_GOLD)),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    story.append(cert_table)
    story.append(PageBreak())

    story.append(Paragraph("ENGAGEMENT RECORD", styles["kicker"]))
    story.append(Paragraph("Conversation transcript", styles["title"]))
    story.append(
        Paragraph(
            "Messages are shown in chronological order. Original multilingual text is preserved.",
            styles["deck"],
        )
    )
    if not session.messages:
        story.append(Paragraph("No conversation messages were recorded.", styles["body"]))
    for message in session.messages:
        is_agent = message.role == "agent"
        role = "HIVE PERSONA" if is_agent else "EXTERNAL PARTY"
        accent = "#667085" if is_agent else _BRAND_GOLD
        background = "#F2F4F7" if is_agent else "#FFF8E6"
        provenance = "PRE-TAKEOVER TRIGGER" if message.pre_takeover else message.platform.upper()
        captured = (
            f" | Captured {_xml(_timestamp(message.captured_ts))}"
            if message.captured_ts is not None and message.captured_ts != message.ts
            else ""
        )
        body = Paragraph(
            f"<font name=\"{bold_font}\" size=\"7\">{role} | {provenance} | "
            f"Message {_xml(message.msg_id)} | {_xml(_timestamp(message.ts))}{captured}"
            f"</font><br/>{_message_xml(message.text, emoji_font)}",
            styles["message"],
        )
        bubble = Table([[body]], colWidths=[doc.width])
        bubble.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(background)),
                    ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(accent)),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        story.append(bubble)
        image_card = _embedded_image_card(message, doc.width, styles)
        if image_card is not None:
            story.append(Spacer(1, 2 * mm))
            story.append(image_card)
        story.append(Spacer(1, 3 * mm))

    _section(story, "Extracted intelligence", styles)
    if session.hvis:
        rows = [
            [
                Paragraph(value, styles["table_header"])
                for value in ("TYPE", "VALUE", "CONF.", "SOURCE")
            ]
        ]
        rows.extend(
            [
                Paragraph(_xml(item.kind.replace("_", " ").upper()), styles["table"]),
                Paragraph(_xml(item.value), styles["table"]),
                Paragraph(f"{item.confidence:.0%}", styles["table"]),
                Paragraph(str(item.source_msg_id), styles["table"]),
            ]
            for item in session.hvis
        )
        story.append(_data_table(rows, [35 * mm, 86 * mm, 20 * mm, 20 * mm]))
    else:
        story.append(Paragraph("No high-value indicators were extracted.", styles["body"]))

    _section(story, "Media intelligence", styles)
    if session.media_analysis:
        for index, analysis in enumerate(session.media_analysis, start=1):
            source = str(analysis.get("source") or "unknown").replace("_", " ").upper()
            finding = (
                f"<b>Media finding {index}: {_xml(source)}</b><br/>"
                f"Source message: {_xml(analysis.get('source_msg_id', 'unknown'))}<br/>"
                f"Indicators extracted: {_xml(analysis.get('indicator_count', 0))}<br/>"
                f"Description / local text: {_xml(analysis.get('description', ''))}"
            )
            story.append(
                Table(
                    [[Paragraph(finding, styles["body"])]],
                    colWidths=[doc.width],
                    style=TableStyle(
                        [
                            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(_BRAND_LINE)),
                            ("LEFTPADDING", (0, 0), (-1, -1), 8),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                            ("TOPPADDING", (0, 0), (-1, -1), 7),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                        ]
                    ),
                )
            )
            story.append(Spacer(1, 3 * mm))
    else:
        story.append(Paragraph("No media analyses were recorded.", styles["body"]))

    _section(story, "Threat-intelligence observations", styles)
    if session.threat_intelligence:
        rows = [
            [
                Paragraph(value, styles["table_header"])
                for value in ("PROVIDER", "OBSERVABLE", "RESULT", "CHECKED")
            ]
        ]
        rows.extend(
            [
                Paragraph(
                    _xml(item.get("provider_label", item.get("provider", ""))),
                    styles["table"],
                ),
                Paragraph(_xml(item.get("observable", "")), styles["table"]),
                Paragraph(
                    _xml(f"{str(item.get('risk', 'unknown')).upper()}: {item.get('summary', '')}"),
                    styles["table"],
                ),
                Paragraph(_xml(_timestamp(item.get("checked_ts"))), styles["table"]),
            ]
            for item in session.threat_intelligence
        )
        story.append(_data_table(rows, [28 * mm, 48 * mm, 65 * mm, 28 * mm]))
        story.append(
            Paragraph(
                "Third-party results are time-bounded corroborating observations. "
                "No match means no known report at query time, not proof of safety.",
                styles["small"],
            )
        )
    else:
        story.append(
            Paragraph("No external intelligence observations were recorded.", styles["body"])
        )

    _section(story, "Sandbox findings", styles)
    if session.sandbox_results:
        for index, result in enumerate(session.sandbox_results, start=1):
            finding = _sandbox_finding_xml(index, result)
            card = Table([[Paragraph(finding, styles["body"])]], colWidths=[doc.width])
            card.setStyle(
                TableStyle(
                    [
                        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(_BRAND_LINE)),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 7),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ]
                )
            )
            story.append(card)
            story.append(Spacer(1, 3 * mm))
    else:
        story.append(Paragraph("No sandbox analyses were recorded.", styles["body"]))

    _section(story, "Recommended reporting actions", styles)
    guidance = reporting_guidance()
    story.append(Paragraph(_xml(guidance["disclaimer"]), styles["small"]))
    for index, step in enumerate(guidance["steps"], start=1):
        source = (
            f"<br/><font size=\"7\">Official source: {_xml(step['source_url'])}</font>"
            if step["source_url"]
            else ""
        )
        story.append(
            Paragraph(
                f"<b>{index}. {_xml(step['title'])}</b><br/>"
                f"{_xml(step['action'])}{source}",
                styles["body"],
            )
        )
    story.append(
        Paragraph(
            f"Guidance reviewed {_xml(guidance['reviewed_date'])}; verify current "
            "instructions before use.",
            styles["small"],
        )
    )

    story.append(PageBreak())
    story.append(Paragraph("EVIDENCE INTEGRITY", styles["kicker"]))
    story.append(Paragraph("Cryptographic chain of custody", styles["title"]))
    story.append(
        Paragraph(
            "Each event is linked to the preceding event by SHA-256. The detached RSA-PSS "
            "signature accompanying this PDF seals the final report bytes.",
            styles["deck"],
        )
    )
    integrity = "VALID" if chain.verify() else "BROKEN"
    integrity_color = "#137A4A" if integrity == "VALID" else "#B42318"
    integrity_box = Table(
        [
            [Paragraph("CHAIN STATUS", styles["label"]), Paragraph(integrity, styles["metric"])],
            [
                Paragraph("EVENTS", styles["label"]),
                Paragraph(str(len(chain.entries)), styles["metric"]),
            ],
        ],
        colWidths=[35 * mm, doc.width - 35 * mm],
    )
    integrity_box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(_BRAND_PAPER)),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor(integrity_color)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.append(integrity_box)
    story.append(Spacer(1, 6 * mm))

    chain_rows = [[
        Paragraph("#", styles["table_header"]),
        Paragraph("ENTRY HASH (SHA-256)", styles["table_header"]),
        Paragraph("PREVIOUS HASH", styles["table_header"]),
    ]]
    chain_rows.extend(
        [
            Paragraph(str(entry.index), styles["table"]),
            Paragraph(_xml(entry.entry_hash), styles["small"]),
            Paragraph(_xml(entry.prev_hash or "GENESIS"), styles["small"]),
        ]
        for entry in chain.entries
    )
    story.append(_data_table(chain_rows, [12 * mm, 76 * mm, 73 * mm]))
    story.append(Spacer(1, 8 * mm))
    story.append(
        Paragraph(
            "This report is generated by HIVE - Honeypot for Intelligence, Verdict & "
            "Evidence. Legal admissibility depends on proper operator completion of the "
            "Section 90A certificate and preservation of the PDF, signature, and public key.",
            styles["small"],
        )
    )

    try:
        doc.build(story, onFirstPage=page_chrome, onLaterPages=page_chrome)
        pdf_bytes = temporary.read_bytes()
        digest = hashlib.sha256(pdf_bytes).hexdigest()
        signature_temporary.write_bytes(sign_bytes(pdf_bytes, key_path))
        # Publish the detached signature first and the PDF last. Indexers only
        # discover PDFs, so they can never observe a half-sealed case file.
        signature_temporary.replace(signature_target)
        temporary.replace(target)
        build_evidence_package(
            target,
            key_path,
            output_path=package_target,
            attachments=(
                (message.media_path, message.media_name or Path(message.media_path).name)
                for message in session.messages
                if message.media_path
            ),
        )
    except Exception:
        temporary.unlink(missing_ok=True)
        signature_temporary.unlink(missing_ok=True)
        signature_target.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        package_target.unlink(missing_ok=True)
        raise

    log.info(
        "L5 bundle: wrote %s (%d bytes, sha256=%s) + %s + %s",
        target,
        len(pdf_bytes),
        digest[:12],
        signature_target,
        package_target,
    )
    return str(target)
