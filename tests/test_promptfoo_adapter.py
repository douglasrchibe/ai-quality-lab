"""Tests for the Promptfoo adapter: provider, dataset conversion, shims, JS assertions."""

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ai_quality_lab.dataset import DatasetValidationError, load_dataset
from ai_quality_lab.promptfoo_adapter import (
    OUTPUT_SCHEMA,
    RESPONSE_FIELD,
    build_promptfoo_tests,
    call_api,
)

ROOT = Path(__file__).resolve().parents[1]
PROMPTFOO_DIR = ROOT / "promptfoo"
ON = {"guardrails": {"cooking": {"enabled": True}}}
OFF = {"guardrails": {"cooking": {"enabled": False}}}
PASTA = "Give me a pasta recipe."


def call(message=PASTA, configuration=ON, provider_config=None, prompt=None):
    context = {"vars": {"message": message, "configuration": configuration}}
    options = {"config": provider_config or {}}
    return call_api(prompt if prompt is not None else message, options, context)


def output_of(result):
    assert "error" not in result, result
    return json.loads(result["output"])


def load_shim(name):
    spec = importlib.util.spec_from_file_location(f"promptfoo_{name}", PROMPTFOO_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- Provider -----------------------------------------------------------------------------------


def test_provider_blocks_when_guardrail_enabled():
    assert output_of(call(configuration=ON)) == {
        "response": "Sorry, I can't help with cooking requests.",
        "blocked": True,
        "detected_topics": ["cooking"],
    }


def test_provider_answers_when_guardrail_disabled():
    output = output_of(call(configuration=OFF))
    assert output["blocked"] is False
    assert "pasta recipe" in output["response"]


def test_provider_output_matches_declared_schema_and_hides_mock_only_fields():
    output = output_of(call())
    assert set(output) == set(OUTPUT_SCHEMA["required"])
    assert isinstance(output["response"], str)
    assert isinstance(output["blocked"], bool)
    assert all(isinstance(t, str) for t in output["detected_topics"])


def test_provider_prefers_message_var_over_rendered_prompt():
    output = output_of(call(message="What is the capital of France?", prompt=PASTA))
    assert output["detected_topics"] == []


def test_provider_falls_back_to_prompt_without_message_var():
    result = call_api(PASTA, {}, {"vars": {"configuration": ON}})
    assert output_of(result)["blocked"] is True


@pytest.mark.parametrize("message", ["", "   \t  "])
def test_provider_passes_empty_input_through_to_chatbot(message):
    output = output_of(call(message=message))
    assert output["response"] == "Please enter a question. The message was empty."


def test_provider_accepts_json_string_configuration():
    assert output_of(call(configuration=json.dumps(ON)))["blocked"] is True


def test_provider_is_deterministic():
    assert call() == call()


def test_missing_configuration_returns_error_not_output():
    result = call_api(PASTA, {}, {"vars": {"message": PASTA}})
    assert "output" not in result
    assert "missing the 'configuration' var" in result["error"]


def test_invalid_configuration_returns_error():
    result = call(configuration={"guardrails": {"cooking": {"enabled": "yes"}}})
    assert result["error"].startswith("InvalidGuardrailConfigError:")


def test_simulated_failure_is_off_by_default():
    assert output_of(call_api(PASTA, None, {"vars": {"configuration": ON}}))["blocked"] is True


def test_simulated_failure_enabled_through_provider_config():
    output = output_of(call(provider_config={"simulated_failure": "guardrail_fail_open"}))
    assert output["blocked"] is False
    assert output["detected_topics"] == ["cooking"]


def test_unknown_simulated_failure_returns_error():
    result = call(provider_config={"simulated_failure": "chaos"})
    assert result["error"].startswith("ValueError:")


# --- Dataset conversion -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def generated():
    return build_promptfoo_tests()


def test_one_promptfoo_test_per_dataset_case(generated, repo_dataset_path):
    cases = load_dataset(repo_dataset_path)
    assert [t["metadata"]["case_id"] for t in generated] == [c.id for c in cases]
    assert len({t["description"] for t in generated}) == len(generated)
    for test, case in zip(generated, cases):
        assert test["vars"] == {"message": case.input, "configuration": case.configuration}
        assert test["metadata"]["severity"] == case.severity.value
        assert test["metadata"]["category"] == case.category.value


def test_every_test_starts_with_contract_assertions(generated):
    for test in generated:
        structured, consistency = test["assert"][:2]
        assert structured == {
            "type": "is-json",
            "value": OUTPUT_SCHEMA,
            "metric": "structured_output",
        }
        assert consistency["value"].endswith(":guardrailConsistency")


def test_each_expectation_maps_to_an_equivalent_assertion(generated, repo_dataset_path):
    for test, case in zip(generated, load_dataset(repo_dataset_path)):
        by_metric = {}
        for assertion in test["assert"][2:]:
            by_metric.setdefault(assertion["metric"], []).append(assertion)
        exp = case.expected
        if exp.blocked is not None:
            (blocked,) = by_metric.pop("blocked_status")
            assert blocked["config"] == {"expected": exp.blocked}
        if exp.detected_topics is not None:
            (topics,) = by_metric.pop("detected_topics")
            assert topics["config"] == {"expected": sorted(exp.detected_topics)}
        if exp.response_nonempty is not None:
            (nonempty,) = by_metric.pop("nonempty_response")
            assert nonempty["config"] == {"expected": exp.response_nonempty}
        required = by_metric.pop("required_phrase", [])
        prohibited = by_metric.pop("prohibited_phrase", [])
        assert [(a["type"], a["value"]) for a in required] == [
            ("icontains", p) for p in exp.response_contains
        ]
        assert [(a["type"], a["value"]) for a in prohibited] == [
            ("not-icontains", p) for p in exp.response_not_contains
        ]
        assert all(a["transform"] == RESPONSE_FIELD for a in required + prohibited)
        assert by_metric == {}, f"{case.id}: unexpected assertions {by_metric}"


def test_javascript_assertions_reference_exported_functions(generated):
    source = (PROMPTFOO_DIR / "assertions.js").read_text(encoding="utf-8")
    exports_block = re.search(r"module\.exports = \{([^}]*)\}", source).group(1)
    exported = {name.strip() for name in exports_block.split(",")}
    referenced = {
        a["value"].rsplit(":", 1)[1]
        for t in generated
        for a in t["assert"]
        if a["type"] == "javascript"
    }
    assert referenced <= exported


def test_invalid_dataset_fails_loudly(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema_version": 1, "cases": []}', encoding="utf-8")
    with pytest.raises(DatasetValidationError):
        build_promptfoo_tests(bad)


# --- Promptfoo-side files -----------------------------------------------------------------------


def test_test_generator_shim_reads_shared_dataset(generated):
    assert load_shim("tests").generate_tests() == generated
    assert load_shim("tests").generate_tests({}) == generated


def test_provider_shim_exposes_adapter():
    assert load_shim("provider").call_api is call_api


def test_failure_demo_is_isolated_from_normal_config():
    normal = (PROMPTFOO_DIR / "promptfooconfig.yaml").read_text(encoding="utf-8")
    demo = (PROMPTFOO_DIR / "promptfooconfig.failure-demo.yaml").read_text(encoding="utf-8")
    assert "simulated_failure" not in normal
    assert "simulated_failure: guardrail_fail_open" in demo
    for config in (normal, demo):
        assert "tests: file://tests.py:generate_tests" in config
        assert "id: file://provider.py" in config


def test_no_llm_provider_is_configured():
    for path in PROMPTFOO_DIR.glob("promptfooconfig*.yaml"):
        providers = re.findall(r"^\s*- id: (\S+)", path.read_text(encoding="utf-8"), re.MULTILINE)
        assert providers == ["file://provider.py"], path.name


# --- JavaScript assertion helpers (executed with Node.js) ---------------------------------------

NODE = shutil.which("node")


def run_js(function, output, config=None, configuration=ON):
    script = (
        "const a = require(process.argv[1]);"
        "const [fn, output, config, vars] = JSON.parse(process.argv[2]);"
        "console.log(JSON.stringify(a[fn](output, {config, vars})));"
    )
    payload = json.dumps([function, output, config or {}, {"configuration": configuration}])
    completed = subprocess.run(
        [NODE, "-e", script, str(PROMPTFOO_DIR / "assertions.js"), payload],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")


@needs_node
def test_js_blocked_status_explains_fail_open():
    fail_open = call(provider_config={"simulated_failure": "guardrail_fail_open"})["output"]
    result = run_js("blockedStatus", fail_open, {"expected": True})
    assert result["pass"] is False
    assert result["reason"] == (
        "blocked: expected true, actual false - the guardrail did not prevent the request"
    )
    assert run_js("blockedStatus", call()["output"], {"expected": True})["pass"] is True


@needs_node
def test_js_detected_topics_ignores_order():
    output = json.dumps({"response": "x", "blocked": False, "detected_topics": ["b", "a"]})
    assert run_js("detectedTopics", output, {"expected": ["a", "b"]})["pass"] is True
    assert run_js("detectedTopics", output, {"expected": ["a"]})["pass"] is False


@needs_node
@pytest.mark.parametrize("response, passed", [("text", True), ("   ", False), ("", False)])
def test_js_response_nonempty(response, passed):
    output = json.dumps({"response": response, "blocked": False, "detected_topics": []})
    assert run_js("responseNonempty", output, {"expected": True})["pass"] is passed


@needs_node
@pytest.mark.parametrize(
    "configuration, failure, passed",
    [
        (ON, "none", True),
        (OFF, "none", True),
        (ON, "guardrail_fail_open", False),
        (OFF, "guardrail_fail_open", True),  # nothing should be blocked, nothing is
    ],
)
def test_js_guardrail_consistency_invariant(configuration, failure, passed):
    output = call(configuration=configuration, provider_config={"simulated_failure": failure})
    result = run_js("guardrailConsistency", output["output"], configuration=configuration)
    assert result["pass"] is passed, result["reason"]
