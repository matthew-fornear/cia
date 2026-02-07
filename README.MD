<h1 align="center">CIA Reading Room Search Tool</h1>

Tool for accessing and collecting data from the CIA Freedom of Information Act (FOIA) Electronic Reading Room. Uses authenticated browser session cookies to access search results.

## Overview

The CIA Reading Room implements session-based authentication for access to search results. This tool uses cookies from a manually authenticated browser session to paginate through search results and collect document metadata.

**Key features:**
- Session-based authentication using browser cookies
- Pagination support with resume capability
- Drupal 7 search result parsing
- Session validation and error handling

## Architecture

The site implements session-based access control:
1. **Initial request** → Session verification page with authentication token
2. **Verification POST** → Session validation endpoint with challenge response
3. **Authenticated session** → Valid session cookies grant access

This tool uses cookies from a manually authenticated browser session to maintain access.

## Requirements

**For fetch scripts (cia_fetchmetadata, cia_fetchpdf):**
```bash
pip install requests beautifulsoup4 python-dotenv
# Optional but recommended: TLS fingerprint to reduce Akamai challenges
pip install httpcloak
```

**For PDF OCR (cia_fetchpdf, local_pdftotxt):**
```bash
pip install pymupdf pytesseract Pillow
# System Tesseract
# apt install tesseract-ocr   # Debian/Ubuntu
# brew install tesseract      # macOS
```

## Setup: cookies and Akamai

The reading room is behind **Akamai bot protection**. To run `cia_fetchmetadata.py` or `cia_fetchpdf.py` you must supply session cookies from a real browser that has already passed the challenge.

1. **Open the site in a browser**  
   Go to [CIA Reading Room](https://www.cia.gov/readingroom/) and run a search (e.g. `https://www.cia.gov/readingroom/search/site/SIMULATION`).

2. **Complete the Akamai interstitial if it appears**  
   If you see a “Checking your browser” or verification page, wait for it to finish. Once the real search results load, the cookies in your browser are valid.

3. **Copy the cookies**  
   - Open DevTools (F12) → **Application** (Chrome) or **Storage** (Firefox).  
   - Go to **Cookies** → `https://www.cia.gov`.  
   - Copy the values for:
     - **`_session_`**
     - **`ak_bmsc`** — use the value **after** the challenge (longer string, ~700 chars); this reduces how often you see the challenge again.

4. **Create `.env` in the project root:**
```bash
COOKIE_SESSION=<paste _session_ value>
COOKIE_AK_BMSC=<paste ak_bmsc value>
```

5. **Optional: httpcloak**  
   With `pip install httpcloak`, the scripts use a Chrome-like TLS fingerprint, which often lets the first request through without an interstitial. Without it, the script may hit the challenge more often and solve it in-code when possible.

## Usage

### Basic search (unlimited pages):
```bash
python script/cia_fetchmetadata.py GATE
```

### Search with page limit:
```bash
python script/cia_fetchmetadata.py GATE --max-pages 10
```

### Multi-word search:
```bash
python script/cia_fetchmetadata.py gifted and talented education
```

### Resume from specific page:
```bash
python script/cia_fetchmetadata.py GATE --start-page 100
```

### Reset and start fresh:
```bash
python script/cia_fetchmetadata.py GATE --reset
```

### Fetch PDFs and OCR from a metadata JSONL:
```bash
python script/cia_fetchpdf.py output/SIMULATION.jsonl
# or default input output/SIMULATION.jsonl:
python script/cia_fetchpdf.py
```

### Single local PDF → text (local_pdftotxt.py):
For **one local PDF file** (no CIA URLs): extract text and optionally run Tesseract OCR. Use a file path or run with no args for interactive file/folder picker.

```bash
# One file
python script/local_pdftotxt.py path/to/document.pdf

# Directory of PDFs (batch)
python script/local_pdftotxt.py path/to/pdfs/

# Interactive: pick file or folder (requires questionary)
python script/local_pdftotxt.py
```

**OCR options:** `--ocr` (OCR only pages with little text) or `--force-ocr` (OCR every page). Requires `pymupdf`, `pytesseract`, `Pillow`, and system `tesseract-ocr`. Output goes to `output/pdf-txt/`.

### Options (cia_fetchmetadata):
```
searchterm          Search term (can be multiple words, no quotes needed)
--output-dir DIR    Output directory (default: output/)
--delay SECONDS     Delay between requests (default: 90; site is rate-limited)
--max-pages N       Maximum pages to fetch (default: unlimited)
--start-page N      Starting page number (default: auto-resume from progress)
--reset             Reset progress and start from page 0
```

### Auto-Resume

The tool automatically resumes from the last scraped page when you run the same search term again. The progress is stored in the output file itself. To start fresh, use the `--reset` flag.

## Output Format

**cia_fetchmetadata** writes:
- **`output/{SEARCHTERM}.jsonl`** — one JSON object per line: `{"url": "https://...", "title": "..."}` (the main result list).
- **`output/{SEARCHTERM}.progress.json`** — resume state (last page, pages scraped, all_urls). Used to resume and to retry the last failed page.

File naming: single word → `GATE.jsonl`; multiple words → `GIFTED_AND_TALENTED_EDUCATION.jsonl`. Progress is saved after each page so you can resume if interrupted.

**cia_fetchpdf** writes:
- **`output/pdfs/`** — downloaded PDFs.
- **`output/pdf-txt/`** — OCR text files (one `.txt` per PDF).

**local_pdftotxt** writes:
- **`output/pdf-txt/`** — one `.txt` per input PDF.

## Cookie expiration and 503/unavailable

**Redirect (cookies expired):**  
If you see `Got redirect (status 302)` or “cookies have expired”, get a fresh `_session_` and `ak_bmsc` from the browser (after loading the reading room and passing any Akamai check), then update `.env` and run again. The script resumes from the last saved page.

**503 / “unavailable”:**  
The site may return a rate-limit or “unavailable” page. The scripts wait (`--unavailable-wait`, default 120s), then retry the same page. Use a long `--delay` (default 90s) between successful requests to reduce rate limiting.

## URL Structure

- **Base page**: `https://www.cia.gov/readingroom/search/site/GATE`
- **Pagination**: `https://www.cia.gov/readingroom/search/site/GATE?page=1`
- **Documents**: `https://www.cia.gov/readingroom/document/{doc-id}`

Pagination uses 0-indexed Drupal convention (page 0 = first page, no query param).

## Referer Requirements

The site validates referer headers:
- **Page 0**: Referer = `https://www.cia.gov/readingroom/`
- **Page 1**: Referer = `https://www.cia.gov/readingroom/search/site/gate` (lowercase)
- **Page N**: Referer = previous page URL

## Results Per Page

- **20 documents per page**
- Total pages calculated as: `⌈total_results / 20⌉`
- Example: 33,044 results = 1,653 pages (indexed 0-1652)

## Rate limiting

The site is heavily rate-limited. Default delay between requests is **90 seconds** (`--delay 90`). Increase with `--delay 120` or `--unavailable-wait 180` if you still see timeouts or “unavailable” responses.

## Debugging

Session validation issues (response < 10KB):
```bash
# Script saves small responses to debug files
cat output/debug_page_0_20251105_150000.html
```

Check for session validation indicators:
- Authentication tokens in HTML
- Redirect meta tags
- Response size < 10,000 bytes

## Technical notes

- **Cookies**: Required for `cia_fetchmetadata` and `cia_fetchpdf`; not needed for `local_pdftotxt`.
- **TLS**: With `httpcloak`, scripts use a Chrome-like TLS fingerprint to reduce Akamai challenges; otherwise plain `requests`.
- **Akamai**: On a small or challenge response, the fetch script attempts to solve the interstitial (POST to `/_sec/verify`) and retry. Fresh `ak_bmsc` from the browser after passing the challenge works best.
- **HTML parsing**: BeautifulSoup4; PDF text/OCR via PyMuPDF and Tesseract.

## Limitations

- Cookies must be copied manually from a browser that has passed the Akamai check; they expire (e.g. 30–60 minutes for `ak_bmsc`).
- No advanced search filters (collection, date range) in the metadata script.
- Rate limiting is strict; use long delays and resume when the run stops.

## Legal Notice

This tool is for research and archival purposes. Users must ensure compliance with CIA.gov Terms of Service and all applicable laws and regulations. Rate limiting is recommended to maintain respectful access patterns.

## Project Structure

```
.
├── script/cia_fetchmetadata.py  # Search reading room → JSONL of document URLs (needs cookies)
├── script/cia_fetchpdf.py       # Fetch document pages, download PDFs, OCR to text (needs cookies)
├── script/local_pdftotxt.py     # Single local PDF(s) → text; optional OCR (no cookies)
├── .env                         # Cookie storage (git-ignored)
├── output/                      # JSONL, .progress.json, pdfs/, pdf-txt/
└── README.md
```

## Future enhancements

- [ ] Automatic session refresh via Playwright
- [ ] Collection filtering and date range queries
- [ ] Parallel page fetching (within rate limits)
- [ ] SQLite result storage