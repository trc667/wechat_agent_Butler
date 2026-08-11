# -*- coding: utf-8 -*-
"""news.py 单元测试：RSS/Atom 解析、多源轮流、失败降级。"""
import news

RSS2 = '<?xml version="1.0"?><rss version="2.0"><channel>' \
    '<title>Src</title>' \
    '<item><title>新闻A</title><link>http://a</link></item>' \
    '<item><title>新闻B</title></item>' \
    '<item><title></title></item>' \
    '</channel></rss>'

ATOM = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">' \
    '<title>Src</title><entry><title>原子新闻</title></entry></feed>'


def test_parse_rss2():
    items = news.parse_rss(RSS2, "测试源")
    assert items == [("新闻A", "测试源"), ("新闻B", "测试源")]  # 空标题跳过


def test_parse_atom():
    items = news.parse_rss(ATOM, "测试源")
    assert items == [("原子新闻", "测试源")]


def test_parse_invalid():
    assert news.parse_rss("not xml", "测试源") == []
    assert news.parse_rss("", "测试源") == []


def _fake_rss_resp(body):
    class FakeResp:
        def raise_for_status(self):
            pass

        @property
        def content(self):
            return body

    return FakeResp()


def test_fetch_news_round_robin(monkeypatch):
    """多源轮流取：每个源各出一条，保证混合。"""
    bodies = {
        "https://www.infoq.cn/feed": RSS2,  # 新闻A/B
        "https://www.ithome.com/rss/": "<rss><channel><item><title>新闻C</title></item><item><title>新闻D</title></item></channel></rss>",
        "https://sspai.com/feed": "<rss><channel><item><title>新闻E</title></item></channel></rss>",
    }

    def fake_get(url, **kw):
        return _fake_rss_resp(bodies.get(url, ""))

    monkeypatch.setattr(news.requests, "get", fake_get)
    text = news.fetch_news(max_items=3)
    assert text.startswith("今日科技/AI 新闻：")
    assert "新闻A（InfoQ）" in text
    assert "新闻C（IT之家）" in text
    assert "新闻E（少数派）" in text


def test_fetch_news_all_fail(monkeypatch):
    def boom(*a, **kw):
        raise OSError("网络错误")

    monkeypatch.setattr(news.requests, "get", boom)
    assert news.fetch_news() is None


def test_fetch_news_no_parsable(monkeypatch):
    def fake_get(url, **kw):
        return _fake_rss_resp("<html>not rss</html>")

    monkeypatch.setattr(news.requests, "get", fake_get)
    assert news.fetch_news() is None
