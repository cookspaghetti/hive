"""Machine-verifiable finalization tests for red-team result directories."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from hive.redteam.integrity import (
    MANIFEST_FILENAME,
    ResultIntegrityError,
    finalize_result_directory,
    verify_result_directory,
)
from hive.redteam.runner import RunResult, write_results
from hive.vault.package import build_evidence_package
from hive.vault.signer import generate_keypair, sign_bytes


def _write_complete_directory(root: Path) -> Path:
    evidence = root / "evidence"
    evidence.mkdir(parents=True)
    key = root.parent / f"{root.name}-key.pem"
    generate_keypair(str(key))
    pdf = evidence / "bundle_1_1.pdf"
    pdf.write_bytes(b"%PDF-1.4\ncontrolled evaluation fixture\n%%EOF\n")
    Path(f"{pdf}.sig").write_bytes(sign_bytes(pdf.read_bytes(), str(key)))
    package = build_evidence_package(pdf, key)

    result = RunResult(
        archetype="investment",
        persona="confused_elderly",
        scenario="investment_en",
        replicate=1,
        language="English",
        chain_valid=True,
        evidence_package=str(package),
        evidence_verified=True,
    )
    write_results([result], root)
    metadata = {
        "schema_version": 2,
        "created_utc": "2026-08-31T01:02:03+00:00",
        "run_id": root.name,
        "scenario_count": 1,
        "scenarios": ["investment_en"],
        "personas": ["confused_elderly"],
        "persona_count": 1,
        "repeat_count": 1,
        "expected_runs": 1,
        "runs": 1,
    }
    (root / "redteam_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return finalize_result_directory(root)


def test_final_manifest_covers_outputs_and_distinguishes_checksums_from_signatures(tmp_path):
    root = tmp_path / "results"
    manifest_path = _write_complete_directory(root)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "hive.redteam.result-manifest"
    assert manifest["schema_version"] == 1
    assert manifest["publication_state"] == "complete"
    assert manifest["evaluation_created_utc"] == "2026-08-31T01:02:03+00:00"
    assert manifest["finalized_utc"]
    assert manifest["denominators"] == {
        "scenarios": ["investment_en"],
        "scenario_count": 1,
        "personas": ["confused_elderly"],
        "persona_count": 1,
        "repeat_count": 1,
        "expected_runs": 1,
        "completed_runs": 1,
        "language_run_counts": {"English": 1},
    }
    assert manifest["integrity_model"]["manifest_signature"] is None
    assert "do not authenticate" in manifest["integrity_model"]["checksum_scope"]
    package = next(
        item for item in manifest["artifacts"] if item["role"] == "signed_evidence_package"
    )
    assert package["authentication"]["verified_at_finalization"] is True
    assert all(item["integrity"] == "sha256_checksum" for item in manifest["artifacts"])
    assert {item["role"] for item in manifest["artifacts"]} >= {
        "raw_json",
        "flat_csv",
        "aggregate_summary",
        "run_metadata",
        "evidence_pdf",
        "detached_evidence_signature",
        "signed_evidence_package",
    }

    verification = verify_result_directory(root)
    assert verification["ok"] is True
    assert verification["checks"]["all_checksums_valid"] is True
    assert verification["checks"]["all_evidence_signatures_valid"] is True


def test_raw_result_tampering_fails_checksum_and_verification(tmp_path):
    root = tmp_path / "results"
    _write_complete_directory(root)
    raw = root / "redteam_runs.json"
    tampered = raw.read_text(encoding="utf-8").replace("investment", "tampered")
    raw.write_text(tampered, encoding="utf-8")

    verification = verify_result_directory(root)

    assert verification["ok"] is False
    assert verification["checks"]["checksum:redteam_runs.json"] is False


def test_evidence_package_tampering_fails_checksum_and_signature_verification(tmp_path):
    root = tmp_path / "results"
    _write_complete_directory(root)
    package = next((root / "evidence").glob("*.evidence.zip"))
    with zipfile.ZipFile(package) as source:
        members = {name: source.read(name) for name in source.namelist()}
        evidence_manifest = json.loads(members["manifest.json"])
        pdf_name = evidence_manifest["files"]["evidence_pdf"]["name"]
    members[pdf_name] += b"tampered"
    temporary = package.with_suffix(".tampered")
    with zipfile.ZipFile(temporary, mode="w") as target:
        for name, content in members.items():
            target.writestr(name, content)
    temporary.replace(package)

    verification = verify_result_directory(root)

    relative = package.relative_to(root).as_posix()
    assert verification["ok"] is False
    assert verification["checks"][f"checksum:{relative}"] is False
    assert verification["checks"][f"signature:{relative}"] is False
    assert verification["evidence_packages"][relative]["checks"]["pdf_checksum"] is False
    assert verification["evidence_packages"][relative]["checks"]["pdf_signature"] is False


def test_partial_progress_has_no_manifest_and_cannot_verify_as_final(tmp_path):
    root = tmp_path / "results"
    result = RunResult(
        archetype="investment",
        persona="confused_elderly",
        scenario="investment_en",
    )
    write_results([result], root)

    assert not (root / MANIFEST_FILENAME).exists()
    verification = verify_result_directory(root)
    assert verification["ok"] is False
    assert "partial or not finalized" in verification["errors"][0]
    with pytest.raises(ResultIntegrityError):
        finalize_result_directory(root)
    assert not (root / MANIFEST_FILENAME).exists()


def test_progress_write_invalidates_an_existing_completion_marker(tmp_path):
    root = tmp_path / "results"
    _write_complete_directory(root)
    assert (root / MANIFEST_FILENAME).is_file()

    write_results(
        [
            RunResult(
                archetype="investment",
                persona="confused_elderly",
                scenario="investment_en",
            )
        ],
        root,
    )

    assert not (root / MANIFEST_FILENAME).exists()


def test_failed_run_cannot_receive_or_retain_a_completion_manifest(tmp_path):
    root = tmp_path / "results"
    _write_complete_directory(root)
    assert (root / MANIFEST_FILENAME).is_file()
    failed = RunResult(
        archetype="investment",
        persona="confused_elderly",
        scenario="investment_en",
        replicate=1,
        language="English",
        chain_valid=True,
        evidence_verified=True,
        termination_reason="pipeline_error",
    )

    write_results([failed], root)
    with pytest.raises(ResultIntegrityError, match="quality gate failed"):
        finalize_result_directory(root)

    assert not (root / MANIFEST_FILENAME).exists()
    assert verify_result_directory(root)["ok"] is False


def test_incomplete_declared_matrix_cannot_be_finalized(tmp_path):
    root = tmp_path / "results"
    _write_complete_directory(root)
    (root / MANIFEST_FILENAME).unlink()
    metadata_path = root / "redteam_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["repeat_count"] = 2
    metadata["expected_runs"] = 2
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ResultIntegrityError, match="declared matrix"):
        finalize_result_directory(root)

    assert not (root / MANIFEST_FILENAME).exists()


def test_unlisted_file_after_finalization_fails_inventory_check(tmp_path):
    root = tmp_path / "results"
    _write_complete_directory(root)
    (root / "unexpected.txt").write_text("not declared", encoding="utf-8")

    verification = verify_result_directory(root)

    assert verification["ok"] is False
    assert verification["checks"]["artifact_inventory_complete"] is False
