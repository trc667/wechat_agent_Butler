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


def test_fetch_news_ai_priority(monkeypatch):
    """AI 相关新闻优先：量子位的 DeepSeek 重磅新闻排在普通新闻前面。"""
    bodies = {
        "https://www.qbitai.com/feed":
            "<rss><channel><item><title>刚刚！DeepSeek V4 Pro正式版发布</title></item>"
            "<item><title>普通新闻X</title></item></channel></rss>",
        "https://www.infoq.cn/feed": RSS2,  # 新闻A/B
        "https://www.ithome.com/rss/":
            "<rss><channel><item><title>新闻C</title></item></channel></rss>",
        "https://sspai.com/feed":
            "<rss><channel><item><title>新闻E</title></item></channel></rss>",
    }

    def fake_get(url, **kw):
        return _fake_rss_resp(bodies.get(url, ""))

    monkeypatch.setattr(news.requests, "get", fake_get)
    text = news.fetch_news(max_items=3)
    assert text.startswith("今日科技/AI 新闻：")
    # AI 评分高的 DeepSeek 新闻必须排第一
    lines = text.split("\n")[1:]
    assert "DeepSeek" in lines[0]
    assert len(lines) <= 3


def test_fetch_news_dedup(monkeypatch):
    """同一标题跨源重复时只保留一条。"""
    dup = "<rss><channel><item><title>同一个标题</title></item></channel></rss>"
    bodies = {url: dup for _, url in news.RSS_SOURCES}

    def fake_get(url, **kw):
        return _fake_rss_resp(bodies[url])

    monkeypatch.setattr(news.requests, "get", fake_get)
    text = news.fetch_news(max_items=5)
    assert text.count("同一个标题") == 1


def test_ai_score():
    assert news._ai_score("DeepSeek V4 Pro 正式发布") >= 2
    assert news._ai_score("GPT-5.5 训练细节曝光") >= 2
    assert news._ai_score("苹果面临诉讼") == 0
    assert news._ai_score("") == 0


# ---------- 新闻存档 ----------

def test_save_and_load_history(monkeypatch, tmp_path):
    f = tmp_path / "news_history.json"
    monkeypatch.setattr(news, "HISTORY_FILE", str(f))
    news.save_history("2026-08-14", "周五新闻")
    news.save_history("2026-08-15", "周六新闻")
    hist = news.load_history()
    assert hist["2026-08-14"] == "周五新闻"
    assert hist["2026-08-15"] == "周六新闻"


def test_load_history_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(news, "HISTORY_FILE", str(tmp_path / "no.json"))
    assert news.load_history() == {}


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
