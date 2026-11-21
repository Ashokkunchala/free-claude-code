"""Dependency-aware plans: depends_on validation and topological order."""

import sys
from pathlib import Path

import pytest

from free_claude_code.terminal import InMemoryAuditSink, PlanError, RunEngine, RunPlan
from free_claude_code.terminal.policy import PolicyConfig, PolicyEngine
from tests.terminal.conftest import py

PY_NAME = Path(sys.executable).name.lower().removesuffix(".exe")


def plan(steps):
    return RunPlan.from_dict({"goal": "g", "steps": steps})


def ok_step(step_id, **kw):
    return {"id": step_id, "argv": list(py("pass")), **kw}


def test_depends_on_defaults_to_empty() -> None:
    parsed = plan([ok_step("a")])
    assert parsed.steps[0].depends_on == ()


def test_depends_on_is_parsed() -> None:
    parsed = plan([ok_step("a"), ok_step("b", depends_on=["a"])])
    assert parsed.steps[1].depends_on == ("a",)


def test_depends_on_must_be_string_list() -> None:
    with pytest.raises(PlanError, match="depends_on"):
        plan([ok_step("a", depends_on="a")])
    with pytest.raises(PlanError, match="depends_on"):
        plan([ok_step("a", depends_on=[1])])
    with pytest.raises(PlanError, match="depends_on"):
        plan([ok_step("a", depends_on=[""])])


def test_unknown_dependency_rejected() -> None:
    with pytest.raises(PlanError, match="unknown dependency ghost"):
        plan([ok_step("a", depends_on=["ghost"])])


def test_cycles_rejected() -> None:
    with pytest.raises(PlanError, match="cycle"):
        plan([ok_step("a", depends_on=["a"])])
    with pytest.raises(PlanError, match="cycle"):
        plan([ok_step("a", depends_on=["b"]), ok_step("b", depends_on=["a"])])
    with pytest.raises(PlanError, match="cycle") as excinfo:
        plan(
            [
                ok_step("a", depends_on=["c"]),
                ok_step("b", depends_on=["a"]),
                ok_step("c", depends_on=["b"]),
                ok_step("d"),
            ]
        )
    assert "a" in str(excinfo.value) and "c" in str(excinfo.value)


def test_duplicate_dependencies_are_deduplicated() -> None:
    parsed = plan([ok_step("a"), ok_step("b", depends_on=["a", "a"])])
    assert parsed.steps[1].depends_on == ("a",)


def test_topological_order_respects_dependencies() -> None:
    parsed = plan([ok_step("b", depends_on=["a"]), ok_step("a")])
    assert [s.step_id for s in parsed.ordered_steps()] == ["a", "b"]


def test_topological_order_is_stable_for_independent_steps() -> None:
    parsed = plan(
        [
            ok_step("d", depends_on=["b", "c"]),
            ok_step("b", depends_on=["a"]),
            ok_step("c", depends_on=["a"]),
            ok_step("a"),
            ok_step("free"),
        ]
    )
    assert [s.step_id for s in parsed.ordered_steps()] == ["a", "free", "b", "c", "d"]


def test_engine_executes_in_dependency_order(tmp_path) -> None:
    marker = tmp_path / "order.txt"
    write_a = f"open({str(marker)!r},'a').write('a')"
    write_b = f"open({str(marker)!r},'a').write('b')"
    sink = InMemoryAuditSink()
    eng = RunEngine(
        workspace=str(tmp_path),
        sink=sink,
        engine=PolicyEngine(
            PolicyConfig(workspace_root=str(tmp_path), extra_allow=frozenset({PY_NAME}))
        ),
    )
    # b is declared first but depends on a; the engine must run a first.
    report = eng.run(
        plan(
            [
                {"id": "b", "argv": list(py(write_b)), "depends_on": ["a"]},
                {"id": "a", "argv": list(py(write_a))},
            ]
        ),
        task_id="t1",
    )
    assert report.status == "ok"
    assert marker.read_text() == "ab"
    assert [r.step_id for r in report.steps] == ["a", "b"]
