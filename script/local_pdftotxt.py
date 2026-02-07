#!/usr/bin/env python3
"""
PDF to TXT script.
Extract text from PDF(s) and write to output/pdf-txt.
Use either CLI args (file or directory path) or interactive mode (no args).
Interactive mode: browse and select a file or folder with arrow keys (requires questionary).
For scanned/old documents use --ocr or --force-ocr (requires pytesseract, Pillow, and system Tesseract).
"""

import argparse
import os
import shutil
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    print("Missing dependency: pip install pymupdf", file=sys.stderr)
    print(f"Running with: {sys.executable}", file=sys.stderr)
    print("Run with the Python that has pymupdf installed (e.g. your conda base: use 'python', not /usr/bin/python3).", file=sys.stderr)
    sys.exit(1)

try:
    import questionary
    from questionary import Style
    HAS_QUESTIONARY = True
except ImportError:
    HAS_QUESTIONARY = False

try:
    import pytesseract
    from PIL import Image
    HAS_OCR = True
except ImportError:
    HAS_OCR = False

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "output", "pdf-txt")

OCR_TEXT_THRESHOLD = 50
OCR_DPI = 300


def ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return OUTPUT_DIR


def _ocr_page(page: "fitz.Page") -> str:
    """Render a single page to an image and run Tesseract OCR."""
    mat = fitz.Matrix(OCR_DPI / 72, OCR_DPI / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    try:
        return pytesseract.image_to_string(img) or ""
    except pytesseract.TesseractNotFoundError:
        print("Tesseract not found. Install it: apt install tesseract-ocr (or brew install tesseract)", file=sys.stderr)
        raise


def pdf_to_text(pdf_path: str, out_dir: str, use_ocr: bool = False, force_ocr: bool = False) -> str | None:
    """Extract text from a single PDF; write to out_dir. When use_ocr is True, pages with little or no text are OCR'd; force_ocr OCRs every page."""
    if not os.path.isfile(pdf_path):
        print(f"Not a file: {pdf_path}", file=sys.stderr)
        return None
    if not pdf_path.lower().endswith(".pdf"):
        print(f"Not a PDF: {pdf_path}", file=sys.stderr)
        return None
    if (use_ocr or force_ocr) and not HAS_OCR:
        print("OCR requested but missing dependencies: pip install pytesseract Pillow. Also install Tesseract: apt install tesseract-ocr", file=sys.stderr)
        return None
    base = os.path.splitext(os.path.basename(pdf_path))[0]
    out_path = os.path.join(out_dir, f"{base}.txt")
    try:
        doc = fitz.open(pdf_path)
        chunks = []
        for i, page in enumerate(doc):
            raw = page.get_text()
            if force_ocr or (use_ocr and len(raw.strip()) < OCR_TEXT_THRESHOLD):
                if HAS_OCR:
                    raw = _ocr_page(page)
                    if i == 0 and force_ocr:
                        print(f"  OCR: {os.path.basename(pdf_path)}", file=sys.stderr)
                elif force_ocr:
                    print(f"  Skipping OCR for page {i + 1} (no pytesseract)", file=sys.stderr)
            chunks.append(raw)
        doc.close()
        text = "\n".join(chunks).strip()
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        return out_path
    except Exception as e:
        print(f"Error processing {pdf_path}: {e}", file=sys.stderr)
        return None


def collect_pdfs(path: str) -> list[str]:
    """Return list of PDF file paths: single file or all PDFs under path (recursive)."""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(path):
        return []
    if os.path.isfile(path):
        return [path] if path.lower().endswith(".pdf") else []
    pdfs = []
    for root, _dirs, files in os.walk(path):
        for name in files:
            if name.lower().endswith(".pdf"):
                pdfs.append(os.path.join(root, name))
    return sorted(pdfs)


def _browse_for_path() -> list[str] | None:
    """Interactive file browser: navigate with arrow keys, select a PDF or folder. Bounded to project dir and below."""
    cwd = PROJECT_ROOT
    if not os.path.isdir(cwd):
        cwd = os.path.abspath(".")

    style = Style([
        ("selected", "fg:ansicyan bold"),
        ("pointer", "fg:ansiyellow bold"),
        ("qmark", "fg:ansiyellow bold"),
        ("highlighted", "fg:ansiyellow bold"),
        ("answer", "fg:ansigreen bold"),
    ])

    while True:
        try:
            entries = os.listdir(cwd)
        except OSError:
            entries = []
        dirs = sorted([x for x in entries if os.path.isdir(os.path.join(cwd, x)) and not x.startswith(".")])
        files = sorted([x for x in entries if os.path.isfile(os.path.join(cwd, x)) and x.lower().endswith(".pdf")])
        other = sorted([x for x in entries if os.path.isfile(os.path.join(cwd, x)) and x not in files])

        choices = []
        if cwd != PROJECT_ROOT:
            choices.append(questionary.Choice("  📁 .. (parent)", value=("up", None)))
        for d in dirs:
            full = os.path.join(cwd, d)
            choices.append(questionary.Choice(f"  📁 {d}/", value=("dir", full)))
        for f in files:
            full = os.path.join(cwd, f)
            choices.append(questionary.Choice(f"  📄 {f}", value=("file", full)))
        for f in other[:20]:
            choices.append(questionary.Choice(f"     {f}", value=("skip", None), disabled="(not a PDF)"))
        if len(other) > 20:
            choices.append(questionary.Choice(f"     ... and {len(other) - 20} more", value=("skip", None), disabled=""))

        choices.append(questionary.Separator())
        choices.append(questionary.Choice("  ▶ Use this folder (all PDFs here)", value=("folder", cwd)))
        choices.append(questionary.Choice("  ▶ Use this folder (all PDFs, including subfolders)", value=("folder_recursive", cwd)))
        choices.append(questionary.Separator())
        choices.append(questionary.Choice("  ✕ Cancel", value=("cancel", None)))

        title = f"  {cwd}\n  (↑/↓ move, Enter select)"
        sel = questionary.select(
            title,
            choices=choices,
            style=style,
            use_shortcuts=False,
            use_indicator=True,
        ).ask()
        if sel is None:
            return None
        action, path = sel
        if action == "cancel":
            return None
        if action == "up":
            cwd = os.path.dirname(cwd)
            if not (cwd == PROJECT_ROOT or cwd.startswith(PROJECT_ROOT + os.sep)):
                cwd = PROJECT_ROOT
            continue
        if action == "dir":
            cwd = path
            continue
        if action == "skip":
            continue
        if action == "file":
            return [path]
        if action == "folder":
            return collect_pdfs(path)
        if action == "folder_recursive":
            return collect_pdfs(path)
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Extract text from PDF(s) and write to output/pdf-txt."
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Path to a PDF file or directory containing PDFs",
    )
    parser.add_argument(
        "--ocr",
        action="store_true",
        help="Use OCR for pages with little or no text (scanned/old documents). Requires: pip install pytesseract Pillow, and system Tesseract (apt install tesseract-ocr).",
    )
    parser.add_argument(
        "--force-ocr",
        action="store_true",
        help="Run OCR on every page (ignore embedded text). Use for scanned PDFs.",
    )
    args = parser.parse_args()

    if args.ocr or args.force_ocr:
        if not HAS_OCR:
            print("OCR requested but Python deps missing. Run: pip install pytesseract Pillow", file=sys.stderr)
            sys.exit(1)
        if not shutil.which("tesseract"):
            print("Tesseract not found. Install the system package, then retry:", file=sys.stderr)
            print("  Ubuntu/Debian: sudo apt install tesseract-ocr", file=sys.stderr)
            print("  macOS: brew install tesseract", file=sys.stderr)
            sys.exit(1)

    out_dir = ensure_output_dir()

    if args.path is not None:
        path = os.path.abspath(os.path.expanduser(args.path))
        if not os.path.exists(path):
            print(f"Path does not exist: {path}", file=sys.stderr)
            sys.exit(1)
        pdfs = collect_pdfs(path)
    else:
        if HAS_QUESTIONARY:
            pdfs = _browse_for_path()
            if pdfs is None:
                print("Cancelled.", file=sys.stderr)
                sys.exit(0)
            if not pdfs:
                print("No PDF files in selected folder.", file=sys.stderr)
                sys.exit(1)
        else:
            raw = input("Enter path to a PDF file or directory (pip install questionary for browse UI): ").strip()
            if not raw:
                print("No path entered.", file=sys.stderr)
                sys.exit(1)
            path = os.path.abspath(os.path.expanduser(raw))
            if not os.path.exists(path):
                print(f"Path does not exist: {path}", file=sys.stderr)
                sys.exit(1)
            pdfs = collect_pdfs(path)
            if not pdfs:
                print("No PDF files found at that path.", file=sys.stderr)
                sys.exit(1)

    if not pdfs:
        print("No PDF files found.", file=sys.stderr)
        sys.exit(1)

    use_ocr = args.ocr or args.force_ocr
    ok = 0
    for pdf in pdfs:
        result = pdf_to_text(pdf, out_dir, use_ocr=use_ocr, force_ocr=args.force_ocr)
        if result:
            print(result)
            ok += 1

    print(f"\nWrote {ok}/{len(pdfs)} file(s) to {out_dir}", file=sys.stderr)
    sys.exit(0 if ok == len(pdfs) else 1)


if __name__ == "__main__":
    main()
