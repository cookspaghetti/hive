"""Portable, independently verifiable HIVE evidence packages."""

from __future__ import annotations

import hashlib
import json
import re
import time
import zipfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from hive.vault.signer import (
    public_key_bytes,
    public_key_fingerprint,
    sign_bytes,
    verify_signature_with_public_key,
)

_PACKAGE_NAME = re.compile(r"^bundle_(-?\d+)(?:_(\d+))?\.evidence\.zip$")
_MAX_MEMBER_BYTES = 100 * 1024 * 1024
_README = """HIVE evidence verification package

Contents:
- The signed PDF evidence bundle.
- Captured attachments, when the session contains locally retained media.
- The PDF's detached RSA-PSS/SHA-256 signature.
- The public verification key.
- A signed checksum manifest.

Verify from a HIVE source checkout:
    python -m hive.verify_evidence <package.evidence.zip>

A PASS proves that the packaged files match the signing key and have not been
modified since sealing. It does not by itself establish identity, custody, or
legal admissibility.
"""


def evidence_package_path(pdf_path: str | Path) -> Path:
    """Return the package path paired with a bundle PDF."""
    return Path(pdf_path).with_suffix(".evidence.zip")


def parse_evidence_package_name(filename: str) -> tuple[int, int | None] | None:
    """Parse a package filename without accepting paths."""
    match = _PACKAGE_NAME.fullmatch(filename)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)) if match.group(2) else None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_evidence_package(
    pdf_path: str | Path,
    private_key_path: str | Path,
    *,
    output_path: str | Path | None = None,
    attachments: Iterable[tuple[str | Path, str]] = (),
) -> Path:
    """Build an atomic ZIP containing evidence and all verification material."""
    pdf = Path(pdf_path)
    signature = Path(str(pdf) + ".sig")
    target = Path(output_path) if output_path is not None else evidence_package_path(pdf)
    if not pdf.is_file() or not signature.is_file():
        raise FileNotFoundError("the evidence PDF and detached signature are required")

    pdf_bytes = pdf.read_bytes()
    signature_bytes = signature.read_bytes()
    public_key = public_key_bytes(str(private_key_path))
    packaged_attachments: list[tuple[str, bytes, str]] = []
    for index, (source_value, display_value) in enumerate(attachments, start=1):
        source = Path(source_value)
        if not source.is_file():
            raise FileNotFoundError(f"evidence attachment not found: {source}")
        data = source.read_bytes()
        if len(data) > _MAX_MEMBER_BYTES:
            raise ValueError(f"evidence attachment exceeds size limit: {source.name}")
        display_name = Path(str(display_value or source.name)).name
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", display_name).strip("._")
        archive_name = f"attachment_{index:03d}_{safe_name or 'capture.bin'}"
        packaged_attachments.append((archive_name, data, display_name))

    manifest = {
        "schema_version": 1,
        "package_type": "HIVE evidence package",
        "created_utc": datetime.now(UTC).isoformat(),
        "signature_algorithm": "RSA-PSS/SHA-256",
        "signing_key_fingerprint": public_key_fingerprint(public_key),
        "files": {
            "evidence_pdf": {"name": pdf.name, "sha256": _sha256(pdf_bytes)},
            "evidence_signature": {
                "name": signature.name,
                "sha256": _sha256(signature_bytes),
            },
            "public_key": {"name": "public_key.pem", "sha256": _sha256(public_key)},
        },
        "attachments": [
            {
                "name": name,
                "source_name": display_name,
                "size": len(data),
                "sha256": _sha256(data),
            }
            for name, data, display_name in packaged_attachments
        ],
    }
    manifest_bytes = json.dumps(
        manifest,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    manifest_signature = sign_bytes(manifest_bytes, str(private_key_path))
    temporary = target.with_name(f".{target.name}.{time.time_ns()}.tmp")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            archive.writestr(pdf.name, pdf_bytes)
            archive.writestr(signature.name, signature_bytes)
            archive.writestr("public_key.pem", public_key)
            archive.writestr("manifest.json", manifest_bytes)
            archive.writestr("manifest.sig", manifest_signature)
            archive.writestr("VERIFY.txt", _README.encode("utf-8"))
            for name, data, _display_name in packaged_attachments:
                archive.writestr(name, data)
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    return target


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        path.name == name
        and "/" not in name
        and "\\" not in name
        and name not in {"", ".", ".."}
    )


def verify_evidence_package(package_path: str | Path) -> dict[str, Any]:
    """Verify package structure, checksums, manifest signature, and PDF signature."""
    result: dict[str, Any] = {
        "package": str(Path(package_path)),
        "ok": False,
        "checks": {},
        "errors": [],
    }
    try:
        with zipfile.ZipFile(package_path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            structure_ok = (
                len(names) == len(set(names))
                and all(_safe_member(name) for name in names)
                and all(info.file_size <= _MAX_MEMBER_BYTES for info in infos)
            )
            result["checks"]["safe_structure"] = structure_ok
            if not structure_ok:
                raise ValueError("unsafe, duplicate, or oversized package member")
            required = {"manifest.json", "manifest.sig", "public_key.pem", "VERIFY.txt"}
            if not required.issubset(names):
                raise ValueError("required verification files are missing")

            manifest_bytes = archive.read("manifest.json")
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            if manifest.get("schema_version") != 1:
                raise ValueError("unsupported evidence package schema")
            files = manifest.get("files") or {}
            pdf_meta = files.get("evidence_pdf") or {}
            signature_meta = files.get("evidence_signature") or {}
            key_meta = files.get("public_key") or {}
            pdf_name = str(pdf_meta.get("name") or "")
            signature_name = str(signature_meta.get("name") or "")
            if not _safe_member(pdf_name) or not _safe_member(signature_name):
                raise ValueError("manifest contains unsafe evidence filenames")
            if pdf_name not in names or signature_name not in names:
                raise ValueError("manifest evidence files are missing")

            pdf_bytes = archive.read(pdf_name)
            signature_bytes = archive.read(signature_name)
            public_key = archive.read("public_key.pem")
            checksums = {
                "pdf_checksum": _sha256(pdf_bytes) == pdf_meta.get("sha256"),
                "signature_checksum": (
                    _sha256(signature_bytes) == signature_meta.get("sha256")
                ),
                "public_key_checksum": _sha256(public_key) == key_meta.get("sha256"),
            }
            result["checks"].update(checksums)
            for index, attachment in enumerate(manifest.get("attachments") or [], start=1):
                attachment_name = str(attachment.get("name") or "")
                if not _safe_member(attachment_name) or attachment_name not in names:
                    raise ValueError("manifest attachment is missing or unsafe")
                result["checks"][f"attachment_{index:03d}_checksum"] = _sha256(
                    archive.read(attachment_name)
                ) == attachment.get("sha256")
            result["checks"]["manifest_signature"] = verify_signature_with_public_key(
                manifest_bytes,
                archive.read("manifest.sig"),
                public_key,
            )
            result["checks"]["pdf_signature"] = verify_signature_with_public_key(
                pdf_bytes,
                signature_bytes,
                public_key,
            )
            result["manifest"] = manifest
    except Exception as exc:  # noqa: BLE001 - verifier returns structured failure detail
        result["errors"].append(str(exc))
        return result

    result["ok"] = all(bool(value) for value in result["checks"].values())
    if not result["ok"]:
        result["errors"].append("one or more verification checks failed")
    return result
