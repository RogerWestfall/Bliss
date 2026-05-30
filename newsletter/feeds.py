"""Fetch newsletter content using Claude web search."""

import json
import logging
import re

import anthropic
import requests
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


def _search(prompt: str, system: str) -> str:
    """Run Claude with web search, then force a structured text response."""
    messages = [{"role": "user", "content": prompt}]

    # Phase 1 — let Claude search (may loop if it searches multiple times)
    for _ in range(6):
        resp = _client().messages.create(
            model=_MODEL,
            max_tokens=2048,
            system=system,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=messages,
        )
        text = next(
            (b.text for b in resp.content if getattr(b, "type", "") == "text"), ""
        )
        if text.strip() and resp.stop_reason != "tool_use":
            return text  # Claude searched and responded in one shot

        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason == "tool_use":
            tool_results = [
                {"type": "tool_result", "tool_use_id": b.id, "content": ""}
                for b in resp.content if getattr(b, "type", "") == "tool_use"
            ]
            messages.append({"role": "user", "content": tool_results})
        else:
            break  # Search done but no text yet — move to phase 2

    # Phase 2 — explicitly ask Claude to write the JSON response
    messages.append({
        "role": "user",
        "content": "Based on your research, now provide the JSON response as instructed.",
    })
    final = _client().messages.create(
        model=_MODEL,
        max_tokens=1024,
        system=system,
        messages=messages,
    )
    return next(
        (b.text for b in final.content if getattr(b, "type", "") == "text"), ""
    )


def _og_image(url: str) -> str:
    """Fetch og:image from an article page."""
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


# ── Good News ─────────────────────────────────────────────────────────────────

_GOOD_NEWS_SYSTEM = (
    "You are the editor of Bliss, a daily newsletter dedicated to positivity. "
    "You find real, current, uplifting news stories. "
    "Write in a warm, engaging, human tone. "
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


def fetch_good_news() -> dict:
    prompt = (
        "Search for 4 real, uplifting positive news stories published in the last 48 hours. "
        "Look for: acts of human kindness, scientific breakthroughs, environmental wins, "
        "community achievements. Avoid politics and tragedy. "
        "For the first story write a warm, engaging 2-3 sentence blurb. "
        "Reply ONLY with this exact JSON structure:\n"
        '{"stories": [\n'
        '  {"headline": "...", "blurb": "...", "link": "https://..."},\n'
        '  {"headline": "...", "link": "https://..."},\n'
        '  {"headline": "...", "link": "https://..."},\n'
        '  {"headline": "...", "link": "https://..."}\n'
        "]}"
    )
    try:
        text = _search(prompt, _GOOD_NEWS_SYSTEM)
        stories = _extract_json(text).get("stories", [])
        if not stories:
            return _FALLBACK_GOOD_NEWS
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
    except Exception as exc:
        logger.warning("fetch_good_news failed: %s", exc)
        return _FALLBACK_GOOD_NEWS


# ── Impactful AI ──────────────────────────────────────────────────────────────

_AI_SYSTEM = (
    "You are the editor of Bliss, a daily newsletter dedicated to positivity. "
    "You find real stories of AI creating genuine positive impact in the world. "
    "Focus on healthcare, climate, accessibility, education, scientific discovery, "
    "or humanitarian aid. Avoid hype — real demonstrated impact only. "
    "Write in an optimistic, accessible tone. "
    "Respond ONLY with valid JSON — no other text, no markdown."
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
    prompt = (
        "Search for 4 real stories published recently about AI being used for genuine positive impact. "
        "Examples: AI detecting cancer earlier, AI modeling climate solutions, AI helping people "
        "with disabilities, AI accelerating drug discovery, AI supporting humanitarian work. "
        "Avoid AI hype pieces — only stories with real demonstrated results. "
        "For the first story write an accessible, optimistic 2-3 sentence blurb. "
        "Reply ONLY with this exact JSON structure:\n"
        '{"stories": [\n'
        '  {"headline": "...", "blurb": "...", "link": "https://..."},\n'
        '  {"headline": "...", "link": "https://..."},\n'
        '  {"headline": "...", "link": "https://..."},\n'
        '  {"headline": "...", "link": "https://..."}\n'
        "]}"
    )
    try:
        text = _search(prompt, _AI_SYSTEM)
        stories = _extract_json(text).get("stories", [])
        if not stories:
            return _FALLBACK_AI
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
    except Exception as exc:
        logger.warning("fetch_ai_impact failed: %s", exc)
        return _FALLBACK_AI
