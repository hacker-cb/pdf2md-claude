"""Custom rules file support for pdf2md-claude.

Parses user rules files that customize the system prompt via four
directives: ``@replace``, ``@append``, ``@add``, and ``@add after``.
Builds a custom system prompt by merging user overrides with the
default rule registry.

Also provides :func:`generate_rules_template` to scaffold a fully
commented template file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pdf2md_claude.prompt import (
    SYSTEM_PROMPT,
    _DEFAULT_REGISTRY,
    _PREAMBLE_BODY,
    build_system_prompt,
)

AUTO_RULES_FILENAME = ".pdf2md.rules"
"""Filename auto-discovered next to each PDF (when no explicit ``--rules``)."""

_PREAMBLE_NAME = "preamble"
"""Directive name that targets the preamble (not a numbered rule)."""

_VALID_NAMES: frozenset[str] = frozenset(
    {_PREAMBLE_NAME} | {name for name, _ in _DEFAULT_REGISTRY}
)
"""Accepted names for ``@replace`` / ``@append`` / ``@add after`` directives."""

_DIRECTIVE_RE = re.compile(
    r"^@(replace|append|add(?:\s+after)?)\s*(\S+)?\s*$"
)
"""Regex that splits a rules file on directive lines."""

_COMMENT_PREFIX = ";"
"""Lines starting with this character are stripped from rule text."""


@dataclass
class RulesFileResult:
    """Parsed contents of a rules file, ready for prompt assembly."""

    replacements: dict[str, str] = field(default_factory=dict)
    """``@replace NAME`` → full replacement text."""

    appends: dict[str, str] = field(default_factory=dict)
    """``@append NAME`` → text appended after the existing rule."""

    insertions: list[tuple[str, str]] = field(default_factory=list)
    """``@add after NAME`` → ``(after_name, text)`` pairs, in file order."""

    extras: list[str] = field(default_factory=list)
    """``@add`` (no name) → new rules appended at the end."""


def _strip_rule_text(lines: list[str]) -> str:
    """Strip comment lines and boundary blank lines from *lines*.

    - Lines starting with ``;`` are removed.
    - Leading and trailing blank lines are removed.
    - Internal blank lines are preserved.

    Returns the cleaned text (may be empty).
    """
    cleaned = [ln for ln in lines if not ln.lstrip().startswith(_COMMENT_PREFIX)]

    # Strip leading blank lines.
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    # Strip trailing blank lines.
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()

    return "\n".join(cleaned)


def parse_rules_file(path: Path) -> RulesFileResult:
    """Parse a rules file into structured overrides.

    Parameters
    ----------
    path:
        Path to the rules file.

    Returns
    -------
    RulesFileResult
        Parsed directives and their associated rule texts.

    Raises
    ------
    ValueError
        On syntax errors: unknown name, missing name for ``@replace``/
        ``@append``, ``@add`` with a name but without ``after``, duplicate
        directives, mixed ``@replace``+``@append`` for the same name, or
        empty rule text.
    """
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()

    result = RulesFileResult()

    # Collect (directive_type, name, line_index) tuples.
    sections: list[tuple[str, str | None, int]] = []
    for idx, line in enumerate(lines):
        m = _DIRECTIVE_RE.match(line.strip())
        if m:
            dtype = m.group(1).strip()  # "replace", "append", "add", "add after"
            name = m.group(2)
            sections.append((dtype, name, idx))

    # Nothing to parse — return empty result.
    if not sections:
        return result

    # Track seen names for duplicate detection.
    seen_replace: set[str] = set()
    seen_append: set[str] = set()

    for i, (dtype, name, start) in enumerate(sections):
        # Determine text range: from line after directive to next directive (or EOF).
        text_start = start + 1
        text_end = sections[i + 1][2] if i + 1 < len(sections) else len(lines)
        text = _strip_rule_text(lines[text_start:text_end])

        # ---- Validate directive + name ----
        if dtype in ("replace", "append"):
            if not name:
                raise ValueError(
                    f"@{dtype} requires a rule name "
                    f"(line {start + 1})"
                )
            if name not in _VALID_NAMES:
                raise ValueError(
                    f"Unknown rule name {name!r} in @{dtype} "
                    f"(line {start + 1}). "
                    f"Valid names: {', '.join(sorted(_VALID_NAMES))}"
                )

        if dtype == "replace":
            if name in seen_replace:
                raise ValueError(
                    f"Duplicate @replace {name} (line {start + 1})"
                )
            if name in seen_append:
                raise ValueError(
                    f"Cannot @replace {name} — already have "
                    f"@append {name}"
                )
            seen_replace.add(name)

        elif dtype == "append":
            if name in seen_append:
                raise ValueError(
                    f"Duplicate @append {name} (line {start + 1})"
                )
            if name in seen_replace:
                raise ValueError(
                    f"Cannot @append {name} — already have "
                    f"@replace {name}"
                )
            seen_append.add(name)

        elif dtype == "add after":
            if not name:
                raise ValueError(
                    f"@add after requires a rule name "
                    f"(line {start + 1})"
                )
            if name not in _VALID_NAMES:
                raise ValueError(
                    f"Unknown rule name {name!r} in @add after "
                    f"(line {start + 1}). "
                    f"Valid names: {', '.join(sorted(_VALID_NAMES))}"
                )

        elif dtype == "add":
            # Bare @add — must NOT have a name (use @add after for that).
            if name:
                raise ValueError(
                    f"@add does not accept a name ({name!r}). "
                    f"Did you mean '@add after {name}'? "
                    f"(line {start + 1})"
                )

        # ---- Validate text is non-empty ----
        if not text:
            label = f"@{dtype}" + (f" {name}" if name else "")
            raise ValueError(
                f"{label} has no rule text (line {start + 1})"
            )

        # ---- Store ----
        if dtype == "replace":
            assert name is not None
            result.replacements[name] = text
        elif dtype == "append":
            assert name is not None
            result.appends[name] = text
        elif dtype == "add after":
            assert name is not None
            result.insertions.append((name, text))
        elif dtype == "add":
            result.extras.append(text)

    return result


def build_custom_system_prompt(parsed: RulesFileResult) -> str:
    """Build a system prompt by merging *parsed* overrides with defaults.

    Parameters
    ----------
    parsed:
        Result from :func:`parse_rules_file`.

    Returns
    -------
    str
        The assembled system prompt string.
    """
    # Start with a mutable copy of the default registry.
    # Each entry is (name | None, text).  name=None for injected extras.
    rules: list[tuple[str | None, str]] = list(_DEFAULT_REGISTRY)

    # Work on copies so we can pop without mutating the original.
    replacements = dict(parsed.replacements)
    appends = dict(parsed.appends)

    # ---- Handle preamble (special, not in the numbered rules) ----
    preamble_body = _PREAMBLE_BODY

    if _PREAMBLE_NAME in replacements:
        preamble_body = replacements.pop(_PREAMBLE_NAME)

    if _PREAMBLE_NAME in appends:
        preamble_body += "\n" + appends.pop(_PREAMBLE_NAME)

    # @add after preamble → insert as rule 1 (index 0 in rules list).
    preamble_insertions = [
        (after, text)
        for after, text in parsed.insertions
        if after == _PREAMBLE_NAME
    ]
    non_preamble_insertions = [
        (after, text)
        for after, text in parsed.insertions
        if after != _PREAMBLE_NAME
    ]
    # Insert in reverse so that file-order is preserved at index 0.
    for _, text in reversed(preamble_insertions):
        rules.insert(0, (None, text))

    # ---- Apply replacements ----
    for name, new_text in replacements.items():
        for idx, (rname, _) in enumerate(rules):
            if rname == name:
                rules[idx] = (rname, new_text)
                break

    # ---- Apply appends ----
    for name, extra_text in appends.items():
        for idx, (rname, existing) in enumerate(rules):
            if rname == name:
                rules[idx] = (rname, existing + "\n" + extra_text)
                break

    # ---- Apply insertions (after named rule) ----
    offset = 0
    for after_name, text in non_preamble_insertions:
        for idx, (rname, _) in enumerate(rules):
            if rname == after_name:
                rules.insert(idx + 1 + offset, (None, text))
                offset += 1
                break

    # ---- Append extras at end ----
    for text in parsed.extras:
        rules.append((None, text))

    # Extract text list and build the prompt.
    texts = [text for _, text in rules]
    return build_system_prompt(texts, preamble_body=preamble_body)


def generate_rules_template(path: Path) -> None:
    """Write a fully commented rules template to *path*.

    The generated file documents all directives and contains every
    built-in rule (preamble + 8 numbered rules) as commented-out
    ``@replace`` blocks.  Loading the file produces zero changes.

    Parameters
    ----------
    path:
        Destination file path.
    """
    lines: list[str] = []

    # Header.
    lines.append("; pdf2md-claude custom rules file")
    lines.append(";")
    lines.append("; Directives:")
    lines.append(";   @replace NAME   -- completely replace a built-in rule or preamble")
    lines.append(";   @append NAME    -- add text to end of a built-in rule or preamble")
    lines.append(";   @add            -- new rule appended after all others")
    lines.append(";   @add after NAME -- new rule inserted after named rule (or preamble)")
    lines.append(";")
    lines.append("; Valid names: " + ", ".join(
        sorted(_VALID_NAMES)
    ))
    lines.append(";")
    lines.append("; Lines starting with ; are comments (stripped from rule text).")
    lines.append("; Lines starting with # are preserved (useful for markdown headings).")
    lines.append(";")
    lines.append("; Auto-discovery: name this file .pdf2md.rules and place it next to")
    lines.append("; your PDF — it will be applied automatically (no --rules needed).")
    lines.append(";")
    lines.append("")

    # Preamble.
    lines.append("; @replace preamble")
    for pline in _PREAMBLE_BODY.splitlines():
        lines.append(f"; {pline}" if pline else ";")
    lines.append("")

    # Each registry rule.
    for name, text in _DEFAULT_REGISTRY:
        lines.append(f"; @replace {name}")
        for rline in text.splitlines():
            lines.append(f"; {rline}" if rline else ";")
        lines.append("")

    # Example @add.
    lines.append("; @add")
    lines.append("; **Custom rule**: Your additional rule text here.")
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
