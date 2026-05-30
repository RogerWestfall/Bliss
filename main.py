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
    "ny_news": {
        "headline": "Bushwick's Newest Skate Spot Is a Community-Built Gem",
        "blurb": (
            "A crew of local skaters and neighborhood volunteers transformed a vacant lot on "
            "Jefferson Avenue into a free community skate space, complete with a mini ramp and "
            "flatground area. Open to all ages, it's already become a daily hangout for kids "
            "and riders from across Brooklyn."
        ),
        "link": "https://www.gothamist.com",
        "image": "https://images.unsplash.com/photo-1547447134-cd3f5c716030?w=600&q=80",
        "more": [
            {"headline": "Mets Rally in the 9th to Take the Series Opener at Citi Field", "link": "https://www.nydailynews.com"},
            {"headline": "Free Outdoor Movie Nights Return to Prospect Park This Weekend", "link": "https://www.timeout.com"},
            {"headline": "Bed-Stuy Block Association Wins City Grant for New Community Garden", "link": "https://www.brooklynpaper.com"},
        ],
    },
}


def main(preview: bool = False, output: str | None = None, mock: bool = False, edition: str = "") -> None:
    from newsletter.renderer import render
    from newsletter.sender import send

    if mock:
        quote = _MOCK["quote"]
        good_news = _MOCK["good_news"]
        ai_impact = _MOCK["ai_impact"]
        ny_news = _MOCK["ny_news"]
        logger.info("Mock mode — using sample content")
    else:
        from newsletter.feeds import fetch_quote, fetch_news
        logger.info("Fetching content...")
        quote = fetch_quote()
        logger.info('Quote: "%s" — %s', quote["quote"][:55], quote["author"])
        good_news, ai_impact, ny_news = fetch_news()
        logger.info("Good News: %s", good_news["headline"])
        logger.info("Impactful AI: %s", ai_impact["headline"])
        logger.info("New York: %s", ny_news["headline"])

    html = render(quote, good_news, ai_impact, ny_news)

    if output:
        with open(output, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("Saved to: %s", output)

    if not preview:
        logger.info("Sending newsletter...")
        send(html, edition=edition)
        logger.info("Done.")
    else:
        logger.info("Preview mode — email not sent.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send the Bliss Daily newsletter.")
    parser.add_argument("--preview", action="store_true", help="Render but do not send.")
    parser.add_argument("--output", metavar="FILE", help="Save rendered HTML to a file.")
    parser.add_argument("--mock", action="store_true", help="Use sample content (no feeds needed).")
    parser.add_argument("--edition", metavar="EDITION", default="", help="Edition label, e.g. 'morning' or 'evening'.")
    args = parser.parse_args()
    try:
        main(preview=args.preview, output=args.output, mock=args.mock, edition=args.edition)
    except Exception as exc:
        logger.error("Fatal: %s", exc, exc_info=True)
        sys.exit(1)
