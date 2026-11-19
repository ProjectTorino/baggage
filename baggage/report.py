"""Render an analysis for humans."""

from __future__ import annotations

import json
import os
import sys

from baggage.cost import Analysis, Hotspot

_GRADE_COLOR = {"A": "32", "B": "36", "C": "33", "D": "31", "F": "1;31"}

_UNICODE_GLYPHS = {"full": "█", "empty": "░", "dot": "·", "ellipsis": "…"}
_ASCII_GLYPHS = {"full": "#", "empty": "-", "dot": "*", "ellipsis": "~"}


def glyphs_for(stream=None) -> dict[str, str]:
    """Pick block-drawing characters the terminal can actually encode.

    Windows consoles default to GBK/cp1252 and raise UnicodeEncodeError on
    ``█``, which would crash the CLI on first run. Nobody reinstalls after that.
    """
    stream = stream or sys.stdout
    encoding = getattr(stream, "encoding", "") or "utf-8"
    try:
        "".join(_UNICODE_GLYPHS.values()).encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return _ASCII_GLYPHS
    return _UNICODE_GLYPHS


def color_enabled(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return bool(getattr(stream, "isatty", lambda: False)())


def _paint(text: str, code: str, enabled: bool) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if enabled else text


def _bar(score: float, glyphs: dict[str, str], width: int = 10) -> str:
    filled = int(round(min(max(score, 0.0), 100.0) / 100.0 * width))
    return glyphs["full"] * filled + glyphs["empty"] * (width - filled)


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}" + ("" if n == 1 else "s")


def render_text(analysis: Analysis, *, top: int = 10, use_color: bool | None = None) -> str:
    if use_color is None:
        use_color = color_enabled()
    glyphs = glyphs_for()

    grade_color = _GRADE_COLOR.get(analysis.grade, "0")
    lines: list[str] = []
    add = lines.append

    add(_paint("baggage", "1", use_color) + f"  {analysis.root}")
    add("")

    score_line = (
        f"  {_paint(f'{analysis.score:5.1f}', grade_color, use_color)} / 100"
        f"   grade {_paint(analysis.grade, grade_color, use_color)}"
        f"   {_bar(analysis.score, glyphs)}"
    )
    add(score_line)

    span = f"{analysis.span_days / 365.0:.1f} years" if analysis.span_days else "unknown span"
    add(f"  {_plural(analysis.commit_count, 'commit')} over {span}")
    note = f"  {_plural(analysis.file_count, 'file')} scored"
    if analysis.excluded:
        parts = ", ".join(f"{n} {category}" for category, n in analysis.excluded.items())
        note += f"  (skipped {parts})"
    add(note)

    for warning in analysis.warnings:
        add(f"  {_paint('!', '33', use_color)} {warning}")

    if not analysis.hotspots:
        add("")
        add("  No hotspots: not enough history to rank anything.")
        return "\n".join(lines)

    add("")
    add(_paint("  Top hotspots", "1", use_color))
    add(f"  {'SCORE':>5}  {'FILE':<44} {'COMMITS':>7} {'CHURN':>9} {'LOC':>8} {'LINKED':>6} {'WHO':>4}")

    for spot in analysis.hotspots[:top]:
        path = _shorten(spot.path, 44, glyphs)
        add(
            f"  {spot.score:5.1f}  {path:<44} {spot.recent_commits:>7} "
            f"{spot.churn:>9,} {spot.loc:>8,} {spot.coupling:>6} {spot.authors:>4}"
        )

    add("")
    add(_paint("  Why the top ones hurt", "1", use_color))
    for spot in analysis.hotspots[: min(3, len(analysis.hotspots))]:
        add(f"  {spot.path}")
        if spot.reasons:
            for reason in spot.reasons:
                add(f"      {glyphs['dot']} {reason}")
        else:
            add(f"      {glyphs['dot']} low overall signal, but the highest in a quiet repo")

    return "\n".join(lines)


def _shorten(path: str, width: int, glyphs: dict[str, str]) -> str:
    if len(path) <= width:
        return path
    return glyphs["ellipsis"] + path[-(width - 1) :]


def hotspot_to_dict(spot: Hotspot) -> dict:
    return {
        "path": spot.path,
        "score": spot.score,
        "commits": spot.commits,
        "recent_commits": spot.recent_commits,
        "churn": spot.churn,
        "loc": spot.loc,
        "coupled_files": spot.coupling,
        "authors": spot.authors,
        "has_test": spot.is_tested,
        "is_test": spot.is_test,
        "reasons": spot.reasons,
    }


def render_json(analysis: Analysis) -> str:
    payload = {
        "root": str(analysis.root),
        "score": analysis.score,
        "grade": analysis.grade,
        "commit_count": analysis.commit_count,
        "span_days": analysis.span_days,
        "files_scored": analysis.file_count,
        "tests_excluded": analysis.excluded.get("test", 0),
        "excluded": analysis.excluded,
        "reliable": analysis.is_reliable,
        "warnings": analysis.warnings,
        "hotspots": [hotspot_to_dict(h) for h in analysis.hotspots],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)
