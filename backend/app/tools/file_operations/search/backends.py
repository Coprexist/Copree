"""搜索后端表（唯一来源）：官方优先、失败轮播、用了哪个要说清楚。

只登记实测过的后端。实测口径 = 在本机容器里发真实查询，看结果是否与查询相关：
- bing（RSS 出口，优先）：可达，且是唯一能搜到 AIsChat 的后端（掘金 / GitHub Coprexist / CSDN /
  gitcode，全部命中）。它偶尔会整轮拿不到东西（同一个请求前后几秒结果不同），所以编排层失败要重试。
- bing-html：同一引擎的网页出口。RSS 空手时兜底；正常查询相关（清华大学 官网 -> tsinghua.edu.cn），
  冷门词会被塞无关填充（Copree AIsChat -> 外设驱动站、视频站），由 rank.relevant 拦掉。
- sogou / so360：可达，作为 Bing 之后的补充；两者的结果链接是跳转链，
  用页面里记录的真实地址字段（linkurl / data-mdurl）还原，还原不了就不采这条。
- duckduckgo：本机网络不可达（Errno 101），不登记。

要接需要密钥的搜索 API（Tavily / Serper 之类）就在这张表里加一个 Backend，
工具契约与排序逻辑都不用动。
"""
from __future__ import annotations

import html as html_mod
import re
from dataclasses import dataclass
from urllib.parse import quote_plus

from app.tools.file_operations.search.authority import domain_of

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}

_TAG = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    """HTML 片段到纯文本（去标签 + 反转义 + 压空白）。"""
    return re.sub(r"\s+", " ", html_mod.unescape(_TAG.sub("", fragment or ""))).strip()


@dataclass(frozen=True)
class Backend:
    name: str
    label: str
    template: str

    def url(self, query: str, count: int) -> str:
        return self.template.format(q=quote_plus(query), n=count)

    def parse(self, page: str, count: int) -> list[dict]:  # pragma: no cover - 子类实现
        raise NotImplementedError


class BingRssBackend(Backend):
    """必应的 RSS 出口：约 4KB 结构化 XML，字段齐全（title/link/description/pubDate），
    没有广告位也不受页面改版影响——同一个引擎，优先走这条。"""

    def parse(self, page: str, count: int) -> list[dict]:
        out: list[dict] = []
        for item in re.findall(r"<item>(.*?)</item>", page, re.DOTALL):
            def field(tag: str) -> str:
                m = re.search(r"<" + tag + r">(.*?)</" + tag + r">", item, re.DOTALL)
                return _text(html_mod.unescape(m.group(1))) if m else ""
            title, link = field("title"), field("link")
            if not title or not link.startswith("http"):
                continue
            out.append({
                "title": title,
                "url": link,
                "snippet": field("description"),
                "published_at": field("pubDate"),
            })
            if len(out) >= count:
                break
        return out


class BingHtmlBackend(Backend):
    """必应的网页出口（RSS 空手时兜底）。结果块 li.b_algo，标题在 h2 > a。"""

    def parse(self, page: str, count: int) -> list[dict]:
        out: list[dict] = []
        for block in re.split(r'<li[^>]*class="b_algo"[^>]*>', page)[1:]:
            m = re.search(r'<h2[^>]*>.*?<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
            if not m:
                continue
            title = _text(m.group(2))
            if not title:
                continue
            snippet = ""
            s = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
            if s:
                snippet = _text(s.group(1))
            date = re.search(r'<span[^>]*class="news_dt"[^>]*>(.*?)</span>', block, re.DOTALL)
            out.append({
                "title": title,
                "url": m.group(1),
                "snippet": snippet,
                "published_at": _text(date.group(1)) if date else "",
            })
            if len(out) >= count:
                break
        return out


class So360Backend(Backend):
    """360 搜索。结果块 li.res-list，真实地址在 a[data-mdurl]，摘要在 p.res-desc。"""

    def parse(self, page: str, count: int) -> list[dict]:
        out: list[dict] = []
        for block in re.split(r'<li[^>]*class="res-list', page)[1:]:
            m = re.search(r'<h3[^>]*>(.*?)</h3>', block, re.DOTALL)
            real = re.search(r'data-mdurl="(https?://[^"]+)"', block)
            if not m or not real:
                continue
            title = _text(m.group(1))
            if not title:
                continue
            snippet = ""
            s = re.search(r'<p[^>]*class="res-desc"[^>]*>(.*?)</p>', block, re.DOTALL)
            if s:
                snippet = _text(s.group(1))
            date = re.search(r'<span[^>]*class="[^"]*g-c-gray[^"]*"[^>]*>(.*?)</span>', block, re.DOTALL)
            out.append({
                "title": title,
                "url": real.group(1),
                "snippet": snippet,
                "published_at": _text(date.group(1)).rstrip("- ").strip() if date else "",
            })
            if len(out) >= count:
                break
        return out


class SogouBackend(Backend):
    """搜狗。结果块 div.vrwrap，真实地址在 a 的 linkurl 属性（无引号）。"""

    def parse(self, page: str, count: int) -> list[dict]:
        out: list[dict] = []
        for block in re.split(r'<div[^>]*class="vrwrap', page)[1:]:
            m = re.search(r'<h3[^>]*class="vr-title[^"]*"[^>]*>\s*<a[^>]*>(.*?)</a>', block, re.DOTALL)
            if not m:
                continue
            title = _text(m.group(1))
            anchor = re.search(r'<h3[^>]*class="vr-title[^"]*"[^>]*>\s*<a([^>]*)>', block, re.DOTALL)
            real = re.search(r'linkurl=(\S+?)[\s>]', anchor.group(1)) if anchor else None
            if not title or not real:
                continue
            snippet = _text(block[:600])
            body = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
            if body:
                snippet = _text(body.group(1))
            out.append({
                "title": title,
                "url": real.group(1).strip('"'),
                "snippet": snippet,
                "published_at": "",
            })
            if len(out) >= count:
                break
        return out


# 顺序即优先级：主后端跑完没结果才轮到后面的
BACKENDS: tuple[Backend, ...] = (
    BingRssBackend("bing", "必应", "https://www.bing.com/search?format=rss&q={q}"),
    BingHtmlBackend("bing-html", "必应(网页)", "https://www.bing.com/search?q={q}&count={n}"),
    So360Backend("so360", "360 搜索", "https://www.so.com/s?q={q}"),
    SogouBackend("sogou", "搜狗", "https://www.sogou.com/web?query={q}"),
)


def backend_names() -> list[str]:
    return [b.name for b in BACKENDS]


def stamp(items, backend: Backend) -> list[dict]:
    """给结果盖上来源后端与域名，排序与展示都要用。"""
    for item in items:
        item["provider"] = backend.name
        item.setdefault("domain", domain_of(item.get("url", "")))
    return items
