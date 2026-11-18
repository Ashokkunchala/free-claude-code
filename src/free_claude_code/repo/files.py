"""Safe repository file operations: read, search, edit and rollback.

Every operation confines paths to the workspace root (symlinks resolved on
both sides, so a symlink pointing outside the root is rejected). Reads are
capped, binary files are detected and skipped, edits produce a unified diff
and keep the previous content for rollback, and writes are atomic
(temp file + replace) so a crash never leaves a truncated file.
"""

import difflib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from free_claude_code.repo.ignore import IgnoreRules
from free_claude_code.terminal.policy import confinement_violation

DEFAULT_MAX_READ_BYTES = 256_000
DEFAULT_MAX_SEARCH_RESULTS = 200
DEFAULT_MAX_SEARCH_FILE_BYTES = 1_000_000
_BINARY_PROBE_BYTES = 8192


class RepoFileError(Exception):
    """Base class for repository file operation errors."""


class ConfinementError(RepoFileError):
    """The path escapes the workspace root."""


class BinaryFileError(RepoFileError):
    """The file looks binary; text operations refuse it."""


class FileTooLargeError(RepoFileError):
    """The file exceeds the operation's size cap."""


def _confined(root: Path, path: str | Path) -> Path:
    root_real = Path(os.path.realpath(root))
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root_real / candidate
    resolved = Path(os.path.realpath(candidate))
    violation = confinement_violation(str(resolved), str(root_real))
    if violation is not None:
        raise ConfinementError(violation)
    return resolved


def _looks_binary(raw: bytes) -> bool:
    return b"\0" in raw[:_BINARY_PROBE_BYTES]


@dataclass(frozen=True)
class ReadResult:
    path: str
    content: str
    size_bytes: int
    truncated: bool


def read_file(
    root: Path, path: str | Path, *, max_bytes: int = DEFAULT_MAX_READ_BYTES
) -> ReadResult:
    """Read a text file with a size cap; oversized content is truncated."""

    resolved = _confined(root, path)
    raw = resolved.read_bytes()
    if _looks_binary(raw):
        raise BinaryFileError(f"{path} looks like a binary file")
    truncated = len(raw) > max_bytes
    text = raw[:max_bytes].decode("utf-8", errors="replace")
    return ReadResult(
        path=str(resolved),
        content=text,
        size_bytes=len(raw),
        truncated=truncated,
    )


@dataclass(frozen=True)
class SearchMatch:
    path: str
    line_number: int
    line: str


@dataclass(frozen=True)
class SearchResult:
    matches: list[SearchMatch]
    truncated: bool
    files_searched: int
    files_skipped: int


def _iter_files(
    root: Path, ignore: IgnoreRules, *, max_file_bytes: int
) -> tuple[list[Path], int]:
    """Walk root, returning searchable files and the skipped count."""

    root_real = Path(os.path.realpath(root))
    files: list[Path] = []
    skipped = 0
    for dirpath, dirnames, filenames in os.walk(root_real):
        dir_rel = os.path.relpath(dirpath, root_real)
        kept: list[str] = []
        for dirname in sorted(dirnames):
            rel = dirname if dir_rel == "." else f"{dir_rel}/{dirname}"
            if not ignore.is_ignored(rel, is_dir=True):
                kept.append(dirname)
        dirnames[:] = kept
        for filename in sorted(filenames):
            rel = filename if dir_rel == "." else f"{dir_rel}/{filename}"
            if ignore.is_ignored(rel):
                skipped += 1
                continue
            candidate = Path(dirpath) / filename
            try:
                if candidate.stat().st_size > max_file_bytes:
                    skipped += 1
                    continue
            except OSError:
                skipped += 1
                continue
            files.append(candidate)
    return files, skipped


def search_files(
    root: Path,
    glob: str,
    *,
    ignore: IgnoreRules | None = None,
    max_results: int = DEFAULT_MAX_SEARCH_RESULTS,
) -> list[str]:
    """Find files by fnmatch-style glob on their root-relative path."""

    rules = ignore if ignore is not None else IgnoreRules.from_root(root)
    root_real = Path(os.path.realpath(root))
    pattern = re.compile(
        re.escape(glob)
        .replace(r"\*\*", ".*")
        .replace(r"\*", "[^/]*")
        .replace(r"\?", "[^/]")
        + "$"
    )
    found: list[str] = []
    files, _ = _iter_files(root_real, rules, max_file_bytes=2**63 - 1)
    for candidate in files:
        rel = os.path.relpath(candidate, root_real).replace("\\", "/")
        if pattern.match(rel):
            found.append(rel)
            if len(found) >= max_results:
                break
    return found


def search_content(
    root: Path,
    pattern: str,
    *,
    glob: str | None = None,
    ignore: IgnoreRules | None = None,
    max_results: int = DEFAULT_MAX_SEARCH_RESULTS,
    max_file_bytes: int = DEFAULT_MAX_SEARCH_FILE_BYTES,
) -> SearchResult:
    """Regex-search file contents, skipping ignored, large and binary files."""

    rules = ignore if ignore is not None else IgnoreRules.from_root(root)
    root_real = Path(os.path.realpath(root))
    regex = re.compile(pattern)
    glob_regex = (
        re.compile(
            re.escape(glob)
            .replace(r"\*\*", ".*")
            .replace(r"\*", "[^/]*")
            .replace(r"\?", "[^/]")
            + "$"
        )
        if glob is not None
        else None
    )
    matches: list[SearchMatch] = []
    files_searched = 0
    truncated = False
    files, skipped = _iter_files(root_real, rules, max_file_bytes=max_file_bytes)
    for candidate in files:
        rel = os.path.relpath(candidate, root_real).replace("\\", "/")
        if glob_regex is not None and not glob_regex.match(rel):
            continue
        try:
            raw = candidate.read_bytes()
        except OSError:
            skipped += 1
            continue
        if _looks_binary(raw):
            skipped += 1
            continue
        files_searched += 1
        text = raw.decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                matches.append(SearchMatch(path=rel, line_number=number, line=line))
                if len(matches) >= max_results:
                    truncated = True
                    return SearchResult(
                        matches=matches,
                        truncated=truncated,
                        files_searched=files_searched,
                        files_skipped=skipped,
                    )
    return SearchResult(
        matches=matches,
        truncated=truncated,
        files_searched=files_searched,
        files_skipped=skipped,
    )


@dataclass(frozen=True)
class EditResult:
    path: str
    diff: str
    backup_content: str | None
    bytes_written: int
    created: bool


def edit_file(root: Path, path: str | Path, new_content: str) -> EditResult:
    """Atomically replace a file's content, returning a diff and backup.

    ``backup_content`` is the previous content (None when the file is new);
    pass the EditResult to :func:`rollback` to undo.
    """

    resolved = _confined(root, path)
    created = not resolved.exists()
    old_text = ""
    if not created:
        raw = resolved.read_bytes()
        if _looks_binary(raw):
            raise BinaryFileError(f"{path} looks like a binary file")
        old_text = raw.decode("utf-8", errors="replace")
    rel = os.path.relpath(resolved, os.path.realpath(root)).replace("\\", "/")
    diff = "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
    )
    resolved.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=resolved.name + ".", dir=str(resolved.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(new_content)
        os.replace(temp_name, resolved)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return EditResult(
        path=str(resolved),
        diff=diff,
        backup_content=None if created else old_text,
        bytes_written=len(new_content.encode("utf-8")),
        created=created,
    )


def rollback(result: EditResult) -> None:
    """Undo an edit: restore the backup, or remove a file edit created."""

    path = Path(result.path)
    if result.created:
        path.unlink(missing_ok=True)
        return
    if result.backup_content is None:
        raise RepoFileError("edit result has no backup content")
    path.write_text(result.backup_content, encoding="utf-8")


__all__ = [
    "DEFAULT_MAX_READ_BYTES",
    "DEFAULT_MAX_SEARCH_FILE_BYTES",
    "DEFAULT_MAX_SEARCH_RESULTS",
    "BinaryFileError",
    "ConfinementError",
    "EditResult",
    "FileTooLargeError",
    "ReadResult",
    "RepoFileError",
    "SearchMatch",
    "SearchResult",
    "edit_file",
    "read_file",
    "rollback",
    "search_content",
    "search_files",
]
