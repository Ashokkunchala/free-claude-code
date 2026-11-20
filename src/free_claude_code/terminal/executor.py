"""TerminalExecutor: one structured primitive for running commands.

Every run emits audit events (planned, started, finished/timed_out/cancelled),
captures output with per-stream caps, streams redacted chunks to an optional
callback, enforces a wall-clock timeout with process-tree kill escalation, and
supports cooperative cancellation through a threading.Event.

Note on redaction: captured buffers are redacted as a whole, so secrets split
across read chunks are caught in the final result. Live stream callbacks get
per-chunk redaction - a secret split exactly across a chunk boundary could
reach the callback; treat callbacks as progress display, not storage.
"""

import contextlib
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from typing import IO

from free_claude_code.terminal import processes
from free_claude_code.terminal.audit import AuditSink, NullAuditSink, make_event
from free_claude_code.terminal.errors import PolicyRejectedError, SpawnError
from free_claude_code.terminal.redaction import Redactor, default_redactor
from free_claude_code.terminal.result import CommandResult, OutputChunk, _Capture
from free_claude_code.terminal.spec import (
    DEFAULT_POLICY_DECISION,
    CommandSpec,
    PolicyDecision,
)

_READ_CHUNK_BYTES = 4096
_CANCEL_POLL_SECONDS = 0.05

OutputCallback = Callable[[OutputChunk], None]


class TerminalExecutor:
    """Runs CommandSpecs deterministically. Safe to share across threads."""

    def __init__(
        self,
        *,
        sink: AuditSink | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self._sink = sink if sink is not None else NullAuditSink()
        self._redactor = redactor if redactor is not None else default_redactor()

    def run(
        self,
        spec: CommandSpec,
        *,
        policy: PolicyDecision = DEFAULT_POLICY_DECISION,
        cancel_event: threading.Event | None = None,
        on_output: OutputCallback | None = None,
        task_id: str | None = None,
        allow_pty: bool = False,
    ) -> CommandResult:
        """Run one command; ``allow_pty`` gates ``spec.pty`` to interactive call sites.

        With a pty the child's stdout and stderr arrive merged: the result
        carries everything in ``stdout`` and ``stderr`` stays empty.
        """

        task = task_id if task_id is not None else uuid.uuid4().hex
        if not policy.allowed:
            self._sink.record(
                make_event(
                    "cancelled",
                    task,
                    spec.argv,
                    spec.cwd,
                    policy_id=policy.policy_id,
                )
            )
            raise PolicyRejectedError(spec.argv, policy.reason)

        self._sink.record(
            make_event("planned", task, spec.argv, spec.cwd, policy_id=policy.policy_id)
        )
        started = time.monotonic()
        use_pty = spec.pty and allow_pty and not spec.inherit_stdio
        try:
            process = processes.spawn(
                spec.argv,
                cwd=spec.cwd,
                env=spec.env,
                inherit_stdio=spec.inherit_stdio,
                isolated=spec.isolated,
                max_cpu_seconds=spec.max_cpu_seconds,
                max_memory_bytes=spec.max_memory_bytes,
                pty=use_pty,
            )
        except (OSError, ValueError) as exc:
            self._sink.record(
                make_event(
                    "failed", task, spec.argv, spec.cwd, policy_id=policy.policy_id
                )
            )
            raise SpawnError(spec.argv, str(exc)) from exc
        self._sink.record(
            make_event("started", task, spec.argv, spec.cwd, policy_id=policy.policy_id)
        )

        stdout_capture = _Capture()
        stderr_capture = _Capture()
        readers: list[threading.Thread] = []
        if not spec.inherit_stdio:
            pty_active = use_pty and processes.pty_supported()
            if process.stdout is None or (process.stderr is None and not pty_active):
                processes.kill_tree(process)
                processes.untrack(process)
                self._sink.record(
                    make_event(
                        "failed", task, spec.argv, spec.cwd, policy_id=policy.policy_id
                    )
                )
                raise SpawnError(spec.argv, "child stdio pipes were not created")
            readers = [
                self._start_reader(
                    process.stdout, "stdout", stdout_capture, spec, started, on_output
                )
            ]
            if not pty_active:
                assert process.stderr is not None
                readers.append(
                    self._start_reader(
                        process.stderr,
                        "stderr",
                        stderr_capture,
                        spec,
                        started,
                        on_output,
                    )
                )

        timed_out = False
        cancelled = False
        resize = (
            processes.forward_resize(process.stdout.fileno())
            if use_pty and processes.pty_supported() and process.stdout is not None
            else contextlib.nullcontext()
        )
        with resize:
            try:
                timed_out, cancelled = self._await_exit(process, spec, cancel_event)
            finally:
                for reader in readers:
                    reader.join(timeout=5.0)
                for pipe in (process.stdout, process.stderr):
                    if pipe is not None:
                        pipe.close()
                processes.untrack(process)

        duration = time.monotonic() - started
        exit_code = process.returncode
        if timed_out:
            kind = "timed_out"
        elif cancelled:
            kind = "cancelled"
        elif exit_code != 0:
            kind = "failed"
        else:
            kind = "finished"
        self._sink.record(
            make_event(
                kind,
                task,
                spec.argv,
                spec.cwd,
                duration_seconds=duration,
                exit_code=exit_code,
                policy_id=policy.policy_id,
            )
        )
        return CommandResult(
            argv=spec.argv,
            cwd=spec.cwd,
            exit_code=exit_code,
            duration_seconds=duration,
            stdout=self._redactor.redact(stdout_capture.joined()),
            stderr=self._redactor.redact(stderr_capture.joined()),
            timed_out=timed_out,
            cancelled=cancelled,
            stdout_truncated=stdout_capture.truncated,
            stderr_truncated=stderr_capture.truncated,
            policy_id=policy.policy_id,
        )

    def _start_reader(
        self,
        stream: IO[bytes],
        name: str,
        capture: _Capture,
        spec: CommandSpec,
        started: float,
        on_output: OutputCallback | None,
    ) -> threading.Thread:
        def read() -> None:
            while True:
                try:
                    chunk = stream.read(_READ_CHUNK_BYTES)
                except OSError:
                    # PTY masters raise EIO once the child's slave side is
                    # gone; that is the pty end-of-stream.
                    return
                if not chunk:
                    return
                capture.append(chunk, spec.output_cap_bytes)
                if on_output is not None:
                    on_output(
                        OutputChunk(
                            stream=name,
                            data=self._redactor.redact(chunk),
                            timestamp_offset_seconds=time.monotonic() - started,
                        )
                    )

        thread = threading.Thread(target=read, name=f"fcc-terminal-{name}", daemon=True)
        thread.start()
        return thread

    def _await_exit(
        self,
        process: subprocess.Popen[bytes],
        spec: CommandSpec,
        cancel_event: threading.Event | None,
    ) -> tuple[bool, bool]:
        deadline = time.monotonic() + spec.timeout_seconds
        while True:
            returncode = process.poll()
            if returncode is not None:
                return False, False
            if cancel_event is not None and cancel_event.is_set():
                processes.kill_tree(process)
                process.wait()
                return False, True
            if time.monotonic() >= deadline:
                processes.kill_tree(process)
                process.wait()
                return True, False
            time.sleep(_CANCEL_POLL_SECONDS)
