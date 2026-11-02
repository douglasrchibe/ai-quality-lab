"""Run and record N live RAG evaluations as one calibration batch (local Ollama only). Phase 9.

Each run is an ordinary ``python -m ai_quality_lab.rag.evaluate --mode rag --llm ollama`` with its
own report file and its own record; nothing is overwritten and nothing is averaged here (that is
``calibrate``). Before the first run the configuration is verified and printed: model, digests,
Ollama version, sampling settings, dataset and corpus. It is re-verified after every run, so a
model re-pull or Ollama upgrade mid-batch stops the batch instead of mixing builds.

An official batch requires a clean, committed working tree (its runs may support a baseline). A
run that fails to complete is reported and not recorded; a run that completes BLOCKED is recorded
like any other: unfavourable results are never dropped.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from ai_quality_lab.llm_client import LLMError, LLMSettings, load_env_file, ollama_provenance
from ai_quality_lab.observability.history import append_records, read_history
from ai_quality_lab.observability.records import (
    RecordError,
    build_record,
    dataset_fingerprint,
    corpus_fingerprint,
    git_source,
)

LIVE_DIR = Path("reports") / "live"
SAFE_LABEL = re.compile(r"^[A-Za-z0-9._-]+$")


def evaluation_command(output: Path) -> list[str]:
    return [sys.executable, "-m", "ai_quality_lab.rag.evaluate", "--mode", "rag", "--llm",
            "ollama", "--embedder", "env", "--output", output.as_posix()]


def _configuration(env: dict[str, str]) -> dict:
    from ai_quality_lab.rag.dataset import DEFAULT_RAG_DATASET
    from ai_quality_lab.rag.documents import DEFAULT_KNOWLEDGE_BASE
    from ai_quality_lab.rag.embeddings import OllamaEmbedder, embedder_from_env
    from ai_quality_lab.rag.retrieval import DEFAULT_TOP_K

    settings = LLMSettings.from_env(env)
    if settings.provider != "ollama":
        raise LLMError("live-batch runs local Ollama only (AIQL_LLM_PROVIDER=ollama); hosted, "
                       "paid providers are not used for calibration")
    embedder = embedder_from_env(env)
    if not isinstance(embedder, OllamaEmbedder):
        raise LLMError("live-batch needs Ollama embeddings (AIQL_EMBED_PROVIDER unset or "
                       "'ollama'), not the offline hashing embedder")
    return {"model": settings.model, "generation": settings.generation_settings(),
            "embed_model": embedder.model, "top_k": DEFAULT_TOP_K,
            **ollama_provenance(settings.base_url, [settings.model, embedder.model]),
            "dataset_fingerprint": dataset_fingerprint(DEFAULT_RAG_DATASET),
            "corpus_fingerprint": corpus_fingerprint(DEFAULT_KNOWLEDGE_BASE)}


def run_live_batch(*, runs: int, batch: str, purpose: str, seeds: str | None, history: Path,
                   check_only: bool = False) -> int:
    seed_list = [s.strip() for s in seeds.split(",")] if seeds else []
    problems = []
    if runs < 1:
        problems.append("--runs must be at least 1")
    if not SAFE_LABEL.match(batch):
        problems.append(f"batch label {batch!r} may only use letters, digits, '.', '_', '-'")
    if purpose == "official" and seed_list:
        problems.append("an official batch uses the shipped seed; --seeds is exploratory only")
    if purpose == "exploratory" and seed_list and (
            len(seed_list) != runs or len(set(seed_list)) != runs
            or not all(s.isdigit() for s in seed_list)):
        problems.append("--seeds needs one distinct non-negative integer per run")
    out_dir = LIVE_DIR / batch
    if out_dir.exists() and any(out_dir.iterdir()):
        problems.append(f"{out_dir} already has reports; a batch is never overwritten")
    if history.exists() and any(r["source"].get("batch") == batch for r in read_history(history)):
        problems.append(f"the history already has runs of batch {batch!r}; use a new label")
    source = git_source()
    if purpose == "official" and (source.get("commit") is None
                                  or source.get("uncommitted_changes")):
        problems.append("an official batch needs a clean, committed working tree (its runs may "
                        "support a baseline); commit first")
    env = dict(os.environ)
    load_env_file(environ=env)
    env["AIQL_EMBED_PROVIDER"] = env.get("AIQL_EMBED_PROVIDER") or "ollama"
    try:
        config = _configuration(env)
    except (LLMError, RecordError) as exc:
        problems.append(str(exc))
        config = None
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 2

    print(f"Live batch {batch!r} ({purpose}), {runs} run(s). Verified configuration:")
    for key in ("model", "generation", "embed_model", "ollama_version", "model_digests", "top_k",
                "dataset_fingerprint", "corpus_fingerprint"):
        print(f"  {key}: {config[key]}")
    print(f"  commit: {source.get('commit')} (uncommitted changes: "
          f"{source.get('uncommitted_changes')})")
    for i in range(1, runs + 1):
        seed = f"  [AIQL_LLM_SEED={seed_list[i - 1]}]" if seed_list else ""
        output = out_dir / f"run-{i}.json"
        print(f"  run {i}: {' '.join(evaluation_command(output))}{seed}")
    if check_only:
        print("Check only: nothing was run.")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    recorded, incomplete = [], []
    for i in range(1, runs + 1):
        run_env = dict(env)
        if seed_list:
            run_env["AIQL_LLM_SEED"] = seed_list[i - 1]
        output = out_dir / f"run-{i}.json"
        print(f"\n=== {batch} run {i}/{runs} ===", flush=True)
        code = subprocess.run(evaluation_command(output), env=run_env).returncode
        if code not in (0, 1) or not output.exists():
            incomplete.append(f"run {i}: evaluate exited {code} without a complete report")
            print(f"INCOMPLETE: run {i} (exit {code}); not recorded", flush=True)
            continue
        try:
            record = build_record("rag", output, run_id=f"{batch}-run-{i}",
                                  source={**source, "batch": batch, "purpose": purpose},
                                  note=f"{purpose} live batch {batch}, run {i}/{runs}")
            append_records(history, [record])
        except (RecordError, OSError, ValueError) as exc:
            incomplete.append(f"run {i}: report could not be recorded: {exc}")
            continue
        recorded.append(record["run_id"])
        print(f"recorded {record['run_id']} status={record['status']}", flush=True)
        try:
            now = _configuration(run_env)
        except (LLMError, RecordError) as exc:
            incomplete.append(f"after run {i}: configuration could not be re-verified: {exc}")
            break
        if (now["model_digests"], now["ollama_version"]) != (config["model_digests"],
                                                             config["ollama_version"]):
            incomplete.append(f"after run {i}: the model build or Ollama version changed; "
                              "batch stopped so builds are not mixed")
            break
    print(f"\nBatch {batch!r}: {len(recorded)} recorded {recorded}; "
          f"{len(incomplete)} incomplete or stopped")
    for item in incomplete:
        print(f"  - {item}")
    return 0 if len(recorded) == runs else 2
