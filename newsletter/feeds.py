"""Fetch newsletter content via Claude web search + Haiku summarization."""

import json
import logging
import re
from datetime import date, timedelta

import anthropic
import requests
from lxml import html as lhtml

from newsletter.config import ANTHROPIC_API_KEY

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


def _web_search(prompt: str, system: str) -> str:
    """Run Claude with web search (max 3 searches) and return the text response."""
    messages = [{"role": "user", "content": prompt}]
    text = ""

    for i in range(10):
        resp = _client().messages.create(
            model=_MODEL,
            max_tokens=2048,
            system=system,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 3,
            }],
            messages=messages,
        )

        block_types = [getattr(b, "type", "?") for b in resp.content]
        logger.info("Round %d | stop_reason=%s | blocks=%s", i + 1, resp.stop_reason, block_types)

        text = "".join(
            getattr(b, "text", "") or ""
            for b in resp.content
            if getattr(b, "type", "") == "text"
        )
        if text:
            logger.info("Got text (%d chars): %s...", len(text), text[:120])

        if resp.stop_reason == "end_turn":
            if text:
                return text
            logger.warning("Round %d: end_turn with no text, requesting synthesis", i + 1)
            messages.append({"role": "assistant", "content": resp.content})
            messages.append({"role": "user", "content": "Now write the JSON response based on your search results."})
            continue

        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})
            tool_results = [
                {"type": "tool_result", "tool_use_id": b.id, "content": "Search completed."}
                for b in resp.content
                if getattr(b, "type", "") == "tool_use"
            ]
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            continue

        break

    return text


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


# ── News ──────────────────────────────────────────────────────────────────────

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

_SYSTEM = (
    "You are the editor of Bliss, a warm, uplifting daily newsletter. "
    "You find real, current stories and write about them with genuine enthusiasm. "
    "Respond ONLY with valid JSON — no other text, no markdown fences."
)


def fetch_news() -> tuple[dict, dict, dict]:
    """Fetch all three sections using Claude web search (max 3 searches)."""
    today = date.today().strftime("%B %d, %Y")
    cutoff = (date.today() - timedelta(days=2)).strftime("%B %d, %Y")

    prompt = (
        f"Today is {today}. Do exactly 3 web searches — one per section below — "
        f"and find stories published on or after {cutoff}. "
        "Each story must be a real, specific news article (not a listicle, calendar, or roundup). "
        "Do not repeat the same story across sections. "
        "If multiple outlets covered the same event, pick the single best source.\n\n"

        "SEARCH 1 — GOOD NEWS: Search for uplifting news from the last 2 days. "
        "Look for acts of kindness, scientific breakthroughs, environmental wins, community achievements. "
        "Prefer sources like BBC, Guardian, Reuters, AP, NPR, NYT, GoodNewsNetwork, Positive.news.\n\n"

        "SEARCH 2 — IMPACTFUL AI: Search for AI being used for genuine positive impact in the last 2 days. "
        "Healthcare, climate, accessibility, education, humanitarian aid — real results, no hype. "
        "Prefer MIT Tech Review, Wired, Nature, New Scientist, Scientific American.\n\n"

        "SEARCH 3 — NEW YORK: Search for Brooklyn and Manhattan news from the last 2 days. "
        "Look for community stories, local culture, neighborhood news in Bed-Stuy and Bushwick, "
        "street art, skateboarding, and sports wins (Mets, Yankees, Knicks, Nets). "
        "Pick at most 1 sports story — the rest must be community or culture. "
        "Prefer Gothamist, Brooklyn Paper, Bklyner, Timeout NY news, Hyperallergic, Curbed NY. "
        "No events calendars, no tourist guides.\n\n"

        "For each section return 4 stories. Write a warm 2-3 sentence blurb for story #1 only. "
        "Exclude paywalled sources: WSJ, Bloomberg, FT, Economist, Washington Post.\n\n"

        "Reply ONLY with this JSON:\n"
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

    try:
        text = _web_search(prompt, _SYSTEM)
        data = _extract_json(text)
        good_news = _shape_stories(data.get("good_news", [])) or _FALLBACK_GOOD_NEWS
        ai_impact = _shape_stories(data.get("ai_impact", [])) or _FALLBACK_AI
        ny_news = _shape_stories(data.get("ny_news", [])) or _FALLBACK_NY
        return good_news, ai_impact, ny_news
    except Exception as exc:
        logger.warning("fetch_news failed: %s", exc)
        return _FALLBACK_GOOD_NEWS, _FALLBACK_AI, _FALLBACK_NY
