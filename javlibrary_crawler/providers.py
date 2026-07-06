from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Protocol
from urllib.parse import quote, urljoin, urlparse

from .errors import JavLibraryError, JavLibraryFetchError, JavLibraryNotFoundError
from .fetcher import FetchResponse
from .models import JavLibrarySearchItem, JavLibraryVideo
from .normalizer import normalize_code
from .parser import Node, clean_text, parse_document, parse_search_results, parse_video_detail, pick_search_result


class MetadataFetcher(Protocol):
    def get(self, url: str) -> FetchResponse: ...

    def post(self, url: str, data: dict[str, str] | None = None) -> FetchResponse: ...


@dataclass(frozen=True)
class ProviderConfig:
    javlibrary_base_url: str = "https://www.javlibrary.com"
    javlibrary_language: str = "cn"
    javdb_base_url: str = "https://javdb.com"
    javbus_base_url: str = "https://www.javbus.com"
    jav321_base_url: str = "https://www.jav321.com"


class BaseProvider:
    source = ""

    def __init__(self, fetcher: MetadataFetcher, config: ProviderConfig) -> None:
        self.fetcher = fetcher
        self.config = config

    def lookup(self, code: str) -> JavLibraryVideo:
        raise NotImplementedError


class JavLibraryProvider(BaseProvider):
    source = "javlibrary"

    def lookup(self, code: str) -> JavLibraryVideo:
        search_response = self.fetcher.get(self._search_url(code))

        try:
            return replace(parse_video_detail(search_response.text, search_response.url), source=self.source)
        except JavLibraryError:
            pass

        results = parse_search_results(search_response.text, search_response.url)
        if not results:
            raise JavLibraryNotFoundError(f"没有找到 {code} 的 Javlibrary 条目")
        result = pick_search_result(results, code)
        detail_response = self.fetcher.get(result.url)
        video = parse_video_detail(detail_response.text, detail_response.url)
        if video.code != code:
            raise JavLibraryNotFoundError(f"没有找到 {code} 的精确匹配条目")
        return replace(video, source=self.source)

    def _search_url(self, code: str) -> str:
        language = self.config.javlibrary_language.strip("/ ")
        base = self.config.javlibrary_base_url.rstrip("/") + "/"
        return urljoin(base, f"{language}/vl_searchbyid.php?keyword={quote(code)}")


class JavBusProvider(BaseProvider):
    source = "javbus"

    def lookup(self, code: str) -> JavLibraryVideo:
        base = self.config.javbus_base_url.rstrip("/")
        last_error: JavLibraryError | None = None

        for detail_url in _unique([f"{base}/{quote(code)}", f"{base}/{quote(code.replace('-', ''))}"]):
            try:
                return self._parse_detail(self.fetcher.get(detail_url).text, detail_url, code)
            except JavLibraryError as exc:
                last_error = exc

        for search_url in [
            f"{base}/search/{quote(code)}&type=&parent=ce",
            f"{base}/uncensored/search/{quote(code)}&type=0&parent=uc",
        ]:
            try:
                response = self.fetcher.get(search_url)
                item = self._pick_search_result(response.text, response.url, code)
                return self._parse_detail(self.fetcher.get(item.url).text, item.url, code)
            except JavLibraryError as exc:
                last_error = exc

        if last_error is not None:
            raise last_error
        raise JavLibraryNotFoundError(f"没有找到 {code} 的 JavBus 条目")

    def _pick_search_result(self, html: str, page_url: str, code: str) -> JavLibrarySearchItem:
        root = parse_document(html)
        clean_code = _clean_code(code)
        candidates: list[JavLibrarySearchItem] = []
        for link in root.find_all(tag="a", class_="movie-box"):
            href = link.attrs.get("href", "")
            title = link.attrs.get("title") or link.text()
            url = _absolute_url(href, page_url)
            if not url:
                continue
            found_code = _code_from_text(title) or _code_from_text(url) or code
            try:
                normalized = normalize_code(found_code)
            except JavLibraryError:
                normalized = code
            candidates.append(JavLibrarySearchItem(code=normalized, title=title or normalized, url=url))

        for item in candidates:
            if _clean_code(item.code) == clean_code or clean_code in _clean_code(item.title + item.url):
                return item
        raise JavLibraryNotFoundError(f"没有找到 {code} 的 JavBus 条目")

    def _parse_detail(self, html: str, url: str, expected_code: str) -> JavLibraryVideo:
        root = parse_document(html)
        raw_title = _first_text(root, tag="h3")
        if not raw_title:
            raise JavLibraryNotFoundError(f"没有找到 {expected_code} 的 JavBus 条目")

        code = _normalize_or_default(_value_after_header(root, ["識別碼", "识别码", "ID"]), expected_code)
        title = _strip_code_from_title(raw_title, code) or raw_title or code
        cover_url = _absolute_url(_first_attr(root, tag="a", class_="bigImage", attr="href"), url)
        release_date = _value_after_header(root, ["發行日期", "发行日期", "Release Date"])
        runtime_minutes = _runtime_minutes(_value_after_header(root, ["長度", "长度", "Runtime"]))
        studio = _first_link_text_by_href(root, ["/studio/"])
        publisher = _first_link_text_by_href(root, ["/label/"]) or studio
        director = _first_link_text_by_href(root, ["/director/"])
        series = _first_link_text_by_href(root, ["/series/"])
        actors = _link_texts_by_container_class(root, "star-name")
        genres = _link_texts_by_href(root, ["/genre/"])

        return JavLibraryVideo(
            code=code,
            title=title,
            url=url,
            source=self.source,
            cover_url=cover_url,
            release_date=release_date,
            runtime_minutes=runtime_minutes,
            director=director,
            studio=studio,
            publisher=publisher,
            series=series,
            actors=actors,
            genres=genres,
        )


class Jav321Provider(BaseProvider):
    source = "jav321"

    def lookup(self, code: str) -> JavLibraryVideo:
        base = self.config.jav321_base_url.rstrip("/")
        response = self.fetcher.post(f"{base}/search", data={"sn": code})
        if "AVが見つかりませんでした" in response.text:
            raise JavLibraryNotFoundError(f"没有找到 {code} 的 Jav321 条目")
        return self._parse_detail(response.text, response.url or base, code)

    def _parse_detail(self, html: str, url: str, expected_code: str) -> JavLibraryVideo:
        root = parse_document(html)
        code = _normalize_or_default(_regex_text(r"<b>品番</b>:\s*([^<\s]+)", html), expected_code)
        title = clean_text(_regex_text(r"<h3>(.*?)\s*<small", html) or _first_text(root, tag="h3"))
        if not title:
            raise JavLibraryNotFoundError(f"没有找到 {expected_code} 的 Jav321 条目")

        detail_url = _absolute_url(_link_href_by_text(root, ["简体中文", "繁體中文", "日本語"]), url) or url
        cover_url = _absolute_url(_first_attr(root, tag="img", class_="img-responsive", attr="src"), url)
        if not cover_url:
            cover_url = _absolute_url(_first_attr(root, tag="video", id_="vjs_sample_player", attr="poster"), url)
        release_date = _regex_text(r"<b>配信開始日</b>:\s*(\d{4}-\d{2}-\d{2})<br", html).replace("0000-00-00", "")
        runtime_minutes = _runtime_minutes(_regex_text(r"<b>収録時間</b>:\s*(\d+)", html))
        actors = _link_texts_by_href(root, ["/star/", "/heyzo_star/"])
        if not actors:
            actors = _split_names(_regex_text(r"<b>出演者</b>:\s*([^<]+?)\s*(?:&nbsp;)?\s*<br", html))
        studio = _first_link_text_by_href(root, ["/company/"])
        series = _first_link_text_by_href(root, ["/series/"])
        genres = _link_texts_by_href(root, ["/genre/"])
        rating = _rating_from_text(_regex_text(r"<b>平均評価</b>:\s*([^<]+)<br", html))

        return JavLibraryVideo(
            code=code,
            title=_strip_code_from_title(title, code) or title,
            url=detail_url,
            source=self.source,
            cover_url=cover_url,
            release_date=release_date or None,
            runtime_minutes=runtime_minutes,
            studio=studio,
            publisher=studio,
            series=series,
            actors=actors,
            genres=genres,
            rating=rating,
        )


class JavDbProvider(BaseProvider):
    source = "javdb"

    def lookup(self, code: str) -> JavLibraryVideo:
        base = self.config.javdb_base_url.rstrip("/")
        search_url = f"{base}/search?q={quote(code)}&locale=zh"
        response = self.fetcher.get(search_url)
        detail_url = self._pick_detail_url(response.text, response.url, code)
        return self._parse_detail(self.fetcher.get(detail_url).text, detail_url, code)

    def search(self, query: str, page: int = 1, limit: int = 10) -> list[JavLibrarySearchItem]:
        base = self.config.javdb_base_url.rstrip("/")
        search_url = f"{base}/search?q={quote(query)}&f=all&locale=zh&page={max(1, page)}"
        response = self.fetcher.get(search_url)
        items = self._parse_cards(response.text, response.url, limit=_candidate_limit(limit))
        return _sort_javdb_search_items(items, query, actor_mode=False)[:limit]

    def search_actor(self, query: str, page: int = 1, limit: int = 10) -> list[JavLibrarySearchItem]:
        base = self.config.javdb_base_url.rstrip("/")
        search_url = f"{base}/search?q={quote(query)}&f=actor&locale=zh&page={max(1, page)}"
        response = self.fetcher.get(search_url)
        items = self._parse_cards(response.text, response.url, limit=_candidate_limit(limit))
        return _sort_javdb_search_items(items, query, actor_mode=True)[:limit]

    def ranking(self, period: str, page: int = 1, limit: int = 10) -> list[JavLibrarySearchItem]:
        period_value = {"day": "daily", "week": "weekly", "month": "monthly"}.get(period)
        if period_value is None:
            raise JavLibraryNotFoundError("JavDB 排行榜类型无效")
        base = self.config.javdb_base_url.rstrip("/")
        ranking_url = f"{base}/rankings/movies?period={period_value}&locale=zh&page={max(1, page)}"
        response = self.fetcher.get(ranking_url)
        items = self._parse_cards(response.text, response.url, limit=limit)
        return [replace(item, rank=index) for index, item in enumerate(items, start=(max(1, page) - 1) * limit + 1)]

    def _pick_detail_url(self, html: str, page_url: str, code: str) -> str:
        root = parse_document(html)
        clean_code = _clean_code(code)
        fallback: str | None = None
        for link in root.find_all(tag="a", class_="box"):
            href = link.attrs.get("href", "")
            title = link.text()
            url = _absolute_url(href, page_url)
            if not url:
                continue
            fallback = fallback or url
            if clean_code in _clean_code(title):
                return url
        if fallback:
            return fallback
        raise JavLibraryNotFoundError(f"没有找到 {code} 的 JavDB 条目")

    def _parse_cards(self, html: str, page_url: str, limit: int) -> list[JavLibrarySearchItem]:
        root = parse_document(html)
        items: list[JavLibrarySearchItem] = []
        for link in root.find_all(tag="a", class_="box"):
            href = link.attrs.get("href", "")
            url = _absolute_url(href, page_url)
            if not url:
                continue
            code = _code_from_text(link.text()) or _code_from_text(url)
            if not code:
                continue
            normalized = _normalize_or_default(code, code.upper())
            title = _javdb_card_title(link, normalized)
            cover_url = _absolute_url(_first_attr(link, tag="img", attr="src"), page_url)
            actors = _javdb_card_actors(link)
            release_date = _javdb_card_date(link)
            items.append(
                JavLibrarySearchItem(
                    code=normalized,
                    title=title or normalized,
                    url=url,
                    cover_url=cover_url,
                    source=self.source,
                    release_date=release_date,
                    actors=actors,
                )
            )
            if len(items) >= limit:
                break
        return items

    def _parse_detail(self, html: str, url: str, expected_code: str) -> JavLibraryVideo:
        root = parse_document(html)
        code = _normalize_or_default(_first_attr(root, tag="a", class_="copy-to-clipboard", attr="data-clipboard-text"), expected_code)
        title = _first_text(root, tag="strong", class_="current-title") or _first_text(root, tag="h2")
        if not title:
            raise JavLibraryNotFoundError(f"没有找到 {expected_code} 的 JavDB 条目")

        cover_url = _absolute_url(_first_attr(root, tag="img", class_="video-cover", attr="src"), url)
        release_date = _value_after_strong(root, ["日期", "Released Date"])
        runtime_minutes = _runtime_minutes(_value_after_strong(root, ["時長", "时长", "Duration"]))
        studio = _first_link_after_strong(root, ["片商", "Maker"])
        publisher = _first_link_after_strong(root, ["發行", "发行", "Publisher"])
        series = _first_link_after_strong(root, ["系列", "Series"])
        director = _first_link_after_strong(root, ["導演", "导演", "Director"])
        actors = _link_texts_by_href(root, ["/actors/"])
        genres = _link_texts_after_strong(root, ["類別", "类别", "Tags"])
        rating = _rating_from_text(_first_text(root, class_="score-stars"))
        trailer_url, trailer_page_url, trailer_requires_login = _javdb_trailer_info(root, url)
        preview_image_urls = _javdb_preview_image_urls(root, url, cover_url)

        return JavLibraryVideo(
            code=code,
            title=_strip_code_from_title(title, code) or title,
            url=url,
            source=self.source,
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
            trailer_url=trailer_url,
            trailer_page_url=trailer_page_url,
            trailer_requires_login=trailer_requires_login,
            preview_image_urls=preview_image_urls,
            resource_page_url=url,
        )


PROVIDER_CLASSES = {
    "javlibrary": JavLibraryProvider,
    "javdb": JavDbProvider,
    "javbus": JavBusProvider,
    "jav321": Jav321Provider,
}


def create_provider(name: str, fetcher: MetadataFetcher, config: ProviderConfig) -> BaseProvider | None:
    cls = PROVIDER_CLASSES.get(name.strip().lower())
    return cls(fetcher, config) if cls else None


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _absolute_url(value: str | None, base_url: str) -> str | None:
    if not value:
        return None
    if value.startswith("//"):
        return "https:" + value
    return urljoin(base_url, value)


def _clean_code(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _code_from_text(value: str) -> str | None:
    match = re.search(r"\b([A-Z]{2,12}[-_\s]?\d{2,8}[A-Z]?|FC2(?:[-_\s]?PPV)?[-_\s]?\d{3,10})\b", value, flags=re.I)
    return match.group(1) if match else None


def _normalize_or_default(value: str | None, default: str) -> str:
    try:
        return normalize_code(value or default)
    except JavLibraryError:
        return default


def _strip_code_from_title(title: str, code: str) -> str:
    text = clean_text(title)
    for token in (code, code.replace("-", ""), code.replace("-", " ")):
        text = re.sub(re.escape(token), "", text, flags=re.I).strip()
    return text.strip(" -_/")


def _runtime_minutes(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"(\d+)", text)
    return int(match.group(1)) if match else None


def _rating_from_text(text: str | None) -> float | None:
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None


def _regex_text(pattern: str, text: str) -> str:
    match = re.search(pattern, text, flags=re.I | re.S)
    return clean_text(match.group(1)) if match else ""


def _split_names(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,/、\s]+", value) if item.strip()]


def _first_text(root: Node, *, tag: str | None = None, id_: str | None = None, class_: str | None = None) -> str:
    node = root.find(tag=tag, id_=id_, class_=class_)
    return node.text() if node else ""


def _first_attr(root: Node, *, attr: str, tag: str | None = None, id_: str | None = None, class_: str | None = None) -> str | None:
    node = root.find(tag=tag, id_=id_, class_=class_)
    return node.attrs.get(attr) if node else None


def _value_after_header(root: Node, labels: list[str]) -> str | None:
    for node in root.find_all(class_="header"):
        label = node.text()
        if not any(item.lower() in label.lower() for item in labels):
            continue
        text = (node.parent.text() if node.parent else "")
        value = clean_text(text.replace(label, "", 1))
        value = re.sub(r"^[：:\s]+", "", value)
        if value:
            return value
    return None


def _value_after_strong(root: Node, labels: list[str]) -> str | None:
    for node in root.find_all(tag="strong"):
        label = node.text()
        if not any(item.lower() in label.lower() for item in labels):
            continue
        text = node.parent.text() if node.parent else ""
        value = clean_text(text.replace(label, "", 1))
        value = re.sub(r"^[：:\s]+", "", value)
        return value or None
    return None


def _first_link_text_by_href(root: Node, needles: list[str]) -> str | None:
    values = _link_texts_by_href(root, needles)
    return values[0] if values else None


def _link_texts_by_href(root: Node, needles: list[str]) -> list[str]:
    values: list[str] = []
    for node in root.find_all(tag="a"):
        href = node.attrs.get("href", "")
        if not any(needle in href for needle in needles):
            continue
        text = node.text()
        if text and text not in values:
            values.append(text)
    return values


def _link_texts_by_container_class(root: Node, class_name: str) -> list[str]:
    values: list[str] = []
    for node in root.find_all(class_=class_name):
        text = node.find(tag="a").text() if node.find(tag="a") else node.text()
        if text and text not in values:
            values.append(text)
    return values


def _first_link_after_strong(root: Node, labels: list[str]) -> str | None:
    values = _link_texts_after_strong(root, labels)
    return values[0] if values else None


def _link_texts_after_strong(root: Node, labels: list[str]) -> list[str]:
    values: list[str] = []
    for node in root.find_all(tag="strong"):
        if not any(label.lower() in node.text().lower() for label in labels):
            continue
        parent = node.parent
        if parent is None:
            continue
        for link in parent.find_all(tag="a"):
            text = link.text()
            if text and text not in values:
                values.append(text)
    return values


def _link_href_by_text(root: Node, labels: list[str]) -> str | None:
    for link in root.find_all(tag="a"):
        text = link.text()
        if any(label in text for label in labels):
            return link.attrs.get("href")
    return None


def _javdb_card_title(card: Node, code: str) -> str:
    title = _first_text(card, class_="video-title") or _first_text(card, class_="title") or card.text()
    title = clean_text(title)
    return _strip_code_from_title(title, code) or title


def _javdb_card_actors(card: Node) -> list[str]:
    values: list[str] = []
    for node in card.find_all(class_="meta"):
        text = node.text()
        if not text:
            continue
        for value in _split_names(text):
            if _code_from_text(value):
                continue
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                continue
            if value and value not in values:
                values.append(value)
    return values[:6]


def _javdb_card_date(card: Node) -> str | None:
    text = card.text()
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    return match.group(1) if match else None


def _javdb_trailer_info(root: Node, page_url: str) -> tuple[str | None, str | None, bool]:
    trailer_page_url: str | None = None
    requires_login = False
    for node in root.find_all():
        if node.tag not in {"video", "source", "a"}:
            continue
        class_text = node.attrs.get("class", "").lower()
        id_text = node.attrs.get("id", "").lower()
        rel_text = node.attrs.get("rel", "").lower()
        text = node.text().lower()
        is_trailer_node = any(
            token in f"{class_text} {id_text} {rel_text} {text}"
            for token in ("trailer", "preview-video", "preview video", "予告", "预告")
        )
        if node.tag in {"video", "source"} or is_trailer_node:
            for attr in ("src", "data-src", "data-video", "data-video-src", "href", "data-href", "data-url"):
                url = _absolute_url(node.attrs.get(attr), page_url)
                if not url:
                    continue
                if _looks_like_video_url(url):
                    return url, trailer_page_url, requires_login
                if _looks_like_login_url(url):
                    requires_login = True
                elif node.tag == "a" and is_trailer_node and trailer_page_url is None:
                    trailer_page_url = url
    return None, trailer_page_url, requires_login


def _looks_like_login_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.path.rstrip("/") in {"/login", "/users/sign_in"}


def _javdb_preview_image_urls(root: Node, page_url: str, cover_url: str | None) -> list[str]:
    urls: list[str] = []
    cover_key = cover_url.split("?", 1)[0] if cover_url else ""
    for node in root.find_all():
        if node.tag not in {"img", "a"}:
            continue
        class_text = node.attrs.get("class", "").lower()
        parent_class = node.parent.attrs.get("class", "").lower() if node.parent is not None else ""
        id_text = node.attrs.get("id", "").lower()
        marker = f"{class_text} {parent_class} {id_text}"
        looks_like_preview = any(
            token in marker
            for token in ("preview", "sample", "screenshot", "gallery", "tile", "cover")
        )
        attr_names = ("src", "data-src", "href") if looks_like_preview else ("data-src", "href")
        for attr in attr_names:
            url = _absolute_url(node.attrs.get(attr), page_url)
            if not url or not _looks_like_image_url(url):
                continue
            clean_url = url.split("?", 1)[0]
            if cover_key and clean_url == cover_key:
                continue
            if url not in urls:
                urls.append(url)
            break
    return urls[:12]


def _looks_like_video_url(value: str) -> bool:
    clean = value.split("?", 1)[0].lower()
    return clean.endswith((".mp4", ".webm", ".mov", ".m3u8"))


def _looks_like_image_url(value: str) -> bool:
    clean = value.split("?", 1)[0].lower()
    return clean.endswith((".jpg", ".jpeg", ".png", ".webp"))


def _candidate_limit(limit: int) -> int:
    safe_limit = max(1, limit)
    return min(max(safe_limit * 3, safe_limit), 30)


def _sort_javdb_search_items(
    items: list[JavLibrarySearchItem],
    query: str,
    *,
    actor_mode: bool,
) -> list[JavLibrarySearchItem]:
    return [
        item
        for _score, _index, item in sorted(
            (
                (_javdb_search_score(item, query, actor_mode=actor_mode), index, item)
                for index, item in enumerate(items)
            ),
            key=lambda entry: (-entry[0], entry[1]),
        )
    ]


def _javdb_search_score(item: JavLibrarySearchItem, query: str, *, actor_mode: bool) -> int:
    query_text = _normalize_search_text(query)
    query_code = _clean_code(query)
    item_code = _clean_code(item.code)
    title_text = _normalize_search_text(item.title)
    actor_texts = [_normalize_search_text(actor) for actor in item.actors]

    score = 0
    if query_code and item_code:
        if query_code == item_code:
            score += 1000
        elif query_code in item_code:
            score += 700

    if not query_text:
        return score

    title_score = _field_match_score(title_text, query_text, exact=620, prefix=460, contains=320)
    actor_score = max(
        (_field_match_score(actor_text, query_text, exact=900, prefix=760, contains=640) for actor_text in actor_texts),
        default=0,
    )

    if actor_mode:
        score += actor_score
        score += min(title_score, 180)
    else:
        score += title_score
        score += min(actor_score, 120)
    return score


def _field_match_score(value: str, query: str, *, exact: int, prefix: int, contains: int) -> int:
    if not value or not query:
        return 0
    if value == query:
        return exact
    if value.startswith(query):
        return prefix
    if query in value:
        return contains
    return 0


def _normalize_search_text(value: str) -> str:
    return re.sub(r"[\s_\-・·./|]+", "", value).casefold()
