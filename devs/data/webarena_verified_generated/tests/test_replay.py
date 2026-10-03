"""Exercise answer/action isolation and quarantine in the replay gate."""

from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
_SPEC = importlib.util.spec_from_file_location("wav_generated_replay", _ROOT / "replay.py")
assert _SPEC is not None and _SPEC.loader is not None
replay = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(replay)


def _candidate(
    batch_root: Path,
    *,
    retrieved_data: list[Any] | None,
    items_schema: dict[str, Any] | None = None,
    task_type: str = "RETRIEVE",
    status: str = "SUCCESS",
    network: bool = True,
    forbidden_event: bool = False,
) -> dict[str, Any]:
    response = {
        "task_type": task_type,
        "status": status,
        "retrieved_data": retrieved_data,
        "error_details": None,
    }
    method = "POST" if task_type == "MUTATE" else "GET"
    evals = [
        {
            "evaluator": "AgentResponseEvaluator",
            "results_schema": (
                {"type": "null"}
                if retrieved_data is None
                else {"type": "array", "items": items_schema}
            ),
            "expected": {key: value for key, value in response.items() if key != "error_details"},
        }
    ]
    if network:
        evals.append(
            {
                "evaluator": "NetworkEventEvaluator",
                "expected": {
                    "url": "__GITLAB__/project",
                    "http_method": method,
                    "response_status": 200,
                },
            }
        )
    if forbidden_event:
        evals.append(
            {
                "evaluator": "NetworkEventEvaluator",
                "should_not_exist": True,
                "expected": {
                    "url": "__GITLAB__/forbidden",
                    "http_method": method,
                    "response_status": 200,
                },
            }
        )
    task = {
        "sites": ["gitlab"],
        "task_id": 2_000_001,
        "intent_template_id": 2_000_001,
        "start_urls": ["__GITLAB__/"],
        "intent": "Complete the synthetic project task.",
        "intent_template": "Complete the synthetic project task.",
        "instantiation_dict": {},
        "revision": 1,
        "eval": evals,
    }
    (batch_root / "private.jsonl").write_text(json.dumps(task) + "\n")
    entry = {
        "request": {
            "url": "http://gitlab.test/project",
            "method": method,
            "headers": [{"name": "accept", "value": "text/html"}],
        },
        "response": {"status": 200},
    }
    (batch_root / "reference.har").write_text(json.dumps({"log": {"entries": [entry]}}))
    with zipfile.ZipFile(batch_root / "reference.zip", "w") as archive:
        archive.writestr(
            "trace.network",
            json.dumps({"type": "resource-snapshot", "snapshot": entry}) + "\n",
        )
    (batch_root / "reference.png").write_bytes(b"synthetic screenshot")
    (batch_root / "observation.json").write_text(json.dumps({"title": "Synthetic project"}))
    artifacts = {
        "har": "reference.har",
        "trace": "reference.zip",
        "screenshot": "reference.png",
        "observation": "observation.json",
    }
    grounding = {
        "artifacts": artifacts,
        "artifact_sha256": {
            name: replay.sha256_file(batch_root / relative) for name, relative in artifacts.items()
        },
        "task_spec_sha256": replay.sha256_json(task),
    }
    baseline = {"site_url": "http://gitlab.test", "snapshot": "synthetic-v02"}
    definition = {"task_type": task_type, "answer": response}
    return {
        "generated_task_id": "wavgen-v02-test-project",
        "task_spec": task,
        "reference_response": response,
        "task_definition": definition,
        "task_definition_sha256": replay.sha256_json(definition),
        "baseline": baseline,
        "baseline_sha256": replay.sha256_json(baseline),
        "grounding": grounding,
        "grounding_sha256": replay.sha256_json(grounding),
    }


@pytest.mark.parametrize(
    ("data", "items_schema"),
    [
        (["Project Alpha", "Project Beta"], {"type": "string"}),
        ([31, 42], {"type": "number"}),
        ([False], {"type": "boolean"}),
        (
            [{"name": "Project Alpha", "count": 31}],
            {
                "type": "object",
                "properties": {"name": {"type": "string"}, "count": {"type": "number"}},
            },
        ),
    ],
)
def test_typed_answers_and_network_contrasts_are_independent(
    tmp_path: Path, data: list[Any], items_schema: dict[str, Any]
) -> None:
    candidate = _candidate(tmp_path, retrieved_data=data, items_schema=items_schema)

    receipt = replay.validate_replay(tmp_path, candidate)

    assert receipt["status"] == "pass"
    assert [result["score"] for result in receipt["negative_wrong_response"]] == [0, 1]
    assert [result["score"] for result in receipt["negative_wrong_task_type"]] == [0, 1]
    assert [result["score"] for result in receipt["negative_wrong_status"]] == [0, 1]
    assert [result["score"] for result in receipt["negative_empty_trace"]] == [1, 0]
    assert json.loads(receipt["negative_empty_trace"][0]["actual"])["retrieved_data"] == data
    assert receipt["task_spec_sha256"] == replay.sha256_json(candidate["task_spec"])
    assert receipt["baseline_sha256"] == candidate["baseline_sha256"]
    assert receipt["artifact_sha256"] == candidate["grounding"]["artifact_sha256"]
    assert receipt["webarena_verified_version"]
    assert receipt["webarena_verified_evaluator_checksum"]


def test_answer_only_task_does_not_invent_an_action_requirement(tmp_path: Path) -> None:
    candidate = _candidate(
        tmp_path, retrieved_data=[True], items_schema={"type": "boolean"}, network=False
    )

    receipt = replay.validate_replay(tmp_path, candidate)

    assert receipt["status"] == "pass"
    assert receipt["expected_scores"]["negative_empty_trace"] == [1]
    assert receipt["negative_empty_trace"][0]["score"] == 1


def test_empty_trace_preserves_forbidden_event_expectation(tmp_path: Path) -> None:
    candidate = _candidate(
        tmp_path,
        retrieved_data=["Project Alpha"],
        items_schema={"type": "string"},
        forbidden_event=True,
    )

    receipt = replay.validate_replay(tmp_path, candidate)

    assert receipt["status"] == "pass"
    assert [result["score"] for result in receipt["negative_empty_trace"]] == [1, 0, 1]


@pytest.mark.parametrize(
    ("task_type", "status"),
    [("NAVIGATE", "SUCCESS"), ("MUTATE", "SUCCESS"), ("RETRIEVE", "NOT_FOUND_ERROR")],
)
def test_valid_null_reference_requires_unexpected_data_to_fail(
    tmp_path: Path, task_type: str, status: str
) -> None:
    candidate = _candidate(tmp_path, retrieved_data=None, task_type=task_type, status=status)

    receipt = replay.validate_replay(tmp_path, candidate)

    assert all(result["score"] == 1 for result in receipt["positive_har"])
    assert receipt["expected_scores"]["negative_wrong_response"] == [0, 1]
    assert [result["score"] for result in receipt["negative_wrong_task_type"]] == [0, 1]
    assert [result["score"] for result in receipt["negative_wrong_status"]] == [0, 1]
    result = receipt["negative_wrong_response"][0]
    if result["status"] == "failure" and result["score"] == 0:
        assert receipt["status"] == "pass"
    else:
        # A pinned evaluator limitation remains visible until the owner is upgraded.
        assert receipt["status"] == "quarantine"
        assert "negative_wrong_response" in receipt["evaluator_limitations"]
        if result["status"] == "error":
            assert receipt["evaluator_errors"][0]["probe"] == "negative_wrong_response"
            assert not receipt["checks"]["no_evaluator_errors"]


def test_evaluator_error_is_not_accepted_as_a_negative_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate(tmp_path, retrieved_data=[31], items_schema={"type": "number"})
    evaluate = replay._evaluate

    def error_on_wrong_answer(task, response, trace, config):
        results = evaluate(task, response, trace, config)
        if json.loads(response)["retrieved_data"] == []:
            results[0].update(status="error", score=0, error_msg="synthetic evaluator error")
        return results

    monkeypatch.setattr(replay, "_evaluate", error_on_wrong_answer)

    receipt = replay.validate_replay(tmp_path, candidate)

    assert receipt["status"] == "quarantine"
    assert not receipt["checks"]["negative_wrong_response_expected_vector"]
    assert receipt["evaluator_errors"][0]["result"]["error_msg"] == "synthetic evaluator error"


@pytest.mark.parametrize(
    "receipt", ["task_definition", "baseline", "grounding", "task_spec", "artifact"]
)
def test_tampered_receipt_is_rejected_before_evaluation(tmp_path: Path, receipt: str) -> None:
    candidate = _candidate(tmp_path, retrieved_data=[31], items_schema={"type": "number"})
    if receipt == "task_definition":
        candidate["task_definition"]["task_type"] = "MUTATE"
    elif receipt == "baseline":
        candidate["baseline"]["snapshot"] = "modified"
    elif receipt == "grounding":
        candidate["grounding"]["artifacts"]["screenshot"] = "modified.png"
    elif receipt == "task_spec":
        candidate["task_spec"]["intent"] = "Modified task."
    else:
        (tmp_path / "reference.png").write_bytes(b"modified screenshot")

    with pytest.raises(replay.ContractError, match="hash mismatch"):
        replay.validate_replay(tmp_path, candidate)


def _post_candidate(batch_root: Path, *, external_payload: bool = False) -> dict[str, Any]:
    candidate = _candidate(batch_root, retrieved_data=None, task_type="MUTATE")
    post_eval = candidate["task_spec"]["eval"][1]
    post_eval["expected"].update(
        {
            "response_status": 302,
            "post_data": {"name": "Project Alpha", "parent": "2", "is_active": "True"},
        }
    )
    post_eval["post_data_schema"] = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "parent": {"type": "number"},
            "is_active": {"type": "boolean"},
        },
    }
    candidate["task_spec"]["eval"].append(
        {
            "evaluator": "NetworkEventEvaluator",
            "expected": {"url": "__GITLAB__/unrelated", "http_method": "GET"},
        }
    )
    body = (
        "".join(
            f'--synthetic-boundary\r\nContent-Disposition: form-data; name="{key}"\r\n'
            f"\r\n{value}\r\n"
            for key, value in {"name": "Project Alpha", "parent": "2", "is_active": "1"}.items()
        )
        + "--synthetic-boundary--\r\n"
    )
    entry = {
        "request": {
            "url": "http://gitlab.test/project",
            "method": "POST",
            "headers": [
                {
                    "name": "content-type",
                    "value": "multipart/form-data; boundary=synthetic-boundary",
                }
            ],
            "postData": {
                "mimeType": "multipart/form-data; boundary=synthetic-boundary",
                "text": body,
            },
        },
        "response": {"status": 302, "redirectURL": "http://gitlab.test/project"},
    }
    unrelated = {
        "request": {"url": "http://gitlab.test/unrelated", "method": "GET", "headers": []},
        "response": {"status": 200},
    }
    (batch_root / "reference.har").write_text(json.dumps({"log": {"entries": [entry, unrelated]}}))
    playwright_entry = json.loads(json.dumps(entry))
    if external_payload:
        playwright_entry["request"]["postData"].update({"text": "", "_sha1": "post-body"})
    with zipfile.ZipFile(batch_root / "reference.zip", "w") as archive:
        archive.writestr(
            "trace.network",
            "".join(
                json.dumps({"type": "resource-snapshot", "snapshot": event}) + "\n"
                for event in (playwright_entry, unrelated)
            ),
        )
        if external_payload:
            archive.writestr("resources/post-body", body)
    grounding = candidate["grounding"]
    grounding["task_spec_sha256"] = replay.sha256_json(candidate["task_spec"])
    grounding["artifact_sha256"] = {
        name: replay.sha256_file(batch_root / relative)
        for name, relative in grounding["artifacts"].items()
    }
    candidate["grounding_sha256"] = replay.sha256_json(grounding)
    return candidate


@pytest.mark.parametrize("external_payload", [False, True])
def test_post_critical_negatives_and_equivalent_representations(
    tmp_path: Path, external_payload: bool
) -> None:
    candidate = _post_candidate(tmp_path, external_payload=external_payload)

    receipt = replay.validate_replay(tmp_path, candidate)

    assert [result["score"] for result in receipt["positive_har"]] == [1, 1, 1]
    assert [result["score"] for result in receipt["positive_playwright"]] == [1, 1, 1]
    contrasts = receipt["post_data_contrasts"]
    assert contrasts["status"] == "pass"
    assert len(contrasts["probes"]) == 6
    for probe in contrasts["probes"]:
        name = probe["probe"]
        expected = [1, 0, 1] if name.startswith("negative_") else [1, 1, 1]
        assert [result["score"] for result in receipt[name]] == expected
        assert receipt[name][1]["status"] == ("failure" if expected[1] == 0 else "success")
        assert probe["artifact_kind"] == "synthetic_contrast"
        assert probe["source_artifact_sha256"] == receipt["artifact_sha256"]["har"]
        assert probe["synthetic_trace_sha256"]
    active = receipt["equivalent_post_data_e1_is_active"][1]["actual"][0]["post_data"]
    parent = receipt["equivalent_post_data_e1_parent"][1]["actual"][0]["post_data"]
    assert active["is_active"] == "true"
    assert parent["parent"] == "2.0"


def test_task_without_post_conditions_marks_post_contrasts_not_applicable(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path, retrieved_data=[31], items_schema={"type": "number"})

    receipt = replay.validate_replay(tmp_path, candidate)

    assert receipt["post_data_contrasts"] == {"status": "not_applicable", "probes": []}


@pytest.mark.parametrize("field", ["$.*", "$.note.quantity", r"$.^cart\[\d+\]\[qty\]$"])
def test_jsonpath_scalar_post_contrasts_preserve_ignored_fields_and_other_events(
    tmp_path: Path, field: str
) -> None:
    from urllib.parse import urlencode

    candidate = _post_candidate(tmp_path)
    post_eval = candidate["task_spec"]["eval"][1]
    post_eval["expected"]["post_data"] = {field: "2"}
    post_eval["post_data_schema"] = {
        "type": "object",
        "properties": {field: {"type": "number"}},
    }
    post_eval["ignored_post_data_params_patterns"] = ["^form_key$"]
    nested = field == "$.note.quantity"
    body = (
        {"form_key": "synthetic-token", "note": {"quantity": "2"}}
        if nested
        else {"form_key": "synthetic-token", "cart[9274][qty]": "2"}
    )
    mime_type = "application/json" if nested else "application/x-www-form-urlencoded"
    entries = json.loads((tmp_path / "reference.har").read_text())["log"]["entries"]
    entries[0]["request"]["postData"] = {
        "mimeType": mime_type,
        "text": json.dumps(body) if nested else urlencode(body),
    }
    entries[0]["request"]["headers"][0]["value"] = mime_type
    (tmp_path / "reference.har").write_text(json.dumps({"log": {"entries": entries}}))
    with zipfile.ZipFile(tmp_path / "reference.zip", "w") as archive:
        archive.writestr(
            "trace.network",
            "".join(
                json.dumps({"type": "resource-snapshot", "snapshot": entry}) + "\n"
                for entry in entries
            ),
        )
    grounding = candidate["grounding"]
    grounding["task_spec_sha256"] = replay.sha256_json(candidate["task_spec"])
    grounding["artifact_sha256"] = {
        name: replay.sha256_file(tmp_path / relative)
        for name, relative in grounding["artifacts"].items()
    }
    candidate["grounding_sha256"] = replay.sha256_json(grounding)

    receipt = replay.validate_replay(tmp_path, candidate)

    # POST contrasts are independent of the installed AR null-answer limitation.
    # A dependency that accepts unexpected data still quarantines the full gate.
    answer_rejected = receipt["checks"]["negative_wrong_response_expected_vector"]
    assert receipt["status"] == ("pass" if answer_rejected else "quarantine")
    assert receipt["evaluator_limitations"] == (
        [] if answer_rejected else ["negative_wrong_response"]
    )
    assert all(
        passed
        for check, passed in receipt["checks"].items()
        if check != "negative_wrong_response_expected_vector"
    )
    assert [result["score"] for result in receipt["positive_har"]] == [1, 1, 1]
    assert [result["score"] for result in receipt["positive_playwright"]] == [1, 1, 1]
    probes = receipt["post_data_contrasts"]["probes"]
    assert len(probes) == 2
    assert receipt["post_data_contrasts"]["status"] == "pass"
    for probe in probes:
        negative = probe["probe"].startswith("negative_")
        results = receipt[probe["probe"]]
        assert [result["score"] for result in results] == ([1, 0, 1] if negative else [1, 1, 1])
        assert results[1]["status"] == ("failure" if negative else "success")
        actual = results[1]["actual"][0]["post_data"]
        quantity = actual["note"]["quantity"] if nested else actual["cart[9274][qty]"]
        assert float(quantity) == (3 if negative else 2)
        if not negative:
            assert quantity == "2.0"
        assert actual["form_key"] == "synthetic-token"
        assert probe["artifact_kind"] == "synthetic_contrast"
        assert probe["post_encoding"] == mime_type


def test_equivalent_post_representation_failure_quarantines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _post_candidate(tmp_path)
    evaluate = replay._evaluate

    def reject_equivalent_boolean(task, response, trace, config):
        results = evaluate(task, response, trace, config)
        if any((event.post_data or {}).get("is_active") == "true" for event in trace.events):
            results[1].update(status="failure", score=0)
        return results

    monkeypatch.setattr(replay, "_evaluate", reject_equivalent_boolean)

    receipt = replay.validate_replay(tmp_path, candidate)

    assert receipt["status"] == "quarantine"
    assert receipt["post_data_contrasts"]["status"] == "quarantine"
    assert "equivalent_post_data_e1_is_active" in receipt["evaluator_limitations"]
