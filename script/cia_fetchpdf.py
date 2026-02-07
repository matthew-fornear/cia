#!/usr/bin/env python3
"""
CIA Reading Room: fetch document pages and download PDFs only.
Reads URLs from JSONL (from cia_fetchmetadata). For each URL: fetch HTML, get PDF URL (regex or fallback), download PDF to output/pdfs/.
OCR is done separately (e.g. local_pdftotxt or another script).
"""

import argparse
import os
import re
import subprocess
import time
from urllib.parse import urljoin, urlparse

try:
    from httpcloak import Session as HTTPCloakSession
    USE_HTTPCLOAK = True
except ImportError:
    USE_HTTPCLOAK = False
    import requests

from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)


def get_base_headers():
    """Match browser curl that works for PDF: accept, priority, sec-gpc, etc."""
    return {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9',
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
    load_dotenv()
    cookies = {}
    if os.getenv('COOKIE_SESSION'):
        cookies['_session_'] = os.getenv('COOKIE_SESSION')
    if os.getenv('COOKIE_AK_BMSC'):
        cookies['ak_bmsc'] = os.getenv('COOKIE_AK_BMSC')
    return cookies


def extract_pdf_url(html_content: str, page_url: str) -> str | None:
    """CIA: PDF is always .../readingroom/docs/{DOCID}.pdf with DOCID from document URL (uppercased)."""
    path = urlparse(page_url).path.strip('/')
    if 'readingroom/document/' in path or '/readingroom/document/' in page_url:
        doc_id = path.split('/')[-1].split('?')[0]
        if doc_id:
            return urljoin(page_url, f"/readingroom/docs/{doc_id.upper()}.pdf")
    # Non-CIA or odd URL: try regex in HTML
    m = re.search(r'href\s*=\s*["\']([^"\']+\.pdf[^"\']*)["\']', html_content, re.I)
    if m:
        return urljoin(page_url, m.group(1).strip())
    return None


def slug_from_url(url: str) -> str:
    path = urlparse(url).path.strip('/')
    name = path.split('/')[-1] or 'document'
    return re.sub(r'[^\w\-.]', '_', name)[:120] or 'document'


def download_pdf_curl(pdf_url: str, referer: str, cookies: dict, output_path: str, timeout: int = 60) -> bool:
    """Download PDF using curl (same as your working terminal command). Returns True if saved a valid PDF."""
    if not cookies:
        return False
    cookie_str = '; '.join(f'{k}={v}' for k, v in cookies.items())
    args = [
        'curl', '-s', '-S', '-o', output_path,
        '--max-time', str(timeout),
        '-b', cookie_str,
        '-H', 'accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        '-H', 'accept-language: en-US,en;q=0.9',
        '-H', 'priority: u=0, i',
        '-H', f'referer: {referer}',
        '-H', 'sec-ch-ua: "Not(A:Brand";v="8", "Chromium";v="144", "Brave";v="144"',
        '-H', 'sec-ch-ua-mobile: ?0',
        '-H', 'sec-ch-ua-platform: "Linux"',
        '-H', 'sec-fetch-dest: document',
        '-H', 'sec-fetch-mode: navigate',
        '-H', 'sec-fetch-site: same-origin',
        '-H', 'sec-fetch-user: ?1',
        '-H', 'sec-gpc: 1',
        '-H', 'user-agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
        pdf_url,
    ]
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout + 5)
        if r.returncode != 0:
            return False
        if not os.path.isfile(output_path):
            return False
        with open(output_path, 'rb') as f:
            magic = f.read(8)
        return magic.startswith(b'%PDF') and os.path.getsize(output_path) > 500
    except Exception:
        return False


def load_urls_from_jsonl(path: str) -> list[dict]:
    import json
    out = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def main():
    parser = argparse.ArgumentParser(description='Fetch CIA document pages and download PDFs only (no OCR).')
    parser.add_argument(
        'input',
        nargs='?',
        default=os.path.join(PROJECT_ROOT, 'output', 'SIMULATION.jsonl'),
        help='JSONL with "url" (and "title") per line',
    )
    parser.add_argument('--output-dir', default=os.path.join(PROJECT_ROOT, 'output'))
    parser.add_argument('--pdf-dir', default=None, help='Where to save PDFs (default: <output-dir>/pdfs)')
    parser.add_argument('--delay', type=float, default=90.0)
    parser.add_argument('--overwrite', action='store_true', help='Re-download even if PDF exists')
    parser.add_argument('--timeout', type=int, default=60)
    args = parser.parse_args()

    pdf_dir = args.pdf_dir or os.path.join(args.output_dir, 'pdfs')
    os.makedirs(pdf_dir, exist_ok=True)

    if not os.path.isfile(args.input):
        print(f"Input file not found: {args.input}")
        return 1

    entries = load_urls_from_jsonl(args.input)
    if not entries:
        print("No URLs in JSONL.")
        return 1

    load_dotenv()
    cookies = get_cookies_from_env()
    if USE_HTTPCLOAK:
        session = HTTPCloakSession(preset="chrome-143", allow_redirects=False, timeout=args.timeout)
    else:
        session = requests.Session()
    for name, value in (cookies or {}).items():
        if USE_HTTPCLOAK:
            session.set_cookie(name, value)
        else:
            session.cookies.set(name, value, domain='.cia.gov', path='/')

    headers = get_base_headers()
    done = 0
    for i, entry in enumerate(entries):
        url = entry.get('url') or entry.get('link')
        if not url:
            continue
        slug = slug_from_url(url)
        pdf_path = os.path.join(pdf_dir, f"{slug}.pdf")
        if not args.overwrite and os.path.isfile(pdf_path):
            print(f"[{i+1}/{len(entries)}] Skip (exists): {slug}")
            done += 1
            continue

        print(f"[{i+1}/{len(entries)}] {url}")
        try:
            r = session.get(url, headers={**headers, 'referer': 'https://www.cia.gov/readingroom/'}, timeout=args.timeout)
            r.raise_for_status()
        except Exception as e:
            print(f"  Failed: {e}")
            time.sleep(args.delay)
            continue

        pdf_url = extract_pdf_url(r.text, url)
        if not pdf_url:
            print("  No PDF URL")
            time.sleep(args.delay)
            continue

        # Use curl for PDF (same as your working terminal); Python session often gets 302/blank.
        if not download_pdf_curl(pdf_url, url, cookies, pdf_path, args.timeout):
            if os.path.isfile(pdf_path):
                try:
                    os.remove(pdf_path)
                except OSError:
                    pass
            print("  PDF download failed (curl)")
            time.sleep(args.delay)
            continue
        size = os.path.getsize(pdf_path)
        print(f"  → {pdf_path} ({size:,} bytes)")
        done += 1

        if i < len(entries) - 1:
            print(f"  Waiting {args.delay:.0f}s...")
            time.sleep(args.delay)

    if USE_HTTPCLOAK and hasattr(session, 'close'):
        session.close()
    print(f"\nDone. {done}/{len(entries)} PDFs in {pdf_dir}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
