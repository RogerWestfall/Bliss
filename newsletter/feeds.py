"""Fetch newsletter content using Brave Search + Claude summarization."""

import json
import logging
import re
from datetime import date

import anthropic
import requests
from lxml import html as lhtml

from newsletter.config import ANTHROPIC_API_KEY, TAVILY_API_KEY

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "BlissNewsletter/2.0 (rogerlwestfall@gmail.com)"}
_MODEL = "claude-haiku-4-5-20251001"
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


def _tavily_search(
    query: str,
    max_results: int = 10,
    days: int = 3,
    include_domains: list[str] | None = None,
) -> list[dict]:
    """Return web results from Tavily Search API."""
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "basic",
        "max_results": max_results,
        "days": days,
        "exclude_domains": _EXCLUDED_DOMAINS,
    }
    if include_domains:
        payload["include_domains"] = include_domains
    resp = requests.post(
        "https://api.tavily.com/search",
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    results = resp.json().get("results", [])
    logger.info("Tavily search '%s' → %d results", query, len(results))
    return results


def _results_to_text(results: list[dict]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.get('title', '')} — {r.get('url', '')}")
        snippet = r.get("content") or r.get("description", "")
        if snippet:
            lines.append(f"   {snippet[:200]}")
    return "\n".join(lines)


def _summarize(content: str, instruction: str) -> str:
    """Ask Haiku to pick and write up stories from raw search results."""
    resp = _client().messages.create(
        model=_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": f"{instruction}\n\n{content}"}],
    )
    return resp.content[0].text


def _shape_stories(stories: list) -> dict | None:
    if not stories:
        return None
    main = stories[0]
    return {
        "headline": main.get("headline", ""),
        "blurb": main.get("blurb", ""),
        "link": main.get("link", ""),
        "image": _og_image(main.get("link", "")),
        "more": [
            {"headline": s.get("headline", ""), "link": s.get("link", "")}
            for s in stories[1:]
        ],
    }


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


# ── News sources ─────────────────────────────────────────────────────────────
#
# PREFERRED — quality sources to pull from for each category.
# EXCLUDED  — never surface these (paywalls, low quality, etc.).

_MAINSTREAM_DOMAINS = [
    "bbc.com",           # free
    "theguardian.com",   # free
    "reuters.com",       # free
    "apnews.com",        # free
    "npr.org",           # free
    "nytimes.com",       # subscription (user has access)
]

_POSITIVE_DOMAINS = [
    "goodnewsnetwork.org",
    "positive.news",
    "reasonstobecheerful.world",
    "upworthy.com",
]

_AI_DOMAINS = [
    "technologyreview.com",  # MIT Tech Review
    "wired.com",
    "nature.com",
    "newscientist.com",
    "scientificamerican.com",
    "npr.org",
    "bbc.com",
    "theguardian.com",
    "reuters.com",
    "apnews.com",
]

_NYC_DOMAINS = [
    "timeout.com",          # events, things to do
    "gothamist.com",        # NYC local news
    "brooklynpaper.com",    # Brooklyn hyperlocal
    "bklyner.com",          # Brooklyn neighborhood news
    "amny.com",             # AM New York
    "ny1.com",              # NY1 local news
    "nydailynews.com",      # NY Daily News
    "nypost.com",           # NY Post
    "nycgo.com",            # NYC official events/tourism
    "untappedcities.com",   # NYC hidden gems & culture
]

# Domains to exclude from all searches (paywalls, etc.)
_EXCLUDED_DOMAINS = [
    "washingtonpost.com",  # paywall
    "wsj.com",             # paywall
    "ft.com",              # paywall
    "bloomberg.com",       # paywall
    "economist.com",       # paywall
]

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

_FALLBACK_NY = {
    "headline": "Brooklyn and Manhattan: Always Something to Discover",
    "blurb": (
        "From the skate parks of Bushwick to the courts of Bed-Stuy, New York City "
        "keeps delivering moments worth stepping outside for."
    ),
    "link": "",
    "image": "",
    "more": [],
}

_NEWS_INSTRUCTION = (
    "You are the editor of Bliss, a daily newsletter dedicated to positivity. "
    "From the search results below, select the 4 best stories for each section "
    "and write a warm 2-3 sentence blurb for the first story in each section. "
    "Respond ONLY with valid JSON — no other text, no markdown:\n"
    '{"good_news":['
    '{"headline":"...","blurb":"...","link":"https://..."},'
    '{"headline":"...","link":"https://..."},'
    '{"headline":"...","link":"https://..."},'
    '{"headline":"...","link":"https://..."}'
    '],"ai_impact":['
    '{"headline":"...","blurb":"...","link":"https://..."},'
    '{"headline":"...","link":"https://..."},'
    '{"headline":"...","link":"https://..."},'
    '{"headline":"...","link":"https://..."}'
    '],"ny_news":['
    '{"headline":"...","blurb":"...","link":"https://..."},'
    '{"headline":"...","link":"https://..."},'
    '{"headline":"...","link":"https://..."},'
    '{"headline":"...","link":"https://..."}'
    "]}"
)


def fetch_news() -> tuple[dict, dict, dict]:
    """Fetch good news, AI impact, and NYC news using Tavily, summarized by Haiku."""
    today = date.today().strftime("%B %d, %Y")
    try:
        # Good news: 7 from mainstream + 3 from dedicated positive sites
        mainstream_results = _tavily_search(
            "uplifting positive news kindness breakthrough community environment",
            max_results=7,
            days=2,
            include_domains=_MAINSTREAM_DOMAINS,
        )
        positive_results = _tavily_search(
            "good news uplifting positive",
            max_results=3,
            days=2,
            include_domains=_POSITIVE_DOMAINS,
        )
        good_results = mainstream_results + positive_results

        ai_results = _tavily_search(
            "AI artificial intelligence positive impact healthcare climate accessibility education",
            max_results=10,
            days=7,
            include_domains=_AI_DOMAINS,
        )

        ny_results = _tavily_search(
            "New York City Brooklyn Manhattan good news events skateboarding baseball basketball "
            "Bed-Stuy Bushwick free things to do Mets Yankees Knicks Nets",
            max_results=10,
            days=7,
            include_domains=_NYC_DOMAINS,
        )

        combined = (
            f"TODAY: {today}\n\n"
            "=== GOOD NEWS SEARCH RESULTS ===\n"
            + _results_to_text(good_results)
            + "\n\n=== AI IMPACT SEARCH RESULTS ===\n"
            + _results_to_text(ai_results)
            + "\n\n=== NEW YORK CITY SEARCH RESULTS ===\n"
            + _results_to_text(ny_results)
        )

        text = _summarize(combined, _NEWS_INSTRUCTION)
        data = _extract_json(text)

        good_news = _shape_stories(data.get("good_news", [])) or _FALLBACK_GOOD_NEWS
        ai_impact = _shape_stories(data.get("ai_impact", [])) or _FALLBACK_AI
        ny_news = _shape_stories(data.get("ny_news", [])) or _FALLBACK_NY
        return good_news, ai_impact, ny_news

    except Exception as exc:
        logger.warning("fetch_news failed: %s", exc)
        return _FALLBACK_GOOD_NEWS, _FALLBACK_AI, _FALLBACK_NY
