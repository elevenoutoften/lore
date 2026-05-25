from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from html import escape
from typing import Iterable
from urllib.parse import urldefrag, urlparse

import nh3
from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token

from .repository import InvalidPageId, normalize_page_id
from .schemas import PageDetail, RenderedLink, TocEntry

WIKI_LINK_PATTERN = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
FENCE_PATTERN = re.compile(r"^\s*(```|~~~)")
HEADING_PATTERN = re.compile(r"^#\s+(.+?)\s*#*\s*$")
SLUG_WORD_PATTERN = re.compile(r"[a-z0-9]+")
DANGEROUS_HTML_BLOCK_PATTERN = re.compile(
    r"<(script|style|iframe|object|embed)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)

ALLOWED_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "del",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "img",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}

_ALLOW_GLOBAL_ATTRS = {"class", "id"}

ALLOWED_ATTRIBUTES = {
    "a": _ALLOW_GLOBAL_ATTRS | {"href", "rel", "title", "target"},
    "blockquote": _ALLOW_GLOBAL_ATTRS,
    "br": _ALLOW_GLOBAL_ATTRS,
    "code": _ALLOW_GLOBAL_ATTRS | {"class"},
    "del": _ALLOW_GLOBAL_ATTRS,
    "div": _ALLOW_GLOBAL_ATTRS,
    "em": _ALLOW_GLOBAL_ATTRS,
    "h1": _ALLOW_GLOBAL_ATTRS,
    "h2": _ALLOW_GLOBAL_ATTRS,
    "h3": _ALLOW_GLOBAL_ATTRS,
    "h4": _ALLOW_GLOBAL_ATTRS,
    "h5": _ALLOW_GLOBAL_ATTRS,
    "h6": _ALLOW_GLOBAL_ATTRS,
    "hr": _ALLOW_GLOBAL_ATTRS,
    "img": _ALLOW_GLOBAL_ATTRS | {"alt", "loading", "src", "title"},
    "li": _ALLOW_GLOBAL_ATTRS,
    "ol": _ALLOW_GLOBAL_ATTRS,
    "p": _ALLOW_GLOBAL_ATTRS,
    "pre": _ALLOW_GLOBAL_ATTRS,
    "span": _ALLOW_GLOBAL_ATTRS,
    "strong": _ALLOW_GLOBAL_ATTRS,
    "table": _ALLOW_GLOBAL_ATTRS,
    "tbody": _ALLOW_GLOBAL_ATTRS,
    "td": _ALLOW_GLOBAL_ATTRS | {"align"},
    "th": _ALLOW_GLOBAL_ATTRS | {"align"},
    "thead": _ALLOW_GLOBAL_ATTRS,
    "tr": _ALLOW_GLOBAL_ATTRS,
    "ul": _ALLOW_GLOBAL_ATTRS,
}

ALLOWED_PROTOCOLS = {"http", "https", "mailto", "tel"}


@dataclass
class RenderContext:
    current_page_id: str
    page_ids: set[str]
    toc: list[TocEntry] = field(default_factory=list)
    links: list[RenderedLink] = field(default_factory=list)
    slug_counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RenderedMarkdown:
    html: str
    toc: list[TocEntry]
    links: list[RenderedLink]
    missing_links: list[RenderedLink]


class LoreRenderer(RendererHTML):
    def renderToken(self, tokens: list[Token], idx: int, options: dict, env: dict) -> str:
        token = tokens[idx]
        if token.type == "heading_open":
            context = get_context(env)
            level = int(token.tag[1])
            title = heading_title(tokens, idx)
            slug = unique_slug(title, context.slug_counts)
            token.attrSet("id", slug)
            if level <= 3:
                context.toc.append(TocEntry(level=level, id=slug, title=title))
            return f'<{token.tag}{self.renderAttrs(token)}><a class="heading-anchor" href="#{slug}" aria-label="Link to this section">#</a>'
        return super().renderToken(tokens, idx, options, env)


def render_page_markdown(page: PageDetail, page_ids: Iterable[str]) -> RenderedMarkdown:
    body = strip_duplicate_title(page.body, page.title)
    body = DANGEROUS_HTML_BLOCK_PATTERN.sub("", body)
    body = replace_wiki_links(body)
    context = RenderContext(current_page_id=page.id, page_ids=set(page_ids))
    md = MarkdownIt(
        "commonmark",
        {
            "breaks": False,
            "html": False,
            "linkify": True,
            "typographer": False,
        },
        renderer_cls=LoreRenderer,
    ).enable(["table", "strikethrough", "linkify"])

    env = {"lore_context": context}
    tokens = md.parse(body, env)
    rewrite_links(tokens, context)
    rendered = md.renderer.render(tokens, md.options, env)
    safe_html = nh3.clean(
        rendered,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_PROTOCOLS,
        strip_comments=True,
        link_rel=None,
    )
    safe_html = wrap_tables(safe_html)
    missing_links = [link for link in context.links if not link.external and not link.exists]
    return RenderedMarkdown(html=safe_html, toc=context.toc, links=context.links, missing_links=missing_links)


def strip_duplicate_title(body: str, title: str) -> str:
    lines = body.lstrip("\n").splitlines()
    if not lines:
        return body
    match = HEADING_PATTERN.match(lines[0])
    if not match or normalize_title(match.group(1)) != normalize_title(title):
        return body
    index = 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    return "\n".join(lines[index:]).lstrip("\n")


def normalize_title(value: str) -> str:
    return " ".join(value.casefold().split())


def replace_wiki_links(markdown: str) -> str:
    lines = markdown.splitlines(keepends=True)
    in_fence = False
    output: list[str] = []
    for line in lines:
        if FENCE_PATTERN.match(line):
            in_fence = not in_fence
            output.append(line)
            continue
        if in_fence:
            output.append(line)
            continue
        output.append(WIKI_LINK_PATTERN.sub(wiki_link_to_markdown, line))
    return "".join(output)


def wiki_link_to_markdown(match: re.Match[str]) -> str:
    inner = match.group(1).strip()
    if "|" in inner:
        label, target = [part.strip() for part in inner.split("|", 1)]
    else:
        label = inner.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").title()
        target = inner
    if not label or not target:
        return escape(match.group(0))
    parsed = urlparse(target)
    if not parsed.scheme and not parsed.netloc and not target.startswith(("/", ".", "#")):
        target = f"/{target}"
    return f"[{escape_markdown_label(label)}]({target})"


def escape_markdown_label(label: str) -> str:
    return label.replace("[", "\\[").replace("]", "\\]")


def rewrite_links(tokens: list[Token], context: RenderContext) -> None:
    for token in tokens:
        children = token.children or []
        for index, child in enumerate(children):
            if child.type != "link_open":
                continue
            href = child.attrGet("href") or ""
            label = link_label(children, index)
            rewritten = rewrite_href(href, label, context)
            if rewritten is None:
                child.attrSet("rel", "noopener noreferrer")
                context.links.append(RenderedLink(href=href, label=label, page_id=None, exists=True, external=True))
                continue
            child.attrSet("href", rewritten.href)
            child.attrSet("class", "wiki-link" if rewritten.exists else "wiki-link wiki-link--missing")
            if not rewritten.exists:
                child.attrSet("title", f"Lore page not found: {rewritten.page_id}")
            context.links.append(rewritten)


def rewrite_href(href: str, label: str | None, context: RenderContext) -> RenderedLink | None:
    parsed = urlparse(href)
    if parsed.scheme or parsed.netloc or href.startswith("#"):
        return None

    path, fragment = urldefrag(href)
    if not path:
        return None

    candidate = resolve_page_id(path, context.current_page_id)
    if candidate is None:
        return None

    exists = candidate in context.page_ids
    rewritten = f"/{candidate}"
    if fragment:
        rewritten = f"{rewritten}#{fragment}"
    return RenderedLink(href=rewritten, label=label, page_id=candidate, exists=exists)


def resolve_page_id(raw_path: str, current_page_id: str) -> str | None:
    normalized_path = raw_path.strip().replace("\\", "/")
    if not normalized_path:
        return None
    if normalized_path.startswith("/"):
        candidate = normalized_path.strip("/")
    else:
        base_dir = posixpath.dirname(current_page_id)
        candidate = posixpath.normpath(posixpath.join(base_dir, normalized_path))
    if candidate in {".", ""} or candidate.startswith("../"):
        return None
    if candidate.endswith(".md"):
        candidate = candidate[:-3]
    try:
        return normalize_page_id(candidate)
    except InvalidPageId:
        return None


def link_label(tokens: list[Token], open_index: int) -> str | None:
    pieces: list[str] = []
    depth = 0
    for token in tokens[open_index + 1 :]:
        if token.type == "link_open":
            depth += 1
        elif token.type == "link_close":
            if depth == 0:
                break
            depth -= 1
        elif token.type == "text" or token.type == "code_inline":
            pieces.append(token.content)
    label = " ".join("".join(pieces).split())
    return label or None


def heading_title(tokens: list[Token], heading_index: int) -> str:
    if heading_index + 1 >= len(tokens):
        return "section"
    inline = tokens[heading_index + 1]
    if inline.type != "inline":
        return "section"
    text = collect_text(inline.children or []) or inline.content
    return " ".join(text.split()) or "section"


def collect_text(tokens: list[Token]) -> str:
    pieces: list[str] = []
    for token in tokens:
        if token.type in {"text", "code_inline"}:
            pieces.append(token.content)
        elif token.children:
            pieces.append(collect_text(token.children))
    return "".join(pieces)


def unique_slug(title: str, counts: dict[str, int]) -> str:
    words = SLUG_WORD_PATTERN.findall(title.casefold())
    base = "-".join(words) or "section"
    count = counts.get(base, 0)
    counts[base] = count + 1
    return base if count == 0 else f"{base}-{count + 1}"


def get_context(env: dict) -> RenderContext:
    context = env.get("lore_context")
    if not isinstance(context, RenderContext):
        raise RuntimeError("Lore render context is missing.")
    return context


def wrap_tables(html: str) -> str:
    return html.replace("<table>", '<div class="table-scroll"><table>').replace("</table>", "</table></div>")
