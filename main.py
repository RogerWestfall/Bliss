#!/usr/bin/env python3
"""Entry point: fetch content, render HTML, send email."""

import logging
import argparse
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main(preview: bool = False, output: str | None = None) -> None:
    from newsletter.content import fetch_quote, fetch_good_news, fetch_ai_impact
    from newsletter.renderer import render
    from newsletter.sender import send

    logger.info("Fetching content...")
    quote = fetch_quote()
    logger.info("Quote: %s — %s", quote["quote"][:60], quote["author"])

    good_news = fetch_good_news()
    logger.info("Good News: %s", good_news["headline"])

    ai_impact = fetch_ai_impact()
    logger.info("AI Impact: %s", ai_impact["headline"])

    logger.info("Rendering newsletter...")
    html = render(quote, good_news, ai_impact)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("Saved HTML preview to: %s", output)

    if not preview:
        logger.info("Sending newsletter...")
        send(html)
        logger.info("Done.")
    else:
        logger.info("Preview mode — email not sent.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send the Bliss Daily newsletter.")
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Generate the newsletter but do not send it.",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        help="Save the rendered HTML to a file (useful with --preview).",
    )
    args = parser.parse_args()
    try:
        main(preview=args.preview, output=args.output)
    except Exception as exc:
        logger.error("Fatal error: %s", exc, exc_info=True)
        sys.exit(1)
