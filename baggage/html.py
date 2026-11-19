"""Render an analysis as one self-contained, offline HTML file.

No CDN, no fonts, no JavaScript framework. One file you can email, attach to a
PR, or screenshot for a README. That shareability is the whole point: a
terminal table stays in your terminal.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone

from baggage.cost import Analysis

GRADE_COLORS = {
    "A": "#2ea043",
    "B": "#3fb950",
    "C": "#d29922",
    "D": "#f0883e",
    "F": "#f85149",
}

_CSS = """
:root {
  --bg: #0d1117;
  --panel: #161b22;
  --border: #30363d;
  --text: #e6edf3;
  --muted: #8b949e;
  --accent: #58a6ff;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 40px 24px 80px;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.5 ui-sans-serif, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
.wrap { max-width: 980px; margin: 0 auto; }
.brand { font-size: 13px; letter-spacing: .18em; text-transform: uppercase; color: var(--muted); }
h1 { font-size: 22px; margin: 6px 0 2px; font-weight: 600; word-break: break-all; }
.sub { color: var(--muted); font-size: 13px; }
.card {
  background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
  padding: 20px 24px; margin-top: 24px;
}
.score-row { display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }
.score-num { font-size: 46px; font-weight: 700; line-height: 1; }
.score-max { color: var(--muted); font-size: 15px; }
.grade {
  border-radius: 8px; padding: 6px 14px; font-weight: 700; font-size: 20px;
  color: #0d1117;
}
.meter { flex: 1; min-width: 180px; height: 10px; background: #21262d; border-radius: 999px; overflow: hidden; }
.meter span { display: block; height: 100%; border-radius: 999px; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 16px; margin-top: 22px; }
.stat-k { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .06em; }
.stat-v { font-size: 17px; font-weight: 600; }
h2 { font-size: 15px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); margin: 0 0 16px; font-weight: 600; }
.bars { display: flex; flex-direction: column; gap: 8px; }
.bar-row { display: grid; grid-template-columns: 240px 1fr 52px; gap: 12px; align-items: center; }
.bar-path { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar-track { height: 18px; background: #21262d; border-radius: 4px; overflow: hidden; }
.bar-fill { height: 100%; border-radius: 4px; }
.bar-val { text-align: right; font-variant-numeric: tabular-nums; font-size: 12.5px; color: var(--muted); }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th, td { padding: 7px 10px; text-align: right; border-bottom: 1px solid var(--border); }
th:first-child, td:first-child { text-align: left; }
th { color: var(--muted); font-weight: 600; text-transform: uppercase; font-size: 11px; letter-spacing: .05em; cursor: pointer; user-select: none; }
tbody tr:hover { background: #1c2129; }
td.mono, th.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.num { font-variant-numeric: tabular-nums; }
.why { margin-top: 8px; color: var(--muted); }
.why li { margin: 2px 0; }
.warn { border-left: 3px solid #d29922; padding: 10px 14px; background: #1c1a13; border-radius: 6px; margin-top: 12px; color: #e3b341; }
.chip { display: inline-block; padding: 1px 7px; border-radius: 999px; background: #21262d; color: var(--muted); font-size: 11px; margin-right: 6px; }
footer { margin-top: 36px; color: #484f58; font-size: 12px; }
"""

_JS = """
document.querySelectorAll('th[data-sort]').forEach(function (th, index) {
  var asc = false;
  th.addEventListener('click', function () {
    var tbody = th.closest('table').querySelector('tbody');
    var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
    rows.sort(function (a, b) {
      var av = a.children[index].dataset.v || a.children[index].textContent;
      var bv = b.children[index].dataset.v || b.children[index].textContent;
      var an = parseFloat(av), bn = parseFloat(bv);
      var cmp = (isNaN(an) || isNaN(bn)) ? av.localeCompare(bv) : an - bn;
      return asc ? cmp : -cmp;
    });
    asc = !asc;
    rows.forEach(function (r) { tbody.appendChild(r); });
  });
});
"""


def _esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def _hue_for(score: float) -> str:
    """Green through red, so the chart reads without a legend."""
    if score >= 65:
        return GRADE_COLORS["F"]
    if score >= 50:
        return GRADE_COLORS["D"]
    if score >= 35:
        return GRADE_COLORS["C"]
    if score >= 20:
        return GRADE_COLORS["B"]
    return GRADE_COLORS["A"]


def render_html(analysis: Analysis, *, top: int = 25, generated_at: str | None = None) -> str:
    """Return a complete standalone HTML document."""
    stamp = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    grade_color = GRADE_COLORS.get(analysis.grade, "#8b949e")
    spots = analysis.hotspots[:top]
    peak = max((s.score for s in spots), default=0.0) or 1.0

    parts: list[str] = []
    add = parts.append

    add("<!DOCTYPE html>")
    add('<html lang="en"><head><meta charset="utf-8">')
    add('<meta name="viewport" content="width=device-width, initial-scale=1">')
    add(f"<title>baggage · {_esc(analysis.root.name or analysis.root)}</title>")
    add(f"<style>{_CSS}</style></head><body><div class=\"wrap\">")

    add('<div class="brand">baggage</div>')
    add(f"<h1>{_esc(analysis.root)}</h1>")
    span = f"{analysis.span_days / 365.0:.1f} years" if analysis.span_days else "unknown span"
    add(
        f'<div class="sub">{analysis.commit_count:,} commits over {span} · '
        f"{analysis.file_count:,} source files scored</div>"
    )

    # --- score card --------------------------------------------------------
    add('<div class="card">')
    add('<div class="score-row">')
    add(f'<div class="score-num" style="color:{grade_color}">{analysis.score:.1f}</div>')
    add('<div class="score-max">/ 100</div>')
    add(f'<div class="grade" style="background:{grade_color}">{_esc(analysis.grade)}</div>')
    width = min(max(analysis.score, 0.0), 100.0)
    add(
        f'<div class="meter"><span style="width:{width:.1f}%;'
        f'background:{grade_color}"></span></div>'
    )
    add("</div>")

    add('<div class="stats">')
    for label, value in (
        ("files scored", f"{analysis.file_count:,}"),
        ("commits", f"{analysis.commit_count:,}"),
        ("lines churned", f"{analysis.total_churn:,}"),
        ("skipped", f"{analysis.excluded_total:,}"),
    ):
        add(f'<div><div class="stat-k">{label}</div><div class="stat-v">{value}</div></div>')
    add("</div>")

    if analysis.excluded:
        chips = "".join(
            f'<span class="chip">{_esc(cat)} {count}</span>'
            for cat, count in analysis.excluded.items()
        )
        add(f'<div class="stats" style="grid-template-columns:1fr"><div>{chips}</div></div>')

    for warning in analysis.warnings:
        add(f'<div class="warn">{_esc(warning)}</div>')
    add("</div>")

    if not spots:
        add('<div class="card"><h2>No hotspots</h2>')
        add('<p class="sub">Nothing moved in the window. Either the repository is '
            "very quiet or the history is too short.</p></div>")
        add("</div></body></html>")
        return "\n".join(parts)

    # --- bar chart ---------------------------------------------------------
    add('<div class="card"><h2>Top hotspots</h2><div class="bars">')
    for spot in spots:
        pct = max(2.0, spot.score / peak * 100.0)
        add('<div class="bar-row">')
        add(f'<div class="bar-path" title="{_esc(spot.path)}">{_esc(spot.path)}</div>')
        add(
            f'<div class="bar-track"><div class="bar-fill" '
            f'style="width:{pct:.1f}%;background:{_hue_for(spot.score)}"></div></div>'
        )
        add(f'<div class="bar-val">{spot.score:.1f}</div>')
        add("</div>")
    add("</div></div>")

    # --- table -------------------------------------------------------------
    add('<div class="card"><h2>All hotspots</h2><table>')
    add("<thead><tr>")
    for label, key in (
        ("File", "path"),
        ("Score", "score"),
        ("Commits", "commits"),
        ("Churn", "churn"),
        ("Lines", "loc"),
        ("Linked", "coupling"),
        ("Authors", "authors"),
    ):
        cls = "mono" if key == "path" else ""
        add(f'<th class="{cls}" data-sort="{key}">{label}</th>')
    add("</tr></thead><tbody>")
    for spot in spots:
        add("<tr>")
        add(f'<td class="mono" title="{_esc(spot.path)}">{_esc(spot.path)}</td>')
        add(f'<td class="num" style="color:{_hue_for(spot.score)}" data-v="{spot.score}">'
            f"{spot.score:.1f}</td>")
        add(f'<td class="num" data-v="{spot.recent_commits}">{spot.recent_commits:,}</td>')
        add(f'<td class="num" data-v="{spot.churn}">{spot.churn:,}</td>')
        add(f'<td class="num" data-v="{spot.loc}">{spot.loc:,}</td>')
        add(f'<td class="num" data-v="{spot.coupling}">{spot.coupling:,}</td>')
        add(f'<td class="num" data-v="{spot.authors}">{spot.authors:,}</td>')
        add("</tr>")
    add("</tbody></table></div>")

    # --- why ---------------------------------------------------------------
    add('<div class="card"><h2>Why the top ones hurt</h2>')
    for spot in spots[:3]:
        add(f'<div class="mono" style="margin-top:10px"><strong>{_esc(spot.path)}</strong></div>')
        add('<ul class="why">')
        for reason in spot.reasons or ["highest cost in a quiet repository"]:
            add(f"<li>{_esc(reason)}</li>")
        add("</ul>")
    add("</div>")

    add(f"<footer>generated by baggage · {_esc(stamp)}</footer>")
    add(f"<script>{_JS}</script>")
    add("</div></body></html>")
    return "\n".join(parts)


def render_badge_svg(analysis: Analysis, *, label: str = "baggage") -> str:
    """Return a shields-style SVG badge, e.g. ``baggage | 21.1 B``."""
    value = f"{analysis.score:.1f} {analysis.grade}"
    color = GRADE_COLORS.get(analysis.grade, "#8b949e")
    label_width = 7 * len(label) + 10
    value_width = 7 * len(value) + 10
    total = label_width + value_width
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" '
        f'role="img" aria-label="{label}: {value}">'
        f'<title>{label}: {value}</title>'
        f'<linearGradient id="s" x2="0" y2="100%">'
        f'<stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        f'<stop offset="1" stop-opacity=".1"/></linearGradient>'
        f'<rect width="{label_width}" height="20" fill="#555"/>'
        f'<rect x="{label_width}" width="{value_width}" height="20" fill="{color}"/>'
        f'<rect width="{total}" height="20" fill="url(#s)"/>'
        f'<g fill="#fff" text-anchor="middle" '
        f'font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">'
        f'<text x="{label_width / 2:.0f}" y="14">{html.escape(label)}</text>'
        f'<text x="{label_width + value_width / 2:.0f}" y="14">{html.escape(value)}</text>'
        f"</g></svg>"
    )
