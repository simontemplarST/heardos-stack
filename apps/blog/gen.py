#!/usr/bin/env python3
"""gen.py — build the example.com blog from the Listmonk public archive (2026-10-18).

Reads Listmonk's archive RSS (full content is on: app.enable_public_archive_rss_content), keeps only the campaign body
(the inside of the archive template's <div class="wrap">), and writes a static site to OUT:
  index.html, p/<slug>/index.html, feed.xml, style.css.
A campaign shows up here once it is sent AND "Publish to public archive" is ticked on its Archive tab.
Stdlib only. Builds into a temp dir, then swaps it in, so nginx never serves a half-written site.
Env: FEED (default http://listmonk:9000/archive.xml), OUT (default /site), SITE_URL, SITE_TITLE, SUBSCRIBE_URL."""
import email.utils, html, os, re, shutil, sys, tempfile, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

FEED = os.environ.get("FEED", "http://listmonk:9000/archive.xml")
OUT = Path(os.environ.get("OUT", "/site"))
SITE_URL = os.environ.get("SITE_URL", "https://example.com").rstrip("/")
TITLE = os.environ.get("SITE_TITLE", "example.com")
SUBSCRIBE = os.environ.get("SUBSCRIBE_URL", "https://news.example.com/subscription/form")
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}encoded"
HERE = Path(__file__).parent


class WrapExtractor(HTMLParser):
    """Finds the raw source between <div class="wrap"> and its matching </div>."""
    def __init__(self, src):
        super().__init__(convert_charrefs=False)
        self.src, self.lines = src, src.splitlines(keepends=True)
        self.depth, self.start, self.end = 0, None, None

    def _off(self):
        line, col = self.getpos()
        return sum(len(l) for l in self.lines[:line - 1]) + col

    def handle_starttag(self, tag, attrs):
        if tag != "div" or self.end is not None:
            return
        if self.start is None:
            if "wrap" in (dict(attrs).get("class") or "").split():
                self.start = self._off() + len(self.get_starttag_text())
                self.depth = 1
        else:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag == "div" and self.start is not None and self.end is None:
            self.depth -= 1
            if self.depth == 0:
                self.end = self._off()


def body_of(doc):
    """Campaign body only: inside .wrap if the template has one, else inside <body>, else the whole thing."""
    p = WrapExtractor(doc)
    p.feed(doc)
    if p.start is not None and p.end is not None:
        return doc[p.start:p.end].strip()
    m = re.search(r"<body[^>]*>(.*)</body>", doc, re.S | re.I)
    return (m.group(1) if m else doc).strip()


class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.out, self.skip = [], 0
    def handle_starttag(self, t, a): self.skip += t in ("style", "script")
    def handle_endtag(self, t): self.skip -= t in ("style", "script") and self.skip > 0
    def handle_data(self, d):
        if not self.skip: self.out.append(d)


def excerpt(fragment, n=220):
    t = Text(); t.feed(fragment)
    s = re.sub(r"\s+", " ", "".join(t.out)).strip()
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0] + "…"


def fetch_posts():
    posts, seen, page = [], set(), 1
    while True:
        req = urllib.request.Request(f"{FEED}?page={page}&per_page=50", headers={"User-Agent": "example-blog"})
        with urllib.request.urlopen(req, timeout=60) as r:
            root = ET.fromstring(r.read())
        items = root.findall("./channel/item")
        new = 0
        for it in items:
            link = (it.findtext("link") or "").strip()
            if not link or link in seen:
                continue
            seen.add(link); new += 1
            slug = re.sub(r"[^A-Za-z0-9._-]", "-", link.rstrip("/").rsplit("/", 1)[-1]) or f"post-{len(posts)}"
            date = email.utils.parsedate_to_datetime(it.findtext("pubDate")) if it.findtext("pubDate") else datetime.now(timezone.utc)
            content = it.findtext(CONTENT_NS) or it.findtext("description") or ""
            body = body_of(content)
            posts.append({"title": (it.findtext("title") or "Untitled").strip(), "slug": slug, "date": date,
                          "body": body, "excerpt": excerpt(body), "source": link})
        if not items or not new or len(items) < 50:
            break
        page += 1
    posts.sort(key=lambda p: p["date"], reverse=True)
    return posts


def page(title, main, desc="", canonical=""):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(desc)}">
<link rel="canonical" href="{html.escape(canonical or SITE_URL + '/')}">
<link rel="alternate" type="application/rss+xml" title="{html.escape(TITLE)}" href="/feed.xml">
<link rel="icon" href="https://cloud.example.com/brand/favicon.ico">
<link rel="stylesheet" href="/style.css">
</head>
<body>
<header class="site"><a class="brand" href="/">{html.escape(TITLE)}</a>
<nav><a href="{html.escape(SUBSCRIBE)}">Subscribe</a><a href="/feed.xml">RSS</a></nav></header>
<main>
{main}
</main>
<footer class="site">Posts from the newsletter · <a href="{html.escape(SUBSCRIBE)}">get them by email</a></footer>
</body>
</html>
"""


def fmt(d):
    return f"{d:%B} {d.day}, {d.year}"


def build(posts, out):
    (out / "style.css").write_text((HERE / "style.css").read_text())
    if posts:
        items = "\n".join(
            f'<article class="card"><time datetime="{p["date"].date().isoformat()}">{fmt(p["date"])}</time>'
            f'<h2><a href="/p/{p["slug"]}/">{html.escape(p["title"])}</a></h2>'
            f'<p>{html.escape(p["excerpt"])}</p></article>' for p in posts)
    else:
        items = f'<p class="empty">No posts yet. <a href="{html.escape(SUBSCRIBE)}">Subscribe</a> to get the first one by email.</p>'
    (out / "index.html").write_text(page(TITLE, f'<section class="list">\n{items}\n</section>', f"Posts from the {TITLE} newsletter"))
    for i, p in enumerate(posts):
        newer, older = (posts[i - 1] if i else None), (posts[i + 1] if i + 1 < len(posts) else None)
        pager = "".join([
            f'<a class="older" href="/p/{older["slug"]}/">← {html.escape(older["title"])}</a>' if older else "<span></span>",
            f'<a class="newer" href="/p/{newer["slug"]}/">{html.escape(newer["title"])} →</a>' if newer else "<span></span>"])
        main = (f'<article class="post"><time datetime="{p["date"].date().isoformat()}">{fmt(p["date"])}</time>'
                f'<h1>{html.escape(p["title"])}</h1>\n<div class="body">\n{p["body"]}\n</div></article>\n<nav class="pager">{pager}</nav>')
        d = out / "p" / p["slug"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(page(f'{p["title"]} · {TITLE}', main, p["excerpt"], f'{SITE_URL}/p/{p["slug"]}/'))
    rss = "".join(
        f"<item><title>{html.escape(p['title'])}</title><link>{SITE_URL}/p/{p['slug']}/</link>"
        f"<guid>{SITE_URL}/p/{p['slug']}/</guid><pubDate>{email.utils.format_datetime(p['date'])}</pubDate>"
        f"<description>{html.escape(p['body'])}</description></item>" for p in posts)
    (out / "feed.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{html.escape(TITLE)}</title>'
        f"<link>{SITE_URL}/</link><description>Posts from the {html.escape(TITLE)} newsletter</description>{rss}</channel></rss>")


def main():
    try:
        posts = fetch_posts()
    except Exception as e:   # keep serving the last good build
        print(f"fetch failed, keeping current site: {e}", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=OUT, prefix=".build-"))
    build(posts, tmp)
    os.chmod(tmp, 0o755)
    cur, old = OUT / "current", OUT / ".old"
    shutil.rmtree(old, ignore_errors=True)
    if cur.exists():
        cur.rename(old)
    tmp.rename(cur)
    shutil.rmtree(old, ignore_errors=True)
    print(f"built {len(posts)} post(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
