#!/usr/bin/env python3
"""Entry point: fetch content, render HTML, send email."""

import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Sample content used with --mock (no live feeds needed)
_MOCK = {
    "quote": {
        "quote": "I may not have gone where I intended to go, but I think I have ended up where I intended to be.",
        "author": "Douglas Adams",
    },
    "good_news": {
        "headline": "Volunteers Plant One Million Trees Across Drought-Stricken Communities",
        "blurb": (
            "A coalition of grassroots volunteers completed a landmark reforestation project this week, "
            "planting one million native trees across communities hardest hit by decades of drought. "
            "Organizers say the restored canopy will lower summer temperatures, improve air quality, "
            "and provide habitat for over 200 local species within the next decade."
        ),
        "link": "https://www.goodnewsnetwork.org",
        "image": "https://images.unsplash.com/photo-1542601906990-b4d3fb778b09?w=600&q=80",
        "more": [
            {"headline": "Teen Raises $200,000 to Build Libraries in Rural Communities", "link": "https://www.goodnewsnetwork.org"},
            {"headline": "City Converts Abandoned Lots Into Community Gardens", "link": "https://www.positive.news"},
            {"headline": "Scientists Discover Coral Reefs Recovering Faster Than Expected", "link": "https://www.goodnewsnetwork.org"},
        ],
    },
    "ai_impact": {
        "headline": "AI System Detects Early-Stage Pancreatic Cancer With 90% Accuracy",
        "blurb": (
            "Researchers at Johns Hopkins have developed an AI diagnostic tool that identifies "
            "pancreatic cancer at its earliest, most treatable stage with 90% accuracy — nearly "
            "double the current clinical benchmark. The model is being fast-tracked for clinical "
            "trials and could save tens of thousands of lives annually worldwide."
        ),
        "link": "https://www.technologyreview.com",
        "image": "https://images.unsplash.com/photo-1532187863486-abf9dbad1b69?w=600&q=80",
        "more": [
            {"headline": "AI Tool Helps Farmers Predict Crop Disease Weeks in Advance", "link": "https://www.technologyreview.com"},
            {"headline": "New Model Cuts Time to Develop Clean Energy Materials by 70%", "link": "https://www.technologyreview.com"},
            {"headline": "AI-Powered App Gives Blind Users Real-Time Scene Descriptions", "link": "https://www.technologyreview.com"},
        ],
    },
}


def main(preview: bool = False, output: str | None = None, mock: bool = False) -> None:
    from newsletter.renderer import render
    from newsletter.sender import send

    if mock:
        quote, good_news, ai_impact = _MOCK["quote"], _MOCK["good_news"], _MOCK["ai_impact"]
        logger.info("Mock mode — using sample content")
    else:
        from newsletter.feeds import fetch_quote, fetch_good_news, fetch_ai_impact
        logger.info("Fetching content...")
        quote = fetch_quote()
        logger.info('Quote: "%s" — %s', quote["quote"][:55], quote["author"])
        good_news = fetch_good_news()
        logger.info("Good News: %s", good_news["headline"])
        ai_impact = fetch_ai_impact()
        logger.info("Impactful AI: %s", ai_impact["headline"])

    html = render(quote, good_news, ai_impact)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("Saved to: %s", output)

    if not preview:
        logger.info("Sending newsletter...")
        send(html)
        logger.info("Done.")
    else:
        logger.info("Preview mode — email not sent.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send the Bliss Daily newsletter.")
    parser.add_argument("--preview", action="store_true", help="Render but do not send.")
    parser.add_argument("--output", metavar="FILE", help="Save rendered HTML to a file.")
    parser.add_argument("--mock", action="store_true", help="Use sample content (no feeds needed).")
    args = parser.parse_args()
    try:
        main(preview=args.preview, output=args.output, mock=args.mock)
    except Exception as exc:
        logger.error("Fatal: %s", exc, exc_info=True)
        sys.exit(1)
