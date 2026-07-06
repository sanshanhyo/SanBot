from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urljoin

from .errors import JavLibraryNotFoundError, JavLibraryParseError
from .models import JavLibrarySearchItem, JavLibraryVideo
from .normalizer import normalize_code


@dataclass
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)
    parent: "Node | None" = None

    def text(self) -> str:
        pieces = list(self.text_parts)
        for child in self.children:
            pieces.append(child.text())
        return clean_text(" ".join(pieces))

    def iter(self) -> Iterable["Node"]:
        yield self
        for child in self.children:
            yield from child.iter()

    def find(self, *, tag: str | None = None, id_: str | None = None, class_: str | None = None) -> "Node | None":
        for node in self.iter():
            if _matches(node, tag=tag, id_=id_, class_=class_):
                return node
        return None

    def find_all(self, *, tag: str | None = None, id_: str | None = None, class_: str | None = None) -> list["Node"]:
        return [node for node in self.iter() if _matches(node, tag=tag, id_=id_, class_=class_)]


class TreeBuilder(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack: list[Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag.lower(), {key.lower(): value or "" for key, value in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(node)
        if node.tag not in self.VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].text_parts.append(data)


def parse_document(html: str) -> Node:
    builder = TreeBuilder()
    builder.feed(html)
    return builder.root


def parse_video_detail(html: str, page_url: str) -> JavLibraryVideo:
    root = parse_document(html)
    code = _field_text(root, "video_id")
    if not code:
        raise JavLibraryParseError("Javlibrary 页面解析失败：未找到番号")
    code = normalize_code(code)

    title = _title(root) or code
    cover_url = _cover_url(root, page_url)
    release_date = _field_text(root, "video_date")
    runtime_minutes = _runtime_minutes(_field_text(root, "video_length"))
    director = _first_link_text(root, "video_director") or _field_text(root, "video_director")
    studio = _first_link_text(root, "video_maker") or _field_text(root, "video_maker")
    publisher = _first_link_text(root, "video_label") or _field_text(root, "video_label")
    series = _first_link_text(root, "video_series") or _field_text(root, "video_series")
    actors = _link_texts(root, "video_cast", class_="star")
    genres = _link_texts(root, "video_genres", class_="genre")
    rating = _rating(root)

    return JavLibraryVideo(
        code=code,
        title=title,
        url=page_url,
        cover_url=cover_url,
        release_date=release_date,
        runtime_minutes=runtime_minutes,
        director=director,
        studio=studio,
        publisher=publisher,
        series=series,
        actors=actors,
        genres=genres,
        rating=rating,
    )


def parse_search_results(html: str, base_url: str) -> list[JavLibrarySearchItem]:
    root = parse_document(html)
    results: list[JavLibrarySearchItem] = []
    for video_node in root.find_all(tag="div", class_="video"):
        link = _first_child(video_node, tag="a")
        href = link.attrs.get("href", "") if link else ""
        url = _detail_url(href, base_url)
        code_text = _node_text(_first_descendant(video_node, class_="id"))
        title = _node_text(_first_descendant(video_node, class_="title"))
        img = _first_descendant(video_node, tag="img")
        cover_url = _absolute_url(img.attrs.get("src"), base_url) if img else None
        try:
            code = normalize_code(code_text)
        except Exception:
            code_match = re.search(r"\b([A-Z]{2,12}-?\d{2,8}|FC2(?:-?PPV)?-?\d{3,10})\b", title, flags=re.I)
            if code_match is None:
                continue
            code = normalize_code(code_match.group(1))
        results.append(JavLibrarySearchItem(code=code, title=title or code, url=url, cover_url=cover_url))
    return results


def pick_search_result(results: list[JavLibrarySearchItem], code: str) -> JavLibrarySearchItem:
    for result in results:
        if result.code == code:
            return result
    if results:
        return results[0]
    raise JavLibraryNotFoundError(f"没有找到 {code} 的 Javlibrary 条目")


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _matches(node: Node, *, tag: str | None, id_: str | None, class_: str | None) -> bool:
    if tag is not None and node.tag != tag:
        return False
    if id_ is not None and node.attrs.get("id") != id_:
        return False
    if class_ is not None and class_ not in node.attrs.get("class", "").split():
        return False
    return True


def _node_text(node: Node | None) -> str:
    return node.text() if node is not None else ""


def _field_text(root: Node, field_id: str) -> str | None:
    node = root.find(id_=field_id)
    if node is None:
        return None
    text_node = node.find(class_="text")
    text = _node_text(text_node) or node.text()
    text = re.sub(r"^[^:：]+[:：]\s*", "", text)
    return text or None


def _title(root: Node) -> str | None:
    node = root.find(id_="video_title")
    if node is None:
        return None
    title = _node_text(node.find(tag="h3")) or node.text()
    return title or None


def _cover_url(root: Node, page_url: str) -> str | None:
    jacket = root.find(id_="video_jacket_img") or root.find(id_="video_jacket")
    img = jacket.find(tag="img") if jacket is not None else None
    return _absolute_url(img.attrs.get("src"), page_url) if img is not None else None


def _absolute_url(url: str | None, base_url: str) -> str | None:
    if not url:
        return None
    if url.startswith("//"):
        return "https:" + url
    return urljoin(base_url, url)


def _detail_url(href: str, base_url: str) -> str:
    if href.startswith("?"):
        return urljoin(base_url, "./" + href)
    return urljoin(base_url, href)


def _runtime_minutes(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else None


def _first_link_text(root: Node, field_id: str) -> str | None:
    node = root.find(id_=field_id)
    if node is None:
        return None
    link = node.find(tag="a")
    text = _node_text(link)
    return text or None


def _link_texts(root: Node, field_id: str, *, class_: str) -> list[str]:
    node = root.find(id_=field_id)
    if node is None:
        return []
    texts: list[str] = []
    for item in node.find_all(class_=class_):
        text = _node_text(item.find(tag="a")) or item.text()
        if text and text not in texts:
            texts.append(text)
    return texts


def _rating(root: Node) -> float | None:
    score_node = root.find(class_="score")
    text = _node_text(score_node)
    if not text:
        text = root.find(id_="video_review").text() if root.find(id_="video_review") else ""
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", text)
    return float(match.group(1)) if match else None


def _first_child(node: Node, *, tag: str | None = None, class_: str | None = None) -> Node | None:
    for child in node.children:
        if _matches(child, tag=tag, id_=None, class_=class_):
            return child
    return None


def _first_descendant(node: Node, *, tag: str | None = None, class_: str | None = None) -> Node | None:
    for child in node.iter():
        if child is not node and _matches(child, tag=tag, id_=None, class_=class_):
            return child
    return None
