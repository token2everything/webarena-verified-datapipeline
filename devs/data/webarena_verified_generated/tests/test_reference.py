"""Verify public-only inputs, baseline binding, and independently scored answers."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from contract import sha256_json
from generated_env import (
    audit_final_state,
    build_public_instruction,
    register_generated_task,
    resolve_public_task,
    score_reference,
)
from run_reference import validate_execution_baseline


def _candidate() -> dict:
    task = {
        "task_id": 2_000_001,
        "intent_template_id": 2_000_100,
        "revision": 1,
        "sites": ["gitlab"],
        "start_urls": ["__GITLAB__/projects"],
        "intent": "Count the listed public projects. Return the number as a list.",
        "intent_template": "Count the listed public projects. Return the number as a list.",
        "instantiation_dict": {},
        "eval": [
            {
                "evaluator": "AgentResponseEvaluator",
                "results_schema": {"type": "array", "items": {"type": "number"}},
                "expected": {"task_type": "RETRIEVE", "status": "SUCCESS", "retrieved_data": [917]},
            },
            {
                "evaluator": "NetworkEventEvaluator",
                "expected": {
                    "url": "__GITLAB__/projects",
                    "http_method": "GET",
                    "response_status": 200,
                },
            },
        ],
    }
    baseline = {
        "snapshot": "frozen-seed",
        "image_digest": "sha256:" + "a" * 64,
        "site_url": "http://gitlab.test",
    }
    return {
        "generated_task_id": "wavgen-v02-test-projects",
        "task_spec": task,
        "baseline": baseline,
        "baseline_sha256": sha256_json(baseline),
        "grounding": {"task_spec_sha256": sha256_json(task)},
        "task_definition": {"parameters": {"name": "Persisted Project"}},
    }


class _Page:
    def __init__(
        self,
        *,
        url: str = "http://gitlab.test/projects",
        text: str = "Old Project",
        persisted: str = "Persisted Project",
    ) -> None:
        self.url = url
        self.text = text
        self.persisted = persisted
        self.reloaded = False

    def reload(self, **kwargs: object) -> None:
        self.text = self.persisted
        self.reloaded = True

    def wait_for_timeout(self, milliseconds: int) -> None:
        pass

    def title(self) -> str:
        return "Projects"

    def locator(self, selector: str) -> _Page:
        return self

    def all(self) -> list[_Page]:
        return [self]

    def is_visible(self) -> bool:
        return True

    def inner_text(self) -> str:
        return self.text


def _write_artifacts(output: Path, candidate: dict) -> None:
    entry = {
        "request": {
            "url": "http://gitlab.test/projects",
            "method": "GET",
            "headers": [{"name": "accept", "value": "text/html"}],
        },
        "response": {"status": 200},
    }
    (output / "private_task.json").write_text(json.dumps(candidate["task_spec"]))
    (output / "network.har").write_text(json.dumps({"log": {"entries": [entry]}}))
    with zipfile.ZipFile(output / "trace.zip", "w") as archive:
        archive.writestr(
            "trace.network", json.dumps({"type": "resource-snapshot", "snapshot": entry}) + "\n"
        )
    for name in ("initial.png", "terminal.png", "state_audit.png"):
        (output / name).write_bytes(b"synthetic screenshot")
    (output / "initial_observation.json").write_text(json.dumps({"body_text": "Old Project"}))
    final_state = audit_final_state(candidate, {"gitlab": "http://gitlab.test"}, _Page())
    (output / "final_state.json").write_text(json.dumps(final_state))


def test_instruction_is_independent_of_private_answer_and_evaluator() -> None:
    candidate = _candidate()
    public = resolve_public_task(candidate, {"gitlab": "http://new-stack.test"})
    prompt = build_public_instruction(public)
    changed = copy.deepcopy(candidate)
    changed["task_spec"]["eval"][0]["expected"]["retrieved_data"] = [812345]
    changed["task_spec"]["eval"].append(
        {"evaluator": "NetworkEventEvaluator", "expected": {"url": "PRIVATE_TARGET"}}
    )
    assert (
        build_public_instruction(resolve_public_task(changed, {"gitlab": "http://new-stack.test"}))
        == prompt
    )
    assert "917" not in prompt and "812345" not in prompt and "PRIVATE_TARGET" not in prompt
    assert "http://new-stack.test/projects" in prompt
    assert set(public) == {"generated_task_id", "sites", "start_urls", "intent"}


def test_prompt_rejects_private_fields() -> None:
    public = resolve_public_task(_candidate(), {"gitlab": "http://new-stack.test"})
    public["eval"] = []
    with pytest.raises(ValueError, match="exact public"):
        build_public_instruction(public)


def test_site_mapping_cannot_add_another_site_or_credentials() -> None:
    with pytest.raises(ValueError, match="single site"):
        resolve_public_task(
            _candidate(), {"gitlab": "http://gitlab.test", "reddit": "http://reddit.test"}
        )
    with pytest.raises(ValueError, match="credentials"):
        resolve_public_task(_candidate(), {"gitlab": "http://secret:password@gitlab.test"})


def test_fresh_execution_baseline_must_match_grounded_snapshot() -> None:
    candidate = _candidate()
    receipt = {
        "session_id": "test-owned",
        "sandbox_id": "test-owned-sandbox",
        "generated_task_id": candidate["generated_task_id"],
        "created_at": "2026-10-03T00:00:00Z",
        "snapshot": "frozen-seed",
        "image_digest": candidate["baseline"]["image_digest"],
        "site_url": "http://new-stack.test",
        "fresh_baseline": True,
    }
    validate_execution_baseline(receipt, candidate, {"gitlab": "http://new-stack.test"})
    receipt["snapshot"] = "different-seed"
    with pytest.raises(ValueError, match="snapshot"):
        validate_execution_baseline(receipt, candidate, {"gitlab": "http://new-stack.test"})
    receipt["snapshot"] = "frozen-seed"
    receipt["fresh_baseline"] = False
    with pytest.raises(ValueError, match="fresh"):
        validate_execution_baseline(receipt, candidate, {"gitlab": "http://new-stack.test"})


@pytest.mark.parametrize(("answer", "expected"), [([917], "pass"), ([1], "fail"), (None, "fail")])
def test_ended_episode_is_scored_independently(
    tmp_path: Path, answer: list | None, expected: str
) -> None:
    candidate = _candidate()
    _write_artifacts(tmp_path, candidate)
    response = json.dumps(
        {
            "task_type": "RETRIEVE",
            "status": "SUCCESS",
            "retrieved_data": answer,
            "error_details": None,
        }
    )
    receipt = score_reference(candidate, response, tmp_path, {"gitlab": "http://gitlab.test"})
    assert receipt["agent_ended"] is True
    assert receipt["execution_status"] == "completed"
    assert receipt["status"] == expected
    assert [row["score"] for row in receipt["har_results"]] == [
        1 if expected == "pass" else 0,
        1,
    ]
    assert receipt["public_projection_sha256"]
    assert receipt["artifacts"]["har"] == "network.har"
    assert receipt["artifact_sha256"]["trace"]


def test_absent_final_response_remains_incomplete(tmp_path: Path) -> None:
    candidate = _candidate()
    _write_artifacts(tmp_path, candidate)
    receipt = score_reference(candidate, None, tmp_path, {"gitlab": "http://gitlab.test"})
    assert receipt["status"] == "fail"
    assert receipt["agent_ended"] is False
    assert receipt["execution_status"] == "incomplete"


@pytest.mark.parametrize(
    ("task_type", "status"),
    [("NAVIGATE", "SUCCESS"), ("MUTATE", "SUCCESS"), ("RETRIEVE", "NOT_FOUND_ERROR")],
)
def test_null_answer_profiles_can_complete_correctly(
    tmp_path: Path, task_type: str, status: str
) -> None:
    candidate = _candidate()
    response = {
        "task_type": task_type,
        "status": status,
        "retrieved_data": None,
        "error_details": None,
    }
    candidate["task_spec"]["eval"][0].update(
        results_schema={"type": "null"},
        expected={key: value for key, value in response.items() if key != "error_details"},
    )
    if task_type == "MUTATE":
        candidate["grounding"]["final_state_checks"] = [
            {"kind": "visible_text", "parameter": "name"}
        ]
    _write_artifacts(tmp_path, candidate)
    receipt = score_reference(
        candidate, json.dumps(response), tmp_path, {"gitlab": "http://gitlab.test"}
    )
    assert receipt["agent_ended"] is True
    assert receipt["status"] == "pass"


def test_mutation_requires_persistent_readback_even_when_both_evaluators_pass(
    tmp_path: Path,
) -> None:
    candidate = _candidate()
    response = {
        "task_type": "MUTATE",
        "status": "SUCCESS",
        "retrieved_data": None,
        "error_details": None,
    }
    candidate["task_spec"]["eval"][0].update(
        results_schema={"type": "null"},
        expected={key: value for key, value in response.items() if key != "error_details"},
    )
    _write_artifacts(tmp_path, candidate)
    receipt = score_reference(
        candidate, json.dumps(response), tmp_path, {"gitlab": "http://gitlab.test"}
    )
    assert receipt["checks"]["all_evaluators_pass"] is True
    assert receipt["checks"]["final_state_confirmed"] is False
    assert receipt["status"] == "quarantine"


def test_mutation_audit_checks_reloaded_state_instead_of_unsaved_display() -> None:
    candidate = _candidate()
    candidate["task_spec"]["eval"][0]["expected"]["task_type"] = "MUTATE"
    candidate["grounding"]["final_state_checks"] = [
        {"kind": "visible_text", "parameter": "name", "selector": "#project-name"}
    ]
    page = _Page(text="Persisted Project", persisted="Old Project")
    result = audit_final_state(candidate, {"gitlab": "http://gitlab.test"}, page)
    assert page.reloaded is True
    assert result["confirmed"] is False
    assert result["checks"][0]["actual"] == "Old Project"


def test_navigation_audit_requires_the_target_terminal_url() -> None:
    candidate = _candidate()
    candidate["task_spec"]["eval"][0]["expected"]["task_type"] = "NAVIGATE"
    result = audit_final_state(
        candidate, {"gitlab": "http://gitlab.test"}, _Page(url="http://gitlab.test/wrong")
    )
    assert result["confirmed"] is False
    assert result["checks"][0]["actual"] == "http://gitlab.test/wrong"


@pytest.fixture
def readback_page() -> Any:
    """Use an owned browser and data URL; no website server is required."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip("Readback DOM tests require the configured Chromium runtime")
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        try:
            yield page
        finally:
            context.close()
            browser.close()


def _mutation_candidate(parameters: dict, checks: list[dict]) -> dict:
    candidate = _candidate()
    candidate["task_spec"]["eval"][0].update(
        results_schema={"type": "null"},
        expected={"task_type": "MUTATE", "status": "SUCCESS", "retrieved_data": None},
    )
    candidate["task_definition"]["parameters"] = parameters
    candidate["grounding"]["final_state_checks"] = checks
    return candidate


@pytest.mark.parametrize(
    ("html", "actual", "visible_count", "confirmed"),
    [
        ('<div class="description">Original description</div>', "Original description", 1, True),
        (
            '<div class="description">Original description<p>Unrequested addition</p></div>',
            "Original description\n\nUnrequested addition",
            1,
            False,
        ),
        (
            '<div class="description">Replacement description</div>',
            "Replacement description",
            1,
            False,
        ),
        (
            '<div class="description">Original description</div>'
            '<div class="description">Original description</div>',
            ["Original description", "Original description"],
            2,
            False,
        ),
        (
            '<div class="description" style="display:none">Original description</div>',
            [],
            0,
            False,
        ),
        (
            '<div class="description" style="display:none">Replacement description</div>'
            '<div class="description">Original description</div>',
            "Original description",
            1,
            True,
        ),
    ],
)
def test_visible_text_exact_requires_one_complete_persisted_text(
    readback_page: Any, html: str, actual: str | list[str], visible_count: int, confirmed: bool
) -> None:
    readback_page.goto("data:text/html," + quote(html))
    candidate = _mutation_candidate(
        {"description": "Original description"},
        [{"kind": "visible_text_exact", "parameter": "description", "selector": ".description"}],
    )
    result = audit_final_state(candidate, {"gitlab": "http://gitlab.test"}, readback_page)
    assert result["reloaded_for_persistence"] is True
    assert result["confirmed"] is confirmed
    assert result["checks"][0]["actual"] == actual
    assert result["checks"][0]["visible_count"] == visible_count


@pytest.mark.parametrize(
    ("frame_text", "hidden_frame", "actual", "confirmed"),
    [
        ("<p>Original description</p>", False, "Original description", True),
        (
            "<p>Original description</p><p>Unrequested addition</p>",
            False,
            "Original description\n\nUnrequested addition",
            False,
        ),
        ("<p>Wrong description</p>", False, "Wrong description", False),
        ("<p>Original description</p>", True, [], False),
    ],
)
def test_visible_text_exact_reads_complete_visible_frame_after_ui_preparation(
    readback_page: Any, frame_text: str, hidden_frame: bool, actual: str | list, confirmed: bool
) -> None:
    hidden = "hidden" if hidden_frame else ""
    html = (
        "<div>Original description</div>"
        '<button id="open" onclick="document.getElementById(\'panel\').hidden=false">Open</button>'
        '<div id="panel" hidden>'
        f'<iframe id="editor" src="data:text/html,{quote(frame_text)}" {hidden}></iframe></div>'
    )
    readback_page.goto("data:text/html," + quote(html))
    candidate = _mutation_candidate(
        {"description": "Original description"},
        [
            {
                "kind": "visible_text_exact",
                "parameter": "description",
                "selector": "body",
                "frame_selector": "#editor",
                "prepare_selectors": ["#open"],
            }
        ],
    )
    result = audit_final_state(candidate, {"gitlab": "http://gitlab.test"}, readback_page)
    assert result["reloaded_for_persistence"] is True
    assert result["confirmed"] is confirmed
    check = result["checks"][0]
    assert check["actual"] == actual
    assert check["frame_match_count"] == 1
    assert check["frame_visible_count"] == (0 if hidden_frame else 1)
    assert check["preparation"] == [{"selector": "#open", "clicked": True}]


def test_visible_text_containment_still_accepts_additional_text(readback_page: Any) -> None:
    readback_page.goto(
        "data:text/html,"
        + quote('<div class="description">Original description<p>Unrequested addition</p></div>')
    )
    candidate = _mutation_candidate(
        {"description": "Original description"},
        [{"kind": "visible_text", "parameter": "description", "selector": ".description"}],
    )
    result = audit_final_state(candidate, {"gitlab": "http://gitlab.test"}, readback_page)
    assert result["confirmed"] is True
    assert result["checks"][0]["actual"] == "Original description\n\nUnrequested addition"


def test_input_value_reads_persisted_value_after_reload(readback_page: Any) -> None:
    readback_page.goto("data:text/html," + quote('<input name="name" value="Saved Project">'))
    readback_page.locator('input[name="name"]').fill("Unsaved Display")
    candidate = _mutation_candidate(
        {"name": "Saved Project"},
        [{"kind": "input_value", "parameter": "name", "selector": 'input[name="name"]'}],
    )
    result = audit_final_state(candidate, {"gitlab": "http://gitlab.test"}, readback_page)
    assert result["confirmed"] is True
    assert result["checks"][0]["actual"] == "Saved Project"
    assert result["checks"][0]["visible_count"] == 1


def test_wrong_input_value_is_a_failed_readback(readback_page: Any) -> None:
    readback_page.goto("data:text/html," + quote('<input name="name" value="Original Project">'))
    candidate = _mutation_candidate(
        {"name": "Required Project"},
        [{"kind": "input_value", "parameter": "name", "selector": 'input[name="name"]'}],
    )
    result = audit_final_state(candidate, {"gitlab": "http://gitlab.test"}, readback_page)
    assert result["confirmed"] is False
    assert result["checks"][0]["actual"] == "Original Project"
    assert "readback_error" not in result["checks"][0]


@pytest.mark.parametrize(
    ("saved_value", "confirmed"), [("Saved Project", True), ("Wrong Project", False)]
)
def test_input_value_prepares_ui_after_reload_before_visible_readback(
    readback_page: Any, saved_value: str, confirmed: bool
) -> None:
    html = (
        '<button id="expand" onclick="this.insertAdjacentHTML(\'afterend\', '
        f"'<input name=name value=&quot;{saved_value}&quot;>')\">Open details</button>"
    )
    readback_page.goto("data:text/html," + quote(html))
    readback_page.locator("#expand").click()
    readback_page.locator('input[name="name"]').fill("Unsaved Display")
    candidate = _mutation_candidate(
        {"name": "Saved Project"},
        [
            {
                "kind": "input_value",
                "parameter": "name",
                "selector": 'input[name="name"]',
                "prepare_selectors": ["#expand"],
            }
        ],
    )
    result = audit_final_state(candidate, {"gitlab": "http://gitlab.test"}, readback_page)
    assert result["confirmed"] is confirmed
    assert result["reloaded_for_persistence"] is True
    assert result["checks"][0]["actual"] == saved_value
    assert result["checks"][0]["visible_count"] == 1
    assert result["checks"][0]["preparation"] == [{"selector": "#expand", "clicked": True}]


def test_input_value_records_ordered_ui_preparation_for_collapsed_editor(
    readback_page: Any,
) -> None:
    html = """
    <button id="open" onclick="document.getElementById('panel').hidden=false">Open</button>
    <div id="panel" hidden>
      <button id="reveal" onclick="document.getElementById('name').hidden=false">Show text</button>
      <textarea id="name" hidden>Saved Project</textarea>
    </div>
    """
    readback_page.goto("data:text/html," + quote(html))
    candidate = _mutation_candidate(
        {"name": "Saved Project"},
        [
            {
                "kind": "input_value",
                "parameter": "name",
                "selector": "#name",
                "prepare_selectors": ["#open", "#reveal"],
            }
        ],
    )
    result = audit_final_state(candidate, {"gitlab": "http://gitlab.test"}, readback_page)
    assert result["confirmed"] is True
    assert result["checks"][0]["actual"] == "Saved Project"
    assert result["checks"][0]["visible_count"] == 1
    assert result["checks"][0]["preparation"] == [
        {"selector": "#open", "clicked": True},
        {"selector": "#reveal", "clicked": True},
    ]


def test_input_value_failed_ui_preparation_cannot_confirm_visible_value(readback_page: Any) -> None:
    readback_page.goto("data:text/html," + quote('<input id="name" value="Saved Project">'))
    candidate = _mutation_candidate(
        {"name": "Saved Project"},
        [
            {
                "kind": "input_value",
                "parameter": "name",
                "selector": "#name",
                "prepare_selectors": ["#missing", "#never"],
            }
        ],
    )
    result = audit_final_state(candidate, {"gitlab": "http://gitlab.test"}, readback_page)
    assert result["confirmed"] is False
    assert result["checks"][0]["actual"] == "Saved Project"
    preparation = result["checks"][0]["preparation"]
    assert len(preparation) == 1
    assert preparation[0]["selector"] == "#missing"
    assert preparation[0]["clicked"] is False
    assert "error" in preparation[0]


@pytest.mark.parametrize("checked", [True, False])
def test_checked_reads_native_hidden_control_with_visible_label(
    readback_page: Any, checked: bool
) -> None:
    native_state = "checked" if checked else ""
    readback_page.goto(
        "data:text/html,"
        + quote(
            f'<input id="active" name="active" type="checkbox" {native_state} '
            'style="display:none"><label for="active">Active</label>'
        )
    )
    candidate = _mutation_candidate(
        {"active": checked},
        [{"kind": "checked", "parameter": "active", "selector": 'input[name="active"]'}],
    )
    result = audit_final_state(candidate, {"gitlab": "http://gitlab.test"}, readback_page)
    assert result["confirmed"] is True
    assert result["checks"][0]["actual"] is checked
    assert result["checks"][0]["control_visible"] is False
    assert result["checks"][0]["associated_label_visible"] is True


def test_hidden_checked_state_without_visible_label_is_unconfirmed(readback_page: Any) -> None:
    readback_page.goto(
        "data:text/html,"
        + quote('<input name="active" type="checkbox" checked style="display:none">')
    )
    candidate = _mutation_candidate(
        {"active": True},
        [{"kind": "checked", "parameter": "active", "selector": 'input[name="active"]'}],
    )
    result = audit_final_state(candidate, {"gitlab": "http://gitlab.test"}, readback_page)
    assert result["confirmed"] is False
    assert result["checks"][0]["actual"] is True
    assert result["checks"][0]["associated_label_visible"] is False


def test_hidden_checked_control_can_use_wrapping_label_with_an_id(readback_page: Any) -> None:
    readback_page.goto(
        "data:text/html,"
        + quote(
            '<label>Active<input id="active" type="checkbox" checked style="display:none"></label>'
        )
    )
    candidate = _mutation_candidate(
        {"active": True},
        [{"kind": "checked", "parameter": "active", "selector": "#active"}],
    )
    result = audit_final_state(candidate, {"gitlab": "http://gitlab.test"}, readback_page)
    assert result["confirmed"] is True
    assert result["checks"][0]["associated_label_visible"] is True


@pytest.mark.parametrize(
    ("parent_label", "passed"), [("Workspace Root (ID: 17)", True), ("Wrong Root (ID: 18)", False)]
)
def test_ancestor_text_proves_immediate_parent_instead_of_global_presence(
    readback_page: Any, parent_label: str, passed: bool
) -> None:
    readback_page.goto(
        "data:text/html,"
        + quote(
            "<div>Wrong Root (ID: 18)</div><ul><li><div><a>"
            "<span>Workspace Root (ID: 17) (12)</span></a></div><ul><li><div><a>"
            '<span id="target">New Project (ID: 31) (0)</span></a></div></li></ul></li></ul>'
        )
    )
    candidate = _mutation_candidate(
        {"parent_label": parent_label},
        [
            {
                "kind": "ancestor_text",
                "parameter": "parent_label",
                "selector": "#target",
                "ancestor_selector": "xpath=ancestor::li[1]/parent::ul/parent::li/div/a/span",
            }
        ],
    )
    result = audit_final_state(candidate, {"gitlab": "http://gitlab.test"}, readback_page)
    assert result["confirmed"] is passed
    assert result["checks"][0]["actual"] == ["Workspace Root (ID: 17) (12)"]
    assert result["checks"][0]["ancestor_visible_count"] == 1


def test_text_set_preserved_accepts_hidden_loaded_labels_and_changed_counts(
    readback_page: Any,
) -> None:
    readback_page.goto(
        "data:text/html,"
        + quote(
            '<span class="node">Workspace Root (ID: 17) (13)</span>'
            '<span class="node" style="display:none">Archived Group (ID: 19) (4)</span>'
        )
    )
    labels = ["Archived Group (ID: 19)", "Workspace Root (ID: 17)"]
    candidate = _mutation_candidate(
        {"existing_labels": labels},
        [{"kind": "text_set_preserved", "parameter": "existing_labels", "selector": ".node"}],
    )
    result = audit_final_state(candidate, {"gitlab": "http://gitlab.test"}, readback_page)
    assert result["confirmed"] is True
    assert result["checks"][0]["match_count"] == 2
    assert result["checks"][0]["visible_count"] == 1
    assert result["checks"][0]["missing_labels"] == []


def test_text_set_preserved_can_expand_ui_and_records_preparation(readback_page: Any) -> None:
    html = (
        '<span class="node">Workspace Root (ID: 17) (13)</span>'
        '<button id="expand" onclick="this.insertAdjacentHTML(\'afterend\', '
        "'<span class=node>Archived Group (ID: 19) (4)</span>')\">Expand All</button>"
    )
    readback_page.goto("data:text/html," + quote(html))
    candidate = _mutation_candidate(
        {"existing_labels": ["Workspace Root (ID: 17)", "Archived Group (ID: 19)"]},
        [
            {
                "kind": "text_set_preserved",
                "parameter": "existing_labels",
                "selector": ".node",
                "expand_selector": "#expand",
            }
        ],
    )
    result = audit_final_state(candidate, {"gitlab": "http://gitlab.test"}, readback_page)
    assert result["confirmed"] is True
    assert result["checks"][0]["preparation"]["clicked"] is True
    assert result["checks"][0]["match_count"] == 2


def test_text_set_preserved_reports_unloaded_or_missing_label(readback_page: Any) -> None:
    readback_page.goto(
        "data:text/html," + quote('<span class="node">Workspace Root (ID: 17) (13)</span>')
    )
    candidate = _mutation_candidate(
        {"existing_labels": ["Workspace Root (ID: 17)", "Missing Group (ID: 19)"]},
        [{"kind": "text_set_preserved", "parameter": "existing_labels", "selector": ".node"}],
    )
    result = audit_final_state(candidate, {"gitlab": "http://gitlab.test"}, readback_page)
    assert result["confirmed"] is False
    assert result["checks"][0]["missing_labels"] == ["Missing Group (ID: 19)"]
    assert result["checks"][0]["match_count"] == 1


@pytest.mark.parametrize(
    ("extra_label", "confirmed"),
    [
        (None, True),
        ("Unrequested Project (ID: 32) (0)", False),
        ("New Project Archive (ID: 31) (0)", False),
    ],
)
def test_text_set_preserved_requires_exact_requested_addition(
    readback_page: Any, extra_label: str | None, confirmed: bool
) -> None:
    requested_label = "New Project (ID: 31) (0)"
    labels = ["Workspace Root (ID: 17) (13)", "Archived Group (ID: 19) (4)"]
    if extra_label and extra_label.startswith("New Project Archive"):
        labels.append(extra_label)
    else:
        labels.append(requested_label)
        if extra_label:
            labels.append(extra_label)
    readback_page.goto(
        "data:text/html," + quote("".join(f'<span class="node">{label}</span>' for label in labels))
    )
    candidate = _mutation_candidate(
        {
            "existing_labels": ["Workspace Root (ID: 17)", "Archived Group (ID: 19)"],
            "new_name": "New Project",
            "new_count": 1,
        },
        [
            {
                "kind": "text_set_preserved",
                "parameter": "existing_labels",
                "selector": ".node",
                "added_count_parameter": "new_count",
                "added_name_parameter": "new_name",
            }
        ],
    )
    result = audit_final_state(candidate, {"gitlab": "http://gitlab.test"}, readback_page)
    assert result["confirmed"] is confirmed
    check = result["checks"][0]
    assert check["missing_labels"] == []
    assert check["expected_added_count"] == 1
    assert check["expected_added_name"] == "New Project"
    assert check["unexpected_added_labels"] == ([extra_label] if extra_label else [])
    assert check["actual_added_count"] == (2 if extra_label and "Unrequested" in extra_label else 1)


@pytest.mark.parametrize("final_response", ["valid", "malformed", "empty"])
def test_generated_gui_actions_ignore_initial_greeting_until_explicit_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, final_response: str
) -> None:
    from importlib import import_module

    import gymnasium
    from playwright.sync_api import Page, sync_playwright

    registry = import_module("lite.gym.registry")

    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip("Generated environment regression requires the configured Chromium runtime")

    candidate = _candidate()
    candidate["generated_task_id"] = f"wavgen-v02-termination-{final_response}"
    # This regression owns a local page and tests terminal signaling, with the
    # real answer evaluator. It requires no network endpoint or model provider.
    candidate["task_spec"]["eval"] = candidate["task_spec"]["eval"][:1]
    candidate["grounding"]["task_spec_sha256"] = sha256_json(candidate["task_spec"])
    (tmp_path / "private_task.json").write_text(json.dumps([candidate["task_spec"]]))
    html = (
        '<body style="margin:0"><button style="width:100px;height:50px" '
        "onclick=\"document.getElementById('result').textContent='Clicked'\">Click</button>"
        '<p id="result">Ready</p><textarea id="text"></textarea></body>'
    )
    original_goto = Page.goto

    def local_start(page: Page, url: str, **kwargs: Any) -> Any:
        if url == "http://gitlab.test/projects":
            return original_goto(page, "data:text/html," + quote(html), **kwargs)
        return original_goto(page, url, **kwargs)

    monkeypatch.setattr(Page, "goto", local_start)
    factories = {}

    def capture_registration(*, key: str, entry_point: Any, split: str) -> None:
        factories[key] = entry_point

    monkeypatch.setattr(registry, "register", capture_registration)
    key = register_generated_task(
        candidate, {"gitlab": "http://gitlab.test"}, tmp_path, max_steps=10
    )
    env = factories[key]()
    response = (
        json.dumps(
            {
                "task_type": "RETRIEVE",
                "status": "SUCCESS",
                "retrieved_data": [917],
                "error_details": None,
            }
        )
        if final_response == "valid"
        else "not JSON"
        if final_response == "malformed"
        else ""
    )

    async def exercise() -> None:
        try:
            await env.reset()
            assert any(
                message["role"] == "assistant" for message in env._env.unwrapped.chat.messages
            )
            clicked = await env._execute_bgym_action("mouse_click(25, 20)")
            assert clicked is not None
            assert clicked["bg_terminated"] is False
            assert (
                await env._run_in_thread(
                    lambda: env._env.unwrapped.page.locator("#result").inner_text()
                )
                == "Clicked"
            )
            assert env._env.unwrapped.task.raw_response is None
            # A finish function name inside ordinary typed text is not a call.
            await env._run_in_thread(lambda: env._env.unwrapped.page.locator("#text").focus())
            typed = await env._execute_bgym_action(
                'keyboard_type("send_msg_to_user(\\"not a response\\")")'
            )
            assert typed is not None
            assert typed["bg_terminated"] is False
            assert env._env.unwrapped.task.requested_response is None
            finished = await env._execute_bgym_action(f"send_msg_to_user({response!r})")
            assert finished is not None
            assert finished["bg_terminated"] is True
            assert finished["reward"] == (1.0 if final_response == "valid" else 0.0)
            assert env._env.unwrapped.task.raw_response == response
        finally:
            await env.close()

    try:
        asyncio.run(exercise())
        assert (tmp_path / "agent_response.txt").read_text() == response
    finally:
        gymnasium.envs.registry.pop(f"browsergym/wavgen_v02.{candidate['generated_task_id']}", None)
