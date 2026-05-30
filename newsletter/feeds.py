"""Fetch newsletter content: RSS for fresh articles, Claude for curation and writing."""

import json
import logging
import re

import anthropic
import requests
from lxml import etree
from lxml import html as lhtml

from newsletter.config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "BlissNewsletter/2.0 (rogerlwestfall@gmail.com)"}
_MODEL = "claude-opus-4-8"
_client_instance = None


def _client() -> anthropic.Anthropic:
    global _client_instance
    if _client_instance is None:
        _client_instance = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client_instance


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        return json.loads(text[start:end + 1])
    return json.loads(text)


# ── Quote of the Day ─────────────────────────────────────────────────────────

_FALLBACK_QUOTE = {
    "quote": "Keep your face always toward the sunshine, and shadows will fall behind you.",
    "author": "Walt Whitman",
}


def fetch_quote() -> dict:
    try:
        resp = requests.get(
            "https://zenquotes.io/api/random", headers=_HEADERS, timeout=10
        )
        resp.raise_for_status()
        data = resp.json()[0]
        return {"quote": data["q"], "author": data["a"]}
    except Exception as exc:
        logger.warning("ZenQuotes failed (%s) — using fallback", exc)
        return _FALLBACK_QUOTE


# ── RSS helpers ───────────────────────────────────────────────────────────────

def _clean_html(raw: str, max_chars: int = 400) -> str:
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
    if not url:
        return ""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        root = lhtml.fromstring(resp.content)
        for xpath, attr in [
            ('.//meta[@property="og:image"]', "content"),
            ('.//meta[@name="twitter:image"]', "content"),
        ]:
            el = root.find(xpath)
            if el is not None:
                src = el.get(attr, "")
                if src.startswith("http"):
                    return src
    except Exception as exc:
        logger.debug("og:image failed for %s: %s", url, exc)
    return ""


def _parse_feed(url: str) -> list[dict]:
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
        title_el = item.find("title") or item.find("{http://www.w3.org/2005/Atom}title")
        title = (title_el.text or "").strip() if title_el is not None else ""
        if not title:
            continue

        link_el = item.find("link")
        if link_el is not None:
            link = (link_el.text or link_el.get("href", "")).strip()
        else:
            atom_link = item.find("{http://www.w3.org/2005/Atom}link")
            link = atom_link.get("href", "").strip() if atom_link is not None else ""

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

        image = ""
        mt = item.find("{http://search.yahoo.com/mrss/}thumbnail")
        if mt is not None:
            image = mt.get("url", "")
        if not image:
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


def _fetch_all(feeds: list[str]) -> list[dict]:
    """Collect entries from all feeds, deduplicating by link."""
    seen, results = set(), []
    for url in feeds:
        for entry in _parse_feed(url):
            if entry["link"] not in seen:
                seen.add(entry["link"])
                results.append(entry)
    return results


# ── Claude curation ───────────────────────────────────────────────────────────

def _claude_curate(entries: list[dict], section: str, system: str) -> dict | None:
    """Ask Claude to pick the best story and write a warm blurb."""
    if not entries:
        return None

    candidates = json.dumps(
        [{"i": i, "title": e["headline"], "summary": e["blurb"][:300]}
         for i, e in enumerate(entries[:20])],
        indent=2,
    )

    msg = _client().messages.create(
        model=_MODEL,
        max_tokens=800,
        system=system,
        messages=[{"role": "user", "content": (
            f"Here are today's candidate {section} stories:\n{candidates}\n\n"
            "Pick the single most uplifting, interesting story and write a warm, "
            "engaging 2-3 sentence blurb. Also pick the 3 next-best stories for "
            "additional links (different from the main pick). "
            "Reply ONLY with valid JSON:\n"
            '{"index": 0, "headline": "rewritten headline if needed", '
            '"blurb": "...", "more_indices": [1, 2, 3]}'
        )}],
    )

    text = next(
        (b.text for b in msg.content if getattr(b, "type", "") == "text"), ""
    )
    data = _extract_json(text)

    idx = int(data.get("index", 0))
    if not (0 <= idx < len(entries)):
        idx = 0
    main = entries[idx]

    more = []
    for mi in data.get("more_indices", [])[:3]:
        if isinstance(mi, int) and 0 <= mi < len(entries) and mi != idx:
            more.append({"headline": entries[mi]["headline"], "link": entries[mi]["link"]})

    return {
        "headline": data.get("headline", main["headline"]),
        "blurb": data.get("blurb", main["blurb"]),
        "link": main["link"],
        "image": main["image"] or _og_image(main["link"]),
        "more": more,
    }


# ── Good News ─────────────────────────────────────────────────────────────────

_GOOD_NEWS_FEEDS = [
    "https://www.goodnewsnetwork.org/feed/",
    "https://www.positive.news/feed/",
    "https://www.sunnyskyz.com/feed/rss",
    "https://happynews.com/feed/",
    "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
]

_GOOD_NEWS_SYSTEM = (
    "You are the editor of Bliss, a daily newsletter dedicated to positivity. "
    "Select the most uplifting, feel-good story — prioritise human kindness, "
    "surprising breakthroughs, environmental wins, and community achievements. "
    "Write in a warm, conversational tone. "
    "Respond ONLY with valid JSON — no other text."
)

_FALLBACK_GOOD_NEWS = {
    "headline": "Volunteers Around the World Continue to Make a Difference",
    "blurb": (
        "Every day, millions of people quietly dedicate their time to making their "
        "communities better — planting trees, teaching skills, and lifting each other up."
    ),
    "link": "",
    "image": "",
    "more": [],
}


def fetch_good_news() -> dict:
    entries = _fetch_all(_GOOD_NEWS_FEEDS)
    if not entries:
        logger.warning("No good news entries from feeds — using fallback")
        return _FALLBACK_GOOD_NEWS
    try:
        result = _claude_curate(entries, "good news", _GOOD_NEWS_SYSTEM)
        return result or _FALLBACK_GOOD_NEWS
    except Exception as exc:
        logger.warning("Claude curation failed for good news: %s", exc)
        return _FALLBACK_GOOD_NEWS


# ── Impactful AI ──────────────────────────────────────────────────────────────

_AI_FEEDS = [
    "https://www.technologyreview.com/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://feeds.feedburner.com/TechCrunch/AI",
    "https://news.mit.edu/rss/topic/artificial-intelligence2",
    "https://www.sciencedaily.com/rss/computers_math/artificial_intelligence.xml",
    "https://deepmind.google/blog/rss.xml",
]

_AI_SYSTEM = (
    "You are the editor of Bliss, a daily newsletter dedicated to positivity. "
    "Select the story that best demonstrates AI creating genuine real-world benefit — "
    "healthcare breakthroughs, climate solutions, accessibility tools, scientific discovery, "
    "or humanitarian impact. Avoid hype — look for real demonstrated results. "
    "Write in an optimistic, accessible tone. "
    "Respond ONLY with valid JSON — no other text."
)

_FALLBACK_AI = {
    "headline": "AI Is Accelerating Breakthroughs Across Science and Medicine",
    "blurb": (
        "From mapping proteins to detecting disease earlier than ever, AI is transforming "
        "how researchers tackle humanity's hardest problems."
    ),
    "link": "",
    "image": "",
    "more": [],
}


def fetch_ai_impact() -> dict:
    entries = _fetch_all(_AI_FEEDS)
    if not entries:
        logger.warning("No AI entries from feeds — using fallback")
        return _FALLBACK_AI
    try:
        result = _claude_curate(entries, "impactful AI", _AI_SYSTEM)
        return result or _FALLBACK_AI
    except Exception as exc:
        logger.warning("Claude curation failed for AI impact: %s", exc)
        return _FALLBACK_AI
