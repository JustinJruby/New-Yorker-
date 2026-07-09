# New Yorker → Kindle

Scrapes the current issue of The New Yorker, bundles the articles into a
single Kindle-friendly file (cover page + table of contents + articles;
EPUB by default, PDF or plain text if you prefer), and emails it to your
Send-to-Kindle address so it shows up on your Kindle automatically.

For personal use with your own New Yorker subscription.

## Setup (macOS)

### 1. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Find your Send-to-Kindle address and approve your sender

1. Go to [amazon.com → Manage Your Content and Devices → Preferences → Personal Document Settings](https://www.amazon.com/hz/mycd/myx#/home/settings/payment).
2. Note your Kindle's email address (looks like `yourname_ABC123@kindle.com`).
3. Under **Approved Personal Document E-mail List**, add the email
   address you'll be sending from (e.g. your Gmail address). Amazon
   silently drops mail from unapproved senders.

### 3. Create a Gmail App Password

If you send via Gmail (the default), you need an App Password —
your normal password won't work:

1. Enable 2-Step Verification on your Google account.
2. Go to <https://myaccount.google.com/apppasswords> and create one.
3. Use the 16-character password in `config.ini`.

Any other SMTP provider works too — just change `host`/`port`.

### 4. Configure

```bash
cp config.example.ini config.ini
# then edit config.ini with your Kindle address and SMTP credentials
```

`config.ini` and `cookies.txt` are gitignored, so your credentials
never end up in the repo.

### 5. (Recommended) Export your subscriber cookies

Without login cookies, paywalled articles are truncated to the free
preview. To get full articles:

1. Log in to <https://www.newyorker.com> in your browser.
2. Export cookies for `newyorker.com` in Netscape/cookies.txt format
   using a browser extension (e.g. "Get cookies.txt LOCALLY" for
   Chrome, or "cookies.txt" for Firefox).
3. Save the file as `cookies.txt` next to the script (or point
   `cookies_file` in `config.ini` somewhere else).

Cookies expire after a while — if articles start coming back
truncated, re-export them.

## Usage

```bash
# The whole pipeline: scrape → PDF → email to Kindle
python3 newyorker_to_kindle.py

# Just build the PDF locally, don't email
python3 newyorker_to_kindle.py --no-email

# Quick test with only the first 2 articles
python3 newyorker_to_kindle.py --limit 2 --no-email

# Browse the archive (https://www.newyorker.com/archive)
python3 newyorker_to_kindle.py --list-issues

# Build a specific back issue
python3 newyorker_to_kindle.py --issue 2026-06-29
```

The scraper works from the issue's own table-of-contents page
(`newyorker.com/magazine/YYYY/MM/DD`), so you get exactly the articles
in that issue — by default the current one, or any back issue you pick
from the archive with `--issue`.

The PDF is written to `new-yorker-<issue-date>.pdf` in the current
directory (override with `--output`).

### Which format?

Set `format` in `config.ini` (or pass `--format`):

- **epub** (default, recommended) — Amazon converts it to a native
  Kindle book: reflowable text, adjustable font size, images, and a
  real table of contents you can jump around with.
- **pdf** — fixed 6"×8" pages. With `convert = true` the email subject
  is set to `convert`, which asks Amazon to reflow the PDF into a
  Kindle document; with `false` you get the fixed pages as-is.
- **txt** — plain text, maximally robust, but no images, no italics,
  and no chapter navigation.

## Run it automatically every week

A new issue appears on the site on Mondays. To have the magazine on
your Kindle every Monday morning without doing anything:

```bash
# Edit com.newyorker.kindle.plist first: replace the two REPLACE_ME
# entries with the absolute path to this repo, e.g. /Users/you/New-Yorker-
cp com.newyorker.kindle.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.newyorker.kindle.plist
```

It runs Mondays at 8:00 AM (edit `Hour`/`Weekday` in the plist to
taste) and logs to `/tmp/newyorker-kindle.log`. Unlike cron, launchd
runs the job at next wake if your Mac was asleep at the scheduled time.

To stop it:

```bash
launchctl unload ~/Library/LaunchAgents/com.newyorker.kindle.plist
```

## Troubleshooting

- **Nothing arrives on the Kindle** — check that the `from` address is
  on Amazon's Approved Personal Document E-mail List, and check the
  spam-free delivery can take a few minutes. Amazon also emails you a
  rejection notice if the document was refused.
- **Articles are cut off** — your cookies are missing or expired;
  re-export `cookies.txt` from a logged-in browser.
- **`SMTPAuthenticationError`** — you're using your real Gmail
  password instead of an App Password.
- **"No article links found"** — The New Yorker changed their page
  markup; the selectors in `newyorker_to_kindle.py` need updating.
