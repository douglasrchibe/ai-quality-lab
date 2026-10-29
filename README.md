# AI Quality Lab

An automated evaluation framework for a **mock** AI chatbot with configurable topic guardrails.
It is a learning project demonstrating QA automation practices for AI evaluation: deterministic
test cases, regression detection, quality metrics, reporting, and CI quality gates.

## Current status

**Phase 1 (project foundation) is complete.** The package structure, tooling, and a setup test exist.
The chatbot, evaluator, runner, and reporting modules are **placeholders. No functionality is
implemented yet.**

## Prerequisites

- Windows with PowerShell
- Python 3.12+ (`py --list` to check)
- Git
- No LLM, API key, database, Docker, or cloud service is required.

## Setup (Windows PowerShell)

From the project root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

`requirements.txt` installs this package in editable mode plus dev tools (pytest), as declared in
`pyproject.toml`.

### Activating the virtual environment

```powershell
.\.venv\Scripts\Activate.ps1
```

Your prompt shows `(.venv)` when it is active. Run `deactivate` to leave it.
If activation is blocked by execution policy, check `Get-ExecutionPolicy -List`. `RemoteSigned` for
CurrentUser is sufficient. Alternatively, call the venv's Python directly without activating:
`.\.venv\Scripts\python.exe -m pytest`.

## Running tests

```powershell
python -m pytest
```

## Project layout

```
src/ai_quality_lab/   package: mock_agent, evaluator, runner, reporting
tests/                pytest unit tests
evals/                eval case definitions (JSON)
reports/              generated reports (git-ignored)
```

## Milestones

| Phase | Scope | Status |
|---|---|---|
| 1 | Environment, structure, docs, setup test | Done |
| 2 | Mock chatbot with configurable guardrails and a deliberate failure mode | Planned |
| 3 | Evaluator and eval case schema | Planned |
| 4 | Runner and regression detection against a baseline | Planned |
| 5 | Reports (JSON/Markdown) and quality metrics | Planned |
| 6 | CI pipeline with quality gates | Planned |

Phase 2 acceptance criteria are documented in [CLAUDE.md](CLAUDE.md).
