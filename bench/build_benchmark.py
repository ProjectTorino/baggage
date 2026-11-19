"""Build a synthetic repository with a known answer key.

The point is not realism for its own sake: it is to have an expected ranking.
If the model cannot find the file we planted as the obvious money pit, or if it
promotes the trivial-but-frequently-touched decoy, the model is noise.

Ground truth planted here:

  src/core/engine.py   rewritten every commit, untested, one author, ripples
                       into three distant folders      -> must rank #1
  src/legacy/monolith.py 600 lines, rewritten 3 times  -> must rank top 3
  src/api/routes.py    churns, but its test moves too  -> must stay below engine
  src/utils/format.py  2 lines, touched 20 times       -> must NOT rank high
  docs/, scripts/      touched once each               -> must sit at the bottom

Run:  python bench/build_benchmark.py bench/repo
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

START = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)


def force_rmtree(path: Path) -> None:
    """Remove a tree even though git marks its objects read-only on Windows."""

    def on_error(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=on_error)

FILLER = [
    "src/models/user.py",
    "src/services/billing.py",
    "src/config/settings.py",
    "docs/install.md",
    "docs/api.md",
    "docs/faq.md",
    "web/static/app.js",
    "scripts/backup.sh",
]


def run(repo: Path, *args: str, when: datetime, author: str = "Alice") -> None:
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S +0000")
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": author,
            "GIT_AUTHOR_EMAIL": f"{author.lower()}@example.com",
            "GIT_COMMITTER_NAME": author,
            "GIT_COMMITTER_EMAIL": f"{author.lower()}@example.com",
            "GIT_AUTHOR_DATE": stamp,
            "GIT_COMMITTER_DATE": stamp,
        }
    )
    subprocess.run(["git", "-C", str(repo), *args], env=env, check=True, capture_output=True)


def write(repo: Path, rel: str, content: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def build(out: Path) -> Path:
    """Create the benchmark repository at *out* and return it."""
    if out.exists():
        force_rmtree(out)
    out.mkdir(parents=True)
    run(out, "init", "-q", "-b", "main", when=START)

    clock = START
    day = timedelta(days=1)

    def tick(days: int = 3) -> None:
        nonlocal clock
        clock += day * days

    def commit(message: str) -> None:
        run(out, "add", "-A", when=clock)
        run(out, "commit", "-q", "-m", message, when=clock)

    # -- scaffold ----------------------------------------------------------
    write(out, "src/core/engine.py", "class Engine:\n    pass\n")
    write(out, "src/legacy/monolith.py", "".join(f"def m{i}():\n    return {i}\n\n" for i in range(200)))
    write(out, "src/utils/format.py", "def fmt(x):\n    return x\n")
    write(out, "src/utils/log.py", "def log(m):\n    print(m)\n")
    write(out, "src/api/routes.py", "def routes():\n    return []\n")
    write(out, "tests/test_routes.py", "def test_routes():\n    assert True\n")
    for rel in FILLER:
        write(out, rel, f"# {rel}\n")
    commit("initial scaffold")

    # -- the money pit: real rewrites plus cross-folder ripples ------------
    ripple = ["src/api/handlers.py", "src/db/schema.py", "src/cli/main.py"]
    for i in range(40):
        tick()
        body = "\n".join(f"def step_{j}(x):\n    return x + {j + i}\n" for j in range(30))
        write(out, "src/core/engine.py", body + "\n")
        write(out, ripple[i % 3], f"# synced {i}\n")
        commit(f"engine rewrite {i}")

    # -- decoy: touched constantly, but only two lines ---------------------
    for i in range(20):
        tick()
        write(out, "src/utils/format.py", f"def fmt(x):\n    return str(x) + '{i}'\n")
        write(out, "src/utils/log.py", f"def log(m):\n    print(m, {i})\n")
        commit(f"format tweak {i}")

    # -- tested module: a real module whose test moves with it -------------
    # Deliberately substantial: comparing a trivial decoy against another
    # trivial file proves nothing.
    for i in range(15):
        tick()
        body = "\n".join(
            f"def route_{j}(req):\n    return handle_{j}(req, {i})\n" for j in range(20)
        )
        write(out, "src/api/routes.py", body + "\n")
        write(out, "tests/test_routes.py", f"def test_routes_{i}():\n    assert True\n")
        commit(f"route {i}")

    # -- big legacy file: rare edits, enormous surface ----------------------
    for i in range(3):
        tick(30)
        write(
            out,
            "src/legacy/monolith.py",
            "".join(f"def m{i}_{j}():\n    return {j}\n\n" for j in range(200)),
        )
        commit(f"legacy rewrite {i}")

    # -- cold filler -------------------------------------------------------
    tick(30)
    for rel in FILLER:
        write(out, rel, f"# {rel}\n# touched\n")
    commit("touch filler")

    count = subprocess.run(
        ["git", "-C", str(out), "rev-list", "--count", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    print(f"Built {out}: {count} commits")
    return out


def main() -> int:
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "bench/repo"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
