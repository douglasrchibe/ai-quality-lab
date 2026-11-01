# Phase 8: Quality observability and regression detection

This system answers one question reliably: **did quality get worse than the last approved state,
and can we trust that answer?** Every result is one of three outcomes:

| Outcome | Exit | Meaning |
|---|---|---|
| **PASS** | 0 | No regression beyond the documented tolerances |
| **BLOCKED** | 1 | A release-blocking regression was found |
| **ERROR** | 2 | The comparison couldn't be done reliably. Missing or corrupt history, baselines or rules, a missing evaluation record, or runs that aren't comparable. **Never treated as a pass.** |

A PASS means "no worse than the approved baseline, in the tested scenarios". It doesn't mean the AI
system is safe. The open known defects (D-6.1 to D-6.4) are part of the approved baselines and
appear in every report.

## Architecture

```
existing reports ──record──▶ records (JSONL history) ──compare──▶ PASS / BLOCKED / ERROR
 runner --json                  │                          ▲
 promptfoo results.json         │                          │
 rag.evaluate JSON              ├──trend / report──▶ Markdown quality report
 skip-audit.json                │
 ragas_advisory.json            └──baseline approve (explicit, local)──▶ reports/baselines/
```

| Module | Role |
|---|---|
| `observability/records.py` | Builds one validated record per evaluation run from the report the existing tool already writes. Nothing is re-scored. Also computes fingerprints and validates records. |
| `observability/history.py` | Append-only JSONL history. It's read strictly: a malformed, invalid or duplicate line makes the whole file unusable rather than skipped. |
| `observability/regression.py` | Comparability check, configurable regression rules, and comparison of a record or a whole run |
| `observability/baselines.py` | Explicit approval, immutable baseline files, a manifest with hashes, and the audit log |
| `observability/cli.py` | `python -m ai_quality_lab.observability record / compare / trend / report / baseline` |
| `reporting.py` | Markdown rendering (the CI job summary, artifacts and the local report) |

Standard library only. **There's no database:** a run produces about 5 records of a few KB each, and
the readers need ordered scans, not queries. JSONL can be inspected in any editor and diffed in Git.

## Data model (record schema version 1)

| Field | Content |
|---|---|
| `run_id` | Unique. Local default: `<UTC time>-<type>-<random>`, or pass `--run-id` |
| `recorded_at` | UTC, `YYYY-MM-DDTHH:MM:SSZ` (validated) |
| `evaluation_type` | `custom_engine`, `promptfoo`, `rag_retrieval`, `rag_mock`, `rag_live`, `pytest`, `ragas_advisory`. RAG types are inferred from the report's own configuration. |
| `source` | `commit`, `branch`, `origin` (local or github-actions), `ci_run_id`, `report_file`, `report_sha256` (hash of the report's parsed content), `note`, and (locally) `uncommitted_changes`. Use `--commit` / `--branch` when recording an older report; `uncommitted_changes` is then unknown (`null`). |
| `dataset` | `path`, `fingerprint` (a hash of the *parsed* JSON, so it's the same on Windows CRLF and Linux LF checkouts), `case_count`, and for RAG `corpus_fingerprint` (from `documents_fingerprint`) |
| `identity` | Everything that must match for two runs to be comparable: type, dataset and corpus fingerprints, model (`llm`), `embedder` (and the judge, for Ragas) |
| `comparability_key` | Hash of `identity`. It's re-checked on every read, so an edited identity is detected. |
| `parameters` | Settings that may legitimately differ and are reported as changes: `top_k`, `chunk_size`, `overlap`, `simulated_failure` |
| `thresholds` | The release thresholds in force (RAG retrieval gates, release policy) |
| `status` | The run's own release decision (PASS / BLOCKED / ERROR), from its report |
| `deterministic` | `false` for `rag_live` and `ragas_advisory`. These get the `live_tolerance` rules. |
| `metrics` | `name → {value, numerator, denominator, kind}`, where `kind` is `deterministic` or `advisory`. Each name has a fixed unit and kind in the metric catalog (below). |
| `unmeasured` | `name → reason`. **Missing data is listed here and is never stored as 0** (for example, abstention in a retrieval-only run, or a rate with denominator 0). |
| `cases` | `[{id, outcome, severity}]`, used to detect *new* critical failures by case |

**Validation** rejects any of the following:
- a missing field or wrong type
- an unsupported `schema_version` (an incompatible version is refused, never guessed)
- a non-UTC or impossible timestamp
- an unknown type or status
- a metric value that isn't a finite number
- a metric name that isn't in the catalog, or a `kind` that doesn't match it
- a numerator greater than its denominator
- a unit violation (see "Metric units")
- a metric that is both measured and unmeasured
- an identity/key mismatch
- duplicate case IDs
- a case count that disagrees with the case results

Building a record also fails in these cases:
- the report's case IDs don't match the dataset being fingerprinted (a stale report)
- a custom-engine report has no `simulated_failure` field (an old format)
- **the report's claimed status disagrees with its own results** (D-8.1). The release policy is
  recomputed from the metrics:
  - **custom engine:** zero cases, any critical FAIL, or any ERROR
  - **RAG:** the same, plus document hit rate below 0.9, recall below 0.8, or restricted
    retrieval

  A report claiming READY with any of these, or BLOCKED with none, is refused. A refused record
  makes CI's comparison ERROR, never PASS. Promptfoo's status is computed by the builder itself.
- a skip audit whose counts don't add up (tests ≠ passed + failed + skipped, or skipped ≠
  expected + unexpected)

Reports are read as UTF-8 with an optional byte-order mark (`utf-8-sig`), because PowerShell 5.1
writes one. Any other encoding, for example UTF-16, or malformed JSON is still refused.

### Metric units

A metric's unit comes from its **name** (the catalog in `records.py`), never from whether its value
happens to be an integer or a float:

| Unit | Metrics | Rules |
|---|---|---|
| count | `case_count`, `error_count`, `critical_/high_/medium_failures`, `safety_failures`, `restricted_retrievals`, `test_count`, `executed_tests`, `passed_tests`, `tests_failed`, `expected_skips`, `unexpected_skips` | A non-negative integer. If a numerator is given, it equals the value. 0 of 0 is valid. |
| rate | `pass_rate`, `answerable_abstention_rate`, `over_abstention_rate`, `executed_pass_rate` (and the legacy `test_pass_rate`) | Numerator and denominator are both required, with denominator > 0. The value must equal numerator/denominator (to 4 decimals) and lie in [0, 1]. An undefined rate goes in `unmeasured`. |
| score | `doc_hit_rate`, `doc_recall`, `doc_precision`, `doc_mrr`, `doc_ndcg`, `ragas_*` | A mean in [0, 1] with no numerator; the denominator is the number of scored items (> 0). `ragas_*` must be `advisory`; all others `deterministic`. |

**Compatibility:** no schema change was needed (still version 1). Every record written by Phase 8
already satisfies the catalog, including the committed baselines. A record with an unknown metric
name is now rejected.

## Metric definitions

Every metric is extracted from an existing report and keeps its denominator.

| Metric | Definition | Denominator | Source | Kind |
|---|---|---|---|---|
| `case_count` | Cases in the run | — | report results | det. |
| `pass_rate` | Passed cases / executed cases | executed cases | report results | det. |
| `error_count` | Cases with outcome ERROR | executed cases | report results | det. |
| `critical_failures`, `high_failures`, `medium_failures` | Cases of that severity with outcome FAIL (errors are counted separately, as in the existing reports) | cases of that severity | report results + dataset severity | det. |
| `safety_failures` (guardrail) | Non-PASS cases among those whose expectation is `blocked: true` | cases that must be blocked | dataset + results | det. |
| `safety_failures` (RAG) | Cases classified `prompt_injection` or `guardrail_failure`. **Not measured** in retrieval-only runs. | executed cases | `failures` per case | det. |
| `restricted_retrievals` | Cases that retrieved a restricted document | executed cases | `no_restricted_retrieval` gate | det. |
| `doc_hit_rate`, `doc_recall`, `doc_precision`, `doc_mrr`, `doc_ndcg` | Document-level retrieval metrics at `top_k` (Phase 6 definitions) | applicable questions | `retrieval_metrics.document` | det. |
| `answerable_abstention_rate` | Answerable questions (`expected_behavior: answer`) whose answer contains a "not found" signal (`failures.is_abstention`). A guardrail block doesn't count. Includes hedged answers that abstain and then answer (e.g. RAG-011, D-6.3). | answerable questions | RAG results | det. (detection rule) |
| `over_abstention_rate` | Answerable questions that abstained **and** failed their checks. This is D-6.1: 6/19 in the Phase 6 live run. | answerable questions | RAG results | det. (detection rule) |
| `test_count` | All collected tests, including skips | — | `skip-audit.json` | det. |
| `executed_tests` | Tests that actually ran: test_count − skipped | test_count | `skip-audit.json` | det. |
| `passed_tests`, `tests_failed` | Executed tests that passed, or failed or errored | executed tests | `skip-audit.json` | det. |
| `executed_pass_rate` | passed_tests / executed_tests. **Skips are excluded,** so adding an opt-in live test (an expected skip) can't lower it (D-8.2). Unmeasured when nothing ran, and the run is then ERROR. | executed tests | `skip-audit.json` | det. |
| `expected_skips`, `unexpected_skips` | Skips in `test_llm_live` / `test_rag_live`, or anywhere else | test_count | `skip-audit.json` | det. |
| `test_pass_rate` (legacy) | The Phase 8.0 definition: passed / all tests, skips included. It's only found in the provisional pytest baseline, isn't produced any more, and has no rule, so it's never compared. | tests | — | det. |
| `ragas_<metric>` | Mean Ragas score | cases scored | `ragas_advisory.json` | **advisory** |

"Det." means computed deterministically *from the report*. A `rag_live` record's values still come
from a live model, so its run is marked non-deterministic.

## Comparability: what is never combined

Two records are compared only if their `identity` matches. The **dataset**, **knowledge base**,
**model** or **embedder** differing makes the comparison **ERROR**, with the field and both values
named.

Coverage is still checked across such a change. If the case count went down, a **block** finding
says so explicitly, because a smaller dataset can make a pass rate look artificially better.

Trends show only runs with the latest run's comparability key. Other runs are counted and listed
as "not combined".

Parameters (`top_k`, chunking, `simulated_failure`) may differ. Their effect is measured and shown
as a parameter change. That's how a configuration change, or a seeded defect, appears as a
regression against the normal baseline.

## Regression rules (`ci/regression_rules.json`)

A metric regresses when it moves in its worse direction by **more than** its tolerance:
- **Deterministic runs:** `tolerance` is 0, because they're exactly reproducible. CI showed
  identical numbers on Windows and Ubuntu.
- **Live runs:** `live_tolerance` allows about one case of run-to-run noise.

| Metric | Direction | Tolerance (det. / live) | Action |
|---|---|---|---|
| `case_count`, `test_count`, `executed_tests` | higher is better | 0 | **block** |
| `executed_pass_rate` | higher | 0 | **block** |
| `pass_rate` | higher | 0 / 0.04 (1 of 25) | **block** |
| `critical_failures`, `error_count`, `safety_failures`, `restricted_retrievals`, `unexpected_skips`, `tests_failed` | lower | 0 | **block** |

**Rule change in Phase 8.1 (D-8.2):** the `test_pass_rate` rule was removed. Its denominator
included expected skips, so adding an opt-in live test gave a false BLOCKED. It's replaced by
`executed_pass_rate` and `executed_tests`. A test that stops running is still caught: by
`executed_tests` if it starts skipping, and by `test_count` if it's removed.
| `high_failures` | lower | 0 / 1 | **block** |
| `medium_failures` | lower | 0 / 1 | investigate |
| `doc_hit_rate`, `doc_recall` (existing release-gate metrics) | higher | 0 / 0.03 | **block** |
| `doc_precision`, `doc_mrr`, `doc_ndcg` | higher | 0 / 0.03 | investigate |
| `over_abstention_rate` (D-6.1) | lower | 0 / 0.06 (≈1 of 19) | **block** |
| `answerable_abstention_rate` | lower | 0 / 0.06 | investigate |
| `ragas_*` | higher | 0.05 | investigate. **Advisory metrics can never block, whatever the file says** (enforced in code and tested). |

Fixed rules in code:
- **A critical case that passed in the baseline and doesn't pass now always blocks**, even if the
  critical-failure count is unchanged (a swap).
- **Missing data is an error.** A metric measured in the baseline but missing now is ERROR, and the
  reason is shown.
- **The worst outcome wins:** ERROR beats BLOCKED, and BLOCKED beats PASS.

Every finding states:
- the metric
- the baseline and current values, with both denominators
- the tolerance and the change
- the reason

**Changing a rule:** a change needs a stated reason in this document. Never loosen a tolerance to
make a run pass.

## Baselines (`reports/baselines/`, committed)

```
manifest.json            current baseline per type: run_id, file, sha256, commit, approver, reason
audit.jsonl              one line per approval (append-only, never rewritten)
<type>/<run_id>.json     the approved record (immutable; never overwritten)
```

When a baseline is loaded, the system verifies:
- its hash (canonical JSON, so a CRLF checkout still verifies)
- its schema
- that it matches its manifest entry

Any failure is ERROR.

**Approval is the only way a baseline changes, and CI never runs it.** A test fails if `ci.yml`
contains `baseline approve`.

The approval command rejects a run that:
- isn't PASS
- ran with a seeded failure
- has unexpected skips
- **has content that fails the release policy, whatever its status says** (D-8.1):
  - critical cases that don't pass, errored cases, or a critical or error count above 0
  - RAG retrieval gates or restricted retrieval
  - pytest failures, or zero executed tests

  This is checked independently of the record builder.
- **can't be reproduced from Git**, because its commit is unknown or it was recorded with
  uncommitted changes. Pass `--allow-uncommitted` to override; it's logged as
  `uncommitted_source`.
- is already the baseline
- isn't comparable with the current baseline, unless `--accept-identity-change` is given. The
  rejection message lists the old and new identity values and every metric that differs, so the
  approver sees what changed. The accepted change is stored as `identity_change` in the audit log.
- regresses against the current baseline, unless `--accept-regression` is given

Every flag is written to the audit log together with the approver and the reason. So a
regression can't vanish by quietly becoming the new baseline, and every earlier baseline file stays
on disk.

`python -m ai_quality_lab.observability baseline verify` checks that:
- every baseline's hash and schema are valid
- each manifest entry matches the latest approval for its type in the audit log

It exits 0 if everything is consistent, otherwise 2.

**The initial baselines** (`custom_engine`, `promptfoo`, `rag_retrieval`, `rag_mock`, `pytest`)
come from the Phase 8 working tree. That's Phase 7's commit `2d28111` plus the uncommitted Phase 8
changes (`uncommitted_changes: true`). Their deterministic results are identical to Phase 7 CI.
They include the open known defects: a baseline is "last accepted", not "defect-free".

**Provisional status:**
- They were approved before Phase 8.1 added the provenance check. They're recorded with
  `uncommitted_changes: true`, so the current approval command would refuse them without
  `--allow-uncommitted`. They stay as they are, and must be re-approved from a committed state.
- **The pytest baseline predates D-8.2.** It has the legacy `test_pass_rate` (689/701) and no
  `executed_tests` or `executed_pass_rate`. Until it's refreshed, only `test_count`, `tests_failed`
  and `unexpected_skips` are compared for pytest; the new metrics are reported as newly measured.
  Refreshing it means recording a clean run on the committed Phase 8.1 state and approving it
  (below). It will compare as an improvement, so no override flag should be needed.

### How to approve a new baseline safely

1. **Be sure the change is intended**, for example a dataset that grew or a deliberately removed
   obsolete test. Run `compare` first and read every finding.
2. **Record a clean run on a committed state.** Commit first, so `uncommitted_changes` is false;
   approval refuses anything else unless overridden.
   ```powershell
   python -m pytest -q --junitxml=ci-artifacts/python-tests/junit.xml
   python -m ai_quality_lab.ci_summary skips --junit ci-artifacts/python-tests/junit.xml --out ci-artifacts/python-tests
   python -m ai_quality_lab.observability record --kind pytest --report ci-artifacts/python-tests/skip-audit.json
   python -m ai_quality_lab.observability compare --types pytest --latest     # read every finding
   ```
3. **Approve it with your name and a reason:**
   ```powershell
   python -m ai_quality_lab.observability baseline approve --run-id <id> --approved-by "<you>" --reason "<why>"
   ```
   Add `--accept-identity-change` or `--accept-regression` only when that is the intent. Say why in
   `--reason`.
4. **Commit `reports/baselines/` in a pull request** so the change is reviewed. The required CI check
   then compares against it.

## Local commands (Windows PowerShell, `.venv` active)

```powershell
$env:AIQL_EMBED_PROVIDER = "hashing"
python -m ai_quality_lab.runner --output reports/quality/engine.json
python -m ai_quality_lab.observability record --kind custom_engine --report reports/quality/engine.json
python -m ai_quality_lab.rag.evaluate --mode rag --llm mock-gullible --embedder hashing --output reports/quality/rag-mock.json
python -m ai_quality_lab.observability record --kind rag --report reports/quality/rag-mock.json
python -m ai_quality_lab.observability compare --types custom_engine,rag_mock --latest   # exit 0 / 1 / 2
python -m ai_quality_lab.observability compare --types custom_engine --run-id <run id>   # one specific run
python -m ai_quality_lab.observability trend --type rag_mock
python -m ai_quality_lab.observability report        # writes reports/quality/quality-report.md
python -m ai_quality_lab.observability baseline show
python -m ai_quality_lab.observability baseline verify
```

**Choosing runs from the local history** (D-8.4). The local history accumulates runs, so `compare`
needs to be told which run to use. Without a choice, two runs of one type are an ERROR, never a
guess.
- **`--latest`:** the newest run of each type. If two runs share the newest timestamp, that's an
  ERROR; use `--run-id` instead.
- **`--run-id`:** exactly the named runs. Repeat the option, or comma-separate the IDs, with one
  per type.

The chosen run is printed under "Runs selected", and comparability with the baseline is still
checked. CI doesn't use either option: each CI run writes a fresh history with one record per
type.

`record` warns, without refusing, when a report with identical content has already been recorded
for that type. That catches the same file being recorded twice. A repeated deterministic run can
legitimately produce an identical report, for example the pytest skip audit, which has no timings.

Live RAG (local only; it needs Ollama). Record it right after the run, so the commit is right:

```powershell
python -m ai_quality_lab.rag.evaluate --mode rag --llm ollama --embedder ollama --output reports/rag/rag-live.json
python -m ai_quality_lab.observability record --kind rag --report reports/rag/rag-live.json
python -m ai_quality_lab.observability trend --type rag_live   # over_abstention_rate tracks D-6.1
```

On Windows, write JSON reports with each tool's `--output` option: `runner --output` (added in
Phase 8.1) and `rag.evaluate --output`. That writes UTF-8 directly.

Avoid `>` and `| Out-File`. PowerShell decodes Python's output with the console code page before
re-encoding it, so non-ASCII text in the report (the runner's JSON contains some) can be garbled.
`record` does accept the UTF-8 byte-order mark that PowerShell 5.1 adds. It still refuses UTF-16,
which is `>`'s default in Windows PowerShell 5.1 without a configured `$PSDefaultParameterValues`.

The local history lives in `reports/history/quality_history.jsonl`. It's git-ignored generated
output; delete it to start over, but baselines are separate and committed.

## CI integration

The `observability` job in `ci.yml` runs after the five quality jobs (`if: always()`):
1. **Records** each report from this run's artifacts into a fresh history. Each record is attempted
   independently; any failure fails the step.
2. **`regression-vs-baseline`** compares `custom_engine`, `promptfoo`, `rag_retrieval`, `rag_mock`
   and `pytest` with the committed baselines. It's expected to PASS and is cross-checked against
   `regression.json` with `ci_check --report`.
3. **Three regression demonstrations must stay BLOCKED.** They take the fail-open, defences-disabled
   and degraded-retrieval reports from the same run, each compared alone, so one demo can't hide
   another's loss of detection.
4. **Writes** `quality-report.md` and appends the regression table to the job summary.

The final gate needs this job, so a regression (BLOCKED), or a missing record, baseline or history
(ERROR), fails the required `Quality gate + summary` check. The gate's summary has a "Regression vs
baseline" line.

### Artifact retention and durable history

CI history exists only in that run's `ci-observability` artifact, kept for 14 days. **Durable
comparison relies on the committed baselines, not on CI history.** That's deliberate:
- the baseline that gates a release is reviewed in Git
- it can't expire

Long-term trend history is local (`reports/history/`).

**Proposed persistent CI history (not implemented):** a separate workflow on `push` to `main`,
with `contents: write` limited to a dedicated `quality-history` branch, that appends that run's
records. It needs:
- its own permission review
- protection so that pull requests can't write to that branch
- the same strict validation on append

## How to investigate a regression

1. Open the run's job summary. The regression table names the evaluation type, the metric, both
   values with denominators, the tolerance and the reason.
2. Check the "parameter changes" line. `simulated_failure`, `top_k` or chunking changes explain many
   findings.
3. Download `ci-observability` (records, `regression.json`, `quality-report.md`) and the job's own
   artifact (for example `ci-rag-deterministic`) for per-case evidence.
4. **For a new critical failure**, the finding names the case. Reproduce it locally with the
   commands above.
5. **ERROR is not a quality verdict.** Fix the missing or corrupt input first. Never approve a
   baseline to make an ERROR go away.

## Limitations

- **Stand-ins.** CI uses deterministic stand-ins. Regressions in live-model behaviour (D-6.1) appear
  only in locally recorded `rag_live` runs.
- **Abstention detection** uses the fixed "not found" phrase list. A paraphrased abstention would
  be missed, and a hedged answer counts as an abstention. That's why there are two separate
  metrics.
- **Live-run noise.** Tolerances are a judgement of about one case. Two live runs can differ by more
  than that without a code change; investigate before approving.
- **One record per type per comparison.** Without `--run-id` or `--latest`, two records of the
  same type are ERROR, not "pick one".
- **Timestamps have one-second resolution.** Two runs recorded in the same second can't be ordered
  by `--latest`; select them with `--run-id`.
- **The first `rag_live` baseline** isn't created in Phase 8. The Phase 6 live report can be
  recorded with `--commit unknown` and approved deliberately.
- **No HTML dashboard.** Markdown tables work in the GitHub job summary, in VS Code and in
  artifacts. Charts would add rendering code without adding information at this data volume.
