"""Renders the newsletter HTML from the Jinja2 template."""

import os
from datetime import datetime
from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")


def render(quote: dict, good_news: dict, ai_impact: dict, ny_news: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("newsletter.html")
    return template.render(
        date=datetime.now().strftime("%A, %B %d, %Y"),
        quote=quote,
        good_news=good_news,
        ai_impact=ai_impact,
        ny_news=ny_news,
    )
