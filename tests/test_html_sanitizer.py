from __future__ import annotations

import nh3

from lore_app.markdown_render import ALLOWED_ATTRIBUTES, ALLOWED_PROTOCOLS, ALLOWED_TAGS


def sanitize_html(raw: str) -> str:
    """Call nh3.clean with the same config used in render_page_markdown."""
    return nh3.clean(
        raw,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_PROTOCOLS,
        strip_comments=True,
        link_rel=None,
    )


def test_xss_script_tag_stripped():
    """Script tags are removed."""
    result = sanitize_html('<script>alert("xss")</script><p>safe</p>')
    assert "<script>" not in result
    assert "safe" in result


def test_xss_onclick_attr_stripped():
    """Event handler attributes are removed."""
    result = sanitize_html('<p onclick="alert(1)">text</p>')
    assert "onclick" not in result
    assert "text" in result


def test_allowed_tags_preserved():
    """Allowed tags are kept in the output."""
    result = sanitize_html("<p>hello</p><strong>world</strong>")
    assert "<p>" in result
    assert "<strong>" in result


def test_disallowed_tags_stripped():
    """Disallowed tags (like <form>) are removed, content preserved."""
    result = sanitize_html("<form>login</form><p>safe</p>")
    assert "<form>" not in result
    assert "login" in result


def test_allowed_attributes_preserved():
    """Allowed attributes are kept."""
    result = sanitize_html('<a href="https://example.com" title="link">click</a>')
    assert 'href="https://example.com"' in result
    assert 'title="link"' in result


def test_disallowed_attributes_stripped():
    """Disallowed attributes are removed."""
    result = sanitize_html('<p data-custom="bad">text</p>')
    assert "data-custom" not in result


def test_javascript_url_scheme_blocked():
    """javascript: URLs are removed."""
    result = sanitize_html('<a href="javascript:alert(1)">link</a>')
    assert "javascript" not in result.lower()


def test_allowed_url_schemes_preserved():
    """Allowed URL schemes (http, https, mailto, tel) are preserved."""
    result = sanitize_html('<a href="https://example.com">link</a>')
    assert "https://example.com" in result


def test_img_tag_preserved():
    """img tags with allowed attributes are kept."""
    result = sanitize_html('<img src="https://example.com/img.png" alt="test">')
    assert "<img" in result
    assert 'src="https://example.com/img.png"' in result


def test_global_attrs_on_all_allowed_tags():
    """class and id attributes are allowed on all allowed tags."""
    result = sanitize_html('<p class="note" id="intro">text</p>')
    assert 'class="note"' in result
    assert 'id="intro"' in result
