"""Unit tests for the generated WebArena-Verified task contract."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "contract.py"
_SPEC = importlib.util.spec_from_file_location("wav_generated_contract", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
contract = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(contract)


def _task(*, task_id: int = 2_000_001, intent: str = "Return the project title.") -> dict:
    return {
        "sites": ["gitlab"],
        "task_id": task_id,
        "intent_template_id": 2_000_001,
        "start_urls": ["__GITLAB__/example/project"],
        "intent": intent,
        "eval": [
            {
                "evaluator": "AgentResponseEvaluator",
                "ordered": False,
                "results_schema": {"type": "array", "items": {"type": "string"}},
                "expected": {
                    "task_type": "retrieve",
                    "status": "SUCCESS",
                    "retrieved_data": ["Example Project"],
                    "error_details": None,
                },
            }
        ],
        "intent_template": "Return the project title.",
        "instantiation_dict": {},
        "revision": 1,
    }


def _candidate(task: dict | None = None) -> dict:
    return {
        "generated_task_id": "wavgen-v02-test-0001",
        "generation_mode": "regrounded_semantic_seed",
        "source": {
            "dataset": "aiming-lab/WebHarbor",
            "artifact_sha256": "a" * 64,
            "record_sha256": "b" * 64,
        },
        "task_spec": task or _task(),
    }


def test_public_projection_is_exact_and_contains_no_hidden_judge() -> None:
    candidate = _candidate()
    public = contract.public_projection(candidate)

    contract.assert_public_projection(public, candidate)
    assert "eval" not in public
    assert "instantiation_dict" not in public


def test_official_task_id_is_rejected() -> None:
    candidate = _candidate(_task(task_id=12))

    with pytest.raises(contract.ContractError, match="2_000_000"):
        contract.validate_candidate_shape(candidate)


def test_official_catalog_cannot_be_a_generation_source() -> None:
    candidate = _candidate()
    candidate["source"]["path"] = "assets/dataset/webarena-verified.json"

    with pytest.raises(contract.ContractError, match="cannot be a generation source"):
        contract.validate_candidate_shape(candidate)


def test_exact_intent_match_is_rejected_by_contamination_gate() -> None:
    candidate = _candidate()
    official = _task(task_id=7)

    receipt = contract.contamination_result(candidate, [official])

    assert receipt["status"] == "reject"
    assert any("normalized intent match" in reason for reason in receipt["reasons"])


def test_independent_task_passes_contamination_gate() -> None:
    candidate = _candidate()
    official = _task(task_id=7, intent="Change the assignee on a bug report.")
    official["intent_template"] = "Change the assignee on issue {{issue}}."
    official["eval"][0]["expected"]["retrieved_data"] = ["Nobody"]

    receipt = contract.contamination_result(candidate, [official])

    assert receipt["status"] == "pass"


def test_mutation_requires_network_evidence() -> None:
    task = _task()
    task["eval"][0]["expected"].update({"task_type": "mutate", "retrieved_data": None})

    with pytest.raises(contract.ContractError, match="require a NetworkEventEvaluator"):
        contract.validate_candidate_shape(_candidate(task))


def test_error_status_is_orthogonal_to_requested_task_type() -> None:
    task = _task()
    task["eval"][0]["expected"].update({"status": "NOT_FOUND_ERROR", "retrieved_data": None})
    task["eval"][0]["results_schema"] = {"type": "null"}

    contract.validate_candidate_shape(_candidate(task))
    contract.validate_upstream_task_spec(task)


def test_v01_integer_namespace_cannot_be_reused() -> None:
    with pytest.raises(contract.ContractError, match="2_000_000"):
        contract.validate_candidate_shape(_candidate(_task(task_id=1_000_001)))
