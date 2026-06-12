# pdf2md-claude

[![CI](https://github.com/hacker-cb/pdf2md-claude/actions/workflows/ci.yml/badge.svg)](https://github.com/hacker-cb/pdf2md-claude/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pdf2md-claude)](https://pypi.org/project/pdf2md-claude/)
[![Python](https://img.shields.io/pypi/pyversions/pdf2md-claude)](https://pypi.org/project/pdf2md-claude/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Convert PDF documents to high-quality Markdown using Claude's native PDF API.
Handles large documents via chunked conversion with context passing, deterministic
merging, and output validation. Preserves tables, formulas, figures, and document structure.

## Installation

Requires Python 3.12+.

```bash
pip install pdf2md-claude
```

### macOS (Homebrew)

Homebrew Python blocks system-wide `pip install` ([PEP 668](https://peps.python.org/pep-0668/)).
Use `pipx` to install CLI tools into isolated environments:

```bash
brew install pipx
pipx install pdf2md-claude
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
brew install uv
uv tool install pdf2md-claude
```

### Install from source

```bash
pip install git+https://github.com/hacker-cb/pdf2md-claude.git
```

### Upgrading

Use the upgrade command matching the way you installed:

```bash
pip install --upgrade pdf2md-claude                                    # pip
pipx upgrade pdf2md-claude                                             # pipx
uv tool upgrade pdf2md-claude                                          # uv
pip install --upgrade git+https://github.com/hacker-cb/pdf2md-claude.git  # from source
```

Verify with:

```bash
pdf2md-claude --version
```

See the [latest release notes](https://github.com/hacker-cb/pdf2md-claude/releases/latest)
for behaviour changes between versions — in particular, default model bumps may
affect cost and invalidate cached chunks under `.staging/`.

## Quick Start

```bash
# Set API key
export PDF2MD_CLAUDE_API_KEY="your-key-here"

# ...or skip the key and route through your local `claude` login instead
# (subscription auth via Claude Code — see "Backends" below):
#   pdf2md-claude convert document.pdf --via-claude-cli

# Convert a single PDF (output: document.md next to the PDF)
pdf2md-claude convert document.pdf

# Convert multiple PDFs
pdf2md-claude convert doc1.pdf doc2.pdf

# Convert all PDFs in a directory (shell glob)
pdf2md-claude convert *.pdf
pdf2md-claude convert docs/*.pdf

# Custom output directory
pdf2md-claude convert *.pdf -o output/

# Convert multiple PDFs in parallel (auto: one worker per document)
pdf2md-claude convert *.pdf -j

# Convert multiple PDFs in parallel (exactly 4 workers)
pdf2md-claude convert *.pdf -j 4

# Also works via python -m
python -m pdf2md_claude convert document.pdf
```

## Output

By default, Markdown files are written next to the source PDF:

| Input | Output |
|---|---|
| `docs/document.pdf` | `docs/document.md` |

With `-o DIR`, all output goes to the specified directory.

## Conversion Pipeline

Chunked conversion with context passing, deterministic merging, and validation:

1. Split PDF into disjoint chunks
2. Convert each chunk with context from the previous chunk's tail
3. Merge chunks by page markers (deterministic, no LLM)
4. Merge continued tables across page boundaries (deterministic, no LLM)
5. Regenerate complex tables with colspan/rowspan from PDF using AI (extended thinking)
6. Extract and inject images from bounding-box markers (deterministic, no LLM)
7. Strip AI-generated image descriptions (optional, `--strip-ai-descriptions`)
8. Format markdown: prettify HTML tables, normalize spacing (deterministic, no LLM)
9. Validate output (page markers, tables, heading gaps, binary sequences, fabrication detection)

## CLI Commands

The CLI uses subcommands. Run `pdf2md-claude COMMAND --help` for full options.

```
pdf2md-claude convert PDF [PDF ...]         Convert PDFs to Markdown
pdf2md-claude validate PDF [PDF ...]       Validate converted output (no API)
pdf2md-claude show-prompt [--rules FILE]    Print the system prompt
pdf2md-claude init-rules [PATH]             Generate a rules template
```

### convert options

```
  -o, --output-dir DIR   Output directory (default: same directory as each PDF)
  -j, --jobs [N]         Process documents in parallel (-j = auto, -j N = fixed)
  -v, --verbose          Enable verbose logging
  -f, --force            Force reconversion even if output exists
  --model MODEL          Claude model alias or full model ID (default: opus).
                         Aliases:
                             opus, opus-4-7 → claude-opus-4-7
                             opus-4-6       → claude-opus-4-6
                             sonnet         → claude-sonnet-4-5
                             haiku          → claude-haiku-4-5
                         Run `pdf2md-claude convert --help` for the canonical list.
  --max-pages N          Convert only first N pages (useful for debugging)
  --cache                Enable prompt caching (1h TTL, reduces re-run cost)
  --pages-per-chunk N    Pages per conversion chunk (default: 10)
  --retries N            Max retries per chunk on transient errors (default: 10)
  --rules FILE           Custom rules file (replace/append/add rules)
  --via-claude-cli       Run conversion through the local `claude` CLI (`claude -p`)
                         instead of the Anthropic API — uses your Claude Code
                         login (e.g. a subscription), no PDF2MD_CLAUDE_API_KEY needed.
                         Opt-in only (must be passed explicitly); `--cache` is a
                         no-op in this mode.
  --no-images            Skip image extraction from bounding-box markers
  --image-mode MODE      Image extraction mode (auto/snap/bbox/debug)
  --image-dpi DPI        DPI for page-region rendering (default: 600)
  --strip-ai-descriptions  Remove AI-generated image descriptions
  --no-fix-tables        Skip AI-based table regeneration (default: enabled, costs extra tokens)
  --no-format            Skip markdown formatting (default: format enabled)
  --from STEP            Skip earlier stages and start from STEP (merge = re-merge from cached chunks;
                         post-processing steps like table fixing may still call API)
```

Run without arguments to display help.

## Custom Rules

Customize the system prompt sent to Claude by creating a rules file. This lets
you replace, extend, or add conversion rules without editing source code.

**Auto-discovery**: Place a file named `.pdf2md.rules` next to your PDF and it
will be applied automatically (no `--rules` flag needed).

### Quick Start

```bash
# Generate a fully commented template
pdf2md-claude init-rules

# Edit .pdf2md.rules to your needs, then convert
pdf2md-claude convert document.pdf

# Or use an explicit rules file
pdf2md-claude convert document.pdf --rules my_rules.txt

# Preview the merged system prompt
pdf2md-claude show-prompt
pdf2md-claude show-prompt --rules my_rules.txt
```

### Directives

| Directive | Description |
|---|---|
| `@replace NAME` | Completely replace a built-in rule (or `preamble`) |
| `@append NAME` | Add text to the end of a built-in rule (or `preamble`) |
| `@add` | New rule appended after all others |
| `@add after NAME` | New rule inserted after the named rule (or `preamble`) |

**Valid names**: `preamble`, `fidelity`, `formatting`, `skip`, `headings`,
`tables`, `formulas`, `images`, `page_markers`

Lines starting with `;` are comments (stripped from rule text). Lines starting
with `#` are preserved (useful for Markdown headings inside rules).

### Example Rules File

```
; Custom rules for IEC standards
@append preamble
The source document is in Chinese. Translate all content to English.

@replace tables
**Tables**: Use Markdown pipe tables for simple tables.

@add
**Code blocks**: Preserve all code listings with fenced blocks.
```

Use `--show-prompt` to inspect the final merged prompt before converting.

## Backends

`convert` can reach Claude two ways:

| Backend | When used | Auth |
|---|---|---|
| Anthropic API (default) | `PDF2MD_CLAUDE_API_KEY` is set (legacy `ANTHROPIC_API_KEY` also honored) | API key (`x-api-key`) |
| `claude` CLI (`claude -p`) | `--via-claude-cli` passed explicitly (no silent auto-fallback) | Whatever `claude` is logged in with (e.g. a Claude subscription) |

The CLI backend shells out to a locally installed
[Claude Code](https://claude.com/claude-code) CLI in headless mode, feeding the
PDF chunks to it over `--input-format stream-json` (the CLI forwards the
`document` blocks straight to the Messages API, so output quality matches the
direct path). Differences to note:

- `--cache` has no effect — Claude Code manages prompt caching itself.
- Beta headers (e.g. the 1M-context window) are not forwarded.
- `max_tokens` follows Claude Code's per-model default, not pdf2md's.
- Token counts in the cost report come from the CLI; on a document's first
  call most input tokens show up as cache-creation tokens.
- Override the executable with `PDF2MD_CLAUDE_BIN` if `claude` isn't on PATH.

## Environment

| Variable | Description |
|---|---|
| `PDF2MD_CLAUDE_API_KEY` | Anthropic API key. Required unless using the `claude` CLI backend (`--via-claude-cli`). |
| `ANTHROPIC_API_KEY` | Deprecated fallback for `PDF2MD_CLAUDE_API_KEY` (logs a warning when used; the new variable takes precedence). |
| `PDF2MD_CLAUDE_BIN` | Name/path of the `claude` executable for the CLI backend (default: `claude`). |

## File Structure

```
pdf2md-claude/
├── pdf2md_claude/              # Main package
│   ├── __init__.py             # Package version export
│   ├── __main__.py             # python -m pdf2md_claude entry point
│   ├── claude_api.py           # Claude API client wrapper with retry and streaming
│   ├── claude_cli_api.py       # Alternative backend: runs `claude -p` (subscription auth)
│   ├── cli.py                  # CLI argument parsing and orchestration
│   ├── converter.py            # Core PDF→Markdown conversion logic
│   ├── formatter.py            # Markdown and HTML table formatter
│   ├── images.py               # Image extraction, rendering, and injection (pymupdf)
│   ├── markers.py              # Centralized marker definitions (MarkerDef)
│   ├── merger.py               # Deterministic page-marker merge + table continuation merging
│   ├── models.py               # Model configurations, pricing, usage tracking
│   ├── pipeline.py             # Single-document orchestration (convert → merge → validate → write)
│   ├── prompt.py               # Prompts for Claude PDF conversion
│   ├── rules.py                # Custom rules file parsing and prompt customization
│   ├── table_fixer.py          # AI-based table regeneration (FixTablesStep)
│   ├── validator.py            # Content validation (page markers, tables, fabrication, etc.)
│   └── workdir.py              # Chunk persistence, resume, and work directory management
├── tests/                      # Unit tests
│   ├── __init__.py
│   ├── conftest.py             # Shared test fixtures
│   ├── test_claude_api.py
│   ├── test_claude_cli_api.py
│   ├── test_cli.py
│   ├── test_converter.py
│   ├── test_formatter.py
│   ├── test_images.py
│   ├── test_markers.py
│   ├── test_models.py
│   ├── test_pipeline.py
│   ├── test_rules.py
│   ├── test_table_fixer.py
│   ├── test_table_merger.py
│   ├── test_validator.py
│   └── test_workdir.py
├── scripts/
│   └── setup-dev.sh            # Dev environment setup (.venv + hooks)
├── .githooks/
│   └── pre-commit              # Runs tests before commit
├── pyproject.toml              # Project metadata and dependencies
├── .gitignore
└── README.md
```

## Development

### Setup

One command sets up everything (.venv, dependencies, git hooks):

```bash
git clone https://github.com/hacker-cb/pdf2md-claude.git
cd pdf2md-claude
bash scripts/setup-dev.sh
```

To recreate the .venv from scratch:

```bash
bash scripts/setup-dev.sh --force
```

### Running Tests

```bash
./.venv/bin/python -m pytest tests/ -v
```

### Debugging

Enable verbose logging:

```bash
pdf2md-claude convert -v document.pdf
```

Use `--cache` to avoid re-paying for PDF content on repeated runs during
prompt/pipeline development:

```bash
pdf2md-claude convert --cache document.pdf --max-pages 5
```
