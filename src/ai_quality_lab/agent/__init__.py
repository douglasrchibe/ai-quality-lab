"""Phase 11: a sandboxed, tool-using IT Service Desk agent and the runtime that bounds it.

Only predefined tools act on a synthetic in-memory sandbox. Every proposed action passes seven
stages (parse, tool selection, schema, arguments, authorization, execution, result handling), each
recorded in the trace. Authorization is enforced in code. Tool results are untrusted data. The
package has no network or filesystem access (see ``docs/phase-11-plan.md``).
"""
