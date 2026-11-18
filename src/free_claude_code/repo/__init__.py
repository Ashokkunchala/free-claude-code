"""Repository file operations: confined reads, searches, edits, rollback."""

from free_claude_code.repo.files import (
    DEFAULT_MAX_READ_BYTES,
    DEFAULT_MAX_SEARCH_FILE_BYTES,
    DEFAULT_MAX_SEARCH_RESULTS,
    BinaryFileError,
    ConfinementError,
    EditResult,
    FileTooLargeError,
    ReadResult,
    RepoFileError,
    SearchMatch,
    SearchResult,
    edit_file,
    read_file,
    rollback,
    search_content,
    search_files,
)
from free_claude_code.repo.ignore import IgnoreRules

__all__ = [
    "DEFAULT_MAX_READ_BYTES",
    "DEFAULT_MAX_SEARCH_FILE_BYTES",
    "DEFAULT_MAX_SEARCH_RESULTS",
    "BinaryFileError",
    "ConfinementError",
    "EditResult",
    "FileTooLargeError",
    "IgnoreRules",
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
