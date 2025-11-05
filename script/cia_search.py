#!/usr/bin/env python3
"""
CIA Reading Room Search Script - Working Version
Uses cookies from a successful browser session to avoid bot protection.
"""

import argparse
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime
import os
import time
from urllib.parse import urljoin, quote_plus
from dotenv import load_dotenv


def get_base_headers():
    """Return the base headers that don't change."""
    return {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.6',
        'priority': 'u=0, i',
        'sec-ch-ua': '"Chromium";v="142", "Brave";v="142", "Not_A Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Linux"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'sec-gpc': '1',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36'
    }


def get_cookies_from_env():
    """
    Load cookies from .env file.
    UPDATE THESE in .env with fresh cookies from DevTools when they expire!
    
    To get fresh cookies:
    1. Open DevTools (F12) in your browser
    2. Go to a working search page: https://www.cia.gov/readingroom/search/site/GATE
    3. Find a successful request in Network tab
    4. Right-click → Copy → Copy as cURL
    5. Extract the cookie values from -b flag
    6. Update COOKIE_SESSION and COOKIE_AK_BMSC in .env
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


def load_existing_output(output_file):
    """Load existing output file if it exists and extract progress information."""
    if os.path.exists(output_file):
        try:
            with open(output_file, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
            print(f"📂 Found existing output file: {output_file}")
            print(f"   Existing pages: {len(existing_data.get('pages', []))}")
            print(f"   Existing URLs: {len(existing_data.get('all_urls', []))}")
            
            # Extract progress information from the output file
            progress = existing_data.get('progress', {})
            if progress:
                print(f"   Last page: {progress.get('last_page', 0)}")
                print(f"   Pages scraped: {len(progress.get('pages_scraped', []))}")
            
            return existing_data
        except Exception as e:
            print(f"⚠️  Error loading existing output file: {e}")
            return None
    return None


def main():
    parser = argparse.ArgumentParser(description='Search CIA Reading Room')
    parser.add_argument('searchterm', nargs='+', help='Search term to query (can be multiple words)')
    parser.add_argument('--output-dir', default='output', help='Output directory')
    parser.add_argument('--delay', type=float, default=2.0, help='Delay between requests (seconds)')
    parser.add_argument('--max-pages', type=int, default=None, help='Maximum pages to fetch (default: unlimited)')
    parser.add_argument('--start-page', type=int, default=None, help='Starting page number (default: auto-resume from progress)')
    parser.add_argument('--reset', action='store_true', help='Reset progress and start from page 0')
    
    args = parser.parse_args()
    
    # Join search term if it's multiple words
    search_term = ' '.join(args.searchterm) if isinstance(args.searchterm, list) else args.searchterm
    
    # Load environment variables from .env file
    load_dotenv()
    
    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)
    
    # Generate output filename (simple name without timestamp)
    # Use search term for filename (replace spaces with underscores)
    output_filename = search_term.upper().replace(' ', '_')
    output_file = os.path.join(args.output_dir, f'{output_filename}.json')
    
    # Load or initialize progress from output file
    if args.reset:
        print("🔄 Reset flag set - starting fresh")
        existing_output = None
        start_page = args.start_page if args.start_page is not None else 0
        pages_scraped = []
    else:
        # Try to load existing output file
        existing_output = load_existing_output(output_file)
        
        if existing_output:
            # Extract progress from output file
            progress = existing_output.get('progress', {})
            if progress:
                # Resume from progress
                start_page = args.start_page if args.start_page is not None else progress.get('last_page', 0) + 1
                pages_scraped = progress.get('pages_scraped', [])
                print(f"📂 Resuming from page {start_page} (from progress in output file)")
            else:
                # No progress field, but we have pages - extract from pages
                existing_pages = existing_output.get('pages', [])
                existing_page_numbers = [p.get('page_number') for p in existing_pages]
                pages_scraped = sorted(existing_page_numbers)
                start_page = args.start_page if args.start_page is not None else (max(pages_scraped) + 1 if pages_scraped else 0)
                print(f"📂 Found existing output - will resume from page {start_page}")
        else:
            # Start fresh
            start_page = args.start_page if args.start_page is not None else 0
            pages_scraped = []
            print(f"🆕 Starting fresh from page {start_page}")
    
    # Create session
    session = requests.Session()
    
    # Set cookies from .env file
    cookies = get_cookies_from_env()
    if not cookies:
        print("❌ Error: No cookies found in .env file!")
        print("Please set COOKIE_SESSION and COOKIE_AK_BMSC in .env file.")
        print("See .env.example for template.")
        return
    
    for name, value in cookies.items():
        session.cookies.set(name, value, domain='.cia.gov', path='/')
    
    print(f"\n{'='*60}")
    print(f"Starting search for: {search_term}")
    print(f"Cookies set: {len(session.cookies)}")
    if args.max_pages:
        print(f"Maximum pages: {args.max_pages}")
    else:
        print(f"Maximum pages: unlimited")
    print(f"Starting from page: {start_page}")
    print(f"{'='*60}")
    
    # Initialize or load existing results
    if existing_output and not args.reset:
        # Load existing output and continue from there
        all_results = existing_output
        # Update start_time if it doesn't exist
        if 'start_time' not in all_results:
            all_results['start_time'] = datetime.now().isoformat()
        # Add a new session start time
        if 'sessions' not in all_results:
            all_results['sessions'] = []
        all_results['sessions'].append({
            'start_time': datetime.now().isoformat(),
            'start_page': start_page
        })
        
        # Ensure all_urls exists
        if 'all_urls' not in all_results:
            all_results['all_urls'] = []
        
        # Ensure progress field exists
        if 'progress' not in all_results:
            all_results['progress'] = {
                'last_page': -1,
                'pages_scraped': [],
                'last_updated': None
            }
        
        print(f"📂 Loaded existing output with {len(all_results.get('pages', []))} pages and {len(all_results.get('all_urls', []))} URLs")
    else:
        # Start fresh
        all_results = {
            'search_term': search_term,
            'start_time': datetime.now().isoformat(),
            'pages': [],
            'all_urls': [],
            'sessions': [{
                'start_time': datetime.now().isoformat(),
                'start_page': start_page
            }],
            'progress': {
                'last_page': -1,
                'pages_scraped': [],
                'last_updated': None
            }
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
        
        try:
            # Make request - don't follow redirects automatically
            response = session.get(url, headers=headers, timeout=30, allow_redirects=False)
            
            # Check for redirects (indicates cookies expired)
            if response.status_code in [301, 302, 303, 307, 308]:
                print(f"⚠️  Got redirect (status {response.status_code})")
                print(f"Location: {response.headers.get('Location', 'N/A')}")
                print(f"This usually means cookies have expired.")
                print(f"Please update cookies in .env file!")
                break
            
            response.raise_for_status()
            
            print(f"Status: {response.status_code}")
            print(f"Content length: {len(response.text):,} bytes")
            
            # Check for bot protection
            if len(response.text) < 10000:
                print(f"⚠️  Response is suspiciously small ({len(response.text)} bytes)")
                print(f"This might be bot protection. Please update cookies!")
                # Save the small response for debugging
                debug_file = os.path.join(args.output_dir, f'debug_page_{page}_{output_filename}.html')
                with open(debug_file, 'w') as f:
                    f.write(response.text)
                print(f"Saved response to: {debug_file}")
                break
            
            # Extract document URLs
            page_urls = extract_document_urls(response.text, url)
            print(f"Found: {len(page_urls)} documents")
            
            if len(page_urls) == 0:
                consecutive_empty += 1
                print(f"⚠️  No documents found (empty count: {consecutive_empty})")
                
                if consecutive_empty >= 2:
                    print(f"Two consecutive empty pages, stopping.")
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
                continue
            
            # Add to all_urls (avoiding duplicates)
            new_urls_count = 0
            for url_item in page_urls:
                if not any(u['url'] == url_item['url'] for u in all_results['all_urls']):
                    all_results['all_urls'].append(url_item)
                    new_urls_count += 1
            
            if new_urls_count < len(page_urls):
                print(f"   ({new_urls_count} new, {len(page_urls) - new_urls_count} duplicate URLs skipped)")
            
            # Update statistics
            all_results['total_pages'] = len(all_results['pages'])
            all_results['total_urls'] = len(all_results['all_urls'])
            
            # Update progress field in output file
            all_results['progress'] = {
                'last_page': page,
                'pages_scraped': sorted(pages_scraped),
                'last_updated': datetime.now().isoformat()
            }
            
            # Save to JSON after each page (incremental save)
            print(f"\n💾 Saving progress to: {output_file}")
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(all_results, f, indent=2, ensure_ascii=False)
            print(f"   ✓ Saved {all_results['total_pages']} page(s), {all_results['total_urls']} total documents")
            
            # Check if there's a next page
            has_next = check_for_next_page(response.text)
            if not has_next:
                print(f"\n✅ No next page link found - reached end of results.")
                break
            
            # Move to next page
            page += 1
            
            # Delay before next request
            print(f"Waiting {args.delay} seconds...")
            time.sleep(args.delay)
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Error: {e}")
            break
    
    # Finalize results
    all_results['end_time'] = datetime.now().isoformat()
    all_results['total_pages'] = len(all_results['pages'])
    all_results['total_urls'] = len(all_results['all_urls'])
    
    # Update progress field in output file
    all_results['progress'] = {
        'last_page': page - 1,
        'pages_scraped': sorted(pages_scraped),
        'last_updated': datetime.now().isoformat()
    }
    
    # Final save to JSON (in case we didn't save in the loop)
    print(f"\n{'='*60}")
    print(f"Final save to: {output_file}")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ Completed!")
    print(f"  Total pages processed: {all_results['total_pages']}")
    print(f"  Total unique documents: {all_results['total_urls']}")
    print(f"  Output file: {output_file}")


if __name__ == '__main__':
    main()