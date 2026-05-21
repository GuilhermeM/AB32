#!/usr/bin/env python3
"""
Extract product descriptions from gearelevation.com (Shopify) product pages.

Usage:
    python3 extract.py <url>                    # one URL
    python3 extract.py -f urls.txt              # file with one URL per line
    python3 extract.py <url1> <url2> ...        # multiple URLs

Writes one .md file per product into ./ (the current directory),
named after the product handle.
"""

import argparse
import html
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from urllib.parse import urlparse

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}

# Polite throttling per-request
MIN_DELAY = 0.3
MAX_DELAY = 0.8
MAX_RETRIES = 4


def fetch(url: str) -> str:
    """Fetch with retry on 429/503/403/timeouts, exponential backoff + jitter."""
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            # Small jitter before each request (spreads parallel workers)
            time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 503, 502, 504, 403):
                # 403 often means temporary anti-bot block: long sleep
                if e.code == 403:
                    wait = 30 + (2 ** attempt) * 10 + random.uniform(0, 5)
                else:
                    ra = e.headers.get("Retry-After") if e.headers else None
                    try:
                        wait = float(ra) if ra else (2 ** attempt) * 2
                    except ValueError:
                        wait = (2 ** attempt) * 2
                    wait += random.uniform(0, 1)
                time.sleep(min(wait, 120))
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            time.sleep((2 ** attempt) + random.uniform(0, 1))
            continue
    raise last_err if last_err else RuntimeError("fetch failed")


def parse_json_string(src: str, start: int) -> str:
    """Parse a JSON-encoded string body starting at `start` (just after the opening quote)."""
    out = []
    i = start
    n = len(src)
    while i < n:
        c = src[i]
        if c == "\\":
            nxt = src[i + 1]
            esc = {"n": "\n", "t": "\t", "r": "\r",
                   '"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f"}
            if nxt in esc:
                out.append(esc[nxt]); i += 2
            elif nxt == "u":
                out.append(chr(int(src[i + 2:i + 6], 16))); i += 6
            else:
                out.append(nxt); i += 2
        elif c == '"':
            break
        else:
            out.append(c); i += 1
    return "".join(out)


def extract_product(src: str):
    """Return (title, handle, description_html) from a Shopify product page."""
    title = handle = desc = None

    m = re.search(r'"product"\s*:\s*\{[^{]*?"title"\s*:\s*"', src)
    if m: title = parse_json_string(src, m.end())

    m = re.search(r'"product"\s*:\s*\{[^{]*?"handle"\s*:\s*"', src)
    if m: handle = parse_json_string(src, m.end())

    m = re.search(r'"product"\s*:\s*\{[^{]*?"description"\s*:\s*"', src)
    if m: desc = parse_json_string(src, m.end())

    return title, handle, desc


class HtmlToMarkdown(HTMLParser):
    """Minimal HTML->Markdown for Shopify description blocks."""
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0
        self.in_strong = 0
        self.in_em = 0
        self.list_stack = []
        self.li_counter = []

    def _emit(self, s): self.parts.append(s)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1; return
        a = dict(attrs)
        if tag in ("h1","h2","h3","h4","h5","h6"):
            level = int(tag[1])
            self._emit("\n\n" + "#" * max(2, level) + " ")
        elif tag == "p":
            self._emit("\n\n" if not self.list_stack else " ")
        elif tag == "br":
            self._emit("\n" if not self.list_stack else " ")
        elif tag == "div":
            if not self.list_stack:
                self._emit("\n")
        elif tag in ("strong","b"): self.in_strong += 1; self._emit("**")
        elif tag in ("em","i"):     self.in_em += 1; self._emit("*")
        elif tag == "ul": self.list_stack.append("ul"); self.li_counter.append(0); self._emit("\n")
        elif tag == "ol": self.list_stack.append("ol"); self.li_counter.append(0); self._emit("\n")
        elif tag == "li":
            depth = max(0, len(self.list_stack) - 1)
            indent = "  " * depth
            if self.list_stack and self.list_stack[-1] == "ol":
                self.li_counter[-1] += 1
                self._emit(f"\n{indent}{self.li_counter[-1]}. ")
            else:
                self._emit(f"\n{indent}- ")
        elif tag == "img":
            alt = a.get("alt") or ""
            src = a.get("src") or ""
            self._emit(f"\n\n*[Image: {alt or src.split('/')[-1].split('?')[0]}]*\n")
        elif tag == "a":
            href = a.get("href", "")
            self._emit("[")
            self._href = href
        elif tag == "tr": self._emit("\n")
        elif tag in ("td","th"): self._emit(" | ")

    def handle_endtag(self, tag):
        if tag in ("script","style") and self.skip:
            self.skip -= 1; return
        if tag in ("strong","b") and self.in_strong:
            self.in_strong -= 1; self._emit("**")
        elif tag in ("em","i") and self.in_em:
            self.in_em -= 1; self._emit("*")
        elif tag in ("ul","ol") and self.list_stack:
            self.list_stack.pop(); self.li_counter.pop(); self._emit("\n")
        elif tag == "a":
            self._emit(f"]({getattr(self, '_href', '')})")

    def handle_data(self, data):
        if not self.skip:
            self._emit(data)

    def result(self) -> str:
        text = "".join(self.parts)
        text = html.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        # Collapse line breaks immediately after a bullet/number marker
        text = re.sub(r"(^|\n)(\s*(?:-|\d+\.))\s*\n+\s*", r"\1\2 ", text)
        # Drop blank lines between consecutive list items (compact lists)
        text = re.sub(r"(\n- [^\n]+)\n\n(?=- )", r"\1\n", text)
        text = re.sub(r"(\n\d+\. [^\n]+)\n\n(?=\d+\. )", r"\1\n", text)
        # Remove lines that are only whitespace
        text = re.sub(r"\n[ \t]*\n", "\n\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        # Strip stray trailing spaces on each line
        text = "\n".join(line.rstrip() for line in text.split("\n"))
        return text.strip()


def to_markdown(title: str, handle: str, url: str, desc_html: str) -> str:
    parser = HtmlToMarkdown()
    parser.feed(desc_html)
    body = parser.result()
    title = (title or "Untitled").replace("&", "&").replace("–", "–")
    return f"# {title}\n\n**Source:** {url}\n\n---\n\n{body}\n"


MAX_FILENAME_LEN = 200  # leave room for ".md" + filesystem overhead


def slug_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] or "product"


def safe_filename(slug: str) -> str:
    """Truncate overly long slugs while keeping them unique-ish."""
    if len(slug) <= MAX_FILENAME_LEN:
        return slug
    # Keep prefix + a short hash of the full slug for uniqueness
    import hashlib
    suffix = hashlib.md5(slug.encode()).hexdigest()[:8]
    return slug[: MAX_FILENAME_LEN - 9] + "-" + suffix


def process(url: str, out_dir: str) -> dict:
    t0 = time.time()
    try:
        src = fetch(url)
        title, handle, desc = extract_product(src)
        if not desc:
            return {"url": url, "ok": False, "error": "no description found", "elapsed": time.time()-t0}
        slug = handle or slug_from_url(url)
        md = to_markdown(title, slug, url, desc)
        path = os.path.join(out_dir, f"{safe_filename(slug)}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(md)
        return {"url": url, "ok": True, "path": path, "title": title, "elapsed": time.time()-t0}
    except Exception as e:
        return {"url": url, "ok": False, "error": str(e), "elapsed": time.time()-t0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="*", help="Product URLs")
    ap.add_argument("-f", "--file", help="File with one URL per line")
    ap.add_argument("-o", "--out", default="pdp content", help="Output directory (default: 'pdp content')")
    ap.add_argument("-j", "--jobs", type=int, default=4, help="Parallel fetches (default: 4)")
    args = ap.parse_args()

    urls = list(args.urls)
    if args.file:
        with open(args.file) as f:
            urls.extend(line.strip() for line in f if line.strip() and not line.strip().startswith("#"))

    if not urls:
        ap.print_help(); sys.exit(1)

    os.makedirs(args.out, exist_ok=True)
    print(f"Extracting {len(urls)} product(s) with {args.jobs} workers -> {args.out}/")
    t0 = time.time()
    ok = fail = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futures = {ex.submit(process, u, args.out): u for u in urls}
        for fut in as_completed(futures):
            r = fut.result()
            if r["ok"]:
                ok += 1
                print(f"  ✓ {r['elapsed']:.1f}s  {r['path']}")
            else:
                fail += 1
                print(f"  ✗ {r['elapsed']:.1f}s  {r['url']}  ({r['error']})")
    print(f"\nDone: {ok} ok, {fail} failed, {time.time()-t0:.1f}s total")


if __name__ == "__main__":
    main()
