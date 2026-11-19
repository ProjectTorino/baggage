"""Render baggage's terminal output as a terminal-style SVG.

Why SVG instead of a GIF: it is a few kilobytes, it scales crisply on retina
displays, the text stays selectable and readable by screen readers, and there
is no binary blob in the repository.

Usage:
    python tools/make_demo_svg.py <repo-path> <out.svg> [--top N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from baggage.cost import analyze_baggage  # noqa: E402
from baggage.html import GRADE_COLORS  # noqa: E402
from baggage.report import render_text  # noqa: E402

PADDING = 18
TITLEBAR = 32
FONT_SIZE = 13
CHAR_W = 7.85
LINE_H = 20
BG = "#0d1117"
BAR = "#161b22"
BORDER = "#30363d"
TEXT = "#e6edf3"
MUTED = "#8b949e"


def build_svg(text: str, *, grade_color: str, title: str = "baggage") -> str:
    lines = text.splitlines()
    width = int(max(len(line) for line in lines) * CHAR_W + PADDING * 2)
    height = TITLEBAR + PADDING + len(lines) * LINE_H + PADDING

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="baggage terminal output">',
        f"<title>{escape(title)}</title>",
        f'<rect width="{width}" height="{height}" rx="10" fill="{BG}" '
        f'stroke="{BORDER}"/>',
        f'<path d="M0 10a10 10 0 0 1 10-10h{width - 20}a10 10 0 0 1 10 10v{TITLEBAR - 10}'
        f'H0z" fill="{BAR}"/>',
    ]

    for index, color in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        parts.append(
            f'<circle cx="{PADDING + index * 18}" cy="{TITLEBAR / 2}" r="5.5" fill="{color}"/>'
        )
    parts.append(
        f'<text x="{width / 2:.0f}" y="{TITLEBAR / 2 + 4}" fill="{MUTED}" font-size="11" '
        f'text-anchor="middle" font-family="ui-monospace, Menlo, Consolas, monospace">'
        f"{escape(Path(title).name)}</text>"
    )

    y = TITLEBAR + PADDING + FONT_SIZE
    for index, line in enumerate(lines):
        if not line.strip():
            y += LINE_H
            continue
        if "/ 100" in line:
            color = grade_color
        elif index == 0:
            color = MUTED
        else:
            color = TEXT
        parts.append(
            f'<text x="{PADDING}" y="{y}" fill="{color}" font-size="{FONT_SIZE}" '
            f'xml:space="preserve" '
            f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">'
            f"{escape(line)}</text>"
        )
        y += LINE_H

    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo")
    parser.add_argument("output")
    parser.add_argument("--top", type=int, default=6)
    parser.add_argument("--title", default=None)
    parser.add_argument(
        "--display-path",
        default=None,
        help="path to show instead of the real one, e.g. /home/you/flask",
    )
    args = parser.parse_args()

    analysis = analyze_baggage(args.repo, top=args.top)
    text = render_text(analysis, top=args.top, use_color=False)

    # The real path is a local absolute path; never ship that in a screenshot.
    # Default avoids a leading slash on purpose: MSYS rewrites POSIX-looking
    # arguments into Windows paths before the process ever sees them.
    shown = args.display_path or f"~/{analysis.root.name}"
    text = text.replace(str(analysis.root), shown)

    title = args.title or analysis.root.name or "baggage"

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        build_svg(text, grade_color=GRADE_COLORS.get(analysis.grade, MUTED), title=title),
        encoding="utf-8",
    )
    print(f"wrote {out} ({analysis.score:.1f}/100, grade {analysis.grade})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
