"""Fetch newsletter content using Brave Search + Claude summarization."""

import json
import logging
import re
from datetime import date

import anthropic
import requests
from lxml import html as lhtml

from newsletter.config import ANTHROPIC_API_KEY, BRAVE_API_KEY

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


def _brave_search(query: str, count: int = 10, freshness: str = "pd") -> list[dict]:
    """Return web results from Brave Search API."""
    resp = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": BRAVE_API_KEY,
        },
        params={"q": query, "count": count, "freshness": freshness},
        timeout=15,
    )
    resp.raise_for_status()
    results = resp.json().get("web", {}).get("results", [])
    logger.info("Brave search '%s' → %d results", query, len(results))
    return results


def _results_to_text(results: list[dict]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.get('title', '')} — {r.get('url', '')}")
        if r.get("description"):
            lines.append(f"   {r['description']}")
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


# ── News (Brave search + Haiku summarization) ─────────────────────────────────

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
    "]}"
)


def fetch_news() -> tuple[dict, dict]:
    """Fetch good news + AI impact using Brave Search, summarized by Haiku."""
    today = date.today().strftime("%B %d, %Y")
    try:
        good_results = _brave_search(
            f"uplifting good news positive stories {today}",
            count=10,
            freshness="pd",
        )
        ai_results = _brave_search(
            f"AI artificial intelligence positive impact breakthrough {today}",
            count=10,
            freshness="pw",
        )

        combined = (
            f"TODAY: {today}\n\n"
            "=== GOOD NEWS SEARCH RESULTS ===\n"
            + _results_to_text(good_results)
            + "\n\n=== AI IMPACT SEARCH RESULTS ===\n"
            + _results_to_text(ai_results)
        )

        text = _summarize(combined, _NEWS_INSTRUCTION)
        data = _extract_json(text)

        good_news = _shape_stories(data.get("good_news", [])) or _FALLBACK_GOOD_NEWS
        ai_impact = _shape_stories(data.get("ai_impact", [])) or _FALLBACK_AI
        return good_news, ai_impact

    except Exception as exc:
        logger.warning("fetch_news failed: %s", exc)
        return _FALLBACK_GOOD_NEWS, _FALLBACK_AI
