# baggage

> **Find the files that are actually costing you money.**

`baggage` reads your git history and ranks the files that hurt: the ones you
rewrite constantly, the ones that drag half the codebase along, the ones nobody
else understands and nothing tests.

One command. No config. No API keys. No LLM. Python 3.9+ and `git`.

```console
$ baggage .
baggage  D:\work\flask

   21.1 / 100   grade B   ##--------
  3,831 commits over 16.6 years
  25 files scored  (skipped 97 docs, 51 test, 42 sample, 10 ci, 10 config, 1 lockfile, 1 packaging)

  Top hotspots
  SCORE  FILE                                         COMMITS     CHURN      LOC LINKED  WHO
   30.0  src/flask/app.py                                  11       235    1,686     51   31
   18.6  src/flask/ctx.py                                   5       109      540     23    8
   14.1  src/flask/helpers.py                               5        58      679     44   22
   12.5  src/flask/sansio/app.py                            5        46    1,014     36    6
   11.4  src/flask/sessions.py                              4        42      385     24    8

  Why the top ones hurt
  src/flask/app.py
      * 1,686 lines to read
      * changes ripple into 51 distant files
      * 235 lines churned recently
```

That output is real. So are these:

| Repository | Grade | Top three |
|---|---|---|
| `expressjs/express` | **A** (13.3) | `lib/response.js`, `lib/request.js`, `lib/application.js` |
| `pallets/flask` | **B** (21.1) | `src/flask/app.py`, `src/flask/ctx.py`, `src/flask/helpers.py` |
| `psf/requests` | **B** (26.7) | `src/requests/models.py`, `src/requests/utils.py`, `src/requests/sessions.py` |
| `psf/black` | **C** (46.3) | `src/black/linegen.py`, `src/black/comments.py`, `src/black/lines.py` |

A mature, quiet project lands near A. A project under active development lands
further down. The tool does not call everything an emergency.

## Why

Writing code got cheap. Understanding it did not.

When a codebase grows faster than the number of people who can hold it in their
heads, the cost shows up as a specific set of files that every change has to
detour through. They are never the files anyone names in a retro. This tool
finds them.

## Install

```bash
pipx install baggage      # or: uvx baggage
```

Or run from source without installing:

```bash
python -m baggage .
```

## Usage

```bash
baggage [PATH]                    # rank hotspots in the terminal
baggage report [PATH] -o out.html # write a self-contained HTML report
baggage badge [PATH]              # emit an SVG badge for your README
```

| Flag | Meaning |
|---|---|
| `PATH` | Repository to analyse. Defaults to `.` |
| `--top N` | How many hotspots to show. Default `10` (`25` for reports) |
| `--since DAYS` | Recency window, counted back from the newest commit. Default `365` |
| `--json` | Machine-readable output (scan only) |
| `-o FILE` | Output path for `report` (`baggage.html`) and `badge` (`-` = stdout) |
| `--open` | Open the HTML report when it is written |
| `--include-tests` | Rank test files too |
| `--include-all` | Also rank lockfiles, CI config, docs and generated files |
| `--no-color` | Disable ANSI colour |

## Sharing the result

`baggage report` produces **one HTML file** with inline CSS and JavaScript —
no CDN, no fonts, no tracking, works offline and opens by double-click. Attach
it to a pull request, drop it in a wiki, or screenshot the chart.

```
$ baggage report . -o baggage.html --open
wrote baggage.html  (21.1/100, grade B)
```

`baggage badge` writes a shields-style SVG you can commit and reference from
any README:

```markdown
![baggage](badge.svg)
```

## How it scores

The score is **effort × risk**. Neither half can carry a file on its own.

**Effort** is how many lines actually moved inside the window:

```
effort = min(1, sqrt(recent_churn / 2000))
```

Measured in lines, not in commits, because commits are not the cost. A two-line
file touched twenty times has not cost you anything. (An early version of this
tool ranked a two-line file second overall. It counted frequency as a scoring
term rather than as a multiplier.)

The `2000` is an absolute reference, not the busiest file in the repo.
Normalising against the repo maximum made quiet projects look expensive:
express, stable for years, scored 78/100 and an F.

**Risk** is a multiplier in `[1, 2]`:

| Signal | Weight | Why |
|---|---|---|
| Coupling | 0.35 | Files that co-change from *different folders* are hidden dependencies |
| Fragility | 0.25 | Bus factor, plus whether a test moves with it |
| Size | 0.20 | A 900-line file costs more to safely edit than a 90-line one |
| Frequency | 0.20 | Constant touch means constant context switching |

A file moving together with **its own test is not coupling** — that is health,
and counting it would reward having tests.

The repository score is the **churn-weighted average** of file costs: the
average cost of the code you actually touch, not of the whole tree.

Grades: `A` under 20, `B` under 35, `C` under 50, `D` under 65, `F` above.

## What gets skipped

Churn in some files is mechanical, not cognitive. Ranking them produced
real-world nonsense — on flask, `uv.lock` took first place at 84/100 and five
of the top eight were GitHub Actions workflows.

| Category | Examples | Why |
|---|---|---|
| `lockfile` | `uv.lock`, `package-lock.json`, `go.sum` | generated |
| `ci` | `.github/workflows/*`, `Jenkinsfile` | config, not code |
| `docs` | `*.md`, `docs/**` | prose |
| `sample` | `examples/**` | not shipped |
| `packaging` | `setup.py`, `Dockerfile`, `Makefile` | metadata churn (172 commits and 43 authors in requests, for nine lines of code) |
| `config` | `*.toml`, `*.yaml`, `*.json`, dotfiles | mechanical |
| `generated` | `*.min.js`, `*.map`, `*.pb.go` | machine output |
| `vendored` | `node_modules/**`, `vendor/**` | not yours |
| `test` | `tests/**`, `*_test.go` | churns constantly; cleaning them is rarely the win |

The count of skipped files is always printed, so nothing disappears silently.
`--include-all` turns the filter off.

## When not to trust it

The tool warns rather than pretending:

- Fewer than 20 commits → the ranking is mostly file size
- History spanning under a month → recency signals are meaningless

Check the `!` lines and the `reliable` field in `--json`.

Other honest limits:

- **Line counting is not parsing.** It cannot tell a comment from a statement,
  or generated code from hand-written code inside a real source file.
- **Coupling is evidence, not proof.** Two files changing together is a hint
  that one change forced the other.
- **Renames are followed** via git's `-M`, but deleted files are dropped — a
  hotspot you cannot open is not actionable.

## Development

```bash
python -m unittest discover -s tests -v
```

`tests/test_ranking.py` builds a synthetic repository with a planted answer key
— two money pits, one decoy touched constantly but only ever changing two lines,
and cold filler — and asserts the ranking survives. Any change to the cost model
has to pass it.

## License

MIT
