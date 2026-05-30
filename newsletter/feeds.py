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

        # Collect any text from this response
        text = "".join(
            getattr(b, "text", "") or ""
            for b in resp.content
            if getattr(b, "type", "") == "text"
        )
        if text:
            logger.info("Got text response: %s...", text[:120])

        if resp.stop_reason == "end_turn":
            if text:
                return text
            # end_turn but no text — Claude searched but didn't write a response.
            # Continue the conversation and ask it to synthesize.
            messages.append({"role": "assistant", "content": resp.content})
            messages.append({
                "role": "user",
                "content": "Now write the JSON response based on your search results.",
            })
            continue

        if resp.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": resp.content})
            # Provide tool results — for server-side tools the server fills these in,
            # but we still need to send the tool_result turn to continue the loop.
            tool_results = [
                {"type": "tool_result", "tool_use_id": b.id, "content": ""}
                for b in resp.content
                if getattr(b, "type", "") == "tool_use"
            ]
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            continue

        # Any other stop reason — return whatever text we have
        break

    return text


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
    "Find real, current, uplifting news stories. Write in a warm, engaging tone. "
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
        "Reply ONLY with this JSON:\n"
        '{"stories": ['
        '{"headline": "...", "blurb": "...", "link": "https://..."}, '
        '{"headline": "...", "link": "https://..."}, '
        '{"headline": "...", "link": "https://..."}, '
        '{"headline": "...", "link": "https://..."}'
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
    "Find real stories of AI creating genuine positive impact in the world. "
    "Focus on healthcare, climate, accessibility, education, or humanitarian aid. "
    "Avoid hype — real demonstrated impact only. "
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
        "Look for: AI detecting disease, fighting climate change, helping people with disabilities, "
        "accelerating drug discovery, supporting humanitarian work. Real results only, no hype. "
        "For the first story write an accessible, optimistic 2-3 sentence blurb. "
        "Reply ONLY with this JSON:\n"
        '{"stories": ['
        '{"headline": "...", "blurb": "...", "link": "https://..."}, '
        '{"headline": "...", "link": "https://..."}, '
        '{"headline": "...", "link": "https://..."}, '
        '{"headline": "...", "link": "https://..."}'
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
