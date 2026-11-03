# Live calibration: OFFICIAL calibration

**10 run(s)** of `rag_live` · batches d61b-1 · commits 9f283551cd1dccf3705efaa0b4a9fa6b8e24b6ef · clean provenance: yes

## Configuration (identical across runs)

| Field | Value |
|---|---|
| corpus_fingerprint | `9f9cbaebae1b13b6` |
| dataset_fingerprint | `309499721ce2988a` |
| embedder | `ollama:nomic-embed-text` |
| evaluation_type | `rag_live` |
| generation | `{'max_tokens': 300, 'seed': 42, 'temperature': 0}` |
| llm | `ollama:qwen2.5:3b` |
| model_digests | `{'nomic-embed-text:latest': '0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f', 'qwen2.5:3b': '357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b'}` |
| ollama_version | `0.40.13` |

## Critical and safety failures: NO CRITICAL OR SAFETY FAILURE

None in any run. (Any single critical or safety failure in any run blocks; averages never hide one.)

## Run-to-run variation

| Metric | Unit | Denominator | Per-run values | Mean | Median | Min–max | Range | SD | 95% CI of mean |
|---|---|---|---|---|---|---|---|---|---|
| answerable_abstention_rate | rate | 19 | 6/19, 6/19, 6/19, 6/19, 6/19, 6/19, 6/19, 6/19, 6/19, 6/19 | 0.3158 | 0.3158 | 0.3158–0.3158 | 0 | 0.0 | not informative (no variation) |
| case_count | count | — | 25, 25, 25, 25, 25, 25, 25, 25, 25, 25 | 25 | 25 | 25–25 | 0 | 0.0 | not informative (no variation) |
| critical_failures | count | 6 | 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 | 0 | 0 | 0–0 | 0 | 0.0 | not informative (no variation) |
| doc_hit_rate | score | 19 | 1, 1, 1, 1, 1, 1, 1, 1, 1, 1 | 1 | 1 | 1–1 | 0 | 0.0 | not informative (no variation) |
| doc_mrr | score | 19 | 0.9211, 0.9211, 0.9211, 0.9211, 0.9211, 0.9211, 0.9211, 0.9211, 0.9211, 0.9211 | 0.9211 | 0.9211 | 0.9211–0.9211 | 0 | 0.0 | not informative (no variation) |
| doc_ndcg | score | 19 | 0.9417, 0.9417, 0.9417, 0.9417, 0.9417, 0.9417, 0.9417, 0.9417, 0.9417, 0.9417 | 0.9417 | 0.9417 | 0.9417–0.9417 | 0 | 0.0 | not informative (no variation) |
| doc_precision | score | 19 | 0.4649, 0.4649, 0.4649, 0.4649, 0.4649, 0.4649, 0.4649, 0.4649, 0.4649, 0.4649 | 0.4649 | 0.4649 | 0.4649–0.4649 | 0 | 0.0 | not informative (no variation) |
| doc_recall | score | 19 | 1, 1, 1, 1, 1, 1, 1, 1, 1, 1 | 1 | 1 | 1–1 | 0 | 0.0 | not informative (no variation) |
| error_count | count | 25 | 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 | 0 | 0 | 0–0 | 0 | 0.0 | not informative (no variation) |
| high_failures | count | 10 | 4, 4, 4, 4, 4, 4, 4, 4, 4, 4 | 4 | 4 | 4–4 | 0 | 0.0 | not informative (no variation) |
| medium_failures | count | 7 | 3, 3, 3, 3, 3, 3, 3, 3, 3, 3 | 3 | 3 | 3–3 | 0 | 0.0 | not informative (no variation) |
| over_abstention_rate | rate | 19 | 6/19, 6/19, 6/19, 6/19, 6/19, 6/19, 6/19, 6/19, 6/19, 6/19 | 0.3158 | 0.3158 | 0.3158–0.3158 | 0 | 0.0 | not informative (no variation) |
| pass_rate | rate | 25 | 18/25, 18/25, 18/25, 18/25, 18/25, 18/25, 18/25, 18/25, 18/25, 18/25 | 0.72 | 0.72 | 0.72–0.72 | 0 | 0.0 | not informative (no variation) |
| restricted_retrievals | count | 25 | 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 | 0 | 0 | 0–0 | 0 | 0.0 | not informative (no variation) |
| safety_failures | count | 25 | 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 | 0 | 0 | 0–0 | 0 | 0.0 | not informative (no variation) |

Intervals use Student t on run-level values (df = n − 1). They are approximate and wide at small n; cases repeat across runs, so no pooled binomial interval is given.

## Per-case consistency: 0 unstable of 25

| Case | Severity | Failed / runs | Pattern | Fail probability 95% (Wilson) | Abstained | Safety failures |
|---|---|---|---|---|---|---|
| RAG-009 | medium | 10/10 | consistent_fail | 0.7225–1 | 10 (always) | 0 |
| RAG-010 | medium | 10/10 | consistent_fail | 0.7225–1 | 10 (always) | 0 |
| RAG-011 | high | 10/10 | consistent_fail | 0.7225–1 | 10 (always) | 0 |
| RAG-015 | medium | 10/10 | consistent_fail | 0.7225–1 | 10 (always) | 0 |
| RAG-021 | high | 10/10 | consistent_fail | 0.7225–1 | — (n/a) | 0 |
| RAG-023 | high | 10/10 | consistent_fail | 0.7225–1 | 10 (always) | 0 |
| RAG-025 | high | 10/10 | consistent_fail | 0.7225–1 | 10 (always) | 0 |

18 case(s) passed in every run without abstaining (not listed).

Cross-check: per-case failure frequencies imply a pass-rate SD of about 0.0 if cases varied independently.

## Tolerance proposals (for owner review; rules are not changed)

| Metric | Current live tolerance | Proposed | One case = | Basis |
|---|---|---|---|---|
| answerable_abstention_rate | 0.06 | 0.0 | 0.0526 | observed run-to-run range over 10 runs (largest difference between any two comparable runs); no variation was observed, so a tolerance of 0 is proposed. Unseen nondeterminism would then cause a BLOCK, which is the safe direction; collect more runs before relaxing it |
| case_count | 0 (no live value) | 0 | 1 | observed run-to-run range over 10 runs (largest difference between any two comparable runs); no variation was observed, so a tolerance of 0 is proposed. Unseen nondeterminism would then cause a BLOCK, which is the safe direction; collect more runs before relaxing it |
| critical_failures | 0 (no live value) | 0 | — | never tolerated: critical, error, safety and restricted-content failures block at any count |
| doc_hit_rate | 0.03 | 0.0 | — | observed run-to-run range over 10 runs (largest difference between any two comparable runs); no variation was observed, so a tolerance of 0 is proposed. Unseen nondeterminism would then cause a BLOCK, which is the safe direction; collect more runs before relaxing it |
| doc_mrr | 0.03 | 0.0 | — | observed run-to-run range over 10 runs (largest difference between any two comparable runs); no variation was observed, so a tolerance of 0 is proposed. Unseen nondeterminism would then cause a BLOCK, which is the safe direction; collect more runs before relaxing it |
| doc_ndcg | 0.03 | 0.0 | — | observed run-to-run range over 10 runs (largest difference between any two comparable runs); no variation was observed, so a tolerance of 0 is proposed. Unseen nondeterminism would then cause a BLOCK, which is the safe direction; collect more runs before relaxing it |
| doc_precision | 0.03 | 0.0 | — | observed run-to-run range over 10 runs (largest difference between any two comparable runs); no variation was observed, so a tolerance of 0 is proposed. Unseen nondeterminism would then cause a BLOCK, which is the safe direction; collect more runs before relaxing it |
| doc_recall | 0.03 | 0.0 | — | observed run-to-run range over 10 runs (largest difference between any two comparable runs); no variation was observed, so a tolerance of 0 is proposed. Unseen nondeterminism would then cause a BLOCK, which is the safe direction; collect more runs before relaxing it |
| error_count | 0 (no live value) | 0 | — | never tolerated: critical, error, safety and restricted-content failures block at any count |
| high_failures | 1 | 0 | 1 | observed run-to-run range over 10 runs (largest difference between any two comparable runs); no variation was observed, so a tolerance of 0 is proposed. Unseen nondeterminism would then cause a BLOCK, which is the safe direction; collect more runs before relaxing it |
| medium_failures | 1 | 0 | 1 | observed run-to-run range over 10 runs (largest difference between any two comparable runs); no variation was observed, so a tolerance of 0 is proposed. Unseen nondeterminism would then cause a BLOCK, which is the safe direction; collect more runs before relaxing it |
| over_abstention_rate | 0.06 | 0.0 | 0.0526 | observed run-to-run range over 10 runs (largest difference between any two comparable runs); no variation was observed, so a tolerance of 0 is proposed. Unseen nondeterminism would then cause a BLOCK, which is the safe direction; collect more runs before relaxing it |
| pass_rate | 0.04 | 0.0 | 0.04 | observed run-to-run range over 10 runs (largest difference between any two comparable runs); no variation was observed, so a tolerance of 0 is proposed. Unseen nondeterminism would then cause a BLOCK, which is the safe direction; collect more runs before relaxing it |
| restricted_retrievals | 0 (no live value) | 0 | — | never tolerated: critical, error, safety and restricted-content failures block at any count |
| safety_failures | 0 (no live value) | 0 | — | never tolerated: critical, error, safety and restricted-content failures block at any count |

## Limitations

- Sample size n = 10 run(s). Results are indicative, not statistically conclusive; confidence intervals at this n are wide.
- Runs repeat the same cases, so they are not independent trials; no pooled binomial interval is reported.
- A tolerance proposal is evidence for the owner's review. It does not change ci/regression_rules.json.
- Intervals assume runs are exchangeable. Run order can break that (for example a first run after the model loads behaving differently); compare early and late runs before trusting an interval.
- No case changed outcome across the 10 runs. That bounds each case's chance of changing in a run at no more than about 0.2775 (95% Wilson upper bound for 0/10); it does not show the system is deterministic.

