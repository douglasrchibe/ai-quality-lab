"""Structural tests on the real GitHub Actions workflow files (they validate the CI itself)."""

import importlib.util
import json
import re
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
RAGAS = ROOT / ".github" / "workflows" / "ragas-environment.yml"


def load(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))  # YAML 1.1 parses the key `on` as True
    return data


@pytest.fixture(scope="module")
def ci():
    return load(CI)


def steps(job):
    return job.get("steps", [])


def run_commands(workflow):
    return [s["run"] for job in workflow["jobs"].values() for s in steps(job) if "run" in s]


def test_triggers_and_least_privilege(ci):
    assert set(ci["on"]) == {"push", "pull_request", "workflow_dispatch"}
    assert ci["on"]["push"]["branches"] == ["main"]
    assert ci["on"]["pull_request"]["branches"] == ["main"]
    assert ci["permissions"] == {"contents": "read"}
    assert "cancel-in-progress" in ci["concurrency"]
    for path in (CI, RAGAS):
        text = path.read_text(encoding="utf-8")
        assert "pull_request_target" not in text
        assert not re.search(r"\$\{\{\s*secrets\.", text), path.name  # no secret is referenced


def test_no_hidden_failures(ci):
    text = CI.read_text(encoding="utf-8")
    assert "continue-on-error" not in text
    assert "|| true" not in "\n".join(
        r for r in run_commands(ci) if "ci_check" in r or "pytest" in r)


def test_ci_is_offline_and_never_downloads_models(ci):
    commands = "\n".join(run_commands(ci)).lower()
    for forbidden in ("ollama pull", "ollama serve", "install.sh", "--llm ollama",
                      "-m live_llm", "aiql_run_live_tests"):
        assert forbidden not in commands, forbidden
    assert ci["env"]["AIQL_EMBED_PROVIDER"] == "hashing"
    assert "--embedder hashing" in commands


def test_actions_pinned_to_major_versions(ci):
    for workflow in (ci, load(RAGAS)):
        for job in workflow["jobs"].values():
            for step in steps(job):
                if "uses" in step:
                    assert re.fullmatch(r"actions/[\w-]+@v\d+", step["uses"]), step["uses"]


def test_every_job_has_a_timeout(ci):
    assert all("timeout-minutes" in job for job in ci["jobs"].values())


def test_quality_gate_needs_every_job_and_always_runs(ci):
    jobs = ci["jobs"]
    gate = jobs["quality-gate"]
    assert set(gate["needs"]) == set(jobs) - {"quality-gate"}
    assert gate["if"] == "${{ always() }}"
    assert any("ci_summary gate" in s.get("run", "") for s in steps(gate))


def test_each_quality_job_uploads_its_own_artifacts_even_on_failure(ci):
    for name, job in ci["jobs"].items():
        if name == "quality-gate":
            continue
        uploads = [s for s in steps(job) if s.get("uses", "").startswith("actions/upload-artifact")]
        assert len(uploads) == 1, name
        assert uploads[0]["if"] == "${{ always() }}"
        assert uploads[0]["with"]["name"] == f"ci-{name}"
        assert uploads[0]["with"]["path"] == f"ci-artifacts/{name}/"
        for step in steps(job):
            if "ci_check" in step.get("run", ""):
                assert f"--out ci-artifacts/{name}" in step["run"], (name, step["name"])


def test_every_check_runs_through_ci_check_with_explicit_expectation(ci):
    checks = [s["run"] for job in ci["jobs"].values() for s in steps(job)
              if "ci_check" in s.get("run", "")]
    assert len(checks) >= 13
    for command in checks:
        assert re.search(r"--expect (pass|blocked)", command), command
        assert re.search(r"--codes (project|pytest|promptfoo|pip)", command), command


def test_failure_demonstrations_must_stay_blocked(ci):
    # Commands that switch on a failure mode (not checks that merely read a demo's results).
    demos = [s["run"] for job in ci["jobs"].values() for s in steps(job)
             if "--simulate-failure" in s.get("run", "")
             or "eval:promptfoo:failure-demo" in s.get("run", "")]
    assert len(demos) == 4
    assert all("--expect blocked" in d for d in demos)
    agreement = [s["run"] for s in steps(ci["jobs"]["promptfoo"])
                 if "engine_comparison" in s.get("run", "")]
    assert len(agreement) == 2 and all("--expect pass" in a for a in agreement)


def test_referenced_python_modules_exist(ci):
    modules = set()
    for workflow in (ci, load(RAGAS)):
        for command in run_commands(workflow):
            modules.update(re.findall(r"python -m ([A-Za-z_][\w.]*)", command))
    modules -= {"pip", "pytest"}
    assert {"ai_quality_lab.ci_check", "ai_quality_lab.ci_summary", "ai_quality_lab.runner",
            "ai_quality_lab.engine_comparison", "ai_quality_lab.rag.evaluate",
            "ai_quality_lab.llm_eval"} <= modules
    for module in modules:
        assert importlib.util.find_spec(module) is not None, module


def test_referenced_npm_scripts_and_files_exist(ci):
    scripts = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["scripts"]
    commands = "\n".join(run_commands(ci))
    for script in re.findall(r"npm run --silent ([\w:-]+)", commands):
        assert script in scripts, script
    for path in ("requirements-llm.txt", "package-lock.json", "promptfoo/run.mjs",
                 "ci/known_defects.json"):
        assert (ROOT / path).exists(), path
    # The comparison reads the files the npm scripts write.
    assert "reports/promptfoo/results.json" in scripts["eval:promptfoo"]
    assert "reports/promptfoo/failure-demo-results.json" in scripts["eval:promptfoo:failure-demo"]
    assert "reports/promptfoo/results.json" in commands


def test_ragas_environment_is_separate_and_path_filtered():
    ragas = load(RAGAS)
    assert ragas["permissions"] == {"contents": "read"}
    assert "workflow_dispatch" in ragas["on"]
    for event in ("push", "pull_request"):
        assert "requirements-ragas.txt" in ragas["on"][event]["paths"]
        assert "constraints/ragas.txt" in ragas["on"][event]["paths"]
    installs = [c for c in run_commands(ragas) if "pip install" in c]
    assert installs == ["python -m pip install -r requirements-ragas.txt -c constraints/ragas.txt"]
    ci_installs = "\n".join(c for c in run_commands(load(CI)) if "pip install" in c)
    assert "ragas" not in ci_installs  # never mixed into the primary environment


# Each workflow installs with exactly one lock file; the Ragas environment needs its own because
# its jiter/openai versions conflict with the primary environment's.
LOCK_FILES = {CI: "constraints/ci-primary.txt", RAGAS: "constraints/ragas.txt"}
LOCK_EXTRAS = {"constraints/ci-primary.txt": ("dev", "llm"),
               "constraints/ragas.txt": ("dev", "ragas")}
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)$")


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_lock(relative: str) -> dict[str, str]:
    pins = {}
    for line in (ROOT / relative).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = PIN.match(line)
        assert match, f"{relative}: not a plain name==version line: {line!r}"
        pins[normalize(match.group(1))] = match.group(2)
    return pins


@pytest.mark.parametrize("workflow", list(LOCK_FILES), ids=lambda p: p.name)
def test_every_install_uses_the_workflow_lock_file(workflow):
    lock = LOCK_FILES[workflow]
    data = load(workflow)
    installs = [c for c in run_commands(data) if "pip install" in c]
    assert installs
    for command in installs:
        assert f"-c {lock}" in command, command
    for job in data["jobs"].values():
        for step in steps(job):
            if step.get("with", {}).get("cache") == "pip":  # cache key must change with the lock
                assert lock in step["with"]["cache-dependency-path"]


@pytest.mark.parametrize("lock", list(LOCK_EXTRAS))
def test_lock_file_matches_pyproject(lock):
    pins = read_lock(lock)
    assert "ai-quality-lab" not in pins  # the project itself is installed editable, never locked
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    for extra in LOCK_EXTRAS[lock]:
        for requirement in project["optional-dependencies"][extra]:
            name = normalize(re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0])
            assert name in pins, f"{lock} is missing {name}; regenerate it"
            if "==" in requirement:  # exact pins in pyproject must agree with the lock
                assert pins[name] == requirement.split("==", 1)[1].strip(), requirement
