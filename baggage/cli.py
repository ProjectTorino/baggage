"""Command-line interface for baggage."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from baggage.cost import Analysis, analyze_baggage
from baggage.gitlog import GitError
from baggage.report import render_json, render_text


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", default=".", help="repository to analyse")
    parser.add_argument(
        "--since",
        type=int,
        default=365,
        metavar="DAYS",
        help="recency window in days, counted back from the newest commit",
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="rank test files too (they churn, but cleaning them is rarely the win)",
    )
    parser.add_argument(
        "--include-all",
        action="store_true",
        help="also rank lockfiles, CI config, docs and generated files",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="baggage",
        description="Find the files that are actually costing you money.",
        epilog="Example: baggage . --top 15   |   baggage report . -o report.html",
    )
    sub = parser.add_subparsers(dest="command")

    scan = sub.add_parser("scan", help="rank the most expensive files (default)")
    _add_common(scan)
    scan.add_argument("--top", type=int, default=10, help="how many hotspots to show")
    scan.add_argument("--json", action="store_true", dest="as_json", help="machine-readable output")
    scan.add_argument("--no-color", action="store_true", help="disable ANSI colour")

    report = sub.add_parser("report", help="write a self-contained HTML report")
    _add_common(report)
    report.add_argument("--top", type=int, default=25, help="how many hotspots to include")
    report.add_argument(
        "-o",
        "--output",
        default="baggage.html",
        metavar="FILE",
        help="where to write the report (default: baggage.html)",
    )
    report.add_argument("--open", action="store_true", help="open the report when done")

    badge = sub.add_parser("badge", help="emit an SVG badge for your README")
    _add_common(badge)
    badge.add_argument(
        "-o",
        "--output",
        default="-",
        metavar="FILE",
        help="write to a file instead of stdout ('-' means stdout)",
    )

    return parser


def _analyse(args: argparse.Namespace) -> Analysis:
    return analyze_baggage(
        Path(args.path),
        recent_days=args.since,
        include_tests=args.include_tests,
        include_all=args.include_all,
        top=max(getattr(args, "top", 25), 1),
    )


def _check_path(path: str) -> int | None:
    root = Path(path)
    if not root.exists():
        print(f"baggage: no such path: {root}", file=sys.stderr)
        return 2
    return None


def _run_scan(args: argparse.Namespace) -> int:
    if args.top < 0:
        print("baggage: --top must be non-negative", file=sys.stderr)
        return 2
    code = _check_path(args.path)
    if code is not None:
        return code

    analysis = _analyse(args)
    if args.as_json:
        print(render_json(analysis))
    else:
        print(render_text(analysis, top=args.top, use_color=not args.no_color))
    return 0


def _run_report(args: argparse.Namespace) -> int:
    from baggage.html import render_html

    code = _check_path(args.path)
    if code is not None:
        return code

    analysis = _analyse(args)
    destination = Path(args.output)
    destination.write_text(render_html(analysis, top=args.top), encoding="utf-8")
    print(f"wrote {destination}  ({analysis.score:.1f}/100, grade {analysis.grade})")

    if args.open:
        import webbrowser

        webbrowser.open(destination.resolve().as_uri())
    return 0


def _run_badge(args: argparse.Namespace) -> int:
    from baggage.html import render_badge_svg

    code = _check_path(args.path)
    if code is not None:
        return code

    analysis = _analyse(args)
    svg = render_badge_svg(analysis)
    if args.output == "-":
        print(svg)
    else:
        Path(args.output).write_text(svg, encoding="utf-8")
        print(f"wrote {args.output}  ({analysis.score:.1f}/100, grade {analysis.grade})")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--version" in argv:
        from baggage import __version__

        print(f"baggage {__version__}")
        return 0

    # Allow `baggage [path]` to mean `baggage scan [path]`.
    commands = {"scan", "report", "badge", "-h", "--help"}
    if not argv or (argv[0] not in commands and not argv[0].startswith("-")):
        argv = ["scan", *argv]

    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {"scan": _run_scan, "report": _run_report, "badge": _run_badge}
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    if args.since < 0:
        print("baggage: --since must be non-negative", file=sys.stderr)
        return 2

    try:
        return handler(args)
    except GitError as exc:
        print(f"baggage: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"baggage: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
