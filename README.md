# course_content_organization

Turn a course's teaching materials into **one continuous, self-contained document**.

Feed it slides, handouts, plain text, Word files and classroom recordings; get back a book-shaped
document you can study from without opening the original slides again.

> Status: **v0.1 alpha**. Runs end to end on real course material: 114-slide decks, 48-page PDFs,
> 245-slide three-chapter sets. Content quality depends on the writing stage, which is model work
> routed through gap files — see [Status](#status).

---

## Why another notes tool

Most "AI notes" tools flatten your materials into one long text and ask a model to summarize it. That
loses the two things you need later: **where every statement came from**, and **which parts changed
when you add lecture 7**.

This project keeps a structured intermediate representation — blocks with locators (`slide 12`,
`00:13:40`, `page 37`) — and every stage above it is built on that representation. Consequences:

- the pipeline runs **deterministically and offline**; no model is required to build a document
- adding a lecture **appends** to the book instead of rewriting it
- every claim can be traced back to its evidence (kept internally; whether it is printed is a setting)
- quality gates check structure, not vibes

## Quick start

```bash
uv venv .venv && uv pip install -e .
# formulas are typeset with KaTeX, installed once for the checkout:
(cd tools && npm install)
mkdir my-course && cd my-course
study init --course "示例课程"
cp ~/slides/lecture-06.pptx inputs/
$EDITOR study.config.yaml     # declare the lecture
study auto            # build + review tasks + status; exit 5 means "needs you"
$EDITOR .study/out/book.md
```

Minimal config:

```yaml
schema_version: 1
course:
  name: 示例课程
  lang: zh
  lectures:
    - number: 6                 # use `number:`, not `no:` — YAML 1.1 reads `no` as false
      title: 示例专题
      sources: [inputs/lecture-06.pptx]
render:
  profile: learn                # learn | review
  source_references: none       # none | footnote | inline
```

## Commands

| Command | Purpose |
|---|---|
| `study init` | create a workspace |
| `study ingest` | parse `inputs/` into blocks (`.study/sources/*.jsonl`) |
| `study probe` | report what the sources can support (slides, notes, audio) |
| `study auto` | the whole cycle in one command, then a list of what is still missing |
| `study build` | ingest → units → outline → render → gates |
| `study render` | re-render only; `--format md\|txt\|tex\|pdf\|docx\|pptx\|all` |
| `study outline show` | inspect / hand-edit the skeleton (`.study/outline.json`) |
| `study review list` | read gate findings |
| `study review prepare` | write per-claim review tasks for a model or a person |
| `study review prepare-l3` | write adversarial review tasks (needs a different model or clean context) |
| `study review apply` | read verdicts, recompute `origin`, emit findings |
| `study review stats` | `unsupported_ratio` and `derived_ratio` |
| `study explain <unit>` | request an explanation patch for a section you did not follow |
| `study explain --apply` / `--remove <unit>` | turn an answer into a patch / delete it |
| `study figure add <id> --file spec.mmd` | register a figure (extracted / redrawn / synthesized) |
| `study figure request <unit> --desc "..."` | ask for a diagram the source does not provide |
| `study figure list` | list figures with origin and format |
| `study align [--tier]` | align speech to slides (A/B/C), write `.study/aligned/` |
| `study doctor` | environment and workspace self-check |
| `study clean` | remove derived data (never user data) |

Exit codes: `0` ok, `1` gate failure, `2` usage, `3` missing input, `4` internal, `5` needs a human
decision.

## What is a workspace

```
my-course/
├── study.config.yaml
├── inputs/                     # raw materials, read-only
└── .study/
    ├── sources/                # blocks with locators
    ├── assets/                 # images, content-hashed
    ├── knowledge/units/        # one file per knowledge point
    ├── outline.json            # the book's skeleton (hand-editable)
    ├── patches/                # your notes; never mixed into units
    ├── review/                 # gate findings, locator audit
    └── out/book.md             # the document
```

`.study/sources/`, `assets/` and `out/` are derived — delete them freely. `patches/`, `review/` and
`outline.json` are yours — nothing ever overwrites them.

## Design

The architecture is specified before the code, deliberately:

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | principles, stages, data contracts |
| [docs/unit-schema.md](docs/unit-schema.md) | knowledge point schema and revision protocol |
| [docs/outline-generation.md](docs/outline-generation.md) | sectioning algorithm, numbering, three-way merge |
| [docs/ingest.md](docs/ingest.md) | adapters, block id stability, image degradation |
| [docs/alignment.md](docs/alignment.md) | audio ↔ slide alignment (designed, not implemented) |
| [docs/review-protocol.md](docs/review-protocol.md) | findings, L2/L3 review, sampling budgets |
| [docs/rendering.md](docs/rendering.md) | templates, views, overlays, determinism |
| [docs/glossary.md](docs/glossary.md) | term table and consistency checks |
| [docs/cli.md](docs/cli.md) | full command set and config |
| [docs/state.md](docs/state.md) | caching, invalidation, user-data protection |
| [docs/validation.md](docs/validation.md) | what real course material actually broke |

## Status

| Area | State |
|---|---|
| PPTX ingest (text, tables, images, OMML→LaTeX subset, speaker notes, hidden slides, heading inference) | implemented |
| TXT / Markdown / YAML student notes | implemented |
| PDF (text layer, columns, header/footer, TOC, paragraph merging, scanned detection) | implemented |
| DOCX (heading styles, lists, tables, inline images) | implemented |
| Audio: `.srt`/`.vtt` sidecar (deterministic) or local `faster-whisper` (`[audio]` extra) | implemented |
| Alignment: A (anchors) / B (monotone DP) / C (skip), speech merged as 讲解 | implemented |
| Deterministic draft units | implemented — verbatim text only, marked `draft: true` |
| Numbering-based sectioning | implemented |
| Continuous Markdown render, `learn` view | implemented |
| Gates: source references, dangling refs, coverage, path leak, numbering, figures | implemented |
| Figures: extracted / redrawn (mermaid, dot, svg, tikz) / synthesized, provenance gates | implemented |
| Template manifest (`templates/<profile>/<format>/manifest.yaml`) | implemented — heading formats and section order are data |
| Output: `md` / `txt` / `tex` / `pdf` / `docx` / `pptx` (`--format all`) | implemented |
| LaTeX layout as data (`templates/<profile>/tex/manifest.yaml`) | implemented — replace the preamble to restyle |
| Full per-layer markdown template files | not implemented |
| `review` view | implemented — section order per profile, plus a 重点清单 and a 公式速查 appendix |
| Gap protocol: register / fill / apply (`image_semantics`, `page_text`, `figure_request`) | implemented |
| L2 review (claim entailment: prepare → fill → apply, `unsupported_ratio`) | implemented |
| L3 adversarial review (contradiction / missing step / wrong arithmetic) | implemented |
| On-demand explanation patches (`explain` → `--apply` → overlay, removable) | implemented |

Draft units carry `draft: true` and often an `auto-…` id; the `auto_unit_id` gate reports them as
`major` so a raw build is never mistaken for a finished book.

## Tests

```bash
python -m pytest
```

The suite builds a synthetic deck in a temporary workspace and asserts the promises from
[docs/architecture.md §17.1](docs/architecture.md): build without a model, no source references in the
body, byte-identical re-render, appending a lecture without rewriting earlier text, and deck-numbered
sections.

## License

MIT
