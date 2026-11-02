# Phase 9: Live RAG calibration and live baseline governance

A live model run is not exactly repeatable. This page explains how to:
- run the live RAG evaluation several times under one fixed configuration
- measure how much each result varies
- find unstable cases
- derive regression tolerances from evidence, not guesses
- let a person approve a live baseline that carries that evidence

**Five runs are an initial sample, not proof.** Every output says so.

## What changed (architecture)

| Component | Change |
|---|---|
| `llm_client` | The seed is a setting (`AIQL_LLM_SEED`, default 42); `generation_settings()`; `ollama_provenance()` reads the Ollama version and exact model digests |
| `rag.evaluate` | Live (Ollama) reports record `generation` and `provenance` |
| `observability.records` | `rag_live` identity adds `generation` (temperature, seed, max_tokens), `ollama_version` and `model_digests`. RAG generation runs gain per-case `answerable`, `abstained` and `safety_failure` flags (booleans only, no text). Source gains `batch` and `purpose`. |
| `observability.live_batch` | `live-batch`: runs and records N live evaluations, verifying the configuration before and after each run |
| `observability.calibration` | `calibrate`: run-level spread, per-case consistency, critical and safety failures, tolerance proposals |
| `observability.baselines` | A `rag_live` approval requires an official calibration; synthetic and exploratory runs can never be baselines; `baseline review` shows what would be approved |
| `observability.synthetic` | Clearly labelled synthetic records for tests and the [example report](live-calibration-example.md) |

**Unchanged:**
- the identities of the deterministic types (custom engine, Promptfoo, RAG retrieval, RAG mock, pytest), so the approved baselines are unaffected
- the regression rules and thresholds
- the CI workflow

## Comparability

Runs are combined only if their **identity** is identical:
- dataset and corpus fingerprints
- chat model and embedder, **with exact digests**
- the **Ollama version**
- temperature, seed and token limit

**What happens when these change:**
- **A re-pulled model or an Ollama upgrade** makes runs not comparable. Comparing them, or approving across them, needs an explicit identity-change review.
- **Live reports from before Phase 9** carry none of these fields, so they're never mixed with new runs.

**Exploratory batches** (`--purpose exploratory`) may differ **only in the seed**. They're:
- labelled EXPLORATORY in every output
- never mixed with official runs
- refused by baseline approval
- excluded from official tolerances

## Statistical methodology

| Question | Method | Why this one |
|---|---|---|
| How much does a metric vary between runs? | Per-run values (with numerator/denominator), mean, median, min, max, range, sample SD | Descriptive. Safe at any n ≥ 2. |
| Where is the mean likely to be? | Student t interval on run-level values, df = n − 1, 95% | Small n with unknown variance. The t distribution widens the interval honestly (t = 2.776 at n = 5). It assumes runs are exchangeable, so it's labelled approximate. |
| How likely is a case to fail? | Wilson score interval on failures/runs per case | Good coverage at small n, and sensible at 0/n and n/n, where the normal approximation collapses to zero width |
| Are the cases independent trials? | **No.** Every run asks the same 25 questions. | Results are **never pooled** into one binomial sample: that would treat 125 correlated results as independent and overstate the precision |
| What if nothing varied? | No interval is reported ("not informative"), plus the Wilson upper bound for 0/n | A zero-width interval would claim a certainty n runs can't give. For 0 changes in 5 runs, each case's chance of changing could still be up to about 43%. |

**Handling of data:**
- **Missing observations are never imputed.** A metric missing from any run, or with a different
  denominator in any run, is shown as *incomplete* or *incomparable* and isn't aggregated.
- **Units come from the metric catalog:** a count, a rate (with numerator/denominator), or a score
  (a mean over n items). Rates are reported as `k/n` per run; percentages are never averaged
  across different denominators.
- **A cross-check:** the per-case failure frequencies imply an expected pass-rate SD,
  √(Σ p(1 − p)) / N, assuming cases vary independently. It's shown next to the observed SD.

## Critical and safety failures

A critical case that fails, a safety failure, or a restricted retrieval in **any single run**:
- is listed individually (run, case) under **BLOCKING**
- makes `calibrate` exit 1 and the verdict BLOCKING
- blocks a live baseline approval

Averages never hide one: a 1-in-10 safety failure is BLOCKING, even though its mean of 0.1 looks
small. These metrics are **never** given a tolerance.

## Tolerance proposals

**Proposed tolerance = the observed run-to-run range:** the largest difference between any two
comparable runs. Each proposal is shown next to:
- the current assumed live tolerance
- the size of one case (1/denominator)

**Rules:**
- **No proposal below 5 runs.**
- **A range of 0 proposes 0.** Unseen nondeterminism then BLOCKs, which is the safe direction.
  Collect more runs before relaxing it.
- **Critical, safety, error and restricted metrics** are fixed at 0.
- **Proposals never change `ci/regression_rules.json`.** Adopting one is a separate pull request by
  the owner, with the evidence linked and the reason recorded.

**Limits:**
- A range from 5 runs underestimates the true spread; it's a minimum, not an upper bound.
- **Recommendation:** ≥ 10 official runs before tightening or relaxing any live tolerance, and a new
  calibration after any identity change.

## Running a calibration (Windows PowerShell, `.venv` active, Ollama running)

1. **Commit first.** An official batch refuses an uncommitted working tree, because its runs may
   support a baseline.
2. **Check the configuration.** This verifies and prints the model, digests, Ollama version,
   sampling settings and fingerprints, plus each exact command, and runs nothing:
   ```powershell
   python -m ai_quality_lab.observability live-batch --runs 5 --batch official-1 --purpose official --check
   ```
3. **Run the batch.** About 8–9 minutes per run on CPU. Each run gets its own report under
   `reports/live/official-1/` and its own record.
   - A BLOCKED run is recorded too.
   - A run that crashes is reported as incomplete.
   - The batch stops if the model build or Ollama version changes.
   ```powershell
   python -m ai_quality_lab.observability live-batch --runs 5 --batch official-1 --purpose official
   ```
4. **Analyse it.** Exit 0 means analysed; 1 means BLOCKING; 2 means the runs can't be combined.
   ```powershell
   python -m ai_quality_lab.observability calibrate --batch official-1 --purpose official --output-md reports/live/official-1/calibration.md
   ```
5. **Exploratory seed sensitivity** (optional):
   ```powershell
   python -m ai_quality_lab.observability live-batch --runs 5 --batch explore-1 --purpose exploratory --seeds 1,2,3,4,5
   ```
   ```powershell
   python -m ai_quality_lab.observability calibrate --batch explore-1 --purpose exploratory
   ```

## Reading a calibration report

1. **Configuration:** confirm it's the model build and settings you mean to approve.
2. **Critical and safety failures:** anything here means stop. The run and case are named.
3. **Run-to-run variation:** the per-run values with denominators. A range of 0 means no change was
   seen, not that none is possible (see Limitations).
4. **Per-case consistency:**
   - *consistent_fail* cases are stable defects; D-6.1's over-abstention should appear here as
     abstained *always*
   - *intermittent* cases are the nondeterministic ones to watch
5. **Tolerance proposals:** evidence for a future rules change, not a change.

## Approving a live baseline (owner only)

**Never** use `--accept-*` or `--allow-uncommitted` for this. Claude Code prepares the evidence; you
approve.

1. **Pick the run to approve.** Choose a run from the official batch, normally the one closest to
   the median pass rate.
2. **Review what would be approved:** status, identity, every metric with its denominator, the full
   calibration, the known defects, and any reason it would be refused. Nothing is written.
   ```powershell
   python -m ai_quality_lab.observability baseline review --run-id official-1-run-<i> --calibration-batch official-1
   ```
3. **Approve.** The calibration is **recomputed from the history**, never read from an editable
   file.
   ```powershell
   python -m ai_quality_lab.observability baseline approve --run-id official-1-run-<i> --calibration-batch official-1 --approved-by "<your name>" --reason "<why>"
   ```
   It's refused unless the calibration:
   - is official, non-synthetic, and covers ≥ 5 comparable runs including this one
   - has no critical or safety failure in any run
   - has clean provenance for every run

   The calibration is stored next to the baseline as `rag_live/<run>.calibration.json`, and its hash
   goes into the audit log.
4. **Verify, then commit through a pull request,** as for every baseline:
   ```powershell
   python -m ai_quality_lab.observability baseline verify
   ```

## Results so far (2026-11-01, real live runs)

Two batches were run on commit `e59602d`. Full results are in
[reports/phase_9_completion_report.md](../reports/phase_9_completion_report.md).

| Batch | Runs | Pass rate per run | Over-abstention (D-6.1) | Unstable cases | Critical / safety |
|---|---|---|---|---|---|
| `official-1` (seed 42) | 5 | 19, 19, 20, 20, 20 /25 | 6, 6, 5, 5, 5 /19 | RAG-001, RAG-011, RAG-025 | none |
| `explore-1` (seeds 1–5) | 5 | 20 /25 every run | 5/19 every run | none | none |

**Main finding:** the first run after the model loads took about 4× longer and behaved differently.
The next 8 runs failed exactly the same 5 cases. That's a **run-order (cold-start) effect,** so
those runs aren't exchangeable samples, and no tolerance or live baseline should be set from this
batch.

The exploratory batch showed that **at temperature 0 the seed doesn't matter** (greedy decoding).

**Recommendation:** pre-declare a warm-up or cold-start protocol, then collect ≥ 10 official runs.

## CI integration

**No live model runs in CI:** hosted runners can't reach a local Ollama, and live results aren't
reproducible.

**What CI does cover:** all Phase 9 logic, through offline tests with synthetic fixtures:
- known-answer statistics
- unequal denominators and missing observations
- incomparable runs, and seed or version changes
- duplicate selection and insufficient samples
- per-case instability, and intermittent critical failures that block
- tolerance proposals
- live-baseline safeguards
- live-batch handling of BLOCKED and crashed runs

The Phase 8 jobs and gates are unchanged. **CI never reports a live result.**

## Security and reproducibility

**Stored data:**
- Records store **no prompts, retrieved text or answers**: only case IDs, outcomes, severities and
  boolean flags.
- Raw live reports, which do contain answers to the synthetic knowledge base, stay in git-ignored
  `reports/live/`.
- No credentials are involved; Ollama is local.

**Reproducible from Git:** code, prompt, dataset, corpus, configuration, and the commit of each run.

**Not reproducible from Git:**
- **The model build** (pinned by digest, but it must be pulled again).
- **Ollama's internals.**
- **CPU/thread nondeterminism.** Temperature 0 with a fixed seed is "as repeatable as the provider
  allows", not guaranteed identical.
