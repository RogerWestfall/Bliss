"""Fetch newsletter content via Claude web search + Haiku summarization."""

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


def _search_section(prompt: str) -> str:
    """One focused web search for one newsletter section. Returns prose."""
    resp = _client().messages.create(
        model=_MODEL,
        max_tokens=1500,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 4,
        }],
        messages=[{"role": "user", "content": prompt}],
    )
    block_types = [getattr(b, "type", "?") for b in resp.content]
    logger.info("stop_reason=%s | blocks=%s", resp.stop_reason, block_types)
    text = "".join(
        getattr(b, "text", "") or ""
        for b in resp.content
        if getattr(b, "type", "") == "text"
    )
    logger.info("Digest (%d chars): %s...", len(text), text[:120])
    return text


def _to_json(digest: str, schema: str) -> str:
    """Convert a prose digest into strict JSON — no tools, cheap call."""
    resp = _client().messages.create(
        model=_MODEL,
        max_tokens=3072,
        system=(
            "Convert the news digest into valid JSON exactly matching the schema. "
            "Copy every URL character-for-character from the digest — "
            "never construct a URL from a domain name alone, never abbreviate or guess any path. "
            "Output ONLY JSON."
        ),
        messages=[{"role": "user", "content": f"{schema}\n\nDIGEST:\n{digest}"}],
    )
    return resp.content[0].text


def _is_article_url(url: str) -> bool:
    """Reject bare homepages — require a URL with a meaningful path."""
    if not url or not url.startswith("http"):
        return False
    from urllib.parse import urlparse
    path = urlparse(url).path.rstrip("/")
    return len(path) > 5


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
    stories = [s for s in stories if _is_article_url(s.get("link", ""))]
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


# ── Quote of the Day ───────────────────────────────────────────────────────────────

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


# ── News ─────────────────────────────────────────────────────────────────────────────

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

_SECTION_RULES = (
    "Find exactly 4 stories. Rules:\n"
    "- Each story must be a specific article about one topic — not a roundup, "
    "digest, or weekly summary.\n"
    "- Each story must come from a different website.\n"
    "- Strongly prefer articles from the last 7 days; use older only if nothing recent exists.\n"
    "- Skip paywalled outlets: WSJ, Bloomberg, FT, Economist, Washington Post.\n"
    "- If you cannot find 4 perfect matches, include the best available stories.\n"
    "For each story provide: headline, the FULL article URL including its path "
    "(e.g. https://www.bbc.com/news/science-12345678), and date.\n"
    "For story #1 also write a warm 2-3 sentence blurb.\n"
    "Format:\n"
    "1. [HEADLINE] | [FULL URL] | [DATE]\n   BLURB: ...\n"
    "2. [HEADLINE] | [FULL URL] | [DATE]\n"
    "3. [HEADLINE] | [FULL URL] | [DATE]\n"
    "4. [HEADLINE] | [FULL URL] | [DATE]\n"
)

_JSON_SCHEMA = (
    "Convert the three section digests below into this exact JSON. "
    "Copy the real headlines and URLs directly — do not invent or paraphrase them.\n"
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
    """Three focused search calls (one per section) + one JSON formatting call."""
    today = date.today()
    today_str = today.strftime("%B %d, %Y")
    rules = _SECTION_RULES

    good_prompt = (
        f"Today is {today_str}. Find 4 uplifting, positive news stories from the past week.\n"
        "Topics: medical breakthroughs, environmental wins, acts of kindness, "
        "community achievements, wildlife recoveries, humanitarian milestones.\n"
        "Search any reputable news source — BBC, Guardian, NYT, NPR, Reuters, AP, "
        "CBC, The Independent, Positive News are all great.\n\n"
        + rules
    )

    ai_prompt = (
        f"Today is {today_str}. Find 4 recent stories about AI delivering "
        "real, demonstrated positive impact.\n"
        "Topics: AI used in healthcare, climate, accessibility, education, or science — "
        "with actual results, not just product announcements.\n"
        "Search any reputable tech or science outlet — MIT Technology Review, Wired, "
        "Nature, New Scientist, Scientific American, NPR, BBC, The Verge, STAT News.\n\n"
        + rules
    )

    ny_prompt = (
        f"Today is {today_str}. Find 4 recent news stories specifically about "
        "Brooklyn or Manhattan neighborhoods.\n"
        "Topics: neighborhood life in Bed-Stuy or Bushwick, street art, skateboarding, "
        "sports results (Mets, Yankees, Knicks, Nets wins), local openings, "
        "community achievements. At most 1 sports story.\n"
        "Search Gothamist, Brooklyn Paper, Bklyner, Hyperallergic, Curbed NY, NY1, "
        "Timeout NY, or New York Times metro section.\n"
        "Skip events-preview articles; only include things that already happened.\n\n"
        + rules
    )

    try:
        logger.info("Searching: Good News...")
        good_digest = _search_section(good_prompt)
        logger.info("Good News digest: %d chars", len(good_digest))

        logger.info("Searching: AI Impact...")
        ai_digest = _search_section(ai_prompt)
        logger.info("AI Impact digest: %d chars", len(ai_digest))

        logger.info("Searching: New York...")
        ny_digest = _search_section(ny_prompt)
        logger.info("New York digest: %d chars", len(ny_digest))

        combined = (
            "=== GOOD NEWS ===\n" + good_digest +
            "\n\n=== IMPACTFUL AI ===\n" + ai_digest +
            "\n\n=== NEW YORK ===\n" + ny_digest
        )

        logger.info("Converting combined digest (%d chars) to JSON...", len(combined))
        text = _to_json(combined, _JSON_SCHEMA)
        logger.info("JSON response (%d chars): %s...", len(text), text[:400])
        data = _extract_json(text)

        for section in ("good_news", "ai_impact", "ny_news"):
            stories = data.get(section, [])
            logger.info("%s: %d stories raw; links=%s", section, len(stories),
                        [s.get("link", "")[:60] for s in stories])

        good_news = _shape_stories(data.get("good_news", []))
        ai_impact = _shape_stories(data.get("ai_impact", []))
        ny_news = _shape_stories(data.get("ny_news", []))
        logger.info("shaped — good=%s ai=%s ny=%s",
                    bool(good_news), bool(ai_impact), bool(ny_news))
        return (
            good_news or _FALLBACK_GOOD_NEWS,
            ai_impact or _FALLBACK_AI,
            ny_news or _FALLBACK_NY,
        )
    except Exception:
        logger.exception("fetch_news failed — using fallbacks")
        return _FALLBACK_GOOD_NEWS, _FALLBACK_AI, _FALLBACK_NY
