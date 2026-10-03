"""Check quarantine reporting, independent execution, and bound human reviews."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
_SPEC = importlib.util.spec_from_file_location("wav_generated_quality", _ROOT / "quality_check.py")
assert _SPEC is not None and _SPEC.loader is not None
quality = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(quality)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _batch(
    batch_root: Path,
    *,
    data: list[Any] | None = None,
    task_type: str = "NAVIGATE",
    status: str = "SUCCESS",
    intent: str = "Open project Alpha.",
) -> tuple[dict, dict]:
    response = {
        "task_type": task_type,
        "status": status,
        "retrieved_data": data,
        "error_details": None,
    }
    task = {
        "sites": ["gitlab"],
        "task_id": 2_000_001,
        "intent_template_id": 2_000_100,
        "start_urls": ["__GITLAB__/"],
        "intent": intent,
        "intent_template": intent,
        "instantiation_dict": {},
        "revision": 1,
        "eval": [
            {"evaluator": "AgentResponseEvaluator", "expected": response},
            {"evaluator": "NetworkEventEvaluator"},
        ],
    }
    artifacts = {name: name + ".bin" for name in ("har", "trace", "screenshot", "observation")}
    for name, relative in artifacts.items():
        (batch_root / relative).write_bytes(f"Synthetic {name} evidence".encode())
    grounding = {
        "artifacts": artifacts,
        "artifact_sha256": {
            name: quality.sha256_file(batch_root / relative) for name, relative in artifacts.items()
        },
    }
    baseline = {"site_url": "http://gitlab.test", "snapshot": "synthetic-v02"}
    definition = {"task_type": task_type, "answer": response}
    candidate = {
        "generated_task_id": "wavgen-v02-test-alpha",
        "generation_mode": "regrounded_semantic_seed",
        "source": {"artifact_sha256": "a" * 64, "record_sha256": "b" * 64},
        "task_spec": task,
        "reference_response": response,
        "task_definition": definition,
        "task_definition_sha256": quality.sha256_json(definition),
        "baseline": baseline,
        "baseline_sha256": quality.sha256_json(baseline),
        "grounding": grounding,
        "grounding_sha256": quality.sha256_json(grounding),
    }
    public = {
        "generated_task_id": candidate["generated_task_id"],
        "sites": task["sites"],
        "start_urls": task["start_urls"],
        "intent": intent,
    }
    replay = {
        "gate_version": 2,
        "generated_task_id": candidate["generated_task_id"],
        "status": "pass",
        "checks": {"synthetic_receipt_check": True},
        "task_definition_sha256": candidate["task_definition_sha256"],
        "task_spec_sha256": quality.sha256_json(task),
        "baseline_sha256": candidate["baseline_sha256"],
        "grounding_sha256": candidate["grounding_sha256"],
        "reference_response_sha256": quality.sha256_json(response),
        "artifact_sha256": grounding["artifact_sha256"],
        "webarena_verified_version": "synthetic-v02",
        "webarena_verified_evaluator_checksum": "c" * 64,
    }
    contamination = {
        "generated_task_id": candidate["generated_task_id"],
        "status": "pass",
        "candidate_sha256": quality.sha256_json(candidate),
        "official_catalog_sha256": quality.OFFICIAL_CATALOG_SHA256,
    }
    _write_jsonl(batch_root / "private.jsonl", [candidate])
    _write_jsonl(batch_root / "public.jsonl", [public])
    _write_jsonl(batch_root / "replay_receipts.jsonl", [replay])
    _write_jsonl(batch_root / "contamination_receipts.jsonl", [contamination])
    return candidate, replay


def _independent_receipt(batch_root: Path, candidate: dict, replay: dict) -> tuple[Path, dict]:
    case_root = batch_root / "reference_runs" / candidate["generated_task_id"]
    case_root.mkdir(parents=True)
    artifacts = {
        "har": "reference.har",
        "trace": "reference.zip",
        "trajectory": "trajectory.parquet",
        "initial_screenshot": "initial.png",
    }
    for name, relative in artifacts.items():
        (case_root / relative).write_bytes(f"Synthetic independent {name}".encode())
    receipt = {
        "generated_task_id": candidate["generated_task_id"],
        "reference_kind": "independent_model_execution",
        "status": "pass",
        "execution_status": "completed",
        "agent_ended": True,
        "public_only": True,
        "model_id": "synthetic-teacher",
        "task_spec_sha256": quality.sha256_json(candidate["task_spec"]),
        "baseline_sha256": candidate["baseline_sha256"],
        "public_projection_sha256": quality.sha256_json(
            json.loads((batch_root / "public.jsonl").read_text())
        ),
        "execution_baseline_receipt_sha256": "d" * 64,
        "webarena_verified_version": replay["webarena_verified_version"],
        "webarena_verified_evaluator_checksum": replay["webarena_verified_evaluator_checksum"],
        "checks": {
            "response_contract_valid": True,
            "all_evaluators_pass": True,
            "har_playwright_results_equal": True,
            "canonical_trajectory_present": True,
            "final_state_confirmed": True,
        },
        "har_results": [{"status": "success", "score": 1}] * 2,
        "playwright_results": [{"status": "success", "score": 1}] * 2,
        "artifacts": artifacts,
        "artifact_sha256": {
            name: quality.sha256_file(case_root / relative) for name, relative in artifacts.items()
        },
    }
    path = case_root / "reference_receipt.json"
    path.write_text(json.dumps(receipt))
    return path, receipt


@pytest.mark.parametrize(
    ("data", "task_type", "status"),
    [
        ([31], "RETRIEVE", "SUCCESS"),
        ([False], "RETRIEVE", "SUCCESS"),
        ([{"name": "Alpha", "count": 31}], "RETRIEVE", "SUCCESS"),
        (None, "NAVIGATE", "SUCCESS"),
        (None, "MUTATE", "SUCCESS"),
        (None, "RETRIEVE", "NOT_FOUND_ERROR"),
    ],
)
def test_typed_and_null_answers_report_pending_independent_execution(
    tmp_path: Path, data: list[Any] | None, task_type: str, status: str
) -> None:
    _batch(tmp_path, data=data, task_type=task_type, status=status)

    report = quality.build_review_report(tmp_path, expected_count=1)

    row = report["tasks"][0]
    assert row["stages"]["author_replay"]["status"] == "pass"
    assert row["stages"]["independent_reference"]["status"] == "pending"
    assert row["stages"]["human_review"]["status"] == "pending"
    assert row["status"] == "quarantine"
    assert not row["training_eligible"]
    assert report["training_eligible_count"] == 0
    assert (tmp_path / "reports" / "machine_validation.json").is_file()
    assert not (tmp_path / "human_review_receipts.jsonl").exists()


def test_goal_parameter_overlap_is_a_review_flag_not_an_automatic_rejection(tmp_path: Path) -> None:
    _batch(
        tmp_path,
        data=[{"name": "Alpha", "count": 31}],
        task_type="RETRIEVE",
        intent="Find the member count of project Alpha.",
    )

    report = quality.build_review_report(tmp_path, expected_count=1)

    row = report["tasks"][0]
    assert row["stages"]["author_replay"]["status"] == "pass"
    flags = row["answer_overlap_review_flags"]
    assert [flag["field"] for flag in flags] == ["/retrieved_data/0/name"]
    assert "legitimate target parameter" in flags[0]["reason"]


def test_numeric_substring_is_not_an_answer_overlap(tmp_path: Path) -> None:
    _batch(tmp_path, data=[31], task_type="RETRIEVE", intent="Find the count for project 131.")

    report = quality.build_review_report(tmp_path, expected_count=1)

    assert report["tasks"][0]["answer_overlap_review_flags"] == []


@pytest.mark.parametrize(
    "binding",
    [
        "task_spec_sha256",
        "baseline_sha256",
        "public_projection_sha256",
        "webarena_verified_evaluator_checksum",
        "artifact",
    ],
)
def test_stale_independent_receipt_is_quarantined(tmp_path: Path, binding: str) -> None:
    candidate, replay = _batch(tmp_path)
    receipt_path, receipt = _independent_receipt(tmp_path, candidate, replay)
    if binding == "artifact":
        (receipt_path.parent / "trajectory.parquet").write_bytes(b"tampered trajectory")
    else:
        receipt[binding] = "modified"
        receipt_path.write_text(json.dumps(receipt))

    report = quality.build_review_report(tmp_path, expected_count=1)

    stage = report["tasks"][0]["stages"]["independent_reference"]
    assert stage["status"] == "quarantine"
    assert stage["reasons"]
    assert not report["tasks"][0]["ready_for_stage"]


def test_network_pass_without_final_state_confirmation_is_quarantined(tmp_path: Path) -> None:
    candidate, replay = _batch(tmp_path, task_type="MUTATE")
    receipt_path, receipt = _independent_receipt(tmp_path, candidate, replay)
    receipt["checks"]["final_state_confirmed"] = False
    receipt_path.write_text(json.dumps(receipt))

    report = quality.build_review_report(tmp_path, expected_count=1)

    stage = report["tasks"][0]["stages"]["independent_reference"]
    assert stage["status"] == "quarantine"
    assert "reference check did not pass: final_state_confirmed" in stage["reasons"]


@pytest.mark.parametrize("decision", ["reject", "revise"])
def test_unaccepted_human_review_cannot_promote(tmp_path: Path, decision: str) -> None:
    candidate, replay = _batch(tmp_path)
    _independent_receipt(tmp_path, candidate, replay)
    initial = quality.build_review_report(tmp_path, expected_count=1)
    review = {
        "generated_task_id": candidate["generated_task_id"],
        "decision": decision,
        "reviewer": "synthetic-reviewer",
        "reviewed_at": "2026-10-03T00:00:00Z",
        "bindings": initial["tasks"][0]["bindings"],
    }
    review_path = tmp_path / "human_review_receipts.jsonl"
    _write_jsonl(review_path, [review])
    before = review_path.read_bytes()

    report = quality.build_review_report(tmp_path, expected_count=1)

    assert report["tasks"][0]["stages"]["independent_reference"]["status"] == "pass"
    assert report["tasks"][0]["stages"]["human_review"]["status"] == "quarantine"
    assert not report["tasks"][0]["ready_for_stage"]
    assert review_path.read_bytes() == before


def test_bound_human_acceptance_is_read_only_and_stage_export_remain_pending(
    tmp_path: Path,
) -> None:
    candidate, replay = _batch(
        tmp_path, data=["Alpha"], task_type="RETRIEVE", intent="Find project Alpha's title."
    )
    _independent_receipt(tmp_path, candidate, replay)
    initial = quality.build_review_report(tmp_path, expected_count=1)
    review = {
        "generated_task_id": candidate["generated_task_id"],
        "decision": "accept",
        "reviewer": "synthetic-reviewer",
        "reviewed_at": "2026-10-03T00:00:00Z",
        "bindings": initial["tasks"][0]["bindings"],
        "answer_overlap_reviewed": True,
    }
    review_path = tmp_path / "human_review_receipts.jsonl"
    _write_jsonl(review_path, [review])
    before = review_path.read_bytes()

    report = quality.build_review_report(tmp_path, expected_count=1)

    row = report["tasks"][0]
    assert row["stages"]["human_review"]["status"] == "pass"
    assert row["ready_for_stage"]
    assert row["stages"]["canonical_stage"]["status"] == "pending"
    assert row["stages"]["target_model_export"]["status"] == "pending"
    assert not row["training_eligible"]
    assert review_path.read_bytes() == before


def test_accepted_review_with_old_binding_is_quarantined(tmp_path: Path) -> None:
    candidate, replay = _batch(tmp_path)
    _independent_receipt(tmp_path, candidate, replay)
    initial = quality.build_review_report(tmp_path, expected_count=1)
    review = {
        "generated_task_id": candidate["generated_task_id"],
        "decision": "accept",
        "reviewer": "synthetic-reviewer",
        "reviewed_at": "2026-10-03T00:00:00Z",
        "bindings": initial["tasks"][0]["bindings"],
    }
    review["bindings"]["task_spec_sha256"] = "stale"
    _write_jsonl(tmp_path / "human_review_receipts.jsonl", [review])

    report = quality.build_review_report(tmp_path, expected_count=1)

    assert report["tasks"][0]["stages"]["human_review"]["status"] == "quarantine"
    assert not report["tasks"][0]["ready_for_stage"]


def test_cli_writes_quarantine_report_then_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _batch(tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["quality_check", "--batch-root", str(tmp_path), "--expected-count", "1"]
    )

    with pytest.raises(quality.ContractError, match="reports were written"):
        quality.main()

    report = json.loads((tmp_path / "reports" / "machine_validation.json").read_text())
    assert report["status"] == "quarantine"
    assert report["task_count"] == 1
