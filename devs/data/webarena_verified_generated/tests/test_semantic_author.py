"""Verify semantic compilation, bench response profiles, and grounding receipts."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from jsonschema import ValidationError

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
_SPEC = importlib.util.spec_from_file_location("wav_semantic_author", _ROOT / "author.py")
assert _SPEC is not None and _SPEC.loader is not None
author = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(author)


def _draft() -> dict:
    return {
        "generated_task_id": "wavgen-v02-test-0001",
        "task_id": 2_000_001,
        "intent_template_id": 2_000_100,
        "site": "gitlab",
        "start_url": "__GITLAB__",
        "revision": 1,
        "source_artifact": "seeds.jsonl",
        "source_record_id": "independent-seed",
        "baseline": {"site_url": "http://example.test", "snapshot": "test-fixture"},
        "task_definition": {
            "task_type": "RETRIEVE",
            "goal_template": "Find the title of project {project}.",
            "parameters": {"project": "Independent Project", "slug": "independent-project"},
            "answer": {
                "status": "SUCCESS",
                "values": ["Independent Project"],
                "item_schema": {"type": "string"},
                "ordered": False,
            },
            "completion_events": [
                {
                    "url_template": "__GITLAB__/example/{slug}",
                    "http_method": "GET",
                    "response_status": 200,
                    "last_event_only": True,
                }
            ],
        },
        "grounding": {
            "artifacts": {
                name: f"evidence/{name}" for name in ("har", "trace", "screenshot", "observation")
            },
            "field_evidence": [
                {
                    "field": "/task_definition/answer/values/0",
                    "artifact": "observation",
                    "location": "/visible_title",
                    "modality": "text",
                }
            ],
        },
    }


@pytest.mark.parametrize(
    ("schema", "values", "description"),
    [
        ({"type": "string"}, ["Independent Project"], "strings"),
        ({"type": "number", "description": "in USD"}, [23.5], "numbers (in USD)"),
        ({"type": "boolean"}, [True], "booleans"),
        (
            {
                "type": "object",
                "properties": {"name": {"type": "string"}, "count": {"type": "number"}},
                "required": ["name", "count"],
                "additionalProperties": False,
            },
            [{"name": "Independent Project", "count": 2}],
            "objects with fields name (strings), count (numbers)",
        ),
    ],
)
def test_retrieval_schema_generates_query_and_typed_evaluator(
    schema: dict, values: list, description: str
) -> None:
    draft = _draft()
    draft["task_definition"]["answer"].update({"item_schema": schema, "values": values})

    task, response = author._task_spec(draft)

    assert "Independent Project" in task["intent"]
    assert description in task["intent"]
    assert task["eval"][0]["results_schema"] == {"type": "array", "items": schema}
    assert response["retrieved_data"] == values
    assert set(response) == {"task_type", "status", "retrieved_data", "error_details"}
    assert "error_details" not in task["eval"][0]["expected"]
    author.validate_upstream_task_spec(task)


@pytest.mark.parametrize("task_type", ["NAVIGATE", "MUTATE"])
def test_action_profiles_preserve_null_results(task_type: str) -> None:
    draft = _draft()
    draft["task_definition"]["task_type"] = task_type
    draft["task_definition"]["answer"].update({"item_schema": None, "values": None})

    task, response = author._task_spec(draft)

    assert task["eval"][0]["results_schema"] == {"type": "null"}
    assert response["retrieved_data"] is None
    assert response["task_type"] == task_type
    author.validate_upstream_task_spec(task)


def test_error_outcome_keeps_requested_shape_in_query_and_null_evaluation() -> None:
    draft = _draft()
    draft["task_definition"]["answer"].update({"status": "NOT_FOUND_ERROR", "values": None})

    task, response = author._task_spec(draft)

    assert "list of strings" in task["intent"]
    assert "NOT_FOUND_ERROR" not in task["intent"]
    assert task["eval"][0]["results_schema"] == {"type": "null"}
    assert response["task_type"] == "RETRIEVE"
    author.validate_upstream_task_spec(task)


def test_goal_and_operation_constraints_share_parameters() -> None:
    draft = _draft()
    definition = draft["task_definition"]
    definition["goal_template"] = "Set project {project} to visibility {visibility}."
    definition["parameters"]["visibility"] = "private"
    definition["task_type"] = "MUTATE"
    definition["answer"].update({"item_schema": None, "values": None})
    definition["completion_events"][0].update(
        {"http_method": "POST", "post_data": {"visibility": "{visibility}"}}
    )

    task, _ = author._task_spec(draft)

    assert "visibility private" in task["intent"]
    assert task["eval"][1]["expected"]["post_data"]["visibility"] == "private"
    assert task["eval"][1]["expected"]["url"].endswith("/independent-project")


def test_wrong_ground_truth_type_is_rejected_before_publication() -> None:
    draft = _draft()
    draft["task_definition"]["answer"].update({"item_schema": {"type": "number"}})

    with pytest.raises(ValidationError):
        author._task_spec(draft)


def _fixture_roots(tmp_path: Path) -> tuple[Path, Path]:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "seeds.jsonl").write_text(
        json.dumps({"id": "independent-seed", "family": "project lookup"}) + "\n"
    )
    batch_root = tmp_path / "batch"
    (batch_root / "evidence").mkdir(parents=True)
    for name in ("har", "trace", "screenshot", "observation"):
        (batch_root / "evidence" / name).write_text(
            "Synthetic evidence fixture; not production grounding.\n"
        )
    return source_root, batch_root


def test_compilation_pins_field_artifact_definition_and_exact_public_projection(
    tmp_path: Path,
) -> None:
    source_root, batch_root = _fixture_roots(tmp_path)
    draft = _draft()

    candidate = author.compile_candidate(draft, source_root, batch_root)

    record = candidate["grounding"]["field_evidence"][0]
    assert record["field_value_sha256"] == author.sha256_json("Independent Project")
    assert record["artifact_sha256"] == author.sha256_file(batch_root / "evidence" / "observation")
    assert candidate["task_definition_sha256"] == author.sha256_json(draft["task_definition"])
    assert author.public_projection(candidate) == {
        "generated_task_id": "wavgen-v02-test-0001",
        "sites": ["gitlab"],
        "start_urls": ["__GITLAB__"],
        "intent": candidate["task_spec"]["intent"],
    }


def test_missing_field_evidence_blocks_candidate(tmp_path: Path) -> None:
    source_root, batch_root = _fixture_roots(tmp_path)
    draft = _draft()
    draft["task_definition"]["answer"]["values"].append("Second result")

    with pytest.raises(ValueError, match="answer leaves lack field evidence"):
        author.compile_candidate(draft, source_root, batch_root)
