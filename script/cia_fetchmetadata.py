#!/usr/bin/env python3
"""
CIA Reading Room metadata fetcher (cia_fetchmetadata).
Searches the reading room and outputs a JSONL of document URLs and titles.
Uses cookies from .env; with httpcloak uses TLS fingerprint (Chrome) to avoid Akamai challenges.
"""

import argparse
import os
import re
import time
from urllib.parse import urljoin, quote_plus

try:
    from httpcloak import Session as HTTPCloakSession
    USE_HTTPCLOAK = True
except ImportError:
    USE_HTTPCLOAK = False
    import requests

from bs4 import BeautifulSoup
import json
from datetime import datetime
from dotenv import load_dotenv


def get_base_headers():
    """Return the base headers (match browser/curl to avoid bot challenge)."""
    return {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9',
        'cache-control': 'max-age=0',
        'priority': 'u=0, i',
        'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Brave";v="144"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Linux"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'sec-fetch-user': '?1',
        'sec-gpc': '1',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
    }


def get_cookies_from_env():
    """
    Load cookies from .env file.
    Use cookies from AFTER you've loaded a search page (so use the ak_bmsc set after the challenge).
    Then the first request gets real results and the challenge solver is only used if cookies are stale.
    DevTools → Application → Cookies → cia.gov → copy _session_ and ak_bmsc.
    """
    cookies = {}
    
    session_value = os.getenv('COOKIE_SESSION')
    if session_value:
        cookies['_session_'] = session_value
    
    ak_bmsc_value = os.getenv('COOKIE_AK_BMSC')
    if ak_bmsc_value:
        cookies['ak_bmsc'] = ak_bmsc_value
    
    return cookies


def get_search_url(search_term, page=0):
    """Build the search URL with optional page parameter."""
    # URL encode the search term (spaces become + or %20, but the site uses +)
    # The site uses uppercase and spaces in the URL path
    encoded_term = quote_plus(search_term.upper())
    base_url = f'https://www.cia.gov/readingroom/search/site/{encoded_term}'
    if page > 0:
        return f"{base_url}?page={page}"
    return base_url


def get_referer(search_term, page=0):
    """Get the appropriate referer for this page."""
    if page == 0:
        return 'https://www.cia.gov/readingroom/'
    else:
        encoded_term = quote_plus(search_term.lower())
        if page == 1:
            return f'https://www.cia.gov/readingroom/search/site/{encoded_term}'
        else:
            return f'https://www.cia.gov/readingroom/search/site/{encoded_term}?page={page-1}'


def extract_document_urls(html_content, base_url):
    """Extract document URLs with titles from the search results page."""
    soup = BeautifulSoup(html_content, 'html.parser')
    results = []
    
    # Find the search results list
    search_results = soup.find('ol', class_='search-results')
    if not search_results:
        return []
    
    # Find all list items (each is a search result)
    for item in search_results.find_all('li', recursive=False):
        # Find the title link
        title_elem = item.find('h3', class_='title')
        if not title_elem:
            continue
        
        link = title_elem.find('a')
        if not link or not link.get('href'):
            continue
        
        url = link.get('href')
        title = link.get_text(strip=True)
        
        # Only include document URLs
        if '/readingroom/document/' in url:
            results.append({
                'url': url,
                'title': title
            })
    
    return results


def parse_akamai_interstitial(html):
    """
    Parse Akamai interstitial challenge page. Returns (bm_verify, pow_j) or (None, None).
    Challenge contains: var i = N; var j = i + Number("9026" + "45594"); and "bm-verify": "..." in JSON.
    """
    if "_sec/verify" not in html or "bm-verify" not in html:
        return None, None
    m_i = re.search(r"var\s+i\s*=\s*(\d+)\s*;", html)
    m_bm = re.search(r'"bm-verify"\s*:\s*"([^"]+)"', html)
    if not m_i or not m_bm:
        return None, None
    i = int(m_i.group(1))
    j = i + 902645594  # Number("9026" + "45594")
    return m_bm.group(1), j


def solve_akamai_interstitial(session, url, headers, challenge_html, use_httpcloak):
    """
    Given challenge HTML from first GET, POST to _sec/verify then GET url again.
    Returns (response_text, True) if retry returned real content; (challenge_html, False) on failure.
    """
    bm_verify, pow_j = parse_akamai_interstitial(challenge_html)
    if bm_verify is None:
        return challenge_html, False
    verify_url = "https://www.cia.gov/_sec/verify?provider=interstitial"
    post_headers = dict(get_base_headers())
    post_headers["referer"] = url
    post_headers["content-type"] = "application/json"
    body = json.dumps({"bm-verify": bm_verify, "pow": pow_j})
    try:
        if use_httpcloak:
            session.post(verify_url, headers=post_headers, data=body, timeout=30)
        else:
            session.post(verify_url, headers=post_headers, data=body, timeout=30, allow_redirects=True)
        if use_httpcloak:
            r3 = session.get(url, headers=headers, timeout=30)
        else:
            r3 = session.get(url, headers=headers, timeout=30, allow_redirects=False)
        return r3.text, len(r3.text) > 10000
    except Exception as e:
        print(f"   Challenge solve failed: {e}")
        return challenge_html, False


def write_jsonl(all_urls, jsonl_path):
    """Write one JSON object per line: {"url": "...", "title": "..."}."""
    with open(jsonl_path, 'w', encoding='utf-8') as f:
        for entry in all_urls:
            line = json.dumps({"url": entry["url"], "title": entry["title"]}, ensure_ascii=False) + "\n"
            f.write(line)


def check_for_next_page(html_content):
    """Check if there's a next page by looking for pagination."""
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Look for the pager
    pager = soup.find('ul', class_='pager')
    if not pager:
        return False
    
    # Look for "next" link
    next_link = pager.find('li', class_='pager-next')
    return next_link is not None


def get_progress_path(output_dir, output_filename):
    """Path to minimal progress file for resume (no redundant .json)."""
    return os.path.join(output_dir, f'{output_filename}.progress.json')


def load_existing_output(progress_file, legacy_json_file=None):
    """Load progress from .progress.json (or legacy .json) for resume."""
    if os.path.exists(progress_file):
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            print(f"📂 Found progress file: {progress_file}")
            all_urls = data.get('all_urls', [])
            pages_scraped = data.get('pages_scraped', [])
            last_page = data.get('last_page', (max(pages_scraped) if pages_scraped else -1))
            print(f"   Pages scraped: {len(pages_scraped)}, URLs: {len(all_urls)}, last page: {last_page}")
            return {
                'all_urls': all_urls,
                'progress': {'last_page': last_page, 'pages_scraped': pages_scraped},
                'pages': [{'page_number': p} for p in pages_scraped],
            }
        except Exception as e:
            print(f"⚠️  Error loading progress file: {e}")
    # Backward compat: load from legacy .json if present
    if legacy_json_file and os.path.exists(legacy_json_file):
        try:
            with open(legacy_json_file, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
            print(f"📂 Found legacy output file: {legacy_json_file}")
            progress = existing_data.get('progress', {})
            pages = existing_data.get('pages', [])
            all_urls = existing_data.get('all_urls', [])
            pages_scraped = sorted(p.get('page_number') for p in pages if p.get('page_number') is not None)
            last_page = progress.get('last_page', (max(pages_scraped) if pages_scraped else -1))
            return {
                'all_urls': all_urls,
                'progress': {'last_page': last_page, 'pages_scraped': pages_scraped},
                'pages': pages,
            }
        except Exception as e:
            print(f"⚠️  Error loading legacy file: {e}")
    return None


def main():
    parser = argparse.ArgumentParser(description='Search CIA Reading Room')
    parser.add_argument('searchterm', nargs='+', help='Search term to query (can be multiple words)')
    parser.add_argument('--output-dir', default='output', help='Output directory')
    parser.add_argument('--delay', type=float, default=90.0, help='Delay between successful requests (seconds); site is heavily rate-limited (default: 90)')
    parser.add_argument('--unavailable-wait', type=float, default=120.0, help='Seconds to wait when served 503/unavailable before retry (default: 120)')
    parser.add_argument('--max-retries', type=int, default=10, help='Max retries per page for timeout or unavailable (default: 10)')
    parser.add_argument('--max-pages', type=int, default=None, help='Maximum pages to fetch (default: unlimited)')
    parser.add_argument('--start-page', type=int, default=None, help='Starting page number (default: auto-resume from progress, including retrying last failed page)')
    parser.add_argument('--reset', action='store_true', help='Reset progress and start from page 0')
    
    args = parser.parse_args()
    
    # Join search term if it's multiple words
    search_term = ' '.join(args.searchterm) if isinstance(args.searchterm, list) else args.searchterm
    
    # Load environment variables from .env file
    load_dotenv()
    
    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)
    
    # Output: only .jsonl + minimal .progress.json (no redundant .json)
    output_filename = search_term.upper().replace(' ', '_')
    jsonl_file = os.path.join(args.output_dir, f'{output_filename}.jsonl')
    progress_file = get_progress_path(args.output_dir, output_filename)
    legacy_json_file = os.path.join(args.output_dir, f'{output_filename}.json')

    # Load or initialize progress from progress file (or legacy .json)
    if args.reset:
        print("🔄 Reset flag set - starting fresh")
        existing_output = None
        start_page = args.start_page if args.start_page is not None else 0
        pages_scraped = []
    else:
        existing_output = load_existing_output(progress_file, legacy_json_file)
        if existing_output:
            progress = existing_output.get('progress', {})
            start_page = args.start_page if args.start_page is not None else progress.get('last_page', 0) + 1
            pages_scraped = progress.get('pages_scraped', [])
            print(f"📂 Resuming from page {start_page}")
        else:
            start_page = args.start_page if args.start_page is not None else 0
            pages_scraped = []
            print(f"🆕 Starting fresh from page {start_page}")
    
    # Create session (httpcloak = TLS impersonation to avoid Akamai challenge; else plain requests)
    request_timeout = 60  # give server time before "context deadline exceeded"
    if USE_HTTPCLOAK:
        session = HTTPCloakSession(
            preset="chrome-143",
            allow_redirects=False,
            timeout=request_timeout,
        )
    else:
        session = requests.Session()

    # Set cookies from .env file
    cookies = get_cookies_from_env()
    if not cookies:
        print("❌ Error: No cookies found in .env file!")
        print("Please set COOKIE_SESSION and COOKIE_AK_BMSC in .env file.")
        print("See .env.example for template.")
        return

    if USE_HTTPCLOAK:
        for name, value in cookies.items():
            session.set_cookie(name, value)
    else:
        for name, value in cookies.items():
            session.cookies.set(name, value, domain='.cia.gov', path='/')

    print(f"\n{'='*60}")
    print(f"Starting search for: {search_term}")
    print(f"Cookies set: {len(cookies)}")
    if USE_HTTPCLOAK:
        print("Using httpcloak (TLS fingerprint: Chrome)")
    else:
        print("Tip: pip install httpcloak (or use local httpcloak) for TLS impersonation to avoid bot challenges.")
    if args.max_pages:
        print(f"Maximum pages: {args.max_pages}")
    else:
        print(f"Maximum pages: unlimited")
    print(f"Starting from page: {start_page} (resume retries the last failed page)")
    print(f"Delay between requests: {args.delay}s | Unavailable wait: {args.unavailable_wait}s | Max retries/page: {args.max_retries}")
    print(f"{'='*60}")
    
    # Initialize or load existing results (in-memory only; we persist .jsonl + .progress.json)
    if existing_output and not args.reset:
        all_results = {
            'search_term': search_term,
            'pages': existing_output.get('pages', []),
            'all_urls': existing_output.get('all_urls', []),
            'progress': existing_output.get('progress', {'last_page': -1, 'pages_scraped': []}),
        }
        print(f"📂 Loaded progress: {len(all_results['pages'])} pages, {len(all_results['all_urls'])} URLs")
    else:
        all_results = {
            'search_term': search_term,
            'pages': [],
            'all_urls': [],
            'progress': {'last_page': -1, 'pages_scraped': []},
        }
    
    page = start_page
    consecutive_empty = 0  # Track consecutive empty pages
    
    # Continue until no more pages or max-pages limit reached
    while True:
        # Check max-pages limit
        if args.max_pages and page >= (start_page + args.max_pages):
            print(f"\n✅ Reached maximum pages limit ({args.max_pages})")
            break
        
        # Skip pages that were already scraped
        if page in pages_scraped:
            print(f"\n{'='*60}")
            print(f"Skipping page {page} (already scraped)")
            page += 1
            continue
        
        # Build URL
        url = get_search_url(search_term, page)
        
        # Build headers with correct referer
        headers = get_base_headers()
        headers['referer'] = get_referer(search_term, page)
        
        print(f"\n{'='*60}")
        print(f"Fetching page {page}")
        print(f"URL: {url}")
        print(f"Referer: {headers['referer']}")
        
        request_failed = True
        stop_search = False  # end of results, redirect, or two empty pages
        for attempt in range(args.max_retries):
            try:
                if USE_HTTPCLOAK:
                    response = session.get(url, headers=headers, timeout=request_timeout)
                else:
                    response = session.get(url, headers=headers, timeout=request_timeout, allow_redirects=False)

                # Check for redirects (indicates cookies expired)
                if response.status_code in [301, 302, 303, 307, 308]:
                    loc = response.headers.get('Location') or response.headers.get('location') or 'N/A'
                    print(f"⚠️  Got redirect (status {response.status_code})")
                    print(f"Location: {loc}")
                    print(f"This usually means cookies have expired.")
                    print(f"Please update cookies in .env file!")
                    request_failed = False
                    stop_search = True
                    break

                # Detect rate-limit / unavailable (false-flag or 503) — wait and retry same page
                if response.status_code == 503 or (response.text and 'unavailable' in response.text.lower()):
                    print(f"⚠️  Rate limited or unavailable (status {response.status_code}), waiting {args.unavailable_wait:.0f}s then retrying (attempt {attempt + 1}/{args.max_retries})...")
                    time.sleep(args.unavailable_wait)
                    continue
                
                response.raise_for_status()
                
                print(f"Status: {response.status_code}")
                print(f"Content length: {len(response.text):,} bytes")
                # Log what we got back (headers + body preview)
                ct = response.headers.get('Content-Type', '')
                cl = response.headers.get('Content-Length', '')
                print(f"   Content-Type: {ct}")
                if cl:
                    print(f"   Content-Length (header): {cl}")
                preview = (response.text or '')[:500].replace('\r', '')
                preview = preview.replace('\n', '\n      ')  # indent continuation lines
                print(f"   Body preview:\n      {preview}")
                if len(response.text) > 500:
                    print(f"      ... ({len(response.text) - 500} more bytes)")

                response_text = response.text
                # Fallback: if we still got the challenge (e.g. stale cookies), solve it and retry
                if len(response_text) < 10000 and parse_akamai_interstitial(response_text)[0]:
                    print(f"   Solving Akamai interstitial challenge...")
                    response_text, solved = solve_akamai_interstitial(
                        session, url, headers, response_text, USE_HTTPCLOAK
                    )
                    if solved:
                        print(f"   Challenge solved, content length: {len(response_text):,} bytes")
                    else:
                        print(f"⚠️  Response still small after challenge solve")
                        debug_file = os.path.join(args.output_dir, f'debug_page_{page}_{output_filename}.html')
                        with open(debug_file, 'w') as f:
                            f.write(response_text)
                        print(f"Saved response to: {debug_file}")
                        request_failed = False
                        stop_search = True
                        break

                if len(response_text) < 10000:
                    print(f"⚠️  Response is suspiciously small ({len(response_text)} bytes)")
                    print(f"This might be bot protection. Please update cookies!")
                    debug_file = os.path.join(args.output_dir, f'debug_page_{page}_{output_filename}.html')
                    with open(debug_file, 'w') as f:
                        f.write(response_text)
                    print(f"Saved response to: {debug_file}")
                    request_failed = False
                    stop_search = True
                    break

                # Extract document URLs
                page_urls = extract_document_urls(response_text, url)
                print(f"Found: {len(page_urls)} documents")
                
                if len(page_urls) == 0:
                    consecutive_empty += 1
                    print(f"⚠️  No documents found (empty count: {consecutive_empty})")
                    # Save HTML for debugging when parser finds nothing (e.g. different layout after challenge)
                    if len(response_text) > 50000:
                        debug_file = os.path.join(args.output_dir, f'debug_empty_page_{page}_{output_filename}.html')
                        with open(debug_file, 'w', encoding='utf-8') as f:
                            f.write(response_text)
                        print(f"   Saved {len(response_text):,} bytes to {debug_file} for inspection.")
                    if consecutive_empty >= 2:
                        print(f"Two consecutive empty pages, stopping.")
                        request_failed = False
                        stop_search = True
                        break
                else:
                    consecutive_empty = 0  # Reset counter
                    
                    # Show first few documents
                    for i, doc in enumerate(page_urls[:3], 1):
                        print(f"  {i}. {doc['title'][:60]}...")
                
                # Store results (check if page already exists to avoid duplicates)
                existing_page_numbers = {p.get('page_number') for p in all_results.get('pages', [])}
                if page not in existing_page_numbers:
                    page_data = {
                        'page_number': page,
                        'url': url,
                        'urls_found': len(page_urls),
                        'urls': page_urls
                    }
                    all_results['pages'].append(page_data)
                    pages_scraped.append(page)
                else:
                    print(f"   ⚠️  Page {page} already exists in output, skipping duplicate")
                    page += 1
                    request_failed = False
                    break
                
                # Add to all_urls (avoiding duplicates)
                new_urls_count = 0
                for url_item in page_urls:
                    if not any(u['url'] == url_item['url'] for u in all_results['all_urls']):
                        all_results['all_urls'].append(url_item)
                        new_urls_count += 1
                
                if new_urls_count < len(page_urls):
                    print(f"   ({new_urls_count} new, {len(page_urls) - new_urls_count} duplicate URLs skipped)")
                
                # Update progress
                all_results['progress'] = {
                    'last_page': page,
                    'pages_scraped': sorted(pages_scraped),
                    'last_updated': datetime.now().isoformat()
                }
                
                # Save only .jsonl + minimal .progress.json (no redundant .json)
                write_jsonl(all_results['all_urls'], jsonl_file)
                progress_data = {
                    'search_term': search_term,
                    'last_page': page,
                    'pages_scraped': sorted(pages_scraped),
                    'all_urls': all_results['all_urls'],
                    'last_updated': all_results['progress']['last_updated'],
                }
                with open(progress_file, 'w', encoding='utf-8') as f:
                    json.dump(progress_data, f, indent=2, ensure_ascii=False)
                print(f"\n💾 Saved {len(pages_scraped)} page(s), {len(all_results['all_urls'])} documents → {jsonl_file}")
                
                # Check if there's a next page (only trust when we got documents; 0 docs may mean wrong/different HTML)
                has_next = check_for_next_page(response_text)
                if not has_next:
                    if len(page_urls) == 0:
                        print(f"   No pager and 0 documents (parser may have failed or wrong page) - trying next page.")
                    else:
                        print(f"\n✅ No next page link found - reached end of results.")
                        request_failed = False
                        stop_search = True
                        break
                
                # Move to next page
                page += 1

                # Refresh session before each new page so every request uses a fresh connection.
                if USE_HTTPCLOAK:
                    try:
                        saved = session.get_cookies()
                        session.close()
                        session = HTTPCloakSession(preset="chrome-143", allow_redirects=False, timeout=request_timeout)
                        for name, value in saved.items():
                            session.set_cookie(name, value)
                    except Exception as e:
                        print(f"   Session refresh failed: {e}")
                
                # Delay before next request
                print(f"Waiting {args.delay} seconds...")
                time.sleep(args.delay)
                
                request_failed = False
                break

            except Exception as e:
                err_msg = str(e)
                if 'context deadline exceeded' in err_msg or 'timeout' in err_msg.lower():
                    print(f"⚠️  Timeout ({err_msg[:60]}...), waiting {args.unavailable_wait:.0f}s then retrying (attempt {attempt + 1}/{args.max_retries})...")
                    time.sleep(args.unavailable_wait)
                    if USE_HTTPCLOAK:
                        try:
                            saved = session.get_cookies()
                            session.close()
                            session = HTTPCloakSession(preset="chrome-143", allow_redirects=False, timeout=request_timeout)
                            for name, value in saved.items():
                                session.set_cookie(name, value)
                        except Exception as refresh_err:
                            print(f"   Session refresh failed: {refresh_err}")
                    continue
                print(f"❌ Error: {e}")
                break

        if request_failed or stop_search:
            break

    if USE_HTTPCLOAK:
        session.close()

    # Final save: .jsonl + .progress.json only
    last_page = max(pages_scraped) if pages_scraped else (page - 1)
    all_results['progress'] = {
        'last_page': last_page,
        'pages_scraped': sorted(pages_scraped),
        'last_updated': datetime.now().isoformat()
    }
    write_jsonl(all_results['all_urls'], jsonl_file)
    progress_data = {
        'search_term': search_term,
        'last_page': last_page,
        'pages_scraped': sorted(pages_scraped),
        'all_urls': all_results['all_urls'],
        'last_updated': all_results['progress']['last_updated'],
    }
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress_data, f, indent=2, ensure_ascii=False)
    print(f"\n{'='*60}")
    print(f"Final save: {jsonl_file}")
    print(f"   Progress: {progress_file}")

    print(f"\n✅ Completed!")
    print(f"  Total pages processed: {len(pages_scraped)}")
    print(f"  Total unique documents: {len(all_results['all_urls'])}")
    print(f"  Output: {jsonl_file}")


if __name__ == '__main__':
    main()