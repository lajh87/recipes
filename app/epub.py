from __future__ import annotations

import posixpath
import re
import tempfile
import xml.etree.ElementTree as ET
from typing import Any

from bs4 import BeautifulSoup
from ebooklib import ITEM_DOCUMENT, epub

from app.models import CookbookTocEntry


IGNORED_CHAPTER_LABELS = {
    "about the author",
    "about the book",
    "acknowledgements",
    "also by the author",
    "also by nigella lawson",
    "contents",
    "copyright",
    "cover",
    "dedication",
    "ecopyright",
    "for reference",
    "glossary",
    "guidance notes",
    "image",
    "images",
    "index",
    "introduction",
    "list of recipes",
    "list of tables",
    "note to readers",
    "recipes",
    "stockists",
    "thanks",
    "title",
    "title page",
}
CHAPTER_LABEL_RE = re.compile(r"^(?:chapter\b|\d+[.:)]\s|\d+\s+-\s)", re.IGNORECASE)


def normalize_epub_path(href: str, *, relative_to: str | None = None) -> str:
    value = (href or "").strip()
    if not value or "://" in value:
        return ""

    path = value.split("#", 1)[0].split("?", 1)[0].strip()
    base_path = (relative_to or "").split("#", 1)[0].split("?", 1)[0].strip()
    if not path:
        path = base_path
    elif path.startswith("/"):
        path = path.lstrip("/")
    elif base_path:
        path = posixpath.join(posixpath.dirname(base_path), path)

    normalized = posixpath.normpath(path)
    return "" if normalized in {"", "."} else normalized.lstrip("./")


def _clean_chapter_label(value: str) -> str:
    return " ".join(value.split())


def _is_ignored_chapter_label(label: str) -> bool:
    normalized = label.strip().lower().rstrip(":")
    return normalized in IGNORED_CHAPTER_LABELS


def _looks_like_chapter_label(label: str) -> bool:
    letters = re.sub(r"[^A-Za-z]+", "", label)
    return bool(letters) and (CHAPTER_LABEL_RE.search(label) is not None or letters == letters.upper())


def _parse_html_toc_entries(content: bytes, *, item_href: str) -> list[CookbookTocEntry]:
    soup = BeautifulSoup(content, "html.parser")
    nav_roots = (
        soup.select("nav[epub\\:type='toc']")
        or soup.select("nav[role='doc-toc']")
        or soup.find_all("nav")
    )
    entries: list[CookbookTocEntry] = []

    def parse_list(list_node: Any) -> list[CookbookTocEntry]:
        parsed: list[CookbookTocEntry] = []
        for item in list_node.find_all("li", recursive=False):
            link = item.find("a", recursive=False)
            nested = item.find(["ol", "ul"], recursive=False)
            if not link:
                continue

            label = _clean_chapter_label(link.get_text(" ", strip=True))
            href = normalize_epub_path(link.get("href") or "", relative_to=item_href)
            children = parse_list(nested) if nested else []
            if not label or not href:
                continue
            parsed.append(CookbookTocEntry(label=label, href=href, children=children))
        return parsed

    for nav_root in nav_roots:
        root_list = nav_root.find(["ol", "ul"])
        if root_list:
            entries.extend(parse_list(root_list))
    return entries


def _parse_ncx_toc_entries(content: bytes, *, item_href: str) -> list[CookbookTocEntry]:
    try:
        root = ET.fromstring(content.decode("utf-8", errors="ignore").lstrip("\ufeff"))
    except ET.ParseError:
        return []

    namespace = {"ncx": "http://www.daisy.org/z3986/2005/ncx/"}

    def parse_nav_point(node: ET.Element) -> CookbookTocEntry | None:
        label = _clean_chapter_label(
            "".join(node.findtext("./ncx:navLabel/ncx:text", default="", namespaces=namespace))
        )
        content_node = node.find("./ncx:content", namespace)
        href = normalize_epub_path(
            content_node.attrib.get("src", "") if content_node is not None else "",
            relative_to=item_href,
        )
        children = [
            parsed
            for child in node.findall("./ncx:navPoint", namespace)
            if (parsed := parse_nav_point(child)) is not None
        ]
        if not label or not href:
            return None
        return CookbookTocEntry(label=label, href=href, children=children)

    entries: list[CookbookTocEntry] = []
    for nav_point in root.findall("./ncx:navMap/ncx:navPoint", namespace):
        parsed = parse_nav_point(nav_point)
        if parsed:
            entries.append(parsed)
    return entries


def _spine_document_paths(book: epub.EpubBook) -> list[str]:
    id_to_item = {item.get_id(): item for item in book.get_items()}
    paths: list[str] = []
    seen: set[str] = set()
    for spine_entry in getattr(book, "spine", []):
        item_id = spine_entry[0] if isinstance(spine_entry, tuple) else spine_entry
        item = id_to_item.get(item_id)
        if not item or item.get_type() != ITEM_DOCUMENT:
            continue
        path = normalize_epub_path(getattr(item, "file_name", None) or item.get_name())
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    if paths:
        return paths

    for item in book.get_items_of_type(ITEM_DOCUMENT):
        path = normalize_epub_path(getattr(item, "file_name", None) or item.get_name())
        if path and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def _flatten_toc_entries(
    entries: list[CookbookTocEntry],
    depth: int = 0,
) -> list[tuple[CookbookTocEntry, int]]:
    flattened: list[tuple[CookbookTocEntry, int]] = []
    for entry in entries:
        flattened.append((entry, depth))
        flattened.extend(_flatten_toc_entries(entry.children, depth + 1))
    return flattened


def build_chapter_map_from_toc_entries(
    entries: list[CookbookTocEntry],
    *,
    spine_paths: list[str],
    recipe_paths: set[str] | None = None,
) -> dict[str, str]:
    if not entries or not spine_paths:
        return {}

    spine_index = {path: index for index, path in enumerate(spine_paths)}
    flattened = _flatten_toc_entries(entries)
    markers: list[tuple[int, int, str]] = []

    for index, (entry, depth) in enumerate(flattened):
        if _is_ignored_chapter_label(entry.label):
            continue
        start_index = spine_index.get(entry.href)
        if start_index is None:
            continue
        path_is_relevant = entry.href in (recipe_paths or set(spine_paths))

        next_sibling_index: int | None = None
        for later_entry, later_depth in flattened[index + 1 :]:
            later_index = spine_index.get(later_entry.href)
            if later_index is None or later_depth > depth:
                continue
            next_sibling_index = later_index
            break

        span = (next_sibling_index or len(spine_paths)) - start_index
        if not entry.children:
            if depth > 0 and not _looks_like_chapter_label(entry.label):
                continue
            if depth == 0 and span <= 1 and not _looks_like_chapter_label(entry.label) and not path_is_relevant:
                continue

        markers.append((start_index, depth, entry.label))

    if not markers:
        return {}

    mapping: dict[str, str] = {}
    relevant_paths = recipe_paths or set(spine_paths)
    markers.sort(key=lambda item: (item[0], item[1]))
    collapsed_markers: list[tuple[int, int, str]] = []

    for start_index, depth, label in markers:
        if collapsed_markers and collapsed_markers[-1][0] == start_index:
            previous_start, previous_depth, previous_label = collapsed_markers[-1]
            if depth < previous_depth:
                collapsed_markers[-1] = (start_index, depth, label)
            else:
                collapsed_markers[-1] = (previous_start, previous_depth, previous_label)
            continue
        collapsed_markers.append((start_index, depth, label))

    for position, (start_index, _depth, label) in enumerate(collapsed_markers):
        next_index = len(spine_paths)
        for later_start, _later_depth, _later_label in collapsed_markers[position + 1 :]:
            if later_start > start_index:
                next_index = later_start
                break

        for path in spine_paths[start_index:next_index]:
            if path in relevant_paths:
                mapping[path] = label

    return mapping


def _score_toc_entries(
    entries: list[CookbookTocEntry],
    *,
    spine_paths: list[str],
    recipe_paths: set[str] | None = None,
) -> tuple[int, int, int]:
    mapping = build_chapter_map_from_toc_entries(
        entries,
        spine_paths=spine_paths,
        recipe_paths=recipe_paths,
    )
    flattened = _flatten_toc_entries(entries)
    if recipe_paths:
        return (len(set(mapping) & recipe_paths), len(flattened), len(mapping))
    return (len(flattened), len(mapping), 0)


def _best_toc_entries(
    book: epub.EpubBook,
    *,
    recipe_paths: set[str] | None = None,
) -> tuple[list[CookbookTocEntry], list[str]]:
    spine_paths = _spine_document_paths(book)
    best_entries: list[CookbookTocEntry] = []
    best_score = (-1, -1, -1)

    for item in book.get_items():
        item_href = getattr(item, "file_name", None) or item.get_name()
        lowered = item_href.lower()
        entries: list[CookbookTocEntry] = []
        if lowered.endswith(".ncx"):
            entries = _parse_ncx_toc_entries(item.get_content(), item_href=item_href)
        elif lowered.endswith((".xhtml", ".html")) and ("nav" in lowered or "toc" in lowered or "contents" in lowered):
            entries = _parse_html_toc_entries(item.get_content(), item_href=item_href)
        if not entries:
            continue

        score = _score_toc_entries(entries, spine_paths=spine_paths, recipe_paths=recipe_paths)
        if score > best_score:
            best_entries = entries
            best_score = score

    return best_entries, spine_paths


def extract_epub_table_of_contents(
    file_bytes: bytes,
    *,
    recipe_paths: set[str] | None = None,
) -> list[CookbookTocEntry]:
    with tempfile.NamedTemporaryFile(suffix=".epub") as handle:
        handle.write(file_bytes)
        handle.flush()
        book = epub.read_epub(handle.name)

    entries, _spine_paths = _best_toc_entries(book, recipe_paths=recipe_paths)
    return entries


def build_epub_chapter_map(
    file_bytes: bytes,
    *,
    recipe_paths: set[str] | None = None,
) -> dict[str, str]:
    with tempfile.NamedTemporaryFile(suffix=".epub") as handle:
        handle.write(file_bytes)
        handle.flush()
        book = epub.read_epub(handle.name)

    entries, spine_paths = _best_toc_entries(book, recipe_paths=recipe_paths)
    return build_chapter_map_from_toc_entries(
        entries,
        spine_paths=spine_paths,
        recipe_paths=recipe_paths,
    )
