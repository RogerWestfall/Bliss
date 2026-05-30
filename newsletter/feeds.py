"""Fetch content for each newsletter section from free RSS feeds and APIs."""

import logging
import re

import requests
from lxml import etree
from lxml import html as lhtml

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "BlissNewsletter/2.0 (rogerlwestfall@gmail.com)"}

# Namespaces used in RSS/Atom feeds
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


# ── Quote of the Day ─────────────────────────────────────────────────────────

_FALLBACK_QUOTE = {
    "quote": "Keep your face always toward the sunshine, and shadows will fall behind you.",
    "author": "Walt Whitman",
}


def fetch_quote() -> dict:
    """Returns {quote, author} from the ZenQuotes free API."""
    try:
        resp = requests.get(
            "https://zenquotes.io/api/today", headers=_HEADERS, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()[0]
        return {"quote": data["q"], "author": data["a"]}
    except Exception as exc:
        logger.warning("ZenQuotes failed (%s) — using fallback quote", exc)
        return _FALLBACK_QUOTE


# ── RSS helpers ───────────────────────────────────────────────────────────────

def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _clean_html(raw: str, max_chars: int = 350) -> str:
    """Strip HTML tags, collapse whitespace, truncate."""
    if not raw:
        return ""
    try:
        text = lhtml.fromstring(raw).text_content()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "…"
    return text


def _img_from_html(raw: str) -> str:
    """Find the first <img src> in an HTML string."""
    if not raw:
        return ""
    try:
        root = lhtml.fromstring(raw)
        for img in root.iter("img"):
            src = img.get("src", "")
            if src.startswith("http"):
                return src
    except Exception:
        pass
    return ""


def _parse_feed(url: str) -> list[dict]:
    """Fetch and parse an RSS or Atom feed. Returns a list of entry dicts."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        root = etree.fromstring(resp.content)
    except Exception as exc:
        logger.warning("Feed %s failed: %s", url, exc)
        return []

    # RSS 2.0 items live at ./channel/item; Atom entries at ./entry
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")

    entries = []
    for item in items[:15]:
        # Title
        title = _text(
            item.find("title") or item.find("{http://www.w3.org/2005/Atom}title")
        )

        # Link
        link_el = item.find("link")
        if link_el is not None:
            link = (link_el.text or link_el.get("href", "")).strip()
        else:
            atom_link = item.find("{http://www.w3.org/2005/Atom}link")
            link = atom_link.get("href", "").strip() if atom_link is not None else ""

        # Summary / description
        for tag in ("description", "{http://www.w3.org/2005/Atom}summary",
                    "{http://www.w3.org/2005/Atom}content",
                    "{http://purl.org/rss/1.0/modules/content/}encoded"):
            summary_el = item.find(tag)
            if summary_el is not None and summary_el.text:
                summary = summary_el.text
                break
        else:
            summary = ""

        # Image — media:thumbnail, media:content, then parse HTML
        image = ""
        mt = item.find("{http://search.yahoo.com/mrss/}thumbnail")
        if mt is not None:
            image = mt.get("url", "")
        if not image:
            mc = item.find("{http://search.yahoo.com/mrss/}content")
            if mc is not None and (mc.get("type", "") or "").startswith("image"):
                image = mc.get("url", "")
        if not image:
            image = _img_from_html(summary)

        blurb = _clean_html(summary)
        if not blurb or not title:
            continue

        entries.append({
            "headline": title,
            "blurb": blurb,
            "link": link,
            "image": image,
        })

    return entries


def _top_matches(feeds: list[str], n: int, keywords: list[str] | None = None) -> list[dict]:
    """Return up to n deduplicated entries across feeds matching optional keywords."""
    results = []
    seen = set()
    for url in feeds:
        for entry in _parse_feed(url):
            if entry["link"] in seen:
                continue
            if keywords:
                haystack = (entry["headline"] + " " + entry["blurb"]).lower()
                if not any(kw in haystack for kw in keywords):
                    continue
            results.append(entry)
            seen.add(entry["link"])
            if len(results) >= n:
                return results
    return results


# ── Good News ─────────────────────────────────────────────────────────────────

_GOOD_NEWS_FEEDS = [
    "https://www.goodnewsnetwork.org/feed/",
    "https://www.positive.news/feed/",
    "https://www.bbc.co.uk/news/10628494#atom.xml",
    "https://www.sunnyskyz.com/feed/rss",
    "https://happynews.com/feed/",
]

_FALLBACK_GOOD_NEWS = {
    "headline": "Volunteers Around the World Continue to Make a Difference",
    "blurb": (
        "Every day, millions of people quietly dedicate their time to making their communities "
        "better — planting trees, teaching skills, feeding neighbors, and lifting each other up. "
        "Their collective effort is shaping a kinder world, one act at a time."
    ),
    "link": "https://www.goodnewsnetwork.org",
    "image": "",
}


def fetch_good_news() -> dict:
    entries = _top_matches(_GOOD_NEWS_FEEDS, 4)
    if not entries:
        return {**_FALLBACK_GOOD_NEWS, "more": []}
    main = entries[0]
    main["more"] = [{"headline": e["headline"], "link": e["link"]} for e in entries[1:]]
    return main


# ── Impactful AI ──────────────────────────────────────────────────────────────

_AI_FEEDS = [
    "https://www.technologyreview.com/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://feeds.feedburner.com/TechCrunch/AI",
    "https://news.mit.edu/rss/topic/artificial-intelligence2",
    "https://www.sciencedaily.com/rss/computers_math/artificial_intelligence.xml",
]

_AI_POSITIVE_KEYWORDS = [
    "health", "medical", "cancer", "climate", "environment", "accessib",
    "education", "research", "discover", "humanitarian", "disease", "diagnos",
    "patient", "wildfire", "flood", "drug", "protein", "blind", "deaf",
]

_FALLBACK_AI = {
    "headline": "AI Is Accelerating Breakthroughs Across Science and Medicine",
    "blurb": (
        "From mapping proteins to detecting diseases earlier than ever before, AI is quietly "
        "transforming how researchers tackle humanity's hardest problems. Discoveries that once "
        "took decades are now emerging in years — and the pace is only picking up."
    ),
    "link": "https://www.technologyreview.com",
    "image": "",
}


def fetch_ai_impact() -> dict:
    entries = _top_matches(_AI_FEEDS, 4, keywords=_AI_POSITIVE_KEYWORDS)
    if len(entries) < 4:
        # Top up with any AI entries if keyword filter didn't return enough
        seen = {e["link"] for e in entries}
        for e in _top_matches(_AI_FEEDS, 4):
            if e["link"] not in seen:
                entries.append(e)
                seen.add(e["link"])
            if len(entries) >= 4:
                break
    if not entries:
        return {**_FALLBACK_AI, "more": []}
    main = entries[0]
    main["more"] = [{"headline": e["headline"], "link": e["link"]} for e in entries[1:4]]
    return main
