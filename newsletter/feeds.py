"""Fetch newsletter content via Claude web search + Python story extraction."""

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

_URL_RE = re.compile(r'https?://[^\s\|<>"()\[\]{}]+')


def _client() -> anthropic.Anthropic:
    global _client_instance
    if _client_instance is None:
        _client_instance = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client_instance


def _parse_section_digest(text: str) -> list[dict]:
    """Extract up to 4 stories from a section digest.

    Handles formats the search model might use:
      Labeled:  1. HEADLINE: ...\\n   URL: https://...\\n   BLURB: ...
      Pipe:     1. Headline | https://... | date
      Inline:   1. **Headline** - https://...
      Colon:    1: Headline\\n   URL: https://...
    """
    stories = []
    blocks = re.split(r'\n(?=\s*[1-4][.):\s])', '\n' + text.strip())

    for block in blocks:
        block = block.strip()
        if not re.match(r'[1-4][.):\s]', block):
            continue

        url = ""
        url_label = re.search(r'\bURL:\s*(https?://\S+)', block, re.IGNORECASE)
        if url_label:
            url = url_label.group(1).rstrip('.,;)')
        else:
            url_m = _URL_RE.search(block)
            if url_m:
                url = url_m.group(0).rstrip('.,;)')

        if not url:
            continue

        headline = ""
        h_label = re.search(r'HEADLINE:\s*(.+?)(?:\n|$)', block, re.IGNORECASE)
        if h_label:
            headline = h_label.group(1).strip()
        else:
            before_url = block[:block.index(url)].strip()
            before_url = re.sub(r'^[1-4][.):\s]\s*', '', before_url)
            headline = before_url.split('|')[0].strip().rstrip(':')

        headline = re.sub(r'\*+', '', headline).strip()
        if not headline or len(headline) < 5:
            continue

        blurb = ""
        b_m = re.search(
            r'BLURB:\s*(.+?)(?=\n\s*[1-4][.)]|\Z)',
            block, re.IGNORECASE | re.DOTALL,
        )
        if b_m:
            blurb = b_m.group(1).strip()

        story: dict = {"headline": headline, "link": url}
        if blurb:
            story["blurb"] = blurb
        stories.append(story)

    return stories[:4]


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


def _is_article_url(url: str) -> bool:
    """Reject bare domains — require at least some path beyond '/'."""
    if not url or not url.startswith("http"):
        return False
    from urllib.parse import urlparse
    return len(urlparse(url).path.rstrip("/")) > 0


def _dedup_by_domain(stories: list) -> list:
    seen: set = set()
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

    # Use story #1 as featured; try each story's URL for an og:image
    featured_image = ""
    featured_idx = 0
    for i, s in enumerate(stories):
        img = _og_image(s.get("link", ""))
        if img:
            featured_image = img
            featured_idx = i
            break

    main = stories[featured_idx]
    rest = [s for i, s in enumerate(stories) if i != featured_idx]

    return {
        "headline": main.get("headline", ""),
        "blurb": main.get("blurb", ""),
        "link": main.get("link", ""),
        "image": featured_image,
        "more": [
            {"headline": s.get("headline", ""), "link": s.get("link", "")}
            for s in rest
        ],
    }


def _search_section(prompt: str) -> str:
    """One focused web search call for one newsletter section. Returns prose."""
    resp = _client().messages.create(
        model=_MODEL,
        max_tokens=2000,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 3,
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
    logger.info("Digest (%d chars): %s...", len(text), text[:200])
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


# ── News ──────────────────────────────────────────────────────────────────────

_SECTION_RULES = (
    "Find up to 4 stories from the last 3 days and output them in this exact format:\n\n"
    "1. HEADLINE: [article headline]\n"
    "   URL: [https://full-article-url]\n"
    "   DATE: [publication date]\n"
    "   BLURB: [warm 2-3 sentence description]\n\n"
    "2. HEADLINE: [article headline]\n"
    "   URL: [https://full-article-url]\n"
    "   DATE: [publication date]\n\n"
    "3. HEADLINE: [article headline]\n"
    "   URL: [https://full-article-url]\n"
    "   DATE: [publication date]\n\n"
    "4. HEADLINE: [article headline]\n"
    "   URL: [https://full-article-url]\n"
    "   DATE: [publication date]\n\n"
    "Rules:\n"
    "- Only include articles from the last 3 days. Prefer the most recent.\n"
    "- Each story must be a specific, standalone article — NOT a roundup, digest, "
    "or aggregator post listing multiple stories.\n"
    "- Each story from a different website.\n"
    "- Prefer established outlets (New York Times, Guardian, BBC, NPR, Reuters, AP, Wired, Nature) "
    "but any credible source is fine.\n"
    "- Output only what you find — do not pad with older or off-topic stories.\n"
    "- Every URL must start with https:// and link directly to the article.\n"
)


def fetch_news() -> tuple[dict | None, dict | None, dict | None]:
    """Three focused search calls; returns None for any section with no recent stories."""
    today = date.today()
    today_str = today.strftime("%B %d, %Y")

    good_prompt = (
        f"Today is {today_str}. Find news stories from the last 3 days that would make someone "
        "feel genuinely hopeful, proud, or inspired when they read them over morning coffee.\n"
        "The story could be about anything — a scientific discovery, a community coming together, "
        "an underdog winning, a person beating the odds, the environment recovering, an act of "
        "generosity, a long-overdue milestone, animals thriving, or the world measurably improving "
        "in some way. Any topic qualifies as long as the emotional tone is genuinely uplifting.\n"
        "Prefer established outlets: New York Times, Guardian, BBC, NPR, Reuters, AP, The Independent.\n\n"
        + _SECTION_RULES
    )

    ai_prompt = (
        f"Today is {today_str}. Find news stories from the last 3 days about technology — "
        "especially AI — that would make someone feel excited or hopeful about the future.\n"
        "The story should show technology solving a real problem or opening up new possibilities: "
        "a tool helping people who couldn't be helped before, a research breakthrough that changes "
        "what's possible, a system catching something humans missed, or an application making "
        "everyday life meaningfully better. Focus on demonstrated impact, not product announcements.\n"
        "Prefer: MIT Technology Review, Wired, Nature, New Scientist, The Verge, STAT News, NPR, BBC.\n\n"
        + _SECTION_RULES
    )

    ny_prompt = (
        f"Today is {today_str}. Find New York City stories from the last 3 days that capture "
        "what makes the city feel alive and worth loving.\n"
        "Could be a Brooklyn neighborhood doing something remarkable, a local team or person "
        "winning, a new place opening that people are buzzing about, street art or culture, "
        "community pride, a only-in-New-York moment, or anything that makes a New Yorker smile. "
        "Brooklyn (Bed-Stuy, Bushwick, Crown Heights) and Manhattan preferred. "
        "At most 1 sports result. Only things that already happened — no event previews.\n"
        "Prefer: New York Times metro, Gothamist, Brooklyn Paper, Bklyner, Hyperallergic, "
        "Curbed NY, Timeout NY, amNY, NY1.\n\n"
        + _SECTION_RULES
    )

    try:
        logger.info("Searching: Good News...")
        good_digest = _search_section(good_prompt)
        good_stories = _parse_section_digest(good_digest)
        logger.info("Good News: %d parsed; links=%s",
                    len(good_stories), [s.get("link", "")[:70] for s in good_stories])

        logger.info("Searching: AI Impact...")
        ai_digest = _search_section(ai_prompt)
        ai_stories = _parse_section_digest(ai_digest)
        logger.info("AI Impact: %d parsed; links=%s",
                    len(ai_stories), [s.get("link", "")[:70] for s in ai_stories])

        logger.info("Searching: New York...")
        ny_digest = _search_section(ny_prompt)
        ny_stories = _parse_section_digest(ny_digest)
        logger.info("New York: %d parsed; links=%s",
                    len(ny_stories), [s.get("link", "")[:70] for s in ny_stories])

        good_news = _shape_stories(good_stories)
        ai_impact = _shape_stories(ai_stories)
        ny_news = _shape_stories(ny_stories)

        logger.info("Sections — good: %s | ai: %s | ny: %s",
                    good_news["headline"][:50] if good_news else "OMITTED",
                    ai_impact["headline"][:50] if ai_impact else "OMITTED",
                    ny_news["headline"][:50] if ny_news else "OMITTED")
        return good_news, ai_impact, ny_news

    except Exception:
        logger.exception("fetch_news failed")
        return None, None, None
