"""Phase 8: quality observability, historical records, baselines, and regression detection.

* ``records``: one versioned, validated JSON record per evaluation run (metrics keep their
  numerator, denominator, and deterministic/advisory kind; unmeasured metrics are never zero).
* ``history``: append-only JSONL history with strict validation.
* ``regression``: comparability rules, configurable regression rules, PASS / BLOCKED / ERROR.
* ``baselines``: explicitly approved, immutable baselines with a manifest and an audit log.
* ``cli``: ``python -m ai_quality_lab.observability``.

Rendering lives in ``ai_quality_lab.reporting``. Standard library only.
"""
