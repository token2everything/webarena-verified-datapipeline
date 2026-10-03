"""Exercise isolated evaluator builds against synthetic semantic regressions."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
_SPEC = importlib.util.spec_from_file_location(
    "wav_evaluator_build", _ROOT / "prepare_evaluator.py"
)
assert _SPEC is not None and _SPEC.loader is not None
builder = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(builder)

_PROBE = r"""
import json
import sys
from pathlib import Path
import webarena_verified
from webarena_verified.core.evaluation.evaluators import EVALUATOR_REGISTRY
from webarena_verified.types import WebArenaSite, WebArenaVerifiedTask
from webarena_verified.types.config import EnvironmentConfig, WebArenaVerifiedConfig
from webarena_verified.types.eval import TaskEvalContext
from webarena_verified.types.tracing import NetworkTrace

data_file = Path(sys.argv[1])
config = WebArenaVerifiedConfig(
    test_data_file=data_file,
    environments={WebArenaSite.GITLAB: EnvironmentConfig(urls=["http://example.test"])},
)
trace = NetworkTrace.model_construct(is_playwright=False, src_file=Path("<synthetic>"), events=())
results = []
profiles = [
    ("RETRIEVE", "NOT_FOUND_ERROR", {"type": "null"}, None),
    ("NAVIGATE", "SUCCESS", {"type": "null"}, None),
    ("MUTATE", "SUCCESS", {"type": "null"}, None),
    ("RETRIEVE", "SUCCESS", {"type": "array", "items": {"type": "string"}}, ["Independent item"]),
    ("RETRIEVE", "SUCCESS", {"type": "array", "items": {"type": "number"}}, [23.5]),
    ("RETRIEVE", "SUCCESS", {"type": "array", "items": {"type": "boolean"}}, [True]),
    ("RETRIEVE", "SUCCESS", {"type": "array", "items": {
        "type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]
    }}, [{"name": "Independent item"}]),
]
for task_type, status, schema, expected_data in profiles:
    expected = {
        "task_type": task_type, "status": status,
        "retrieved_data": expected_data, "error_details": None,
    }
    task = WebArenaVerifiedTask.model_validate({
        "sites": ["gitlab"], "task_id": 2000001, "intent_template_id": 2000101,
        "start_urls": ["__GITLAB__"], "intent": "Synthetic independent task.",
        "intent_template": "Synthetic independent task.", "instantiation_dict": {}, "revision": 1,
        "eval": [{
            "evaluator": "AgentResponseEvaluator", "results_schema": schema, "expected": expected
        }],
    })
    probes = [(expected_data, 1.0), (["unexpected"] if expected_data is None else [], 0.0)]
    for actual_data, expected_score in probes:
        actual = {**expected, "retrieved_data": actual_data}
        context = TaskEvalContext(
            task=task, agent_response_raw=json.dumps(actual), network_trace=trace, config=config
        )
        result = EVALUATOR_REGISTRY["AgentResponseEvaluator"]().evaluate(
            context=context, config=task.eval[0]
        )
        results.append({
            "task_type": task_type, "schema": schema,
            "score": result.score, "status": result.status, "expected_score": expected_score,
        })
print(json.dumps({"package_path": webarena_verified.__file__, "results": results}))
"""


def test_build_pins_source_patch_and_excludes_official_assets(tmp_path: Path) -> None:
    output = tmp_path / "build"

    manifest = builder.prepare_evaluator(output)

    assert manifest["base_version"] == "1.2.3"
    assert manifest["source_module_sha256"] == builder.SOURCE_MODULE_SHA256
    assert manifest["changed_files"] == [str(builder.MODULE_PATH)]
    assert not (output / "webarena_verified" / "assets").exists()
    assert not list(output.rglob("__pycache__"))
    assert not list(output.rglob("webarena-verified.json"))
    assert manifest["source_code_sha256"] != manifest["patched_code_sha256"]
    assert (
        builder.sha256_file(Path(manifest["source_package_root"]) / builder.MODULE_PATH)
        == builder.SOURCE_MODULE_SHA256
    )
    assert json.loads((output / "build_manifest.json").read_text()) == manifest


def test_real_evaluator_null_actions_errors_and_typed_retrieval(tmp_path: Path) -> None:
    output = tmp_path / "build"
    builder.prepare_evaluator(output)
    synthetic_file = tmp_path / "synthetic-private.jsonl"
    synthetic_file.write_text("\n")
    env = dict(os.environ, PYTHONPATH=str(output), PYTHONDONTWRITEBYTECODE="1")

    completed = subprocess.run(
        [sys.executable, "-c", _PROBE, str(synthetic_file)],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    packet = json.loads(completed.stdout)
    assert Path(packet["package_path"]).is_relative_to(output)
    assert len(packet["results"]) == 14
    for result in packet["results"]:
        assert result["score"] == result["expected_score"], result
        assert result["status"] == ("success" if result["expected_score"] else "failure"), result
    assert "Falling back to default dataset" not in completed.stderr


def test_existing_build_is_not_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "build"
    builder.prepare_evaluator(output)

    with pytest.raises(FileExistsError):
        builder.prepare_evaluator(output)
