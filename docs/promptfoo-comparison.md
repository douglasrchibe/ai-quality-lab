# Promptfoo vs. the custom evaluation engine

Both engines evaluate the same `MockChatbot` from the same dataset (`evals/guardrail_cases.json`,
33 cases). Promptfoo's tests are generated from that file at run time by `promptfoo/tests.py`. This
document compares the two approaches and records what running them side by side showed.

## Side-by-side

| Aspect | Custom engine (Phases 2–3) | Promptfoo 0.125.1 (Phase 4) |
|---|---|---|
| **Test definition** | JSON dataset with a strict, versioned schema. The whole file is validated before anything runs, and all problems are reported at once. | YAML config (`prompts`, `providers`, `tests`). Tests can be inline, in CSV/JSON/YAML files, or produced by a generator. Here, a Python generator converts the shared dataset. |
| **System under test** | `agent_factory(configuration) -> Agent`, called in-process. A fresh agent per case. | A *provider*. Here, a Python provider (`call_api`) run in a persistent Python worker process started by Node.js. |
| **Assertions** | Five deterministic Python checks (blocked, detected_topics, contains, not_contains, nonempty), each recorded with expected value, actual value and an explanation. | Built-in types (`is-json`, `icontains`, `not-icontains`, …) plus custom `javascript`/`python` assertions returning `{pass, score, reason}`. Assertions can have `metric` names, `weight`s, a `transform` and a test-level `threshold`. |
| **Outcomes** | PASS / FAIL / ERROR, with the error *stage* (setup, execution or evaluation). | passed / failed / error (`failureReason` 0/1/2). |
| **Severity and release decision** | Severity is first-class. Release readiness is BLOCKED on any critical FAIL, any ERROR, or zero cases. Exit codes: 0 READY, 1 BLOCKED, 2 invalid dataset. | No severity concept; carried here as metadata only. Exit code **100** on *any* failure or error, otherwise 0. `PROMPTFOO_PASS_RATE_THRESHOLD` allows a pass-rate gate that treats all cases equally. |
| **Reporting** | Console summary (counts, pass rate, failures by severity, by category, critical IDs, readiness reasons) or `--json`. | Terminal table, `-o` to JSON/CSV/HTML/JUnit XML/YAML, and a local web viewer (`promptfoo view`) with eval history and named-metric aggregates. |
| **Diagnosing failures** | Failed assertions are printed under each case with expected vs. actual and a reason. The JSON keeps the full evidence. | A per-assertion `reason` in the table, JSON and web viewer. Named metrics show *which kind* of check failed across the suite. The web viewer is good for exploring; the JSON is verbose. |
| **CI/CD integration (not implemented yet)** | Run `python -m ai_quality_lab.runner` and gate on exit code 1. Only Python is needed. | Run `npx promptfoo eval` and gate on exit code 100. Needs Node.js and, here, the Python venv. JUnit XML output plugs into most CI test reporters. |
| **Dependencies** | Python standard library + pytest. | Node.js ≥22.22 and the Promptfoo npm package (613 packages installed; `npm audit` reported 5 high-severity transitive advisories at install time). |

## Results of running both

| Run | Custom engine | Promptfoo | Case-by-case agreement |
|---|---|---|---|
| Normal (`npm run eval:promptfoo`) | 33 PASS → READY | 33 passed → exit 0 | 33/33 |
| Fail-open demo (`npm run eval:promptfoo:failure-demo`) | 17 PASS, 16 FAIL (9 critical) → BLOCKED | 17 passed, 16 failed → exit 100 | 33/33 |

Reproduce with `python -m ai_quality_lab.engine_comparison reports/promptfoo/<file>.json`.

## Why matching outcomes do not prove equivalence

The two engines agree on all 33 cases in both runs, but they are not identical checks:

1. **Promptfoo checks more per case.** Every Promptfoo test also runs `is-json` (structure) and a
   `guardrail_consistency` invariant: blocked must be true exactly when a detected topic is enabled
   in the configuration. In the fail-open run, the invariant failed alongside `blocked_status` on
   each blocking case. It would also catch a fail-open on a case that never asserts `blocked`.
   The custom engine has no equivalent invariant.
2. **The structure check happens in different places.** The custom engine validates the reply
   shape in the runner, and a bad shape is an ERROR. Promptfoo's `is-json` makes it an assertion
   failure (FAIL).
3. **Phrase normalization differs.** The custom engine case-folds *and* collapses whitespace.
   Promptfoo's `icontains` only lowercases. A response with a double space or a line break inside a
   phrase would pass in one engine and fail in the other. That doesn't happen with today's data.
4. **Severity changes the decision, not the outcomes.** Both engines fail the same 16 cases, but
   only the custom engine turns that into "9 critical, BLOCKED". With a pass-rate threshold of,
   say, 50%, Promptfoo would have passed this build (17/33 = 51.5%).
5. **Errors are handled differently.** A provider error is `failureReason 2` in Promptfoo but
   shares exit code 100 with failures. The custom engine reports errors separately and by stage.
6. **Coverage is limited to what the dataset expresses.** Both engines inherit the same dataset,
   so they also inherit its blind spots (for example, the known detector gaps characterized by
   GR-022 and GR-031).

## Strengths of each approach

**Custom engine**
- It encodes *this* team's quality policy directly: severity, release readiness, error stages,
  and the meaning of an empty run.
- Its strict dataset validation rejects contradictions and duplicate keys before anything runs.
- It has no runtime dependencies, is fast and easy to unit-test, and has a stable,
  project-defined result format for reporting.

**Promptfoo**
- It provides a reporting and exploration UI, eval history, many output formats (including JUnit),
  and filtering by metadata.
- It has a large assertion library. When a real LLM replaces the mock, it adds model-graded,
  similarity and latency/cost assertions and red-teaming features without new code.
- Its config format is widely used, so test definitions can be shared and reviewed by people who
  don't read Python.
- It can compare several providers or prompts in one run. That's useful when evaluating model or
  prompt changes against the same tests.

## Limitations of deterministic mock-chatbot evaluations

- **The mock's behaviour is exact and repeatable.** A real LLM's isn't. Real evaluations need
  repeated runs, tolerance thresholds and statistical comparison rather than exact
  string checks.
- **Keyword phrase checks verify wording, not meaning.** A real model can leak a recipe without any
  of the prohibited phrases. Real evaluations need semantic checks, such as classifiers or
  model-graded assertions, and those bring their own error rates.
- **The guardrail's `detected_topics` is self-reported.** Both engines (and Promptfoo's invariant)
  trust it. An independent topic oracle would be needed to catch a detector that misses a topic.
- **Passing every case shows agreement with the expected behaviour as written.** Characterization
  cases (tag `known-limitation`) deliberately assert current behaviour, including known gaps.

## Recommendation for this project

Keep both:
- **The custom engine** stays the *policy* layer: severity and the release decision.
- **Promptfoo** is the *exploration and breadth* layer: the UI, many output formats and, later,
  LLM-specific assertions.
- **The shared dataset** keeps them from drifting.
- **`engine_comparison`** shows whenever they disagree.
