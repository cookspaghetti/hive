"""Integrity manifest creation and verification for completed red-team outputs.

The result-directory manifest is an unsigned SHA-256 inventory and completion
marker. It detects accidental or unsophisticated modification when the
manifest is retained independently, but it does not authenticate its author.
Evidence ZIPs remain independently authenticated by their existing RSA-PSS
signatures; no second evaluation signing key is introduced here.
"""

from __future__ import annotations

import csv
import hashlib
import json
import time
from collections import Counter
from datetime import UTC, datetime
from itertools import product
from pathlib import Path, PurePosixPath
from typing import Any

from hive.vault.package import verify_evidence_package

MANIFEST_FILENAME = "redteam_manifest.json"
MANIFEST_SCHEMA = "hive.redteam.result-manifest"
MANIFEST_SCHEMA_VERSION = 1
_CORE_ARTIFACTS = {
    "redteam_runs.json": "raw_json",
    "redteam_runs.csv": "flat_csv",
    "redteam_summary.json": "aggregate_summary",
    "redteam_metadata.json": "run_metadata",
}


class ResultIntegrityError(ValueError):
    """A result directory cannot be finalized as a complete evaluation."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _require_aware_datetime(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ResultIntegrityError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ResultIntegrityError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ResultIntegrityError(f"{field} must include a UTC offset")
    return value


def _safe_artifact_path(root: Path, value: str) -> Path:
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or "\\" in value
    ):
        raise ResultIntegrityError(f"unsafe manifest artifact path: {value!r}")
    candidate = root.joinpath(*relative.parts)
    if candidate.is_symlink() or not candidate.is_file():
        raise ResultIntegrityError(f"manifest artifact is missing or unsafe: {value}")
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ResultIntegrityError(f"artifact escapes result directory: {value}") from exc
    return candidate


def _matrix_denominators(
    metadata: dict[str, Any],
    runs: list[dict[str, Any]],
) -> dict[str, Any]:
    scenarios = metadata.get("scenarios")
    personas = metadata.get("personas")
    repeat_count = metadata.get("repeat_count")
    if (
        not isinstance(scenarios, list)
        or not scenarios
        or not all(isinstance(item, str) and item for item in scenarios)
        or len(scenarios) != len(set(scenarios))
    ):
        raise ResultIntegrityError("metadata scenarios must be a non-empty unique list")
    if (
        not isinstance(personas, list)
        or not personas
        or not all(isinstance(item, str) and item for item in personas)
        or len(personas) != len(set(personas))
    ):
        raise ResultIntegrityError("metadata personas must be a non-empty unique list")
    if type(repeat_count) is not int or repeat_count < 1:
        raise ResultIntegrityError("metadata repeat_count must be a positive integer")

    expected_matrix = set(product(scenarios, personas, range(1, repeat_count + 1)))
    actual_matrix: list[tuple[str, str, int]] = []
    for run in runs:
        if not isinstance(run, dict):
            raise ResultIntegrityError("raw runs must contain JSON objects")
        replicate = run.get("replicate")
        if type(replicate) is not int:
            raise ResultIntegrityError("every raw run must have an integer replicate")
        actual_matrix.append(
            (str(run.get("scenario") or ""), str(run.get("persona") or ""), replicate)
        )
    if len(actual_matrix) != len(set(actual_matrix)):
        raise ResultIntegrityError("raw runs contain duplicate scenario/persona/repeat rows")
    if set(actual_matrix) != expected_matrix:
        missing = sorted(expected_matrix - set(actual_matrix))
        unexpected = sorted(set(actual_matrix) - expected_matrix)
        raise ResultIntegrityError(
            "raw runs do not cover the declared matrix "
            f"(missing={missing[:5]}, unexpected={unexpected[:5]})"
        )

    language_counts = Counter(str(run.get("language") or "unspecified") for run in runs)
    expected_runs = len(expected_matrix)
    return {
        "scenarios": scenarios,
        "scenario_count": len(scenarios),
        "personas": personas,
        "persona_count": len(personas),
        "repeat_count": repeat_count,
        "expected_runs": expected_runs,
        "completed_runs": len(runs),
        "language_run_counts": dict(sorted(language_counts.items())),
    }


def invalidate_result_manifest(output_directory: str | Path) -> None:
    """Remove the completion marker before partial/progress artifacts change."""
    Path(output_directory, MANIFEST_FILENAME).unlink(missing_ok=True)


def finalize_result_directory(output_directory: str | Path) -> Path:
    """Atomically publish a complete manifest after validating every artifact."""
    root = Path(output_directory)
    manifest_path = root / MANIFEST_FILENAME
    manifest_path.unlink(missing_ok=True)
    try:
        core_paths = {name: _safe_artifact_path(root, name) for name in _CORE_ARTIFACTS}
        metadata = _read_json(core_paths["redteam_metadata.json"])
        runs = _read_json(core_paths["redteam_runs.json"])
        summary = _read_json(core_paths["redteam_summary.json"])
    except (OSError, json.JSONDecodeError) as exc:
        raise ResultIntegrityError(f"result artifacts are unreadable: {exc}") from exc
    if not isinstance(metadata, dict) or not isinstance(summary, dict):
        raise ResultIntegrityError("metadata and summary must be JSON objects")
    if not isinstance(runs, list):
        raise ResultIntegrityError("raw runs must be a JSON array")

    denominators = _matrix_denominators(metadata, runs)
    failed_quality_rows = [
        f"{run.get('scenario')}/{run.get('persona')}/repeat-{run.get('replicate')}"
        for run in runs
        if run.get("chain_valid") is not True
        or run.get("evidence_verified") is not True
        or run.get("termination_reason") != "max_exchanges"
        or (run.get("character_assessment") or {}).get("status") == "error"
    ]
    if failed_quality_rows:
        raise ResultIntegrityError(
            "evaluation quality gate failed for " + ", ".join(failed_quality_rows[:5])
        )
    if summary.get("runs") != denominators["completed_runs"]:
        raise ResultIntegrityError("summary run denominator does not match raw runs")
    if metadata.get("runs") != denominators["completed_runs"]:
        raise ResultIntegrityError("metadata run denominator does not match raw runs")
    if metadata.get("scenario_count") != denominators["scenario_count"]:
        raise ResultIntegrityError("metadata scenario denominator is inconsistent")
    if metadata.get("persona_count") != denominators["persona_count"]:
        raise ResultIntegrityError("metadata persona denominator is inconsistent")
    if metadata.get("expected_runs") != denominators["expected_runs"]:
        raise ResultIntegrityError("metadata expected-run denominator is inconsistent")
    evaluation_created_utc = _require_aware_datetime(
        metadata.get("created_utc"), "metadata created_utc"
    )

    try:
        with core_paths["redteam_runs.csv"].open("r", encoding="utf-8", newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
    except (OSError, csv.Error) as exc:
        raise ResultIntegrityError(f"CSV artifact is unreadable: {exc}") from exc
    if len(csv_rows) != denominators["completed_runs"]:
        raise ResultIntegrityError("CSV run denominator does not match raw runs")

    all_output_files = sorted(
        path for path in root.rglob("*") if path.is_file() and path != manifest_path
    )
    if any(path.is_symlink() for path in all_output_files):
        raise ResultIntegrityError("result directories cannot contain symbolic-link artifacts")
    if any(
        path.name.endswith(".tmp") or path.name.startswith(f".{MANIFEST_FILENAME}.")
        for path in all_output_files
    ):
        raise ResultIntegrityError("temporary files remain; result directory is not final")
    evidence_root = root / "evidence"
    evidence_files = [path for path in all_output_files if evidence_root in path.parents]
    packages = [path for path in evidence_files if path.name.endswith(".evidence.zip")]
    pdfs = [path for path in evidence_files if path.suffix.casefold() == ".pdf"]
    signatures = [path for path in evidence_files if path.name.endswith(".pdf.sig")]
    expected = denominators["completed_runs"]
    if (len(packages), len(pdfs), len(signatures)) != (expected, expected, expected):
        raise ResultIntegrityError(
            "evidence artifact counts must match completed runs "
            f"(packages={len(packages)}, pdfs={len(pdfs)}, "
            f"signatures={len(signatures)}, runs={expected})"
        )

    package_verification: dict[Path, dict[str, Any]] = {}
    for package in packages:
        verification = verify_evidence_package(package)
        if not verification.get("ok"):
            raise ResultIntegrityError(
                f"evidence package signature verification failed: {package.name}: "
                f"{verification.get('errors') or 'unknown verification error'}"
            )
        package_verification[package] = verification

    artifacts: list[dict[str, Any]] = []
    for path in all_output_files:
        relative = path.relative_to(root).as_posix()
        if relative in _CORE_ARTIFACTS:
            role = _CORE_ARTIFACTS[relative]
        elif path.name.endswith(".evidence.zip"):
            role = "signed_evidence_package"
        elif path.name.endswith(".pdf.sig"):
            role = "detached_evidence_signature"
        elif path.suffix.casefold() == ".pdf":
            role = "evidence_pdf"
        else:
            role = "evidence_supporting_file"
        artifact = {
            "path": relative,
            "role": role,
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
            "integrity": "sha256_checksum",
        }
        if path in package_verification:
            artifact["authentication"] = {
                "mechanism": "existing evidence-package RSA-PSS/SHA-256 signatures",
                "verified_at_finalization": True,
            }
        artifacts.append(artifact)

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "publication_state": "complete",
        "evaluation_created_utc": evaluation_created_utc,
        "finalized_utc": datetime.now(UTC).isoformat(),
        "run_id": metadata.get("run_id") or root.name,
        "denominators": denominators,
        "integrity_model": {
            "manifest_signature": None,
            "artifact_checksums": "SHA-256",
            "checksum_scope": (
                "Inventory and tamper detection when this manifest is retained "
                "independently; checksums alone do not authenticate the manifest author."
            ),
            "signature_scope": (
                "Each .evidence.zip is independently authenticated by its existing "
                "RSA-PSS/SHA-256 evidence manifest and PDF signatures."
            ),
        },
        "artifacts": artifacts,
    }
    temporary = root / f".{MANIFEST_FILENAME}.{time.time_ns()}.tmp"
    try:
        temporary.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(manifest_path)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest_path


def verify_result_directory(output_directory: str | Path) -> dict[str, Any]:
    """Verify manifest completeness, artifact hashes, matrix, and evidence signatures."""
    root = Path(output_directory)
    manifest_path = root / MANIFEST_FILENAME
    result: dict[str, Any] = {
        "directory": str(root),
        "ok": False,
        "checks": {},
        "errors": [],
        "evidence_packages": {},
    }
    if not manifest_path.is_file() or manifest_path.is_symlink():
        result["errors"].append(
            f"{MANIFEST_FILENAME} is missing; the evaluation is partial or not finalized"
        )
        return result
    try:
        manifest = _read_json(manifest_path)
        if not isinstance(manifest, dict):
            raise ResultIntegrityError("manifest must be a JSON object")
        schema_ok = (
            manifest.get("schema") == MANIFEST_SCHEMA
            and manifest.get("schema_version") == MANIFEST_SCHEMA_VERSION
            and manifest.get("publication_state") == "complete"
        )
        result["checks"]["manifest_schema_and_state"] = schema_ok
        if not schema_ok:
            raise ResultIntegrityError("unsupported or incomplete result manifest")
        _require_aware_datetime(
            manifest.get("evaluation_created_utc"), "manifest evaluation_created_utc"
        )
        _require_aware_datetime(manifest.get("finalized_utc"), "manifest finalized_utc")

        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise ResultIntegrityError("manifest artifact inventory is empty")
        listed: set[str] = set()
        checksum_ok = True
        package_ok = True
        roles: Counter[str] = Counter()
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                raise ResultIntegrityError("manifest artifacts must be objects")
            relative = str(artifact.get("path") or "")
            if relative in listed:
                raise ResultIntegrityError(f"duplicate manifest artifact: {relative}")
            listed.add(relative)
            path = _safe_artifact_path(root, relative)
            roles[str(artifact.get("role") or "")] += 1
            matches = (
                artifact.get("integrity") == "sha256_checksum"
                and artifact.get("bytes") == path.stat().st_size
                and artifact.get("sha256") == _sha256_file(path)
            )
            result["checks"][f"checksum:{relative}"] = matches
            checksum_ok = checksum_ok and matches
            if artifact.get("role") == "signed_evidence_package":
                verification = verify_evidence_package(path)
                verified = bool(verification.get("ok"))
                result["evidence_packages"][relative] = verification
                result["checks"][f"signature:{relative}"] = verified
                package_ok = package_ok and verified

        disk_files = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and path != manifest_path
        }
        inventory_complete = listed == disk_files
        result["checks"]["artifact_inventory_complete"] = inventory_complete
        result["checks"]["all_checksums_valid"] = checksum_ok
        result["checks"]["all_evidence_signatures_valid"] = package_ok

        metadata = _read_json(root / "redteam_metadata.json")
        runs = _read_json(root / "redteam_runs.json")
        summary = _read_json(root / "redteam_summary.json")
        if not isinstance(metadata, dict) or not isinstance(runs, list):
            raise ResultIntegrityError("result JSON artifact types are invalid")
        denominators = _matrix_denominators(metadata, runs)
        denominator_ok = (
            denominators == manifest.get("denominators")
            and summary.get("runs") == len(runs)
            and metadata.get("runs") == len(runs)
            and metadata.get("scenario_count") == denominators["scenario_count"]
            and metadata.get("persona_count") == denominators["persona_count"]
            and metadata.get("expected_runs") == denominators["expected_runs"]
            and roles["signed_evidence_package"] == len(runs)
            and roles["evidence_pdf"] == len(runs)
            and roles["detached_evidence_signature"] == len(runs)
        )
        with (root / "redteam_runs.csv").open("r", encoding="utf-8", newline="") as handle:
            csv_count = sum(1 for _row in csv.DictReader(handle))
        denominator_ok = denominator_ok and csv_count == len(runs)
        result["checks"]["declared_denominators_match"] = denominator_ok
        result["manifest"] = manifest
    except Exception as exc:  # noqa: BLE001 - verifier returns structured failure detail
        result["errors"].append(str(exc))
        return result

    result["ok"] = all(bool(value) for value in result["checks"].values())
    if not result["ok"]:
        result["errors"].append("one or more result integrity checks failed")
    return result
