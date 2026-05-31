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


def _web_search(prompt: str) -> str:
    """Search the web with Claude and return a plain-prose digest.

    web_search_20250305 is server-side: Claude searches up to max_uses times
    within one call. We ask for PROSE (not JSON) here because models reliably
    write text after searching, but often emit no text when also forced into
    strict JSON. A second, tool-free call converts the prose to JSON.

    stop_reason="pause_turn" means a long search needs continuation; we replay
    the content. We cap continuations to keep the search budget (and cost)
    bounded — each request carries its own max_uses.
    """
    messages = [{"role": "user", "content": prompt}]
    text = ""

    for i in range(2):
        resp = _client().messages.create(
            model=_MODEL,
            max_tokens=3072,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 3,
            }],
            messages=messages,
        )

        block_types = [getattr(b, "type", "?") for b in resp.content]
        logger.info("Search round %d | stop_reason=%s | blocks=%s", i + 1, resp.stop_reason, block_types)

        text = "".join(
            getattr(b, "text", "") or ""
            for b in resp.content
            if getattr(b, "type", "") == "text"
        )

        if resp.stop_reason == "pause_turn" and i == 0:
            messages.append({"role": "assistant", "content": resp.content})
            continue

        break

    logger.info("Search digest (%d chars): %s...", len(text), text[:160])
    return text


def _to_json(digest: str, schema_instruction: str) -> str:
    """Convert a prose digest into strict JSON using a cheap, tool-free call."""
    resp = _client().messages.create(
        model=_MODEL,
        max_tokens=3072,
        system="You convert news digests into valid JSON. Output ONLY JSON, no markdown, no commentary.",
        messages=[{"role": "user", "content": f"{schema_instruction}\n\nDIGEST:\n{digest}"}],
    )
    return resp.content[0].text


def _dedup_by_domain(stories: list) -> list:
    """Keep only the first story from each domain."""
    seen = set()
    out = []
    for s in stories:
        url = s.get("link", "")
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.removeprefix("www.")
        except Exception:
            domain = url
        if domain and domain not in seen:
            seen.add(domain)
            out.append(s)
        elif not domain:
            out.append(s)
    return out


def _shape_stories(stories: list) -> dict | None:
    stories = _dedup_by_domain(stories)
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

_JSON_SCHEMA = (
    "Convert the digest below into this exact JSON shape. "
    "Each section has 4 stories; only story #1 of each has a blurb. "
    "Use the real article URLs from the digest for every link.\n"
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
    """Search the web (prose) then format to JSON in a second cheap call."""
    today = date.today().strftime("%B %d, %Y")
    cutoff = (date.today() - timedelta(days=2)).strftime("%B %d, %Y")

    search_prompt = (
        f"You are the editor of Bliss, a warm daily newsletter. Today is {today}. "
        f"Do exactly 3 web searches — one per section — and find stories published on or after {cutoff}.\n\n"

        "RULES for all sections:\n"
        "- Find 4 stories per section, each from a DIFFERENT website. No domain twice in a section.\n"
        "- Each story must be a specific, standalone article about ONE thing. "
        "Skip roundups, digests, listicles, weekly summaries ('good news this week', '5 things to know').\n"
        "- No story may repeat across sections. If outlets cover the same event, pick one.\n"
        "- Exclude paywalled sources: WSJ, Bloomberg, FT, Economist, Washington Post.\n\n"

        "SEARCH 1 — GOOD NEWS: uplifting news from the last 2 days — kindness, scientific "
        "breakthroughs, environmental wins, community achievements. "
        "Prefer BBC, Guardian, Reuters, AP, NPR, NYT, GoodNewsNetwork, Positive.news.\n\n"

        "SEARCH 2 — IMPACTFUL AI: AI for genuine positive impact in the last 2 days — healthcare, "
        "climate, accessibility, education, humanitarian aid. Real results, no hype. "
        "Prefer MIT Tech Review, Wired, Nature, New Scientist, Scientific American.\n\n"

        "SEARCH 3 — NEW YORK: Brooklyn and Manhattan news from the last 2 days — community stories, "
        "local culture, Bed-Stuy and Bushwick, street art, skateboarding, sports wins (Mets, Yankees, "
        "Knicks, Nets). At most 1 sports story; the rest community or culture. No events calendars or "
        "tourist guides. Prefer Gothamist, Brooklyn Paper, Bklyner, Timeout NY news, Hyperallergic, Curbed NY.\n\n"

        "After searching, write a clear digest. For each section list its 4 stories: the full headline, "
        "the exact article URL, and the publication date. For story #1 in each section, also write a "
        "warm 2-3 sentence blurb. Organize under headings: GOOD NEWS, IMPACTFUL AI, NEW YORK."
    )

    try:
        digest = _web_search(search_prompt)
        if not digest.strip():
            raise ValueError("empty search digest")
        text = _to_json(digest, _JSON_SCHEMA)
        data = _extract_json(text)
        good_news = _shape_stories(data.get("good_news", [])) or _FALLBACK_GOOD_NEWS
        ai_impact = _shape_stories(data.get("ai_impact", [])) or _FALLBACK_AI
        ny_news = _shape_stories(data.get("ny_news", [])) or _FALLBACK_NY
        return good_news, ai_impact, ny_news
    except Exception as exc:
        logger.warning("fetch_news failed: %s", exc)
        return _FALLBACK_GOOD_NEWS, _FALLBACK_AI, _FALLBACK_NY
