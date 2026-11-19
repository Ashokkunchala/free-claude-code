"""RunEngine: plan validation, policy/approval flow, verify, safe stop."""

import sys
from pathlib import Path

import pytest

from free_claude_code.terminal import (
    FileApprovalStore,
    InMemoryAuditSink,
    PlanError,
    RunEngine,
    RunPlan,
)
from free_claude_code.terminal.approval import ApprovalTokenIssuer
from free_claude_code.terminal.policy import PolicyConfig, PolicyEngine
from tests.terminal.conftest import py

PY_NAME = Path(sys.executable).name


def plan(steps):
    return RunPlan.from_dict({"goal": "g", "steps": steps})


def ok_step(step_id="s1", **kw):
    return {"id": step_id, "argv": list(py("pass")), **kw}


def allow_python_engine(tmp_path):
    return PolicyEngine(
        PolicyConfig(workspace_root=str(tmp_path), extra_allow=frozenset({PY_NAME}))
    )


def engine(tmp_path, sink=None, store=None, issuer=None, policy=None):
    return RunEngine(
        workspace=str(tmp_path),
        store=store,
        issuer=issuer,
        sink=sink if sink is not None else InMemoryAuditSink(),
        engine=policy if policy is not None else allow_python_engine(tmp_path),
    )


def test_plan_validation() -> None:
    with pytest.raises(PlanError, match="non-empty array"):
        RunPlan.from_dict({"goal": "g", "steps": []})
    with pytest.raises(PlanError, match="argv"):
        RunPlan.from_dict({"steps": [{"argv": "ls"}]})
    with pytest.raises(PlanError, match="unique"):
        RunPlan.from_dict(
            {"steps": [{"id": "a", "argv": ["true"]}, {"id": "a", "argv": ["true"]}]}
        )
    good = RunPlan.from_dict({"steps": [{"argv": ["echo", "hi"]}]})
    assert good.steps[0].step_id == "step-1"
    assert good.steps[0].title == "step-1"


def test_run_ok_path(tmp_path) -> None:
    sink = InMemoryAuditSink()
    marker = tmp_path / "marker.txt"
    report = engine(tmp_path, sink=sink).run(
        plan(
            [
                ok_step("s1"),
                {
                    "id": "s2",
                    "argv": list(py(f"open({str(marker)!r},'w').write('x')")),
                    "verify": {
                        "argv": list(
                            py(
                                f"import sys; sys.exit(0 if open({str(marker)!r}).read()=='x' else 1)"
                            )
                        )
                    },
                },
            ]
        ),
        task_id="t1",
    )
    assert report.status == "ok"
    assert [s.status for s in report.steps] == ["ok", "ok"]
    assert report.steps[1].verify_exit_code == 0
    assert report.residual == ()
    assert marker.read_text() == "x"
    kinds = [event.kind for event in sink.events()]
    assert "planned" in kinds and "finished" in kinds


def test_run_stops_on_failure_with_residual(tmp_path) -> None:
    report = engine(tmp_path).run(
        plan(
            [
                ok_step("s1"),
                {"id": "bad", "argv": list(py("raise SystemExit(3)"))},
                ok_step("s3"),
            ]
        ),
        task_id="t1",
    )
    assert report.status == "failed"
    assert [s.step_id for s in report.steps] == ["s1", "bad"]
    assert report.steps[-1].exit_code == 3
    assert report.residual == ("s3",)


def test_verify_failure_stops_run(tmp_path) -> None:
    report = engine(tmp_path).run(
        plan(
            [
                {
                    "id": "s1",
                    "argv": list(py("pass")),
                    "verify": {"argv": list(py("raise SystemExit(1)"))},
                },
                ok_step("s2"),
            ]
        ),
        task_id="t1",
    )
    assert report.status == "verify_failed"
    assert report.steps[0].verify_exit_code == 1
    assert report.residual == ("s2",)


def test_policy_denied_step(tmp_path) -> None:
    report = engine(tmp_path).run(
        plan([{"id": "evil", "argv": ["sudo", "ls"]}, ok_step("s2")]),
        task_id="t1",
    )
    assert report.status == "denied"
    assert report.steps[0].status == "denied"
    assert report.residual == ("s2",)


def _grant(store, issuer, task_id, approve=True):
    pending = store.list_records()
    assert len(pending) == 1
    record = pending[0]
    token = issuer.mint(record.spec(), task_id) if approve else None
    store.decide(
        record.approval_id,
        approve=approve,
        token=token,
        decision_reason="test",
    )


GIT_PUSH = [{"id": "push", "argv": ["git", "push"]}]


def test_approval_flow(tmp_path) -> None:
    store = FileApprovalStore(tmp_path / "approvals.json")
    issuer = ApprovalTokenIssuer(b"secret-key-for-tests")
    first = engine(tmp_path, store=store, issuer=issuer).run(
        plan(GIT_PUSH), task_id="t1"
    )
    assert first.status == "approval_required"
    assert first.steps[0].risk_level == "R2_PRIVILEGED"
    _grant(store, issuer, "t1")
    second = engine(tmp_path, store=store, issuer=issuer).run(
        plan(GIT_PUSH), task_id="t1"
    )
    # git push runs under the grant and fails fast (no repo/remote), which
    # proves the gate opened: the step no longer stops at approval.
    assert second.status == "failed"
    assert second.steps[0].exit_code not in (0, None)


def test_denied_approval_stays_denied(tmp_path) -> None:
    store = FileApprovalStore(tmp_path / "approvals.json")
    issuer = ApprovalTokenIssuer(b"secret-key-for-tests")
    first = engine(tmp_path, store=store, issuer=issuer).run(
        plan(GIT_PUSH), task_id="t1"
    )
    assert first.status == "approval_required"
    _grant(store, issuer, "t1", approve=False)
    second = engine(tmp_path, store=store, issuer=issuer).run(
        plan(GIT_PUSH), task_id="t1"
    )
    assert second.status == "denied"
