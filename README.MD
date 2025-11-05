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

```bash
pip install requests beautifulsoup4 python-dotenv
```

## Setup

1. **Obtain cookies from browser:**
   - Open DevTools (F12) in Chrome/Firefox
   - Navigate to `https://www.cia.gov/readingroom/search/site/GATE`
   - Complete any required authentication steps
   - Find successful request in Network tab
   - Right-click → Copy → Copy as cURL
   - Extract cookie values

2. **Configure `.env`:**
```bash
COOKIE_SESSION=<_session_ cookie value>
COOKIE_AK_BMSC=<ak_bmsc cookie value>
```

## Usage

### Basic search (unlimited pages):
```bash
python script/cia_search.py GATE
```

### Search with page limit:
```bash
python script/cia_search.py GATE --max-pages 10
```

### Multi-word search:
```bash
python script/cia_search.py gifted and talented education
```

### Resume from specific page:
```bash
python script/cia_search.py GATE --start-page 100
```

### Reset and start fresh:
```bash
python script/cia_search.py GATE --reset
```

### Options:
```
searchterm          Search term (can be multiple words, no quotes needed)
--output-dir DIR    Output directory (default: output/)
--delay SECONDS     Request delay (default: 2.0)
--max-pages N       Maximum pages to fetch (default: unlimited)
--start-page N      Starting page number (default: auto-resume from progress)
--reset             Reset progress and start from page 0
```

### Auto-Resume

The tool automatically resumes from the last scraped page when you run the same search term again. The progress is stored in the output file itself. To start fresh, use the `--reset` flag.

## Output Format

Results are saved as `{SEARCHTERM}.json` in the output directory (spaces replaced with underscores). Progress is stored within the output file itself - no separate progress files.

Output JSON structure:
```json
{
  "search_term": "GATE",
  "start_time": "2025-11-05T14:59:05.878505",
  "end_time": "2025-11-05T15:00:23.123456",
  "total_pages": 10,
  "total_urls": 200,
  "pages": [
    {
      "page_number": 0,
      "url": "https://www.cia.gov/readingroom/search/site/GATE",
      "urls_found": 20,
      "urls": [
        {
          "url": "https://www.cia.gov/readingroom/document/cia-rdp81-00120r000100050011-0",
          "title": "SYSTEM FOR COUNTERMEASURES"
        }
      ]
    }
  ],
  "all_urls": [ /* deduplicated list */ ],
  "sessions": [
    {
      "start_time": "2025-11-05T14:59:05.878505",
      "start_page": 0
    }
  ],
  "progress": {
    "last_page": 9,
    "pages_scraped": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    "last_updated": "2025-11-05T15:00:23.123456"
  }
}
```

**File naming:**
- Single word: `GATE.json`
- Multiple words: `GIFTED_AND_TALENTED_EDUCATION.json`

The output file is saved incrementally after each page, so you won't lose progress if the script is interrupted.

## Cookie Expiration

The `ak_bmsc` cookie typically expires after 30-60 minutes. When this occurs:

**Symptoms:**
```
⚠️  Got redirect (status 302)
This usually means cookies have expired.
```

**Solution:**
1. Refresh browser session at search page
2. Extract new cookies from DevTools
3. Update `.env` with new `COOKIE_AK_BMSC` value
4. Run the same search term again - it will automatically resume from the last page

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

## Rate Limiting

Recommended delay: **2.0 seconds** between requests to maintain respectful access patterns and session stability.

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

## Technical Notes

- **User-Agent**: Chromium 142 (matches typical browser fingerprint)
- **TLS**: Requests library uses system SSL/TLS
- **Session persistence**: `requests.Session()` maintains cookies across requests
- **HTML parsing**: BeautifulSoup4 with `html.parser`
- **Error handling**: Graceful degradation on HTTP errors, redirect loops, empty pages

## Limitations

- Requires manual cookie extraction from authenticated browser session
- Cookies expire periodically (session-based authentication)
- No support for advanced search filters (collection, date range)
- No document content download (metadata only)

## Legal Notice

This tool is for research and archival purposes. Users must ensure compliance with CIA.gov Terms of Service and all applicable laws and regulations. Rate limiting is recommended to maintain respectful access patterns.

## Project Structure

```
.
├── script/cia_search.py       # Main search tool
├── .env                       # Cookie storage (git-ignored)
├── output/                    # JSON results and debug files
└── README.md
```

## Future Enhancements

- [ ] Automatic session refresh via Playwright
- [ ] Document content download
- [ ] Collection filtering support
- [ ] Date range queries
- [ ] Parallel page fetching
- [ ] SQLite result storage