"""Verification audit events (roadmap section 9): the run engine records a
structured ``verified`` or ``verification_failed`` event for every step
postcondition check, so the audit stream shows verification outcomes
alongside plan, approval, command and cancellation events."""

import sys
from pathlib import Path

from free_claude_code.terminal import InMemoryAuditSink, RunEngine, RunPlan
from free_claude_code.terminal.policy import PolicyConfig, PolicyEngine
from tests.terminal.conftest import py

# Policy basenames lowercase and strip ".exe"; match that normalization.
PY_NAME = Path(sys.executable).name.lower().removesuffix(".exe")


def engine(tmp_path, sink, *, allow_python=True):
    extra = frozenset({PY_NAME}) if allow_python else frozenset()
    return RunEngine(
        workspace=str(tmp_path),
        sink=sink,
        engine=PolicyEngine(
            PolicyConfig(workspace_root=str(tmp_path), extra_allow=extra)
        ),
    )


def test_verify_pass_records_verified(tmp_path) -> None:
    sink = InMemoryAuditSink()
    report = engine(tmp_path, sink).run(
        RunPlan.from_dict(
            {
                "goal": "g",
                "steps": [
                    {
                        "id": "s1",
                        "argv": list(py("pass")),
                        "verify": {"argv": list(py("pass"))},
                    }
                ],
            }
        ),
        task_id="t-verify-ok",
    )
    assert report.status == "ok"
    last = sink.events()[-1]
    assert last.kind == "verified"
    assert last.argv == py("pass")
    assert last.exit_code == 0
    assert last.task_id == "t-verify-ok"


def test_verify_mismatch_records_verification_failed(tmp_path) -> None:
    sink = InMemoryAuditSink()
    report = engine(tmp_path, sink).run(
        RunPlan.from_dict(
            {
                "goal": "g",
                "steps": [
                    {
                        "id": "s1",
                        "argv": list(py("pass")),
                        "verify": {"argv": list(py("raise SystemExit(1)"))},
                    }
                ],
            }
        ),
        task_id="t-verify-fail",
    )
    assert report.status == "verify_failed"
    last = sink.events()[-1]
    assert last.kind == "verification_failed"
    assert last.exit_code == 1


def test_verify_needing_privileges_records_verification_failed(tmp_path) -> None:
    sink = InMemoryAuditSink()
    report = engine(tmp_path, sink).run(
        RunPlan.from_dict(
            {
                "goal": "g",
                "steps": [
                    {
                        "id": "s1",
                        "argv": list(py("pass")),
                        "verify": {"argv": ["fcc-not-allowlisted-tool", "check"]},
                    }
                ],
            }
        ),
        task_id="t-verify-priv",
    )
    assert report.status == "verify_failed"
    assert report.steps[0].reason == "verification needs privileges"
    last = sink.events()[-1]
    assert last.kind == "verification_failed"
    assert last.argv == ("fcc-not-allowlisted-tool", "check")
    assert last.exit_code is None
