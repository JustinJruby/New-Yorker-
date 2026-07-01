#!/usr/bin/env python3
"""
New Yorker -> Kindle

Scrapes the articles from the current issue of The New Yorker
(https://www.newyorker.com/magazine), bundles them into a single
Kindle-friendly PDF, and emails it to your Send-to-Kindle address.

Intended for personal use with your own New Yorker subscription:
export your logged-in browser cookies to cookies.txt so the scraper
can read full articles instead of the paywall preview.

Usage:
    python3 newyorker_to_kindle.py                  # scrape, build PDF, email it
    python3 newyorker_to_kindle.py --no-email       # just build the PDF
    python3 newyorker_to_kindle.py --limit 3        # only first 3 articles (testing)

Configuration lives in config.ini (see config.example.ini).
"""

import argparse
import configparser
import re
import smtplib
import ssl
import sys
import tempfile
import time
from collections import Counter
from email.message import EmailMessage
from html import escape
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.newyorker.com"
MAGAZINE_URL = BASE_URL + "/magazine"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"
)
ARTICLE_LINK_RE = re.compile(r"^/magazine/(\d{4})/(\d{2})/(\d{2})/[a-z0-9-]+$")
FETCH_DELAY_SECONDS = 1.0
MAX_IMAGES_PER_ARTICLE = 4


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

def load_config(path: Path) -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    if path.exists():
        config.read(path)
    return config


# --------------------------------------------------------------------------
# Scraping
# --------------------------------------------------------------------------

def make_session(cookies_file: str | None) -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    if cookies_file:
        cookie_path = Path(cookies_file).expanduser()
        if cookie_path.exists():
            jar = MozillaCookieJar(str(cookie_path))
            jar.load(ignore_discard=True, ignore_expires=True)
            session.cookies.update(jar)
            print(f"Loaded {len(jar)} cookies from {cookie_path}")
        else:
            print(f"Warning: cookies file {cookie_path} not found; "
                  "paywalled articles will be truncated.")
    return session


def get_current_issue(session: requests.Session) -> tuple[str, list[str]]:
    """Return (issue_date, article_urls) for the current issue."""
    resp = session.get(MAGAZINE_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    seen: dict[str, str] = {}  # path -> date, insertion-ordered
    for a in soup.find_all("a", href=True):
        match = ARTICLE_LINK_RE.match(a["href"])
        if match:
            date = "-".join(match.groups())
            seen.setdefault(a["href"], date)

    if not seen:
        raise RuntimeError(
            "No article links found on the magazine page. "
            "The New Yorker may have changed its page layout."
        )

    # The magazine page can also link a few older pieces; keep only the
    # issue date that the majority of the article links share.
    issue_date = Counter(seen.values()).most_common(1)[0][0]
    urls = [urljoin(BASE_URL, path)
            for path, date in seen.items() if date == issue_date]
    return issue_date, urls


def _first_text(soup: BeautifulSoup, selector: str) -> str:
    node = soup.select_one(selector)
    return node.get_text(" ", strip=True) if node else ""


def _pick_image_url(img) -> str | None:
    src = img.get("src") or ""
    if src.startswith("http"):
        return src
    srcset = img.get("srcset") or ""
    for candidate in srcset.split(","):
        url = candidate.strip().split(" ")[0]
        if url.startswith("http"):
            return url
    return None


def download_image(session: requests.Session, url: str, dest_dir: Path,
                   index: int) -> Path | None:
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    content_type = resp.headers.get("Content-Type", "")
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
           "image/webp": ".webp"}.get(content_type.split(";")[0].strip())
    if ext is None:
        return None
    path = dest_dir / f"img_{index:04d}{ext}"
    path.write_bytes(resp.content)
    return path


def scrape_article(session: requests.Session, url: str, image_dir: Path,
                   image_counter: list[int]) -> dict | None:
    """Fetch one article and return its metadata plus cleaned body HTML."""
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    title = _first_text(soup, '[data-testid="ContentHeaderHed"]') or _first_text(soup, "h1")
    if not title:
        return None
    dek = _first_text(soup, '[data-testid="ContentHeaderDek"]')
    rubric = _first_text(soup, '[data-testid="ContentHeaderRubric"]')
    bylines = [b.get_text(" ", strip=True)
               for b in soup.select('[data-testid="BylineName"]')]
    byline = ", ".join(dict.fromkeys(bylines))

    body = soup.select_one('[data-testid="BodyWrapper"]') or soup.select_one(".article__body")
    truncated = soup.select_one('[data-testid="PaywallInlineBarrierWrapper"]') is not None

    parts: list[str] = []
    images_used = 0
    if body is not None:
        # Drop everything that isn't article prose.
        for junk in body.select(
            "aside, script, style, iframe, form, button, "
            '[data-testid="PaywallInlineBarrierWrapper"], '
            '[data-testid="cne-audio-embed-figure"], '
            '[class*="recirc"], [class*="Recirc"], [class*="newsletter"], '
            '[class*="ConsumerMarketing"], [class*="ad-"], [class*="Ad-"]'
        ):
            junk.decompose()

        for node in body.find_all(["p", "h2", "h3", "blockquote", "figure"]):
            if node.name == "figure":
                if images_used >= MAX_IMAGES_PER_ARTICLE:
                    continue
                img = node.find("img")
                img_url = _pick_image_url(img) if img else None
                if not img_url:
                    continue
                image_counter[0] += 1
                local = download_image(session, img_url, image_dir, image_counter[0])
                if not local:
                    continue
                images_used += 1
                caption_node = node.find("figcaption")
                caption = caption_node.get_text(" ", strip=True) if caption_node else ""
                parts.append(
                    f'<div class="figure"><img src="{local}"/>'
                    + (f'<p class="caption">{escape(caption)}</p>' if caption else "")
                    + "</div>"
                )
            else:
                text = node.get_text(" ", strip=True)
                if text:
                    parts.append(f"<{node.name}>{escape(text)}</{node.name}>")

    return {
        "url": url,
        "title": title,
        "dek": dek,
        "rubric": rubric,
        "byline": byline,
        "body_html": "\n".join(parts),
        "truncated": truncated,
    }


# --------------------------------------------------------------------------
# PDF generation
# --------------------------------------------------------------------------

PDF_CSS = """
@page {
    size: 6in 8in;
    margin: 0.45in 0.4in;
}
body { font-family: Times-Roman; font-size: 11.5pt; line-height: 1.45; }
h1 { font-size: 20pt; margin: 0 0 6pt 0; }
h2 { font-size: 14pt; margin: 14pt 0 4pt 0; }
h3 { font-size: 12.5pt; margin: 12pt 0 4pt 0; }
p { margin: 0 0 8pt 0; text-align: justify; }
blockquote { margin: 8pt 16pt; font-style: italic; }
.cover { text-align: center; margin-top: 1.5in; }
.cover h1 { font-size: 28pt; }
.cover p { text-align: center; font-size: 13pt; }
.toc h1 { font-size: 18pt; }
.toc p { text-align: left; margin-bottom: 5pt; }
.article { page-break-before: always; }
.rubric { font-size: 10pt; text-transform: uppercase; letter-spacing: 1pt;
          color: #555555; margin-bottom: 2pt; }
.dek { font-size: 13pt; font-style: italic; margin-bottom: 4pt; }
.byline { font-size: 11pt; margin-bottom: 14pt; }
.figure { margin: 10pt 0; }
.figure img { max-width: 100%; }
.caption { font-size: 9pt; color: #555555; text-align: left; }
.truncated { font-size: 10pt; color: #883333; font-style: italic; }
"""


def build_html(issue_date: str, articles: list[dict]) -> str:
    toc = "\n".join(
        f"<p>{escape(a['rubric']) + ': ' if a['rubric'] else ''}"
        f"<b>{escape(a['title'])}</b>"
        f"{' — ' + escape(a['byline']) if a['byline'] else ''}</p>"
        for a in articles
    )
    sections = []
    for a in articles:
        header = ""
        if a["rubric"]:
            header += f'<p class="rubric">{escape(a["rubric"])}</p>'
        header += f"<h1>{escape(a['title'])}</h1>"
        if a["dek"]:
            header += f'<p class="dek">{escape(a["dek"])}</p>'
        if a["byline"]:
            header += f'<p class="byline">By {escape(a["byline"])}</p>'
        notice = ""
        if a["truncated"]:
            notice = ('<p class="truncated">[Article truncated by paywall — '
                      "add subscriber cookies to get full text.]</p>")
        sections.append(
            f'<div class="article">{header}\n{a["body_html"]}\n{notice}</div>'
        )

    return f"""<html><head><style>{PDF_CSS}</style></head><body>
<div class="cover">
  <h1>The New Yorker</h1>
  <p>Issue of {escape(issue_date)}</p>
  <p>{len(articles)} articles</p>
</div>
<div class="article toc">
  <h1>In This Issue</h1>
  {toc}
</div>
{"".join(sections)}
</body></html>"""


def html_to_pdf(html: str, output_path: Path) -> None:
    from xhtml2pdf import pisa

    def link_callback(uri: str, rel: str) -> str:
        return uri  # images are already local absolute paths

    with open(output_path, "wb") as fh:
        status = pisa.CreatePDF(html, dest=fh, link_callback=link_callback)
    if status.err:
        raise RuntimeError(f"PDF generation reported {status.err} error(s)")


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------

def email_to_kindle(config: configparser.ConfigParser, pdf_path: Path,
                    issue_date: str) -> None:
    kindle_email = config.get("kindle", "kindle_email", fallback="").strip()
    host = config.get("smtp", "host", fallback="smtp.gmail.com")
    port = config.getint("smtp", "port", fallback=465)
    username = config.get("smtp", "username", fallback="").strip()
    password = config.get("smtp", "password", fallback="").strip()
    sender = config.get("smtp", "from", fallback=username).strip()
    convert = config.getboolean("kindle", "convert", fallback=True)

    missing = [name for name, value in
               [("kindle.kindle_email", kindle_email),
                ("smtp.username", username),
                ("smtp.password", password)] if not value]
    if missing:
        raise RuntimeError(
            "Missing config values: " + ", ".join(missing)
            + ". Copy config.example.ini to config.ini and fill it in."
        )

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = kindle_email
    # Subject "convert" tells Amazon to convert the PDF to a reflowable
    # Kindle document instead of showing fixed pages.
    msg["Subject"] = "convert" if convert else f"The New Yorker {issue_date}"
    msg.set_content(f"The New Yorker, issue of {issue_date}.")
    msg.add_attachment(
        pdf_path.read_bytes(),
        maintype="application",
        subtype="pdf",
        filename=pdf_path.name,
    )

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context) as smtp:
            smtp.login(username, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port) as smtp:
            smtp.starttls(context=context)
            smtp.login(username, password)
            smtp.send_message(msg)
    print(f"Emailed {pdf_path.name} to {kindle_email}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Scrape the current New Yorker issue into a PDF and "
                    "email it to your Kindle.")
    parser.add_argument("--config", default=str(script_dir / "config.ini"),
                        help="Path to config file (default: config.ini next to script)")
    parser.add_argument("--cookies", default=None,
                        help="Path to Netscape cookies.txt exported from your "
                             "logged-in browser (overrides config)")
    parser.add_argument("--output", default=None,
                        help="Where to write the PDF (default: "
                             "new-yorker-<issue-date>.pdf in current directory)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only scrape the first N articles (for testing)")
    parser.add_argument("--no-email", action="store_true",
                        help="Build the PDF but don't email it")
    args = parser.parse_args()

    config = load_config(Path(args.config))
    cookies_file = args.cookies or config.get("scraper", "cookies_file",
                                              fallback=None)
    session = make_session(cookies_file)

    print("Fetching current issue...")
    issue_date, urls = get_current_issue(session)
    if args.limit:
        urls = urls[: args.limit]
    print(f"Issue of {issue_date}: {len(urls)} articles")

    with tempfile.TemporaryDirectory(prefix="nyk_images_") as tmp:
        image_dir = Path(tmp)
        image_counter = [0]
        articles = []
        for i, url in enumerate(urls, 1):
            slug = url.rstrip("/").split("/")[-1]
            print(f"  [{i}/{len(urls)}] {slug}")
            try:
                article = scrape_article(session, url, image_dir, image_counter)
            except requests.RequestException as exc:
                print(f"    skipped ({exc})")
                continue
            if article is None:
                print("    skipped (could not parse)")
                continue
            if article["truncated"]:
                print("    note: paywall-truncated")
            articles.append(article)
            time.sleep(FETCH_DELAY_SECONDS)

        if not articles:
            print("No articles could be scraped; giving up.", file=sys.stderr)
            return 1

        output = Path(args.output) if args.output else Path(
            f"new-yorker-{issue_date}.pdf")
        print(f"Building PDF ({output})...")
        html_to_pdf(build_html(issue_date, articles), output)
        size_mb = output.stat().st_size / (1024 * 1024)
        print(f"Wrote {output} ({size_mb:.1f} MB)")

    truncated_count = sum(a["truncated"] for a in articles)
    if truncated_count:
        print(f"Warning: {truncated_count} article(s) were paywall-truncated. "
              "Export cookies from a logged-in browser session to fix this.")

    if args.no_email:
        return 0

    print("Emailing to Kindle...")
    email_to_kindle(config, output, issue_date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
