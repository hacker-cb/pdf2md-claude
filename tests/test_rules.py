"""Unit tests for rules file parsing, custom prompt building, and template generation."""

from pathlib import Path

import pytest

from pdf2md_claude.prompt import SYSTEM_PROMPT, _DEFAULT_REGISTRY, _PREAMBLE_BODY
from pdf2md_claude.rules import (
    AUTO_RULES_FILENAME,
    RulesFileResult,
    _VALID_NAMES,
    build_custom_system_prompt,
    generate_rules_template,
    parse_rules_file,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_rules(tmp_path: Path, content: str, name: str = "test.rules") -> Path:
    """Write *content* to a rules file and return the path."""
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# TestParseRulesFile
# ---------------------------------------------------------------------------


class TestParseRulesFile:
    """Tests for ``parse_rules_file()``."""

    def test_replace(self, tmp_path: Path) -> None:
        p = _write_rules(tmp_path, "@replace tables\nCustom table rule.")
        result = parse_rules_file(p)
        assert "tables" in result.replacements
        assert result.replacements["tables"] == "Custom table rule."

    def test_append(self, tmp_path: Path) -> None:
        p = _write_rules(tmp_path, "@append images\nExtra image guidance.")
        result = parse_rules_file(p)
        assert "images" in result.appends
        assert result.appends["images"] == "Extra image guidance."

    def test_add(self, tmp_path: Path) -> None:
        p = _write_rules(tmp_path, "@add\n**Custom rule**: Do something new.")
        result = parse_rules_file(p)
        assert len(result.extras) == 1
        assert result.extras[0] == "**Custom rule**: Do something new."

    def test_add_after(self, tmp_path: Path) -> None:
        p = _write_rules(tmp_path, "@add after headings\nNew sub-rule.")
        result = parse_rules_file(p)
        assert len(result.insertions) == 1
        assert result.insertions[0] == ("headings", "New sub-rule.")

    def test_mixed(self, tmp_path: Path) -> None:
        content = (
            "@replace tables\nCustom tables.\n\n"
            "@append images\nMore image info.\n\n"
            "@add after headings\nInserted rule.\n\n"
            "@add\nBrand new rule."
        )
        p = _write_rules(tmp_path, content)
        result = parse_rules_file(p)
        assert "tables" in result.replacements
        assert "images" in result.appends
        assert len(result.insertions) == 1
        assert result.insertions[0][0] == "headings"
        assert len(result.extras) == 1

    def test_semicolon_comments_stripped(self, tmp_path: Path) -> None:
        content = "@replace tables\n; This is a comment\nActual rule text."
        p = _write_rules(tmp_path, content)
        result = parse_rules_file(p)
        assert result.replacements["tables"] == "Actual rule text."

    def test_hash_lines_preserved(self, tmp_path: Path) -> None:
        content = "@replace tables\n# Heading\nSome text."
        p = _write_rules(tmp_path, content)
        result = parse_rules_file(p)
        assert result.replacements["tables"] == "# Heading\nSome text."

    def test_header_ignored(self, tmp_path: Path) -> None:
        """Lines before the first directive are ignored."""
        content = "; This is just a comment header\n; Another comment"
        p = _write_rules(tmp_path, content)
        result = parse_rules_file(p)
        assert not result.replacements
        assert not result.appends
        assert not result.insertions
        assert not result.extras

    def test_leading_trailing_blanks_stripped(self, tmp_path: Path) -> None:
        content = "@replace tables\n\n\nRule text.\n\n\n"
        p = _write_rules(tmp_path, content)
        result = parse_rules_file(p)
        assert result.replacements["tables"] == "Rule text."

    def test_internal_blank_lines_preserved(self, tmp_path: Path) -> None:
        content = "@replace tables\nFirst paragraph.\n\nSecond paragraph."
        p = _write_rules(tmp_path, content)
        result = parse_rules_file(p)
        assert result.replacements["tables"] == "First paragraph.\n\nSecond paragraph."

    def test_empty_text_raises(self, tmp_path: Path) -> None:
        content = "@replace tables\n; Only a comment, no real text"
        p = _write_rules(tmp_path, content)
        with pytest.raises(ValueError, match="no rule text"):
            parse_rules_file(p)

    def test_unknown_name_raises(self, tmp_path: Path) -> None:
        content = "@replace bogus\nSome text."
        p = _write_rules(tmp_path, content)
        with pytest.raises(ValueError, match="Unknown rule name"):
            parse_rules_file(p)

    def test_duplicate_replace_raises(self, tmp_path: Path) -> None:
        content = "@replace tables\nFirst.\n\n@replace tables\nSecond."
        p = _write_rules(tmp_path, content)
        with pytest.raises(ValueError, match="Duplicate @replace tables"):
            parse_rules_file(p)

    def test_replace_and_append_same_name_raises(self, tmp_path: Path) -> None:
        content = "@replace tables\nReplaced.\n\n@append tables\nAppended."
        p = _write_rules(tmp_path, content)
        with pytest.raises(ValueError, match="Cannot @append tables"):
            parse_rules_file(p)

    def test_append_and_replace_same_name_raises(self, tmp_path: Path) -> None:
        content = "@append tables\nAppended.\n\n@replace tables\nReplaced."
        p = _write_rules(tmp_path, content)
        with pytest.raises(ValueError, match="Cannot @replace tables"):
            parse_rules_file(p)

    def test_add_with_name_no_after_raises(self, tmp_path: Path) -> None:
        content = "@add tables\nSome text."
        p = _write_rules(tmp_path, content)
        with pytest.raises(ValueError, match="@add does not accept a name"):
            parse_rules_file(p)

    def test_replace_without_name_raises(self, tmp_path: Path) -> None:
        content = "@replace\nSome text."
        p = _write_rules(tmp_path, content)
        with pytest.raises(ValueError, match="@replace requires a rule name"):
            parse_rules_file(p)

    def test_append_without_name_raises(self, tmp_path: Path) -> None:
        content = "@append\nSome text."
        p = _write_rules(tmp_path, content)
        with pytest.raises(ValueError, match="@append requires a rule name"):
            parse_rules_file(p)


# ---------------------------------------------------------------------------
# TestBuildCustomPrompt
# ---------------------------------------------------------------------------


class TestBuildCustomPrompt:
    """Tests for ``build_custom_system_prompt()``."""

    def test_replace_swaps_rule(self) -> None:
        parsed = RulesFileResult(replacements={"tables": "Custom table rule."})
        prompt = build_custom_system_prompt(parsed)
        assert "Custom table rule." in prompt
        # The original tables rule text should be gone.
        assert "ALWAYS use HTML `<table>` format" not in prompt

    def test_append_extends_rule(self) -> None:
        parsed = RulesFileResult(appends={"images": "Also handle SVGs."})
        prompt = build_custom_system_prompt(parsed)
        # Both original and appended text should appear.
        assert "Also handle SVGs." in prompt
        assert "IMAGE_RECT" in prompt  # from original images rule

    def test_add_after_inserts(self) -> None:
        parsed = RulesFileResult(insertions=[("headings", "Inserted after headings.")])
        prompt = build_custom_system_prompt(parsed)
        assert "Inserted after headings." in prompt
        # The inserted rule should appear between headings and tables in numbering.
        headings_pos = prompt.index("**Headings**")
        inserted_pos = prompt.index("Inserted after headings.")
        tables_pos = prompt.index("**Tables**")
        assert headings_pos < inserted_pos < tables_pos

    def test_add_at_end(self) -> None:
        parsed = RulesFileResult(extras=["**New rule**: Final extra."])
        prompt = build_custom_system_prompt(parsed)
        assert "**New rule**: Final extra." in prompt
        # Should be at the end (after the last default rule).
        output_pos = prompt.index("**Output**")
        extra_pos = prompt.index("**New rule**: Final extra.")
        assert extra_pos > output_pos

    def test_empty_result_matches_default(self) -> None:
        prompt = build_custom_system_prompt(RulesFileResult())
        assert prompt == SYSTEM_PROMPT

    def test_multiple_add_after_same_name(self) -> None:
        parsed = RulesFileResult(insertions=[
            ("headings", "Insert A."),
            ("headings", "Insert B."),
        ])
        prompt = build_custom_system_prompt(parsed)
        assert "Insert A." in prompt
        assert "Insert B." in prompt
        # Both should appear after headings.
        headings_pos = prompt.index("**Headings**")
        a_pos = prompt.index("Insert A.")
        b_pos = prompt.index("Insert B.")
        assert headings_pos < a_pos < b_pos

    def test_replace_preamble(self) -> None:
        parsed = RulesFileResult(replacements={"preamble": "Custom preamble body."})
        prompt = build_custom_system_prompt(parsed)
        assert "Custom preamble body." in prompt
        assert _PREAMBLE_BODY not in prompt
        # Closing line should still be present.
        assert "Follow these rules strictly:" in prompt

    def test_append_preamble(self) -> None:
        parsed = RulesFileResult(appends={"preamble": "Additional context."})
        prompt = build_custom_system_prompt(parsed)
        assert _PREAMBLE_BODY in prompt
        assert "Additional context." in prompt
        # Appended text should appear before "Follow these rules strictly:"
        appended_pos = prompt.index("Additional context.")
        closing_pos = prompt.index("Follow these rules strictly:")
        assert appended_pos < closing_pos

    def test_add_after_preamble(self) -> None:
        parsed = RulesFileResult(insertions=[("preamble", "New rule one.")])
        prompt = build_custom_system_prompt(parsed)
        assert "New rule one." in prompt
        # Should be rule 1 (before the default fidelity rule).
        assert "1. New rule one." in prompt
        # Fidelity should now be rule 2.
        assert "2. **Content fidelity**" in prompt


# ---------------------------------------------------------------------------
# TestGenerateTemplate
# ---------------------------------------------------------------------------


class TestGenerateTemplate:
    """Tests for ``generate_rules_template()``."""

    def test_roundtrip(self, tmp_path: Path) -> None:
        """Generated template parses to an empty RulesFileResult."""
        p = tmp_path / "template.rules"
        generate_rules_template(p)
        result = parse_rules_file(p)
        assert not result.replacements
        assert not result.appends
        assert not result.insertions
        assert not result.extras

    def test_all_rule_names_present(self, tmp_path: Path) -> None:
        """Template mentions all valid rule names including 'preamble'."""
        p = tmp_path / "template.rules"
        generate_rules_template(p)
        content = p.read_text(encoding="utf-8")
        for name in _VALID_NAMES:
            assert name in content, f"Rule name {name!r} not found in template"

    def test_all_rule_texts_present(self, tmp_path: Path) -> None:
        """Template contains the text of every default rule (commented)."""
        p = tmp_path / "template.rules"
        generate_rules_template(p)
        content = p.read_text(encoding="utf-8")
        # Preamble body should appear.
        assert _PREAMBLE_BODY.splitlines()[0] in content
        # Each registry rule's first non-empty line should appear.
        for name, text in _DEFAULT_REGISTRY:
            first_line = text.splitlines()[0]
            assert first_line in content, (
                f"Rule {name!r} first line not in template"
            )

    def test_auto_rules_filename(self) -> None:
        """``AUTO_RULES_FILENAME`` is the expected value."""
        assert AUTO_RULES_FILENAME == ".pdf2md.rules"
