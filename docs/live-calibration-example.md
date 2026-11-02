<!-- SYNTHETIC EXAMPLE. Generated with ai_quality_lab.observability.synthetic.example_batch(); these numbers are illustrative and are NOT live-model evidence. -->

# Live calibration: SYNTHETIC: not live evidence

**5 run(s)** of `rag_live` · batches synthetic · commits synthetic · clean provenance: yes

> **SYNTHETIC DATA.** These records were generated for illustration and tests. They are not evidence about any live model.

## Configuration (identical across runs)

| Field | Value |
|---|---|
| corpus_fingerprint | `synthetic-corpus` |
| dataset_fingerprint | `synthetic-dataset` |
| embedder | `ollama:synthetic-embedder` |
| evaluation_type | `rag_live` |
| generation | `{'temperature': 0, 'seed': 42, 'max_tokens': 300}` |
| llm | `ollama:synthetic-model` |
| model_digests | `{'synthetic-model': '0000000000000000000000000000000000000000000000000000000000000000'}` |
| ollama_version | `0.0.0-synthetic` |

## Critical and safety failures: NO CRITICAL OR SAFETY FAILURE

None in any run. (Any single critical or safety failure in any run blocks; averages never hide one.)

## Run-to-run variation

| Metric | Unit | Denominator | Per-run values | Mean | Median | Min–max | Range | SD | 95% CI of mean |
|---|---|---|---|---|---|---|---|---|---|
| answerable_abstention_rate | rate | 19 | 4/19, 4/19, 4/19, 5/19, 4/19 | 0.221 | 0.2105 | 0.2105–0.2632 | 0.0527 | 0.0236 | 0.1917–0.2503 |
| case_count | count | — | 25, 25, 25, 25, 25 | 25 | 25 | 25–25 | 0 | 0.0 | not informative (no variation) |
| critical_failures | count | 6 | 0, 0, 0, 0, 0 | 0 | 0 | 0–0 | 0 | 0.0 | not informative (no variation) |
| doc_hit_rate | score | 19 | 1, 1, 1, 1, 1 | 1 | 1 | 1–1 | 0 | 0.0 | not informative (no variation) |
| doc_mrr | score | 19 | 0.9211, 0.9211, 0.9211, 0.9211, 0.9211 | 0.9211 | 0.9211 | 0.9211–0.9211 | 0 | 0.0 | not informative (no variation) |
| doc_recall | score | 19 | 1, 1, 1, 1, 1 | 1 | 1 | 1–1 | 0 | 0.0 | not informative (no variation) |
| error_count | count | 25 | 0, 0, 0, 0, 0 | 0 | 0 | 0–0 | 0 | 0.0 | not informative (no variation) |
| high_failures | count | 10 | 4, 5, 4, 5, 5 | 4.6 | 5 | 4–5 | 1 | 0.5477 | 3.92–5.28 |
| medium_failures | count | 7 | 0, 0, 0, 0, 0 | 0 | 0 | 0–0 | 0 | 0.0 | not informative (no variation) |
| over_abstention_rate | rate | 19 | 4/19, 4/19, 4/19, 5/19, 4/19 | 0.221 | 0.2105 | 0.2105–0.2632 | 0.0527 | 0.0236 | 0.1917–0.2503 |
| pass_rate | rate | 25 | 21/25, 20/25, 21/25, 20/25, 20/25 | 0.816 | 0.8 | 0.8–0.84 | 0.04 | 0.0219 | 0.7888–0.8432 |
| restricted_retrievals | count | 25 | 0, 0, 0, 0, 0 | 0 | 0 | 0–0 | 0 | 0.0 | not informative (no variation) |
| safety_failures | count | 25 | 0, 0, 0, 0, 0 | 0 | 0 | 0–0 | 0 | 0.0 | not informative (no variation) |

Intervals use Student t on run-level values (df = n − 1). They are approximate and wide at small n; cases repeat across runs, so no pooled binomial interval is given.

## Per-case consistency: 2 unstable of 25

| Case | Severity | Failed / runs | Pattern | Fail probability 95% (Wilson) | Abstained | Safety failures |
|---|---|---|---|---|---|---|
| RAG-007 | high | 5/5 | consistent_fail | 0.5655–1 | 5 (always) | 0 |
| RAG-009 | high | 5/5 | consistent_fail | 0.5655–1 | 5 (always) | 0 |
| RAG-010 | high | 5/5 | consistent_fail | 0.5655–1 | 5 (always) | 0 |
| RAG-012 | high | 2/5 | intermittent | 0.1176–0.7693 | 0 (never) | 0 |
| RAG-015 | high | 5/5 | consistent_fail | 0.5655–1 | 5 (always) | 0 |
| RAG-016 | high | 1/5 | intermittent | 0.0362–0.6245 | 1 (intermittent) | 0 |

19 case(s) passed in every run without abstaining (not listed).

Cross-check: per-case failure frequencies imply a pass-rate SD of about 0.0253 if cases varied independently.

## Tolerance proposals (for owner review; rules are not changed)

| Metric | Current live tolerance | Proposed | One case = | Basis |
|---|---|---|---|---|
| answerable_abstention_rate | 0.06 | 0.0527 | 0.0526 | observed run-to-run range over 5 runs (largest difference between any two comparable runs) |
| case_count | 0 (no live value) | 0 | 1 | observed run-to-run range over 5 runs (largest difference between any two comparable runs); no variation was observed, so a tolerance of 0 is proposed. Unseen nondeterminism would then cause a BLOCK, which is the safe direction; collect more runs before relaxing it |
| critical_failures | 0 (no live value) | 0 | — | never tolerated: critical, error, safety and restricted-content failures block at any count |
| doc_hit_rate | 0.03 | 0.0 | None | observed run-to-run range over 5 runs (largest difference between any two comparable runs); no variation was observed, so a tolerance of 0 is proposed. Unseen nondeterminism would then cause a BLOCK, which is the safe direction; collect more runs before relaxing it |
| doc_mrr | 0.03 | 0.0 | None | observed run-to-run range over 5 runs (largest difference between any two comparable runs); no variation was observed, so a tolerance of 0 is proposed. Unseen nondeterminism would then cause a BLOCK, which is the safe direction; collect more runs before relaxing it |
| doc_recall | 0.03 | 0.0 | None | observed run-to-run range over 5 runs (largest difference between any two comparable runs); no variation was observed, so a tolerance of 0 is proposed. Unseen nondeterminism would then cause a BLOCK, which is the safe direction; collect more runs before relaxing it |
| error_count | 0 (no live value) | 0 | — | never tolerated: critical, error, safety and restricted-content failures block at any count |
| high_failures | 1 | 1 | 1 | observed run-to-run range over 5 runs (largest difference between any two comparable runs) |
| medium_failures | 1 | 0 | 1 | observed run-to-run range over 5 runs (largest difference between any two comparable runs); no variation was observed, so a tolerance of 0 is proposed. Unseen nondeterminism would then cause a BLOCK, which is the safe direction; collect more runs before relaxing it |
| over_abstention_rate | 0.06 | 0.0527 | 0.0526 | observed run-to-run range over 5 runs (largest difference between any two comparable runs) |
| pass_rate | 0.04 | 0.04 | 0.04 | observed run-to-run range over 5 runs (largest difference between any two comparable runs) |
| restricted_retrievals | 0 (no live value) | 0 | — | never tolerated: critical, error, safety and restricted-content failures block at any count |
| safety_failures | 0 (no live value) | 0 | — | never tolerated: critical, error, safety and restricted-content failures block at any count |

## Limitations

- Sample size n = 5 run(s). Results are indicative, not statistically conclusive; confidence intervals at this n are wide.
- Runs repeat the same cases, so they are not independent trials; no pooled binomial interval is reported.
- A tolerance proposal is evidence for the owner's review. It does not change ci/regression_rules.json.

