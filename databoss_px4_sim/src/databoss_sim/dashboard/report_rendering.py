"""Render a comparison's report.md to HTML for the dashboard's Report tab.

9/34 real comparisons have no report.md at all (older/legacy folders) -
callers must treat None as a normal case, not an error. Two rewrites are
applied to the raw markdown before conversion, both confirmed necessary
by reading every real report.md in the repo:

1. Local absolute filesystem links to a run
   ([text](/opt/databoss_px4_sim/experiments/runs/<id>)) become the
   dashboard route ([text](/runs/<id>)) - the rendered HTML is injected
   into the SPA at /comparisons/<id>, where a raw filesystem path is
   useless to a browser.
2. Relative image references (![alt](plots/foo.png),
   ![alt](camera_samples/x/frame.jpg)) become absolute /artifacts URLs -
   same class of bug already fixed once this session for the
   terrain-generator proxy: a relative path resolves against whatever
   route served the page, not against report.md's real location on disk.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path

import markdown

from databoss_sim.dashboard.config import PROJECT_ROOT

_RUN_LINK_PATTERN = re.compile(re.escape(f"]({PROJECT_ROOT}/experiments/runs/") + r"([^)]+)\)")
_RELATIVE_IMAGE_PATTERN = re.compile(r"!\[([^\]]*)\]\((plots|camera_samples)/([^)]+)\)")
_ALLOWED_TAGS = {
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "pre",
    "code",
    "em",
    "strong",
    "blockquote",
    "hr",
    "br",
    "a",
    "img",
    "details",
    "summary",
    "del",
}
_VOID_TAGS = {"br", "hr", "img"}
_DROP_CONTENT_TAGS = {"script", "style"}
_ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
}
_URL_ATTRIBUTES = {"href", "src"}


def _is_safe_url(value: str) -> bool:
    return value.startswith(("http://", "https://")) or (value.startswith("/") and not value.startswith("//"))


class _HTMLSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._discard_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _DROP_CONTENT_TAGS:
            self._discard_depth += 1
            return
        if self._discard_depth or tag not in _ALLOWED_TAGS:
            return

        attr_text = self._sanitize_attrs(tag, attrs)
        self._parts.append(f"<{tag}{attr_text}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _DROP_CONTENT_TAGS and self._discard_depth:
            self._discard_depth -= 1
            return
        if self._discard_depth or tag not in _ALLOWED_TAGS or tag in _VOID_TAGS:
            return

        self._parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self._discard_depth:
            self._parts.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if not self._discard_depth:
            self._parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self._discard_depth:
            self._parts.append(f"&#{name};")

    def get_html(self) -> str:
        return "".join(self._parts)

    def _sanitize_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        allowed = _ALLOWED_ATTRIBUTES.get(tag, set())
        kept = []
        for name, value in attrs:
            name = name.lower()
            if name not in allowed or value is None:
                continue
            if name in _URL_ATTRIBUTES and not _is_safe_url(value):
                continue
            kept.append(f' {name}="{html.escape(value, quote=True)}"')
        return "".join(kept)


def _sanitize_html(html_text: str) -> str:
    parser = _HTMLSanitizer()
    parser.feed(html_text)
    parser.close()
    return parser.get_html()


def render_report_html(comparison_dir: Path, comparison_id: str) -> str | None:
    report_path = comparison_dir / "report.md"
    if not report_path.is_file():
        return None

    text = report_path.read_text()
    text = _RUN_LINK_PATTERN.sub(lambda m: f"](/runs/{m.group(1)})", text)
    text = _RELATIVE_IMAGE_PATTERN.sub(
        lambda m: f"![{m.group(1)}](/artifacts/comparisons/{comparison_id}/{m.group(2)}/{m.group(3)})",
        text,
    )
    html_text = markdown.markdown(text, extensions=["tables", "fenced_code"])
    return _sanitize_html(html_text)
