"""Turn raw git history into a ranked, explainable cost model.

The model answers one question: *if someone has to change this file, how much
will it hurt?* Five signals feed into it, all computed from git history and the
working tree. No LLM, no network, no config.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from baggage.gitlog import MAX_FILES_PER_COMMIT, History, read_history

#: A pair of files must co-change at least this often to count as coupled.
MIN_COUPLING = 2

#: How the risk signals amplify the effort signal. Risk is a *multiplier*, not
#: an additive term: a risky file that nobody touches costs nothing, so these
#: weights must never be able to lift a trivial file into the ranking on their
#: own. (An early additive version put a 2-line file at rank #2.)
RISK_WEIGHTS = {
    "coupling": 0.35,
    "bus_factor": 0.25,
    "size": 0.20,
    "frequency": 0.20,
}

#: Risk multiplier ceiling, used to normalise the final score into 0..100.
MAX_RISK = 1.0 + sum(RISK_WEIGHTS.values())

#: Lines of churn inside the window that count as "maximum effort" for one file.
#: This is an *absolute* reference on purpose. Normalising against the busiest
#: file in the repo made dormant repositories look expensive: express, a stable
#: mature project, scored 78/100 (grade F) purely because everything was quiet.
EFFORT_REFERENCE = 2000

#: Effort curve. Square-root rather than log: log compresses too hard, making
#: 60 changed lines look almost as costly as 2,000.
def effort_for(recent_churn: int) -> float:
    if recent_churn <= 0:
        return 0.0
    return min(1.0, math.sqrt(recent_churn / EFFORT_REFERENCE))

#: Files matching this are excluded from hotspots by default: tests churn
#: constantly and would otherwise crowd out the code they are testing.
_TEST_RE = re.compile(
    r"(^|/)(tests?|spec|__tests__|testing)/"
    r"|(^|/)test_[^/]+$"
    r"|_test\.[A-Za-z0-9]+$"
    r"|\.(test|spec)\.[A-Za-z0-9]+$"
    r"|(^|/)conftest\.py$",
)

_BINARY_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".tar",
     ".woff", ".woff2", ".ttf", ".eot", ".so", ".dll", ".exe", ".pyc", ".bin"}
)

# --- file categories -------------------------------------------------------
# Churn in these files is mechanical, not cognitive. Ranking them produced
# real-world nonsense: on flask, ``uv.lock`` took first place at 84/100, and
# five of the top eight were GitHub Actions workflows.

LOCKFILE_NAMES = frozenset(
    {
        "uv.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
        "poetry.lock", "Pipfile.lock", "Cargo.lock", "Gemfile.lock", "composer.lock",
        "go.sum", "flake.lock", "pdm.lock", "mix.lock", "pubspec.lock",
    }
)

CI_PREFIXES = (".github/", ".gitlab/", ".circleci/", ".buildkite/", ".azure-pipelines/")
CI_NAMES = frozenset(
    {".gitlab-ci.yml", "azure-pipelines.yml", ".travis.yml", "Jenkinsfile", "appveyor.yml"}
)

VENDOR_PARTS = frozenset(
    {"node_modules", "vendor", "third_party", "3rdparty", "vendored", "dist",
     "build", ".venv", "venv", ".tox"}
)

GENERATED_SUFFIXES = (".min.js", ".min.css", ".map", ".pb.go", "_pb2.py", "_pb2_grpc.py")

DOC_DIRS = frozenset({"docs", "doc", "website", "site", "documentation"})
SAMPLE_DIRS = frozenset({"examples", "example", "samples", "sample", "demo", "demos"})

#: Build and packaging metadata churns on every release without anyone having
#: to understand it. ``setup.py`` collected 172 commits and 43 authors in
#: requests while containing nine lines of code.
PACKAGING_NAMES = frozenset(
    {
        "setup.py", "setup.cfg", "MANIFEST.in", "Makefile", "makefile", "GNUmakefile",
        "Dockerfile", "docker-compose.yml", "docker-compose.yaml", "tox.ini",
        "noxfile.py", "justfile", "Taskfile.yml", "configure.ac", "Makefile.am",
        "py.typed",
    }
)

DOC_SUFFIXES = frozenset({".md", ".rst", ".txt", ".adoc", ".asciidoc", ".org"})

CONFIG_SUFFIXES = frozenset(
    {".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".conf", ".env", ".lock"}
)


def classify(path: str) -> str:
    """Bucket a path as ``source`` or as one of the mechanical categories."""
    name = path.rsplit("/", 1)[-1]
    lowered = path.lower()

    if name in LOCKFILE_NAMES or name.endswith(".lock"):
        return "lockfile"
    if lowered.startswith(CI_PREFIXES) or name in CI_NAMES:
        return "ci"
    if any(f"/{part}/" in f"/{lowered}" for part in VENDOR_PARTS):
        return "vendored"
    if lowered.endswith(GENERATED_SUFFIXES):
        return "generated"

    parts = lowered.split("/")[:-1]
    if any(part in DOC_DIRS for part in parts):
        return "docs"
    if any(part in SAMPLE_DIRS for part in parts):
        return "sample"
    if name in PACKAGING_NAMES:
        return "packaging"
    if name.startswith("."):
        return "config"

    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if suffix in DOC_SUFFIXES:
        return "docs"
    if suffix in CONFIG_SUFFIXES:
        return "config"
    return "source"


def is_test_file(path: str) -> bool:
    return bool(_TEST_RE.search(path))


def module_of(path: str) -> str:
    """The directory part of *path* (``""`` for a root-level file)."""
    return path.rsplit("/", 1)[0] if "/" in path else ""


def is_distant(a: str, b: str) -> bool:
    """True when two files live in different directories.

    The signal worth chasing is whether a change ripples *outside* the file's
    own folder. Siblings in the same directory move together for obvious
    reasons and would drown out the interesting couplings.
    """
    return module_of(a) != module_of(b)


def count_lines(path: Path, *, limit: int = 2_000_000) -> int:
    """Count lines in *path*, returning 0 for binary or unreadable files."""
    if path.suffix.lower() in _BINARY_SUFFIXES:
        return 0
    try:
        with path.open("rb") as fh:
            data = fh.read()
    except OSError:
        return 0
    if b"\x00" in data[:8192]:
        return 0
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


@dataclass
class Hotspot:
    """One file, scored and with the evidence behind the score."""

    path: str
    score: float
    commits: int
    recent_commits: int
    churn: int
    loc: int
    coupling: int
    authors: int
    is_tested: bool
    is_test: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class Analysis:
    """Repository-level result."""

    root: Path
    score: float
    grade: str
    hotspots: list[Hotspot]
    file_count: int
    commit_count: int
    span_days: int
    total_churn: int
    excluded: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def excluded_total(self) -> int:
        return sum(self.excluded.values())

    @property
    def is_reliable(self) -> bool:
        return not self.warnings

    def top(self, n: int = 10) -> list[Hotspot]:
        return self.hotspots[:n]


def grade_for(score: float) -> str:
    if score < 20:
        return "A"
    if score < 35:
        return "B"
    if score < 50:
        return "C"
    if score < 65:
        return "D"
    return "F"


def _coupling_map(history: History) -> dict[str, int]:
    """Count, per file, how many *distant* partners it co-changes with.

    Only pairs seen at least ``MIN_COUPLING`` times count, which filters out
    coincidence. Mass-refactor commits are skipped entirely.
    """
    seen: set[tuple[str, str]] = set()
    repeated: set[tuple[str, str]] = set()

    for commit in history.commits:
        files = commit.files
        if len(files) < 2 or len(files) > MAX_FILES_PER_COMMIT:
            continue
        for i, a in enumerate(files):
            for b in files[i + 1 :]:
                # A file moving together with its own test is healthy, not a
                # hidden dependency; counting it would reward having tests.
                if is_test_file(a) or is_test_file(b):
                    continue
                if not is_distant(a, b):
                    continue
                key = (a, b) if a < b else (b, a)
                if key in seen:
                    repeated.add(key)
                else:
                    seen.add(key)

    partners: Counter[str] = Counter()
    for a, b in repeated:
        partners[a] += 1
        partners[b] += 1
    return dict(partners)


def analyze_baggage(
    root: str | Path,
    *,
    recent_days: int = 365,
    include_tests: bool = False,
    include_all: bool = False,
    top: int = 20,
) -> Analysis:
    """Analyse the repository at *root* and rank its most expensive files."""
    root_path = Path(root).resolve()
    history = read_history(root_path, recent_days=recent_days)
    coupling = _coupling_map(history)

    # Only score files that still exist: a hotspot you cannot open is not
    # actionable.
    candidates: list[tuple[str, int, int]] = []
    skipped: list[str] = []
    for path in history.files:
        full = root_path / path
        if not full.is_file():
            skipped.append(path)
            continue
        candidates.append((path, count_lines(full), full.stat().st_size))

    scored: list[str] = []
    excluded: Counter[str] = Counter()
    for path, _loc, _size in candidates:
        if not include_all:
            category = classify(path)
            if category != "source":
                excluded[category] += 1
                continue
        if not include_tests and is_test_file(path):
            excluded["test"] += 1
            continue
        scored.append(path)

    # Maxima for log-scale normalisation. Computed over the files we will rank.
    recent_max = max((history.files[p].recent_commits for p in scored), default=0)
    loc_by_path = {p: loc for p, loc, _ in candidates}
    loc_max = max((loc_by_path[p] for p in scored), default=0)
    coupling_max = max((coupling.get(p, 0) for p in scored), default=0)

    def norm(value: int, top_value: int) -> float:
        if top_value <= 0 or value <= 0:
            return 0.0
        return min(1.0, math.log1p(value) / math.log1p(top_value))

    # Which files have a test that changes alongside them?
    tested: set[str] = set()
    for commit in history.commits:
        files = commit.files
        if any(is_test_file(f) for f in files):
            for f in files:
                if not is_test_file(f):
                    tested.add(f)

    hotspots: list[Hotspot] = []
    for path in scored:
        entry = history.files[path]
        loc = loc_by_path[path]
        coupled = coupling.get(path, 0)
        untested = path not in tested

        effort = effort_for(entry.recent_churn)
        risk_factors = {
            "coupling": norm(coupled, coupling_max),
            "bus_factor": 1.0 - min(entry.author_count, 3) / 3.0,
            "size": norm(loc, loc_max),
            "frequency": norm(entry.recent_commits, recent_max),
        }
        # Untested is folded into the fragility term rather than getting its own
        # slot: it raises the cost of a mistake, it does not create one.
        fragility = 0.5 * risk_factors["bus_factor"] + 0.5 * (1.0 if untested else 0.0)
        risk = 1.0 + (
            RISK_WEIGHTS["coupling"] * risk_factors["coupling"]
            + RISK_WEIGHTS["bus_factor"] * fragility
            + RISK_WEIGHTS["size"] * risk_factors["size"]
            + RISK_WEIGHTS["frequency"] * risk_factors["frequency"]
        )
        score = 100.0 * effort * risk / MAX_RISK

        reasons = _explain(entry, loc, coupled, untested, effort, risk_factors)
        hotspots.append(
            Hotspot(
                path=path,
                score=round(score, 1),
                commits=entry.commits,
                recent_commits=entry.recent_commits,
                churn=entry.recent_churn,
                loc=loc,
                coupling=coupled,
                authors=entry.author_count,
                is_tested=not untested,
                is_test=is_test_file(path),
                reasons=reasons,
            )
        )

    hotspots.sort(key=lambda h: (-h.score, h.path))
    # A file that has not moved in the window is not a hotspot; listing it at
    # 0.0 just adds noise to a list whose whole job is to be short.
    hotspots = [h for h in hotspots if h.score > 0]

    # Repo score = cost weighted by churn, i.e. "the average cost of the code
    # you actually touch", not the average of the whole tree. A thousand cold
    # files should not dilute five burning ones.
    total_churn = sum(h.churn for h in hotspots)
    if total_churn > 0:
        repo_score = sum(h.score * h.churn for h in hotspots) / total_churn
    elif hotspots:
        repo_score = sum(h.score for h in hotspots) / len(hotspots)
    else:
        repo_score = 0.0
    repo_score = round(repo_score, 1)

    # A repository with almost no history cannot tell us anything about churn;
    # ranking it would just be a file-size list wearing a confidence costume.
    warnings: list[str] = []
    if history.commit_count < 20:
        n = history.commit_count
        warnings.append(
            f"only {n} commit{'s' if n != 1 else ''} of history: "
            "the ranking mostly reflects file size, not churn"
        )
    if 0 < history.span_days < 30:
        warnings.append("history spans under a month: recency signals are meaningless")

    return Analysis(
        root=root_path,
        score=repo_score,
        grade=grade_for(repo_score),
        hotspots=hotspots[: max(top, 0)] if top else hotspots,
        file_count=len(scored),
        commit_count=history.commit_count,
        span_days=history.span_days,
        total_churn=total_churn,
        excluded=dict(excluded.most_common()),
        skipped=skipped,
        warnings=warnings,
    )


def _explain(
    entry,
    loc: int,
    coupled: int,
    untested: bool,
    effort: float,
    risk_factors: dict[str, float],
) -> list[str]:
    """Human-readable evidence, strongest signal first."""
    reasons: list[tuple[float, str]] = []

    if effort > 0.3:
        reasons.append((effort, f"{entry.recent_churn:,} lines churned recently"))
    if risk_factors["size"] > 0.5:
        reasons.append((risk_factors["size"], f"{loc:,} lines to read"))
    if coupled >= MIN_COUPLING:
        reasons.append((risk_factors["coupling"], f"changes ripple into {coupled} distant files"))
    if entry.author_count <= 2 and entry.commits >= 3:
        reasons.append((risk_factors["bus_factor"], f"only {entry.author_count} author(s)"))
    if untested and effort > 0.2:
        reasons.append((0.3, "no test changes with it"))
    if risk_factors["frequency"] > 0.6 and effort <= 0.3:
        reasons.append((risk_factors["frequency"], "touched often for very little code"))

    reasons.sort(key=lambda r: -r[0])
    return [text for _, text in reasons[:3]]
