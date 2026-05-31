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

# Matches any https:// URL, stops at whitespace or common trailing punctuation
_URL_RE = re.compile(r'https?://[^\s\|<>"()\[\]{}]+')


def _client() -> anthropic.Anthropic:
    global _client_instance
    if _client_instance is None:
        _client_instance = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client_instance


def _parse_section_digest(text: str) -> list[dict]:
    """Extract up to 4 stories from a section digest.

    Handles two formats the search model might use:
      Labeled:  1. HEADLINE: ...\n   URL: https://...\n   BLURB: ...
      Pipe:     1. Headline | https://... | date
    """
    stories = []
    # Split on newline followed by a story number (1–4)
    blocks = re.split(r'\n(?=\s*[1-4][.)]\s)', '\n' + text.strip())

    for block in blocks:
        block = block.strip()
        if not re.match(r'[1-4][.)]', block):
            continue

        # ── URL ───────────────────────────────────────────────────────────────────
        # Prefer the URL: labeled line; fall back to any https:// in the block
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

        # ── Headline ──────────────────────────────────────────────────────────────────
        headline = ""
        h_label = re.search(r'HEADLINE:\s*(.+?)(?:\n|$)', block, re.IGNORECASE)
        if h_label:
            headline = h_label.group(1).strip()
        else:
            # Find text before the URL; in pipe format the headline is before |
            before_url = block[:block.index(url)].strip()
            before_url = re.sub(r'^[1-4][.)]\s*', '', before_url)
            headline = before_url.split('|')[0].strip().rstrip(':')

        headline = re.sub(r'\*+', '', headline).strip()  # strip markdown bold
        if not headline or len(headline) < 5:
            continue

        # ── Blurb (story #1 only) ───────────────────────────────────────────────────────────
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


def _search_section(prompt: str) -> str:
    """One focused web search call for one newsletter section. Returns prose."""
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
    logger.info("Digest (%d chars): %s...", len(text), text[:200])
    return text


# ── Quote of the Day ──────────────────────────────────────────────────────────────────────

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


# ── News ────────────────────────────────────────────────────────────────────────────────

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
    "Find exactly 4 stories and output them in this exact format:\n\n"
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
    "- Each story must be a specific article (not a roundup, digest, or weekly summary).\n"
    "- Each story from a different website.\n"
    "- Prefer articles from the last 7 days; older is fine if nothing recent.\n"
    "- Skip WSJ, Bloomberg, FT, Economist, Washington Post.\n"
    "- Include the best available stories — do not refuse or leave fields blank.\n"
    "- Every URL must start with https:// and link directly to the article.\n"
)


def fetch_news() -> tuple[dict, dict, dict]:
    """Three focused search calls; stories extracted directly by Python regex."""
    today = date.today()
    today_str = today.strftime("%B %d, %Y")

    good_prompt = (
        f"Today is {today_str}. Find 4 uplifting, positive news stories from the past week.\n"
        "Topics: medical breakthroughs, environmental wins, acts of kindness, "
        "community achievements, wildlife recoveries, humanitarian milestones.\n"
        "Search any reputable news source — BBC, Guardian, NYT, NPR, Reuters, AP, "
        "CBC, The Independent, Positive News are all great.\n\n"
        + _SECTION_RULES
    )

    ai_prompt = (
        f"Today is {today_str}. Find 4 recent stories about AI delivering "
        "real, demonstrated positive impact.\n"
        "Topics: AI used in healthcare, climate, accessibility, education, or science — "
        "with actual results, not just product announcements.\n"
        "Search any reputable tech or science outlet — MIT Technology Review, Wired, "
        "Nature, New Scientist, Scientific American, NPR, BBC, The Verge, STAT News.\n\n"
        + _SECTION_RULES
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

        good_news = _shape_stories(good_stories) or _FALLBACK_GOOD_NEWS
        ai_impact = _shape_stories(ai_stories) or _FALLBACK_AI
        ny_news = _shape_stories(ny_stories) or _FALLBACK_NY

        logger.info("Headlines — good: %s | ai: %s | ny: %s",
                    good_news["headline"][:50],
                    ai_impact["headline"][:50],
                    ny_news["headline"][:50])
        return good_news, ai_impact, ny_news

    except Exception:
        logger.exception("fetch_news failed — using fallbacks")
        return _FALLBACK_GOOD_NEWS, _FALLBACK_AI, _FALLBACK_NY
