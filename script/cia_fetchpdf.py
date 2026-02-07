#!/usr/bin/env python3
"""
CIA Reading Room: fetch document pages, find PDF links, download PDFs, and OCR to text.
Reads URLs from a JSONL file (e.g. output from cia_fetchmetadata.py: one JSON object per line with "url" and "title").
For each URL we fetch the document page, dig through the HTML for the PDF link, download the PDF, then run Tesseract OCR.
"""

import argparse
import os
import re
import time
from urllib.parse import urljoin, urlparse

try:
    from httpcloak import Session as HTTPCloakSession
    USE_HTTPCLOAK = True
except ImportError:
    USE_HTTPCLOAK = False
    import requests

from bs4 import BeautifulSoup
from dotenv import load_dotenv

# PDF → text with Tesseract OCR
try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None
try:
    import pytesseract
    from PIL import Image
    HAS_OCR = True
except ImportError:
    HAS_OCR = False

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
OCR_DPI = 300


def get_base_headers():
    """Match browser headers to avoid bot challenge."""
    return {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9',
        'cache-control': 'max-age=0',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
        'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Linux"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'sec-fetch-user': '?1',
        'upgrade-insecure-requests': '1',
    }


def get_cookies_from_env():
    """Load cookies from .env (COOKIE_SESSION, COOKIE_AK_BMSC). Same as cia_fetchmetadata."""
    load_dotenv()
    cookies = {}
    if os.getenv('COOKIE_SESSION'):
        cookies['_session_'] = os.getenv('COOKIE_SESSION')
    if os.getenv('COOKIE_AK_BMSC'):
        cookies['ak_bmsc'] = os.getenv('COOKIE_AK_BMSC')
    return cookies


def extract_pdf_url(html_content: str, page_url: str) -> str | None:
    """
    Dig through document page HTML for the PDF download link.
    CIA uses links like /readingroom/docs/... or /sites/default/files/... or href ending in .pdf.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    base = page_url.rsplit('/', 1)[0]
    if not page_url.startswith('http'):
        base = 'https://www.cia.gov' + base

    # Collect all links that point to a PDF
    candidates = []
    for a in soup.find_all('a', href=True):
        href = (a.get('href') or '').strip()
        if not href or '.pdf' not in href.lower():
            continue
        full = urljoin(page_url, href)
        if full.lower().endswith('.pdf'):
            candidates.append(full)

    # Prefer readingroom/docs/ or document-related paths; otherwise first .pdf link
    for c in candidates:
        if '/readingroom/docs/' in c or '/readingroom/document/' in c or '/sites/default/files/' in c:
            return c
    return candidates[0] if candidates else None


def slug_from_url(url: str) -> str:
    """Safe filename base from document URL (e.g. cia-rdp80t00246a029500340001-7)."""
    path = urlparse(url).path.strip('/')
    name = path.split('/')[-1] or 'document'
    # sanitize
    name = re.sub(r'[^\w\-.]', '_', name)[:120]
    return name or 'document'


def load_urls_from_jsonl(path: str) -> list[dict]:
    """Load list of {url, title} from JSONL file."""
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


def ocr_pdf_to_text(pdf_path: str, out_dir: str) -> str | None:
    """Extract text from PDF using Tesseract OCR (force OCR on every page). Writes to out_dir/<basename>.txt."""
    if not fitz:
        print("  Missing pymupdf: pip install pymupdf")
        return None
    if not HAS_OCR:
        print("  Missing OCR deps: pip install pytesseract Pillow; install tesseract-ocr")
        return None
    if not os.path.isfile(pdf_path) or not pdf_path.lower().endswith('.pdf'):
        return None
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    out_path = os.path.join(out_dir, f"{base}.txt")
    try:
        doc = fitz.open(pdf_path)
        chunks = []
        for i, page in enumerate(doc):
            # Always run Tesseract on each page (CIA docs are often scans)
            mat = fitz.Matrix(OCR_DPI / 72, OCR_DPI / 72)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            raw = pytesseract.image_to_string(img) or ""
            chunks.append(raw)
        doc.close()
        text = "\n".join(chunks).strip()
        os.makedirs(out_dir, exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(text)
        return out_path
    except Exception as e:
        print(f"  OCR error: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(
        description='Fetch CIA document pages, download PDFs from HTML links, and OCR to text.'
    )
    parser.add_argument(
        'input',
        nargs='?',
        default=os.path.join(PROJECT_ROOT, 'output', 'SIMULATION.jsonl'),
        help='Input JSONL file with "url" and "title" per line (default: output/SIMULATION.jsonl)',
    )
    parser.add_argument('--output-dir', default=os.path.join(PROJECT_ROOT, 'output'), help='Base output directory')
    parser.add_argument('--pdf-dir', default=None, help='Directory for downloaded PDFs (default: <output-dir>/pdfs)')
    parser.add_argument('--txt-dir', default=None, help='Directory for OCR text (default: <output-dir>/pdf-txt)')
    parser.add_argument('--delay', type=float, default=90.0, help='Seconds between requests (default: 90)')
    parser.add_argument('--overwrite', action='store_true', help='Re-download and re-OCR even if .txt exists')
    parser.add_argument('--timeout', type=int, default=60, help='Request timeout in seconds (default: 60)')
    args = parser.parse_args()

    pdf_dir = args.pdf_dir or os.path.join(args.output_dir, 'pdfs')
    txt_dir = args.txt_dir or os.path.join(args.output_dir, 'pdf-txt')
    os.makedirs(pdf_dir, exist_ok=True)
    os.makedirs(txt_dir, exist_ok=True)

    if not os.path.isfile(args.input):
        print(f"Input file not found: {args.input}")
        return 1

    entries = load_urls_from_jsonl(args.input)
    if not entries:
        print("No URLs found in JSONL.")
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
        title = (entry.get('title') or '')[:60]
        if not url:
            continue
        slug = slug_from_url(url)
        txt_path = os.path.join(txt_dir, f"{slug}.txt")
        if not args.overwrite and os.path.isfile(txt_path):
            print(f"[{i+1}/{len(entries)}] Skip (exists): {slug}")
            done += 1
            continue

        print(f"[{i+1}/{len(entries)}] {url}")
        try:
            r = session.get(url, headers={**headers, 'referer': 'https://www.cia.gov/readingroom/'}, timeout=args.timeout)
            r.raise_for_status()
        except Exception as e:
            print(f"  Failed to fetch page: {e}")
            time.sleep(args.delay)
            continue

        pdf_url = extract_pdf_url(r.text, url)
        if not pdf_url:
            print(f"  No PDF link found in HTML")
            time.sleep(args.delay)
            continue

        pdf_path = os.path.join(pdf_dir, f"{slug}.pdf")
        try:
            r2 = session.get(pdf_url, headers={**headers, 'referer': url}, timeout=args.timeout)
            r2.raise_for_status()
            with open(pdf_path, 'wb') as f:
                f.write(r2.content)
        except Exception as e:
            print(f"  Failed to download PDF: {e}")
            time.sleep(args.delay)
            continue

        out_txt = ocr_pdf_to_text(pdf_path, txt_dir)
        if out_txt:
            print(f"  → {out_txt}")
            done += 1
        else:
            print(f"  OCR failed for {pdf_path}")

        if i < len(entries) - 1:
            print(f"  Waiting {args.delay}s...")
            time.sleep(args.delay)

    if USE_HTTPCLOAK and hasattr(session, 'close'):
        session.close()
    print(f"\nDone. {done}/{len(entries)} documents written to {txt_dir}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
