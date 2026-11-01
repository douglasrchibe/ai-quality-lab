# Demo guide: AI Quality Lab in 10 minutes

The demo shows the whole evaluation lifecycle offline. Every step is deterministic and needs no
model, API key or internet connection:
- tests
- two independent evaluation engines that agree
- RAG retrieval and security checks
- seeded failures that must be caught
- regression detection against approved baselines

Every command below was run on Windows 11 / PowerShell on 2026-11-01, with the outcome shown.
The same checks run in [GitHub Actions](https://github.com/douglasrchibe/ai-quality-lab/actions)
on every pull request.

**Setup (once):**
1. Follow the README's *Setup*.
2. Open a PowerShell terminal in the project folder.
3. Run `.\.venv\Scripts\Activate.ps1`.
4. Run `$env:AIQL_EMBED_PROVIDER = "hashing"`.

Run each command on its own.

**Exit codes are part of the demo:**
- 0 = PASS / READY
- 1 = BLOCKED: a quality problem was found, which is the expected result for the seeded failures
- 2 = ERROR: the check couldn't run
- 100 = Promptfoo's code for failed tests

## 1. The test suite

```powershell
python -m pytest -q
```

**You'll see:** `756 passed, 12 skipped`. The 12 skips are opt-in live-model tests (they need
Ollama).

**What it proves:** every component has offline, deterministic tests, including the CI scripts,
the workflow file itself, and the regression detector.

## 2. The evaluation engine and a seeded guardrail failure

```powershell
python -m ai_quality_lab.runner
```

```powershell
python -m ai_quality_lab.runner --simulate-failure guardrail_fail_open
```

**You'll see:**
- **First command:** 33/33 cases pass, `Release readiness: READY`, exit 0.
- **Second command:** the guardrail silently "fails open". 16 cases fail, 9 of them critical,
  `BLOCKED`, exit 1.

**What it proves:**
- **Expected behaviour lives in data.** It's defined in `evals/guardrail_cases.json`, with a
  severity per case.
- **The release decision follows a written policy:** any critical failure blocks.
- **A deliberately broken guardrail is detected,** so the tests can catch the regression they
  exist for.

## 3. A second, independent engine (Promptfoo) that must agree

```powershell
npm run eval:promptfoo
```

```powershell
python -m ai_quality_lab.engine_comparison reports/promptfoo/results.json
```

```powershell
npm run eval:promptfoo:failure-demo
```

**You'll see:**
- **Promptfoo:** 33 passed.
- **The comparison:** `Agreements: 33  Disagreements: 0`.
- **The failure demo:** exits **100**. It's expected to fail.

**What it proves:**
- **The engines agree:** an industry tool and the custom engine reach the same verdict on every
  case, from one shared dataset.
- **The comparison is a check in itself:** an engine disagreement would point to a bug in one of
  them.

## 4. RAG: retrieval quality and prompt-injection defences

```powershell
python -m ai_quality_lab.rag.evaluate --mode retrieval --embedder hashing
```

```powershell
python -m ai_quality_lab.rag.evaluate --mode rag --llm mock-gullible --embedder hashing
```

```powershell
python -m ai_quality_lab.rag.evaluate --mode rag --llm mock-gullible --embedder hashing --simulate-failure defenses_disabled
```

**You'll see:**
- **Retrieval:** 24/25, document hit rate 0.9474, recall 0.9211, READY.
- **End-to-end with a deliberately gullible mock model:** 17/25, 0 critical failures, READY.
- **With the defences switched off:** BLOCKED (exit 1). The injected instructions in the
  adversarial documents get through.

**What it proves:**
- **Retrieval is measured against ground truth,** not against what the system retrieved.
- **The security controls work against a worst-case model.**
- **The test detects their removal.**

The 8 non-passing mock cases are expected generation limits of the mock, and are in the baseline.

## 5. Regression detection against approved baselines

This step records a healthy run and a seeded-defect run, then compares each one with the
committed, owner-approved baseline.

```powershell
Remove-Item reports\quality\demo-history.jsonl -ErrorAction SilentlyContinue
```

```powershell
python -m ai_quality_lab.runner --output reports/quality/demo-normal.json
```

```powershell
python -m ai_quality_lab.runner --simulate-failure guardrail_fail_open --output reports/quality/demo-fail-open.json
```

```powershell
python -m ai_quality_lab.observability record --kind custom_engine --report reports/quality/demo-normal.json --history reports/quality/demo-history.jsonl --run-id demo-normal
```

```powershell
python -m ai_quality_lab.observability record --kind custom_engine --report reports/quality/demo-fail-open.json --history reports/quality/demo-history.jsonl --run-id demo-fail-open
```

```powershell
python -m ai_quality_lab.observability compare --types custom_engine --history reports/quality/demo-history.jsonl --run-id demo-normal
```

```powershell
python -m ai_quality_lab.observability compare --types custom_engine --history reports/quality/demo-history.jsonl --run-id demo-fail-open
```

**You'll see:**
- **The second runner command** exits 1. That's expected: it's the seeded defect.
- **The healthy run:** `Regression vs approved baseline: ✅ PASS`, exit 0.
- **The seeded run:** `❌ BLOCKED`, exit 1. Its findings table shows:
  - the parameter change (`simulated_failure: none → guardrail_fail_open`)
  - `critical_failures 0 → 9` against 9 critical cases
  - each new critical failure by case ID (for example GR-001)

**What it proves:**
- **Every finding is precise:** it states the metric, both values with their denominators, the
  tolerance and the reason.
- **Comparisons are only made between comparable runs:** the same dataset, knowledge base, model
  and embedder. A missing or corrupt baseline is ERROR, never PASS.

## 6. Baseline governance

```powershell
python -m ai_quality_lab.observability baseline show
```

```powershell
python -m ai_quality_lab.observability baseline verify
```

**You'll see:**
- **`show`:** five baselines, each with its commit (`865c879`) and the owner's approval.
- **`verify`:** `5 baseline(s) checked: OK`.

**What it proves:** baselines change only through an explicit, named approval, never in CI.
- Approval refuses runs that failed, ran with a seeded defect, regressed, or came from uncommitted
  code, unless an override is given and logged.
- Every earlier baseline and every approval stays in `reports/baselines/` (audit log:
  `audit.jsonl`).

## 7. The quality report

```powershell
python -m ai_quality_lab.observability report --history reports/quality/demo-history.jsonl --output reports/quality/demo-report.md
```

Open `reports/quality/demo-report.md` in VS Code (Ctrl+Shift+V previews it). It shows:
- latest results against the baselines
- trends over comparable runs
- every metric with its denominator
- the known defects

Here the heading says **BLOCKED**, because the latest run in the demo history is the seeded
defect.

## 8. The CI pipeline (on GitHub)

Open the latest
[AI Quality Lab CI run](https://github.com/douglasrchibe/ai-quality-lab/actions/workflows/ci.yml).
- **7 jobs** run the checks above on Ubuntu.
- **The run summary** shows every check's expected and actual outcome, the regression table, and
  the open known defects.
- **The `Quality gate + summary` check is required** to merge into `main`.

## What to say about the limits

- **CI uses deterministic stand-ins:** a mock chatbot, hashing embeddings, mock RAG models. A green
  build shows the tested scenarios behave as specified. It doesn't show the AI is safe in
  general.
- **The real-model results are local and documented, not gated in CI.** The live RAG model still
  over-abstains on 6 of 19 answerable questions (D-6.1). LLM-judge scores are advisory, because the
  local judge proved unreliable (D-6.2).
- **The open defects (D-6.1 to D-6.4) are listed in every CI summary and report,** never hidden.
