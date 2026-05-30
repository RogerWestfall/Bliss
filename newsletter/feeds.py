"""Fetch newsletter content using Claude web search."""

import json
import logging
import re
from datetime import date

import anthropic
import requests
from lxml import html as lhtml

from newsletter.config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "BlissNewsletter/2.0 (rogerlwestfall@gmail.com)"}
_MODEL = "claude-sonnet-4-6"
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


def _search(prompt: str, system: str) -> str:
    """Run Claude with web search and return the final text response."""
    messages = [{"role": "user", "content": prompt}]
    text = ""

    for i in range(8):
        resp = _client().messages.create(
            model=_MODEL,
            max_tokens=2048,
            system=system,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=messages,
        )

        block_types = [getattr(b, "type", "?") for b in resp.content]
        logger.info("Round %d | stop_reason=%s | blocks=%s", i + 1, resp.stop_reason, block_types)

        for j, b in enumerate(resp.content):
            btype = getattr(b, "type", "?")
            if btype == "text":
                btext = getattr(b, "text", "") or ""
                logger.info("  [%d] text (%d chars): %s", j, len(btext), btext[:200])
            elif btype == "tool_use":
                logger.info("  [%d] tool_use id=%s input=%s", j, getattr(b, "id", "?"), str(getattr(b, "input", {}))[:150])
            else:
                logger.info("  [%d] %s: %s", j, btype, str(b)[:200])

        text = "".join(
            getattr(b, "text", "") or ""
            for b in resp.content
            if getattr(b, "type", "") == "text"
        )

        if resp.stop_reason == "end_turn":
            if text:
                return text
            logger.warning("Round %d: end_turn with no text, requesting synthesis", i + 1)
            messages.append({"role": "assistant", "content": resp.content})
            messages.append({
                "role": "user",
                "content": "Now write the JSON response based on your search results.",
            })
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


def _shape_stories(stories: list) -> dict:
    """Convert a list of story dicts into the newsletter section format."""
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


# ── News (good news + AI impact in one search call) ───────────────────────────

_NEWS_SYSTEM = (
    "You are the editor of Bliss, a daily newsletter dedicated to positivity. "
    "Find real, current stories via web search. Write in a warm, engaging tone. "
    "Respond ONLY with valid JSON — no other text, no markdown."
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


def fetch_news() -> tuple[dict, dict]:
    """Return (good_news, ai_impact) using a single web search call."""
    today = date.today().strftime("%B %d, %Y")
    prompt = (
        f"Today is {today}. Search the web and find two sets of stories:\n\n"
        "1. GOOD NEWS — 4 uplifting stories published in the last 48 hours: "
        "acts of kindness, scientific breakthroughs, environmental wins, community achievements. "
        "No politics or tragedy. Write a warm 2-3 sentence blurb for story #1.\n\n"
        "2. AI IMPACT — 4 stories from the last 7 days about AI creating genuine positive impact: "
        "healthcare, climate, accessibility, education, humanitarian aid. Real results only, no hype. "
        "Write an optimistic 2-3 sentence blurb for story #1.\n\n"
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
        "]}"
    )
    try:
        text = _search(prompt, _NEWS_SYSTEM)
        data = _extract_json(text)
        good_news = _shape_stories(data.get("good_news", [])) or _FALLBACK_GOOD_NEWS
        ai_impact = _shape_stories(data.get("ai_impact", [])) or _FALLBACK_AI
        return good_news, ai_impact
    except Exception as exc:
        logger.warning("fetch_news failed: %s", exc)
        return _FALLBACK_GOOD_NEWS, _FALLBACK_AI
