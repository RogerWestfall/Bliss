"""Fetch content for each newsletter section from free RSS feeds and APIs."""

import logging
import re

import requests
from lxml import etree
from lxml import html as lhtml

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "BlissNewsletter/2.0 (rogerlwestfall@gmail.com)"}


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
    """Find the first absolute <img src> in an HTML string."""
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


def _og_image(url: str) -> str:
    """Fetch an article page and extract its og:image or twitter:image."""
    if not url:
        return ""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        root = lhtml.fromstring(resp.content)
        for xpath, attr in [
            ('.//meta[@property="og:image"]', "content"),
            ('.//meta[@name="twitter:image"]', "content"),
            ('.//meta[@name="twitter:image:src"]', "content"),
        ]:
            el = root.find(xpath)
            if el is not None:
                src = el.get(attr, "")
                if src.startswith("http"):
                    return src
    except Exception as exc:
        logger.debug("og:image fetch failed for %s: %s", url, exc)
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

    items = root.findall(".//item")
    if not items:
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")

    entries = []
    for item in items[:20]:
        # Title — required
        title = _text(
            item.find("title") or item.find("{http://www.w3.org/2005/Atom}title")
        )
        if not title:
            continue

        # Link
        link_el = item.find("link")
        if link_el is not None:
            link = (link_el.text or link_el.get("href", "")).strip()
        else:
            atom_link = item.find("{http://www.w3.org/2005/Atom}link")
            link = atom_link.get("href", "").strip() if atom_link is not None else ""

        # Summary / description (optional — not required)
        summary = ""
        for tag in (
            "description",
            "{http://www.w3.org/2005/Atom}summary",
            "{http://www.w3.org/2005/Atom}content",
            "{http://purl.org/rss/1.0/modules/content/}encoded",
        ):
            el = item.find(tag)
            if el is not None and el.text:
                summary = el.text
                break

        # Image — try media tags first, then parse HTML summary
        image = ""
        mt = item.find("{http://search.yahoo.com/mrss/}thumbnail")
        if mt is not None:
            image = mt.get("url", "")
        if not image:
            mc = item.find("{http://search.yahoo.com/mrss/}content")
            if mc is not None and (mc.get("type", "") or "").startswith("image"):
                image = mc.get("url", "")
        if not image:
            # Also try media:content without type attribute
            mc = item.find("{http://search.yahoo.com/mrss/}content")
            if mc is not None and mc.get("url", ""):
                image = mc.get("url", "")
        if not image:
            image = _img_from_html(summary)

        entries.append({
            "headline": title,
            "blurb": _clean_html(summary),
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
    "https://www.sunnyskyz.com/feed/rss",
    "https://happynews.com/feed/",
    "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
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
    # Fetch og:image from article page if RSS didn't provide one
    if not main["image"] and main["link"]:
        logger.info("Fetching og:image for Good News story...")
        main["image"] = _og_image(main["link"])
    main["more"] = [{"headline": e["headline"], "link": e["link"]} for e in entries[1:]]
    return main


# ── Impactful AI ──────────────────────────────────────────────────────────────

_AI_FEEDS = [
    "https://www.technologyreview.com/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://feeds.feedburner.com/TechCrunch/AI",
    "https://news.mit.edu/rss/topic/artificial-intelligence2",
    "https://www.sciencedaily.com/rss/computers_math/artificial_intelligence.xml",
    "https://deepmind.google/blog/rss.xml",
]

_AI_POSITIVE_KEYWORDS = [
    "health", "medical", "cancer", "climate", "environment", "accessib",
    "education", "research", "discover", "humanitarian", "disease", "diagnos",
    "patient", "wildfire", "flood", "drug", "protein", "blind", "deaf",
    "assist", "help", "improve", "breakthrough", "detect",
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
    # Top up with any AI entries if keyword filter didn't return enough
    if len(entries) < 4:
        seen = {e["link"] for e in entries}
        for e in _top_matches(_AI_FEEDS, 8):
            if e["link"] not in seen:
                entries.append(e)
                seen.add(e["link"])
            if len(entries) >= 4:
                break
    if not entries:
        return {**_FALLBACK_AI, "more": []}
    main = entries[0]
    # Fetch og:image from article page if RSS didn't provide one
    if not main["image"] and main["link"]:
        logger.info("Fetching og:image for AI Impact story...")
        main["image"] = _og_image(main["link"])
    main["more"] = [{"headline": e["headline"], "link": e["link"]} for e in entries[1:4]]
    return main
