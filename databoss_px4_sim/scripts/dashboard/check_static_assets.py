#!/usr/bin/env python3
"""Check dashboard static assets that browsers otherwise fail late.

    venv/bin/python scripts/dashboard/check_static_assets.py [--json]

The Python esprima 4.0.1 package only parses through ES2019. This checker
accepts selected newer syntax by parsing an in-memory down-level copy when the
strict parse fails. Class fields are the one modern construct this checker
cannot validate.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import esprima

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = PROJECT_ROOT / "src" / "databoss_sim" / "dashboard" / "static"
INDEX_HTML = STATIC_ROOT / "index.html"
STATUS_ORDER = ("OK", "FAIL", "WARN", "SKIP")
ALLOWED_EXTERNAL_PREFIXES = ("https://app.gazebosim.org",)
URL_RE = re.compile(r"(?i)https?://[^\s\"'`)<>]+|//cdn[^\s\"'`)<>]*|@import\s+url\(\s*['\"]?https?://")


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str


@dataclass
class ParseResult:
    module: Any | None
    error: str | None
    detail: str


class AssetReferences(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.css: list[str] = []
        self.modules: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {key.lower(): value or "" for key, value in attrs}
        if tag == "link" and data.get("rel") == "stylesheet" and data.get("href"):
            self.css.append(data["href"])
        if tag == "script" and data.get("type") == "module" and data.get("src"):
            self.modules.append(data["src"])


def _static_path(ref: str, base: Path = STATIC_ROOT) -> Path:
    if ref.startswith("/static/"):
        return STATIC_ROOT / ref.removeprefix("/static/")
    return (base / ref).resolve(strict=False)


def _js_files() -> list[Path]:
    return sorted(STATIC_ROOT.rglob("*.js"))


def _css_files() -> list[Path]:
    return sorted(STATIC_ROOT.rglob("*.css"))


def _walk(node: Any):
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif hasattr(node, "type"):
        yield node
        for key, value in vars(node).items():
            if key in {"type", "loc", "range"} or key.startswith("_"):
                continue
            yield from _walk(value)


def _format_esprima_version() -> str:
    version = getattr(esprima, "__version__", "unknown")
    if isinstance(version, tuple):
        return ".".join(str(part) for part in version)
    return str(version)


def _format_parse_error(exc: Exception) -> str:
    line = getattr(exc, "lineNumber", None)
    message = str(exc)
    if line:
        message = f"line {line}: {message}"
    return message


def _downlevel_for_esprima(source: str) -> str:
    # esprima 4.0.1 only supports ES2019, and this host has no node parser.
    # These substitutions make newer browser-supported syntax parseable only.
    substitutions = (
        ("?.[", "["),
        ("?.(", "("),
        ("?.", "."),
        ("??=", "="),
        ("??", "||"),
    )
    downlevelled = source
    for old, new in substitutions:
        downlevelled = downlevelled.replace(old, new)
    return downlevelled


def _parse_module(path: Path) -> ParseResult:
    source = path.read_text()
    try:
        return ParseResult(esprima.parseModule(source, loc=True), None, "parsed as ES module")
    except Exception as exc:  # esprima raises Error objects, not SyntaxError.
        original_error = _format_parse_error(exc)
    try:
        module = esprima.parseModule(_downlevel_for_esprima(source), loc=True)
        return ParseResult(module, None, f"ok (modern syntax; esprima {_format_esprima_version()} is ES2019, verified via down-level)")
    except Exception:
        return ParseResult(None, original_error, original_error)


def _check_js_parse() -> list[CheckResult]:
    results = []
    for path in _js_files():
        parsed = _parse_module(path)
        name = str(path.relative_to(STATIC_ROOT))
        status = "FAIL" if parsed.error else "OK"
        results.append(CheckResult(name, status, parsed.detail))
    return results


def _check_imports() -> list[CheckResult]:
    results = []
    for path in _js_files():
        parsed = _parse_module(path)
        if parsed.error:
            results.append(CheckResult(str(path.relative_to(STATIC_ROOT)), "SKIP", f"parse failed: {parsed.error}"))
            continue
        missing = []
        for node in _walk(parsed.module):
            if getattr(node, "type", "") != "ImportDeclaration":
                continue
            spec = getattr(getattr(node, "source", None), "value", "")
            if not isinstance(spec, str) or not spec.startswith(("./", "../")):
                continue
            target = (path.parent / spec).resolve(strict=False)
            if not target.is_file():
                line = getattr(getattr(node, "loc", None), "start", None)
                line_no = getattr(line, "line", "?")
                missing.append(f"line {line_no}: {spec} -> {target}")
        detail = "; ".join(missing) if missing else "all relative imports resolve"
        results.append(CheckResult(str(path.relative_to(STATIC_ROOT)), "FAIL" if missing else "OK", detail))
    return results


def _check_external_urls() -> list[CheckResult]:
    results = []
    for path in [*_js_files(), *_css_files()]:
        bad = []
        for match in URL_RE.finditer(path.read_text()):
            value = match.group(0)
            if value.lower().startswith("//cdn"):
                value = f"https:{value}"
            if value.startswith(ALLOWED_EXTERNAL_PREFIXES):
                continue
            line = path.read_text()[: match.start()].count("\n") + 1
            bad.append(f"line {line}: {match.group(0)}")
        detail = "; ".join(bad) if bad else "no external resource URLs"
        results.append(CheckResult(str(path.relative_to(STATIC_ROOT)), "FAIL" if bad else "OK", detail))
    return results


def _check_index_assets() -> list[CheckResult]:
    parser = AssetReferences()
    parser.feed(INDEX_HTML.read_text())
    results = []
    for kind, refs in (("css", parser.css), ("js module", parser.modules)):
        missing = [ref for ref in refs if not _static_path(ref, INDEX_HTML.parent).is_file()]
        detail = f"{len(refs)} referenced, all exist" if not missing else "missing " + ", ".join(missing)
        results.append(CheckResult(kind, "FAIL" if missing else "OK", detail))
    return results


def _is_inner_html_assignment(node: Any) -> bool:
    if getattr(node, "type", "") != "AssignmentExpression" or getattr(node, "operator", "") != "=":
        return False
    left = getattr(node, "left", None)
    prop = getattr(left, "property", None)
    return getattr(left, "type", "") == "MemberExpression" and getattr(prop, "name", "") == "innerHTML"


def _check_inner_html() -> list[CheckResult]:
    results = []
    allowed = STATIC_ROOT / "js" / "pages" / "comparison_detail.js"
    for path in _js_files():
        parsed = _parse_module(path)
        if parsed.error:
            results.append(CheckResult(str(path.relative_to(STATIC_ROOT)), "SKIP", f"parse failed: {parsed.error}"))
            continue
        bad = []
        for node in _walk(parsed.module):
            if not _is_inner_html_assignment(node):
                continue
            right = getattr(node, "right", None)
            if getattr(right, "type", "") == "Literal" and isinstance(getattr(right, "value", None), str):
                continue
            if path == allowed:
                continue
            line = getattr(getattr(node, "loc", None), "start", None)
            bad.append(f"line {getattr(line, 'line', '?')}: non-literal innerHTML assignment")
        detail = "; ".join(bad) if bad else "no unsafe innerHTML assignment"
        results.append(CheckResult(str(path.relative_to(STATIC_ROOT)), "FAIL" if bad else "OK", detail))
    return results


def run_all_checks() -> dict[str, list[CheckResult]]:
    return {
        "javascript parse": _check_js_parse(),
        "javascript imports": _check_imports(),
        "external urls": _check_external_urls(),
        "index assets": _check_index_assets(),
        "innerHTML": _check_inner_html(),
    }


def _summary(results: dict[str, list[CheckResult]]) -> dict[str, int]:
    counts = {status: 0 for status in STATUS_ORDER}
    for group in results.values():
        for result in group:
            counts[result.status] = counts.get(result.status, 0) + 1
    return counts


def _print_text(results: dict[str, list[CheckResult]]) -> None:
    rows = [(group, result.name, result.status, result.detail) for group, items in results.items() for result in items]
    widths = [
        max(len("group"), *(len(row[0]) for row in rows)),
        max(len("check"), *(len(row[1]) for row in rows)),
        max(len("status"), *(len(row[2]) for row in rows)),
    ]
    print(f"{'group':<{widths[0]}}  {'check':<{widths[1]}}  {'status':<{widths[2]}}  detail")
    print(f"{'-' * widths[0]}  {'-' * widths[1]}  {'-' * widths[2]}  {'-' * 6}")
    for group, name, status, detail in rows:
        print(f"{group:<{widths[0]}}  {name:<{widths[1]}}  {status:<{widths[2]}}  {detail}")
    counts = _summary(results)
    print(", ".join(f"{counts.get(status, 0)} {status}" for status in STATUS_ORDER))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a text table")
    args = parser.parse_args()
    results = run_all_checks()
    if args.json:
        print(json.dumps({"results": {group: [asdict(result) for result in items] for group, items in results.items()}, "summary": _summary(results)}, indent=2, sort_keys=True))
    else:
        _print_text(results)
    return 1 if _summary(results).get("FAIL", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
