"""Shared test fixtures and helpers for pdf2md-claude tests."""

from __future__ import annotations

from pdf2md_claude.markers import PAGE_BEGIN, PAGE_END


def make_page(page_num: int, content: str = "") -> str:
    """Build a single page block with BEGIN/END markers.

    Args:
        page_num: Page number (1-indexed).
        content: Markdown content for the page body (may be empty).

    Returns:
        A string like ``<!-- PDF_PAGE_BEGIN 5 -->\\ncontent\\n<!-- PDF_PAGE_END 5 -->``.
    """
    body = f"\n{content}\n" if content else "\n"
    return f"{PAGE_BEGIN.format(page_num)}{body}{PAGE_END.format(page_num)}"


def make_pages(page_contents: dict[int, str]) -> str:
    """Build markdown with specific content placed on specific pages.

    Args:
        page_contents: Mapping of page number to body content.

    Returns:
        Newline-joined page blocks in page-number order.
    """
    return "\n".join(
        make_page(page, content)
        for page, content in sorted(page_contents.items())
    )


def wrap_pages(content: str, start: int = 1, end: int = 1) -> str:
    """Wrap content in page markers for a page range.

    All *content* is placed inside the first page's markers.  Subsequent
    pages in the range are empty (markers only).

    Args:
        content: Markdown to place on the first page.
        start: First page number.
        end: Last page number (inclusive).
    """
    parts: list[str] = []
    for p in range(start, end + 1):
        if p == start:
            parts.append(make_page(p, content))
        else:
            parts.append(make_page(p))
    return "\n".join(parts)
