from __future__ import annotations

import time
from pathlib import Path

import pytest

from backend.javlibrary_service import JavLibraryService, JavLibraryServiceConfig
from javlibrary_crawler import cli
from javlibrary_crawler.client import JavLibraryCrawler, JavLibraryCrawlerConfig
from javlibrary_crawler.errors import JavLibraryBlockedError, JavLibraryTimeoutError, JavLibraryValidationError
from javlibrary_crawler.fetcher import FetchResponse, _parse_impersonates, looks_blocked
from javlibrary_crawler.models import JavLibrarySearchItem, JavLibraryVideo
from javlibrary_crawler.normalizer import normalize_code
from javlibrary_crawler.option import create_option_by_file
from javlibrary_crawler.parser import parse_search_results, parse_video_detail


DETAIL_HTML = """
<html>
  <body>
    <div id="video_title"><h3 class="post-title text">SSIS-123 A Sample Title</h3></div>
    <div id="video_jacket_img"><img src="//pics.example.test/cover.jpg"></div>
    <div id="video_id" class="item"><table><tr><td class="header">识别码:</td><td class="text">SSIS-123</td></tr></table></div>
    <div id="video_date" class="item"><span class="text">2026-01-02</span></div>
    <div id="video_length" class="item"><span class="text">123 分钟</span></div>
    <div id="video_director" class="item"><span class="text"><a href="/director">A Director</a></span></div>
    <div id="video_maker" class="item"><span class="text"><a href="/studio">A Studio</a></span></div>
    <div id="video_label" class="item"><span class="text"><a href="/label">A Publisher</a></span></div>
    <div id="video_series" class="item"><span class="text"><a href="/series">A Series</a></span></div>
    <div id="video_cast"><span class="star"><a>Alice</a></span><span class="star"><a>Bob</a></span></div>
    <div id="video_genres"><span class="genre"><a>Drama</a></span><span class="genre"><a>HD</a></span></div>
    <div id="video_review"><span class="score">(4.5)</span></div>
  </body>
</html>
"""


SEARCH_HTML = """
<html>
  <body>
    <div class="video">
      <a href="?v=abc123"><img src="/thumb.jpg"></a>
      <div class="id">SSIS-123</div>
      <div class="title">SSIS-123 A Sample Title</div>
    </div>
    <div class="video">
      <a href="?v=def456"></a>
      <div class="id">ABP-456</div>
      <div class="title">ABP-456 Other</div>
    </div>
  </body>
</html>
"""


JAVBUS_DETAIL_HTML = """
<html>
  <body>
    <h3>SSIS-123 JavBus Fallback Title</h3>
    <a class="bigImage" href="/cover.jpg">cover</a>
    <p><span class="header">識別碼:</span> SSIS-123</p>
    <p><span class="header">發行日期:</span> 2026-02-03</p>
    <p><span class="header">長度:</span> 98分鐘</p>
    <a href="/studio/abc">Fallback Studio</a>
    <a href="/label/def">Fallback Publisher</a>
    <a href="/director/ghi">Fallback Director</a>
    <a href="/series/jkl">Fallback Series</a>
    <div class="star-name"><a>Fallback Alice</a></div>
    <a href="/genre/xyz">Fallback Genre</a>
  </body>
</html>
"""


JAV321_DETAIL_HTML = """
<html>
  <body>
    <h3>SSIS-123 Jav321 Fixture Title <small>Sample</small></h3>
    <b>品番</b>: SSIS-123<br>
    <b>配信開始日</b>: 2026-03-04<br>
    <b>収録時間</b>: 88<br>
    <b>平均評価</b>: 4.6<br>
    <img class="img-responsive" src="/cover321.jpg">
    <a href="/cn/v/abc123">简体中文</a>
    <a href="/star/alice">Jav321 Alice</a>
    <a href="/company/studio">Jav321 Studio</a>
    <a href="/series/one">Jav321 Series</a>
    <a href="/genre/drama">Drama</a>
  </body>
</html>
"""


JAVDB_SEARCH_HTML = """
<html>
  <body>
    <a class="box" href="/v/db123"><div>SSIS-123 JavDB Fixture Title</div></a>
  </body>
</html>
"""


JAVDB_LIST_HTML = """
<html>
  <body>
    <a class="box" href="/v/db123">
      <img src="/coverdb.jpg">
      <div class="video-title">SSIS-123 JavDB Fixture Title</div>
      <div class="meta">2026-04-05 三上悠亚</div>
    </a>
    <a class="box" href="/v/db456">
      <div class="video-title">ABP-456 Another Fixture Title</div>
    </a>
  </body>
</html>
"""


JAVDB_SORT_HTML = """
<html>
  <body>
    <a class="box" href="/v/actor-match">
      <div class="video-title">SSIS-111 Ordinary Fixture</div>
      <div class="meta">2026-04-05 三上悠亚</div>
    </a>
    <a class="box" href="/v/title-match">
      <div class="video-title">ABP-222 三上悠亚 Special Title</div>
      <div class="meta">2026-04-04 Other Actor</div>
    </a>
  </body>
</html>
"""


JAVDB_DETAIL_HTML = """
<html>
  <body>
    <strong class="current-title">SSIS-123 JavDB Fixture Title</strong>
    <a class="copy-to-clipboard" data-clipboard-text="SSIS-123"></a>
    <img class="video-cover" src="/coverdb.jpg">
    <div><strong>日期:</strong> 2026-04-05</div>
    <div><strong>時長:</strong> 77 分鐘</div>
    <div><strong>片商:</strong><a>JavDB Studio</a></div>
    <div><strong>發行:</strong><a>JavDB Publisher</a></div>
    <div><strong>系列:</strong><a>JavDB Series</a></div>
    <div><strong>導演:</strong><a>JavDB Director</a></div>
    <div><strong>類別:</strong><a>Drama</a><a>HD</a></div>
    <a href="/actors/alice">JavDB Alice</a>
    <div class="score-stars">4.8</div>
    <video id="preview-video" src="/trailers/SSIS-123.mp4"></video>
    <div class="preview-images">
      <a href="/samples/SSIS-123-1.jpg"><img src="/thumbs/SSIS-123-1.jpg"></a>
      <img data-src="/samples/SSIS-123-2.webp">
      <a href="/samples/SSIS-123_l_3.jpg"><img src="/samples/SSIS-123_s_3.jpg"></a>
      <img data-src="/samples/SSIS-123_s_4.jpg">
    </div>
  </body>
</html>
"""


def test_normalize_javlibrary_code() -> None:
    assert normalize_code("ssis123") == "SSIS-123"
    assert normalize_code("SSIS 123") == "SSIS-123"
    assert normalize_code("fc2ppv1234567") == "FC2-PPV-1234567"


def test_normalize_javlibrary_code_rejects_bad_value() -> None:
    with pytest.raises(JavLibraryValidationError):
        normalize_code("../passwd")


def test_parse_video_detail() -> None:
    video = parse_video_detail(DETAIL_HTML, "https://www.javlibrary.com/cn/?v=abc123")

    assert video.code == "SSIS-123"
    assert video.title == "SSIS-123 A Sample Title"
    assert video.cover_url == "https://pics.example.test/cover.jpg"
    assert video.release_date == "2026-01-02"
    assert video.runtime_minutes == 123
    assert video.director == "A Director"
    assert video.studio == "A Studio"
    assert video.publisher == "A Publisher"
    assert video.series == "A Series"
    assert video.actors == ["Alice", "Bob"]
    assert video.genres == ["Drama", "HD"]
    assert video.rating == 4.5


def test_parse_search_results() -> None:
    results = parse_search_results(SEARCH_HTML, "https://www.javlibrary.com/cn/vl_searchbyid.php?keyword=SSIS-123")

    assert len(results) == 2
    assert results[0].code == "SSIS-123"
    assert results[0].url == "https://www.javlibrary.com/cn/?v=abc123"
    assert results[0].cover_url == "https://www.javlibrary.com/thumb.jpg"


def test_cloudflare_like_page_is_detected() -> None:
    assert looks_blocked("<title>Just a moment...</title><script>window._cf_chl_opt={}</script>")


def test_crawler_lookup_uses_search_result_detail() -> None:
    class FakeFetcher:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def get(self, url: str) -> FetchResponse:
            self.urls.append(url)
            if "vl_searchbyid" in url:
                return FetchResponse(url, 200, SEARCH_HTML)
            return FetchResponse(url, 200, DETAIL_HTML)

        def close(self) -> None:
            pass

    fetcher = FakeFetcher()
    crawler = JavLibraryCrawler(
        JavLibraryCrawlerConfig(provider_order=("javlibrary",)),
        fetcher=fetcher,  # type: ignore[arg-type]
    )

    video = crawler.lookup("ssis123")

    assert video.code == "SSIS-123"
    assert len(fetcher.urls) == 2


def test_crawler_falls_back_to_javbus_when_javlibrary_blocked() -> None:
    class FakeFetcher:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def get(self, url: str) -> FetchResponse:
            self.urls.append(url)
            if "javlibrary.com" in url:
                raise JavLibraryBlockedError("blocked")
            return FetchResponse(url, 200, JAVBUS_DETAIL_HTML)

        def post(self, url: str, data: dict[str, str] | None = None) -> FetchResponse:
            raise AssertionError("post should not be called")

        def close(self) -> None:
            pass

    fetcher = FakeFetcher()
    crawler = JavLibraryCrawler(
        JavLibraryCrawlerConfig(provider_order=("javlibrary", "javbus")),
        fetcher=fetcher,  # type: ignore[arg-type]
    )

    video = crawler.lookup("ssis123")

    assert video.code == "SSIS-123"
    assert video.source == "javbus"
    assert video.title == "JavBus Fallback Title"
    assert video.cover_url == "https://www.javbus.com/cover.jpg"
    assert fetcher.urls[0].startswith("https://www.javlibrary.com/")
    assert fetcher.urls[1] == "https://www.javbus.com/SSIS-123"


def test_crawler_parses_jav321_fixture() -> None:
    class FakeFetcher:
        def post(self, url: str, data: dict[str, str] | None = None) -> FetchResponse:
            assert url == "https://www.jav321.com/search"
            assert data == {"sn": "SSIS-123"}
            return FetchResponse("https://www.jav321.com/video/SSIS-123", 200, JAV321_DETAIL_HTML)

        def get(self, url: str) -> FetchResponse:
            raise AssertionError("get should not be called")

        def close(self) -> None:
            pass

    crawler = JavLibraryCrawler(
        JavLibraryCrawlerConfig(provider_order=("jav321",)),
        fetcher=FakeFetcher(),  # type: ignore[arg-type]
    )

    video = crawler.lookup("ssis123")

    assert video.source == "jav321"
    assert video.code == "SSIS-123"
    assert video.title == "Jav321 Fixture Title"
    assert video.cover_url == "https://www.jav321.com/cover321.jpg"
    assert video.release_date == "2026-03-04"
    assert video.runtime_minutes == 88
    assert video.studio == "Jav321 Studio"
    assert video.series == "Jav321 Series"
    assert video.actors == ["Jav321 Alice"]
    assert video.genres == ["Drama"]
    assert video.rating == 4.6


def test_crawler_parses_javdb_fixture() -> None:
    class FakeFetcher:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def get(self, url: str) -> FetchResponse:
            self.urls.append(url)
            if "/search" in url:
                return FetchResponse(url, 200, JAVDB_SEARCH_HTML)
            return FetchResponse(url, 200, JAVDB_DETAIL_HTML)

        def post(self, url: str, data: dict[str, str] | None = None) -> FetchResponse:
            raise AssertionError("post should not be called")

        def close(self) -> None:
            pass

    fetcher = FakeFetcher()
    crawler = JavLibraryCrawler(
        JavLibraryCrawlerConfig(provider_order=("javdb",)),
        fetcher=fetcher,  # type: ignore[arg-type]
    )

    video = crawler.lookup("SSIS-123")

    assert video.source == "javdb"
    assert video.code == "SSIS-123"
    assert video.title == "JavDB Fixture Title"
    assert video.cover_url == "https://javdb.com/coverdb.jpg"
    assert video.release_date == "2026-04-05"
    assert video.runtime_minutes == 77
    assert video.studio == "JavDB Studio"
    assert video.publisher == "JavDB Publisher"
    assert video.series == "JavDB Series"
    assert video.director == "JavDB Director"
    assert video.actors == ["JavDB Alice"]
    assert video.genres == ["Drama", "HD"]
    assert video.rating == 4.8
    assert video.trailer_url == "https://javdb.com/trailers/SSIS-123.mp4"
    assert video.preview_image_urls == [
        "https://javdb.com/samples/SSIS-123-1.jpg",
        "https://javdb.com/samples/SSIS-123-2.webp",
        "https://javdb.com/samples/SSIS-123_l_3.jpg",
    ]
    assert video.resource_page_url == "https://javdb.com/v/db123"
    assert fetcher.urls == ["https://javdb.com/search?q=SSIS-123&locale=zh", "https://javdb.com/v/db123"]


def test_crawler_detects_javdb_login_only_trailer() -> None:
    detail_html = JAVDB_DETAIL_HTML.replace(
        '<video id="preview-video" src="/trailers/SSIS-123.mp4"></video>',
        '<a class="preview-video-container" href="/login"><span>預告片</span></a>',
    )

    class FakeFetcher:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def get(self, url: str) -> FetchResponse:
            self.urls.append(url)
            if "/search" in url:
                return FetchResponse(url, 200, JAVDB_SEARCH_HTML)
            return FetchResponse(url, 200, detail_html)

        def post(self, url: str, data: dict[str, str] | None = None) -> FetchResponse:
            raise AssertionError("post should not be called")

        def close(self) -> None:
            pass

    crawler = JavLibraryCrawler(
        JavLibraryCrawlerConfig(provider_order=("javdb",)),
        fetcher=FakeFetcher(),  # type: ignore[arg-type]
    )

    video = crawler.lookup("SSIS-123")

    assert video.trailer_url is None
    assert video.trailer_page_url is None
    assert video.trailer_requires_login is True


def test_crawler_searches_javdb_by_chinese_query() -> None:
    class FakeFetcher:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def get(self, url: str) -> FetchResponse:
            self.urls.append(url)
            return FetchResponse(url, 200, JAVDB_LIST_HTML)

        def close(self) -> None:
            pass

    fetcher = FakeFetcher()
    crawler = JavLibraryCrawler(JavLibraryCrawlerConfig(), fetcher=fetcher)  # type: ignore[arg-type]

    results = crawler.search_javdb("三上悠亚", limit=2)

    assert fetcher.urls == ["https://javdb.com/search?q=%E4%B8%89%E4%B8%8A%E6%82%A0%E4%BA%9A&f=all&locale=zh&page=1"]
    assert [item.code for item in results] == ["SSIS-123", "ABP-456"]
    assert results[0].title == "JavDB Fixture Title"
    assert results[0].cover_url == "https://javdb.com/coverdb.jpg"
    assert "三上悠亚" in results[0].actors


def test_crawler_title_search_prioritizes_title_match() -> None:
    class FakeFetcher:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def get(self, url: str) -> FetchResponse:
            self.urls.append(url)
            return FetchResponse(url, 200, JAVDB_SORT_HTML)

        def close(self) -> None:
            pass

    fetcher = FakeFetcher()
    crawler = JavLibraryCrawler(JavLibraryCrawlerConfig(), fetcher=fetcher)  # type: ignore[arg-type]

    results = crawler.search_javdb("三上悠亚", limit=2)

    assert fetcher.urls == ["https://javdb.com/search?q=%E4%B8%89%E4%B8%8A%E6%82%A0%E4%BA%9A&f=all&locale=zh&page=1"]
    assert [item.code for item in results] == ["ABP-222", "SSIS-111"]


def test_crawler_actor_search_prioritizes_actor_match() -> None:
    class FakeFetcher:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def get(self, url: str) -> FetchResponse:
            self.urls.append(url)
            return FetchResponse(url, 200, JAVDB_SORT_HTML)

        def close(self) -> None:
            pass

    fetcher = FakeFetcher()
    crawler = JavLibraryCrawler(JavLibraryCrawlerConfig(), fetcher=fetcher)  # type: ignore[arg-type]

    results = crawler.search_javdb_actor("三上悠亚", limit=2)

    assert fetcher.urls == ["https://javdb.com/search?q=%E4%B8%89%E4%B8%8A%E6%82%A0%E4%BA%9A&f=actor&locale=zh&page=1"]
    assert [item.code for item in results] == ["SSIS-111", "ABP-222"]


def test_crawler_fetches_javdb_ranking() -> None:
    class FakeFetcher:
        def __init__(self) -> None:
            self.urls: list[str] = []

        def get(self, url: str) -> FetchResponse:
            self.urls.append(url)
            return FetchResponse(url, 200, JAVDB_LIST_HTML)

        def close(self) -> None:
            pass

    fetcher = FakeFetcher()
    crawler = JavLibraryCrawler(JavLibraryCrawlerConfig(), fetcher=fetcher)  # type: ignore[arg-type]

    results = crawler.javdb_ranking("week", limit=2)

    assert fetcher.urls == ["https://javdb.com/rankings/movies?period=weekly&locale=zh&page=1"]
    assert [(item.rank, item.code) for item in results] == [(1, "SSIS-123"), (2, "ABP-456")]


def test_crawler_total_timeout_stops_between_requests() -> None:
    class FakeFetcher:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, url: str) -> FetchResponse:
            self.calls += 1
            time.sleep(0.02)
            if self.calls == 1:
                return FetchResponse(url, 200, SEARCH_HTML)
            return FetchResponse(url, 200, DETAIL_HTML)

        def close(self) -> None:
            pass

    fetcher = FakeFetcher()
    crawler = JavLibraryCrawler(
        JavLibraryCrawlerConfig(provider_order=("javlibrary",), total_timeout_seconds=0.001),
        fetcher=fetcher,  # type: ignore[arg-type]
    )

    with pytest.raises(JavLibraryTimeoutError):
        crawler.lookup("SSIS-123")

    assert fetcher.calls == 1


def test_javlibrary_provider_does_not_swallow_unexpected_parse_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeFetcher:
        def get(self, url: str) -> FetchResponse:
            return FetchResponse(url, 200, DETAIL_HTML)

        def close(self) -> None:
            pass

    def explode(_html: str, _url: str) -> JavLibraryVideo:
        raise RuntimeError("parser exploded")

    monkeypatch.setattr("javlibrary_crawler.providers.parse_video_detail", explode)
    crawler = JavLibraryCrawler(
        JavLibraryCrawlerConfig(provider_order=("javlibrary",)),
        fetcher=FakeFetcher(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="parser exploded"):
        crawler.lookup("SSIS-123")


def test_javlibrary_service_caches_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []

    class FakeCrawler:
        def __init__(self, _config: object) -> None:
            pass

        def lookup(self, code: str) -> JavLibraryVideo:
            calls.append(code)
            return JavLibraryVideo(code=code, title="Cached Title", url="https://example.test/video")

        def close(self) -> None:
            pass

    monkeypatch.setattr("backend.javlibrary_service.JavLibraryCrawler", FakeCrawler)
    service = JavLibraryService(JavLibraryServiceConfig(data_dir=tmp_path))
    service.initialize()

    first = service.lookup_video("ssis123")
    second = service.lookup_video("SSIS-123")

    assert first["code"] == "SSIS-123"
    assert second["cache_hit"] is True
    assert calls == ["SSIS-123"]


def test_javlibrary_service_uses_error_specific_cache_ttl(tmp_path: Path) -> None:
    service = JavLibraryService(
        JavLibraryServiceConfig(
            data_dir=tmp_path,
            failure_cache_ttl_seconds=600,
            not_found_cache_ttl_seconds=86400,
            blocked_cache_ttl_seconds=120,
            timeout_cache_ttl_seconds=60,
        )
    )

    assert service._error_cache_ttl("JAV_NOT_FOUND") == 86400
    assert service._error_cache_ttl("JAV_SOURCE_BLOCKED") == 120
    assert service._error_cache_ttl("JAV_FETCH_TIMEOUT") == 60
    assert service._error_cache_ttl("JAV_FETCH_FAILED") == 60
    assert service._error_cache_ttl("JAVLIBRARY_ERROR") == 60


def test_javlibrary_service_searches_actor_alias_candidates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seen_queries: list[str] = []

    class FakeCrawler:
        def __init__(self, _config: object) -> None:
            pass

        def search_javdb_actor(self, query: str, page: int = 1, limit: int = 10) -> list[JavLibrarySearchItem]:
            seen_queries.append(query)
            if query != "橋本ありな":
                return []
            return [
                JavLibrarySearchItem(
                    code="SSIS-123",
                    title="Alias Hit",
                    url="https://javdb.com/v/alias",
                    source="javdb",
                    actors=["橋本ありな"],
                )
            ]

        def close(self) -> None:
            pass

    monkeypatch.setattr("backend.javlibrary_service.JavLibraryCrawler", FakeCrawler)
    service = JavLibraryService(
        JavLibraryServiceConfig(
            data_dir=tmp_path,
            actor_alias_online=False,
        )
    )
    service.initialize()

    payload = service.search_actors("桥本有菜", limit=5)

    assert "桥本有菜" in seen_queries
    assert "橋本ありな" in seen_queries
    assert payload["results"][0]["code"] == "SSIS-123"
    assert "橋本ありな" in service._cached_actor_aliases("桥本有菜")


def test_javlibrary_service_loads_actor_alias_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    alias_path = tmp_path / "actor-aliases.yml"
    alias_path.write_text(
        """
aliases:
  测试译名:
    - テスト女優
""",
        encoding="utf-8",
    )
    seen_queries: list[str] = []

    class FakeCrawler:
        def __init__(self, _config: object) -> None:
            pass

        def search_javdb_actor(self, query: str, page: int = 1, limit: int = 10) -> list[JavLibrarySearchItem]:
            seen_queries.append(query)
            return []

        def close(self) -> None:
            pass

    monkeypatch.setattr("backend.javlibrary_service.JavLibraryCrawler", FakeCrawler)
    service = JavLibraryService(
        JavLibraryServiceConfig(
            data_dir=tmp_path,
            actor_alias_path=alias_path,
            actor_alias_online=False,
        )
    )
    service.initialize()

    service.search_actors("测试译名", limit=5)

    assert "测试译名" in seen_queries
    assert "テスト女優" in seen_queries


def test_option_file_loads_yaml(tmp_path: Path) -> None:
    option_path = tmp_path / "javlibrary-option.yml"
    option_path.write_text(
        """
base_url: https://example.test
language: en
timeout_seconds: 12
total_timeout_seconds: 22
fetcher: browser
provider_order:
  - javlibrary
  - javbus
javbus_base_url: https://bus.example.test
request:
  user_agent: TestAgent
  proxy: http://127.0.0.1:8080
  impersonate: chrome120
  retry_times: 4
browser:
  profile_dir: ./profile
  channel: msedge
  headless: true
  wait_seconds: 90
""",
        encoding="utf-8",
    )

    option = create_option_by_file(option_path)

    assert option.base_url == "https://example.test"
    assert option.language == "en"
    assert option.timeout_seconds == 12
    assert option.total_timeout_seconds == 22
    assert option.fetcher == "browser"
    assert option.provider_order == ("javlibrary", "javbus")
    assert option.javbus_base_url == "https://bus.example.test"
    assert option.user_agent == "TestAgent"
    assert option.proxy == "http://127.0.0.1:8080"
    assert option.impersonate == "chrome120"
    assert option.retry_times == 4
    assert option.browser_profile_dir == str((tmp_path / "profile").resolve())
    assert option.browser_channel == "msedge"
    assert option.browser_headless is True
    assert option.browser_wait_seconds == 90


def test_cli_prints_text(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fake_lookup(code: str, _config: object) -> JavLibraryVideo:
        assert code == "ssis123"
        return JavLibraryVideo(
            code="SSIS-123",
            title="A Sample Title",
            url="https://example.test/video",
            actors=["Alice", "Bob"],
            genres=["Drama"],
            runtime_minutes=123,
        )

    monkeypatch.setattr(cli, "lookup", fake_lookup)

    assert cli.main(["ssis123"]) == 0

    output = capsys.readouterr().out
    assert "Code: SSIS-123" in output
    assert "Actors: Alice / Bob" in output


def test_cli_prints_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fake_lookup(_code: str, _config: object) -> JavLibraryVideo:
        return JavLibraryVideo(code="SSIS-123", title="A Sample Title", url="https://example.test/video")

    monkeypatch.setattr(cli, "lookup", fake_lookup)

    assert cli.main(["SSIS-123", "--json"]) == 0

    payload = capsys.readouterr().out
    assert '"code": "SSIS-123"' in payload
    assert '"title": "A Sample Title"' in payload


def test_cli_accepts_browser_fetcher_args(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_lookup(_code: str, config: object) -> JavLibraryVideo:
        seen["fetcher"] = getattr(config, "fetcher")
        seen["impersonate"] = getattr(config, "impersonate")
        seen["retry_times"] = getattr(config, "retry_times")
        seen["total_timeout_seconds"] = getattr(config, "total_timeout_seconds")
        seen["provider_order"] = getattr(config, "provider_order")
        seen["browser_profile_dir"] = getattr(config, "browser_profile_dir")
        seen["browser_channel"] = getattr(config, "browser_channel")
        seen["browser_headless"] = getattr(config, "browser_headless")
        seen["browser_wait_seconds"] = getattr(config, "browser_wait_seconds")
        return JavLibraryVideo(code="SSIS-123", title="A Sample Title", url="https://example.test/video")

    monkeypatch.setattr(cli, "lookup", fake_lookup)

    assert cli.main(
        [
            "SSIS-123",
            "--fetcher",
            "browser",
            "--impersonate",
            "chrome120",
            "--retry",
            "5",
            "--total-timeout",
            "33",
            "--providers",
            "javbus,jav321",
            "--browser-profile-dir",
            "./profile",
            "--browser-channel",
            "chrome",
            "--browser-headless",
            "--browser-wait",
            "99",
        ]
    ) == 0
    assert seen == {
        "fetcher": "browser",
        "impersonate": "chrome120",
        "retry_times": 5,
        "total_timeout_seconds": 33.0,
        "provider_order": ("javbus", "jav321"),
        "browser_profile_dir": "./profile",
        "browser_channel": "chrome",
        "browser_headless": True,
        "browser_wait_seconds": 99.0,
    }


def test_cli_reports_missing_option_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing_path = tmp_path / "missing.yml"

    assert cli.main(["SSIS-123", "-o", str(missing_path)]) == 8

    captured = capsys.readouterr()
    assert "JAV_OPTION_NOT_FOUND" in captured.err
    assert "Traceback" not in captured.err


def test_cli_resolves_option_path_from_project_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    package_dir = tmp_path / "javlibrary_crawler"
    package_dir.mkdir()
    fake_cli = package_dir / "cli.py"
    fake_cli.write_text("", encoding="utf-8")
    option_dir = tmp_path / "config"
    option_dir.mkdir()
    option_path = option_dir / "javlibrary-option.yml"
    option_path.write_text("base_url: https://example.test\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    monkeypatch.setattr(cli, "__file__", str(fake_cli))
    monkeypatch.chdir(run_dir)

    assert cli._resolve_option_path("config/javlibrary-option.yml") == option_path


def test_parse_random_curl_impersonates() -> None:
    assert "chrome123" in _parse_impersonates("random")
    assert _parse_impersonates("chrome123, firefox135") == ("chrome123", "firefox135")
