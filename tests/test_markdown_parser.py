#!/usr/bin/env python3
"""Verify that _markdown_parser_factory suppresses link/image/autolink tokens.

In Textual 2.1.2, ``Style.from_meta({"@click": link_action})`` (called when
``link_open`` / ``image`` tokens are present) crashes Python's marshal with
``ValueError: bad marshal data (unknown type code)`` when the href/alt string
contains characters such as ``:`` (e.g. ``/tmp/a:1`` from ``[x](/tmp/a:1)``).

This test ensures ``_markdown_parser_factory`` from ``cli.jsonl_viewer`` never
produces those tokens, regardless of content.
"""

import sys
import os

# Ensure project root is on sys.path so imports resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from markdown_it import MarkdownIt

from cli.jsonl_viewer import _markdown_parser_factory


def _collect_inline_types(md: MarkdownIt, text: str) -> list[str]:
    """Return all inline token types produced for *text*."""
    types: list[str] = []
    for block in md.parse(text):
        if block.children:
            for child in block.children:
                types.append(child.type)
    return types


def test_explicit_link_is_plain_text():
    """[x](/tmp/a:1) must NOT produce link_open/link_close."""
    md = _markdown_parser_factory()
    types = _collect_inline_types(md, "[x](/tmp/a:1)")
    assert "link_open" not in types, f"link_open present: {types}"
    assert "link_close" not in types, f"link_close present: {types}"
    # The entire thing should be a single text token
    assert "text" in types, f"no text token: {types}"
    print(f"  [x](/tmp/a:1) -> types={types}  OK")


def test_image_is_plain_text():
    """![alt](/tmp/a.png) must NOT produce an image token."""
    md = _markdown_parser_factory()
    types = _collect_inline_types(md, "![alt](/tmp/a.png)")
    assert "image" not in types, f"image present: {types}"
    assert "text" in types, f"no text token: {types}"
    print(f"  ![alt](/tmp/a.png) -> types={types}  OK")


def test_autolink_is_plain_text():
    """<https://example.com> must NOT produce link_open."""
    md = _markdown_parser_factory()
    types = _collect_inline_types(md, "<https://example.com>")
    assert "link_open" not in types, f"link_open present: {types}"
    assert "text" in types, f"no text token: {types}"
    print(f"  <https://example.com> -> types={types}  OK")


def test_bold_still_works():
    """**bold** must still produce strong_open/strong_close."""
    md = _markdown_parser_factory()
    types = _collect_inline_types(md, "**bold**")
    assert "strong_open" in types, f"strong_open missing: {types}"
    assert "strong_close" in types, f"strong_close missing: {types}"
    print(f"  **bold** -> types={types}  OK")


def test_italic_still_works():
    """*italic* must still produce emphasis tokens."""
    md = _markdown_parser_factory()
    types = _collect_inline_types(md, "*italic*")
    assert "text" in types, f"text missing: {types}"
    print(f"  *italic* -> types={types}  OK")


def test_inline_code_still_works():
    """`code` must still produce code_inline."""
    md = _markdown_parser_factory()
    types = _collect_inline_types(md, "some `code`")
    assert "code_inline" in types, f"code_inline missing: {types}"
    print(f"  some `code` -> types={types}  OK")


def test_heading_still_works():
    """# Heading must still produce heading_open block."""
    md = _markdown_parser_factory()
    block_types = [t.type for t in md.parse("# Heading")]
    assert "heading_open" in block_types, f"heading_open missing: {block_types}"
    print(f"  # Heading -> types={block_types}  OK")


def test_lists_still_work():
    """Bullet lists must still produce list_item_open."""
    md = _markdown_parser_factory()
    block_types = [t.type for t in md.parse("- a\n- b")]
    assert "list_item_open" in block_types, f"list_item_open missing: {block_types}"
    print(f"  - a\\n- b -> types={block_types}  OK")


def test_url_without_markdown_syntax_is_not_linkified():
    """A bare URL without []() should not be linkified (linkify=False)."""
    md = _markdown_parser_factory()
    types = _collect_inline_types(md, "https://example.com/path")
    # No linkify means bare URL stays as plain text
    assert "link_open" not in types, f"link_open present with linkify=False: {types}"
    assert "text" in types, f"text missing: {types}"
    print(f"  https://example.com/path -> types={types}  OK")


def test_link_and_image_mixed_with_other_content():
    """A paragraph mixing links, images, and bold must handle all correctly."""
    md = _markdown_parser_factory()
    text = "**bold** [link](/tmp/x) `code`"
    types = _collect_inline_types(md, text)
    assert "link_open" not in types, f"link_open still present: {types}"
    assert "strong_open" in types, f"strong_open missing: {types}"
    assert "code_inline" in types, f"code_inline missing: {types}"
    print(f"  mixed content -> types={types}  OK")


def main():
    print("=== markdown-it parser regression tests ===")
    print()
    tests = [
        ("explicit link → plain text", test_explicit_link_is_plain_text),
        ("image → plain text", test_image_is_plain_text),
        ("autolink → plain text", test_autolink_is_plain_text),
        ("bold unaffected", test_bold_still_works),
        ("italic unaffected", test_italic_still_works),
        ("inline code unaffected", test_inline_code_still_works),
        ("heading unaffected", test_heading_still_works),
        ("lists unaffected", test_lists_still_work),
        ("bare URL not linkified", test_url_without_markdown_syntax_is_not_linkified),
        ("mixed content", test_link_and_image_mixed_with_other_content),
    ]
    failures = 0
    for label, fn in tests:
        try:
            fn()
        except AssertionError as e:
            print(f"  FAIL [{label}]: {e}")
            failures += 1

    print()
    if failures:
        print(f"FAILED: {failures} test(s)")
        sys.exit(1)
    else:
        print(f"All {len(tests)} tests PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
