"""Phase 11.2: deterministic agent scenarios and their independent evaluation.

Deliberately outside the sandboxed ``ai_quality_lab.agent`` package. ``oracle`` judges plain data
only (scenario facts, the trace, the sandbox's exported state) and imports nothing from the agent
runtime, policy or sandbox, so a broken runtime policy can't make an unsafe run look safe.
"""
