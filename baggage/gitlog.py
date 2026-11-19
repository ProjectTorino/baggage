"""Read git history into per-file churn statistics.

Everything here shells out to ``git``. No dependencies beyond the standard
library, which keeps ``pipx install baggage`` fast and boring.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Machine-readable markers chosen so ordinary commit text cannot collide.
_COMMIT_MARK = "\x01"
_FIELD_SEP = "\x1f"

#: Commits touching more files than this are treated as mass refactors and
#: excluded from coupling analysis, where they would otherwise couple
#: everything to everything.
MAX_FILES_PER_COMMIT = 50


class GitError(RuntimeError):
    """Raised when git is missing or the directory is not a repository."""


@dataclass
class FileHistory:
    """What git remembers about one path."""

    path: str
    commits: int = 0
    added: int = 0
    deleted: int = 0
    authors: set[str] = field(default_factory=set)
    first_ts: int = 0
    last_ts: int = 0
    recent_commits: int = 0
    recent_churn: int = 0

    @property
    def churn(self) -> int:
        """Total lines added plus removed. The crude but honest proxy for effort."""
        return self.added + self.deleted

    @property
    def author_count(self) -> int:
        return len(self.authors)


@dataclass
class Commit:
    """One non-merge commit: when it happened and what it touched."""

    ts: int
    files: list[str] = field(default_factory=list)
    churn: dict[str, int] = field(default_factory=dict)


@dataclass
class History:
    """The whole repository's history, reduced to what the analysis needs."""

    root: Path
    files: dict[str, FileHistory] = field(default_factory=dict)
    commits: list[Commit] = field(default_factory=list)
    first_ts: int = 0
    last_ts: int = 0

    @property
    def commit_count(self) -> int:
        return len(self.commits)

    @property
    def span_days(self) -> int:
        if not self.first_ts or not self.last_ts:
            return 0
        return max(0, (self.last_ts - self.first_ts) // 86_400)


def resolve_rename(raw: str) -> str:
    """Collapse git's rename notation to the *new* path.

    Handles the forms git emits with ``-M``::

        old.py => new.py
        src/{old => new}/engine.py
        src/{ => nested}/engine.py
    """
    if "=>" not in raw:
        return raw

    if "{" in raw and "}" in raw:
        pre, rest = raw.split("{", 1)
        inner, post = rest.split("}", 1)
        _, _, new = inner.partition("=>")
        out = pre + new.strip() + post
    else:
        _, _, new = raw.partition("=>")
        out = new

    return re.sub(r"/{2,}", "/", out).strip()


def _run(root: Path, args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "-c", "core.quotepath=false", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:  # git not installed
        raise GitError("git was not found on PATH") from exc
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or "git command failed")
    return proc.stdout


def read_history(root: str | Path, *, recent_days: int = 365) -> History:
    """Read ``git log`` for *root* and summarise it per file.

    ``recent_days`` is measured backwards from the newest commit rather than
    from today, so the result is reproducible when analysing an archived clone.
    """
    root_path = Path(root).resolve()
    if not (root_path / ".git").exists():
        raise GitError(f"not a git repository: {root_path}")

    out = _run(
        root_path,
        [
            "log",
            "--no-merges",
            "--numstat",
            "-M",
            f"--pretty=format:{_COMMIT_MARK}%H{_FIELD_SEP}%an{_FIELD_SEP}%at",
        ],
    )

    history = History(root=root_path)
    author = ""
    stamp = 0
    in_commit = False
    touched: list[str] = []
    churn_here: dict[str, int] = {}

    def close_commit() -> None:
        nonlocal touched, churn_here, in_commit
        if in_commit and touched:
            history.commits.append(Commit(ts=stamp, files=touched, churn=dict(churn_here)))
        touched = []
        churn_here = {}
        in_commit = False

    for line in out.splitlines():
        if line.startswith(_COMMIT_MARK):
            close_commit()
            parts = line[1:].split(_FIELD_SEP)
            if len(parts) != 3:
                continue
            try:
                stamp = int(parts[2])
            except ValueError:
                stamp = 0
            author = parts[1]
            in_commit = True
            continue

        if not in_commit or not line.strip():
            continue

        fields = line.split("\t")
        if len(fields) != 3:
            continue
        added_s, deleted_s, raw_path = fields

        path = resolve_rename(raw_path)
        if not path:
            continue

        try:
            added = int(added_s)
        except ValueError:
            added = 0  # git prints "-" for binary files
        try:
            deleted = int(deleted_s)
        except ValueError:
            deleted = 0

        entry = history.files.setdefault(path, FileHistory(path=path))
        entry.commits += 1
        entry.added += added
        entry.deleted += deleted
        entry.authors.add(author)
        entry.first_ts = min(entry.first_ts or stamp, stamp)
        entry.last_ts = max(entry.last_ts, stamp)

        if path not in touched:
            touched.append(path)
        churn_here[path] = churn_here.get(path, 0) + added + deleted

    close_commit()

    if history.commits:
        stamps = [c.ts for c in history.commits if c.ts]
        if stamps:
            history.first_ts = min(stamps)
            history.last_ts = max(stamps)

    # Exact recency counts: walk commits once, using the window cut-off.
    if history.last_ts and recent_days > 0:
        cutoff = history.last_ts - recent_days * 86_400
        for commit in history.commits:
            if commit.ts < cutoff:
                continue
            for path in commit.files:
                entry = history.files.get(path)
                if entry is not None:
                    entry.recent_commits += 1
                    entry.recent_churn += commit.churn.get(path, 0)

    return history
