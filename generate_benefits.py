#!/usr/bin/env python3
"""
Generate 4 concise benefit bullets per product from extracted PDP markdown files.

Reads:  pdp content/*.md
Writes: benefits.json  (one consolidated file, products[] array)

Heuristic: pulls section headings from each PDP (### ...), strips marketing
filler, and condenses to 3-6 word benefit phrases. Keeps the first 4. Skips
products with <2 usable sections.
"""

import json
import os
import re
import sys
from pathlib import Path

PDP_DIR = Path("pdp content")
OUTPUT = Path("benefits.json")
EXPERIMENT = "GE|PDP|AB032 - Add bullet point list for all product pages using IA"

# Words/phrases that don't carry benefit value — strip them
FILLER_PATTERNS = [
    r"^\s*(why\s+you[''‚]?ll\s+love\s+(it|them|this)\b[\s\S]*?:?)",
    r"^\s*(here[''‚]?s\s+why\b[\s\S]*?:?)",
    r"^\s*(what[''‚]?s\s+included\b[\s\S]*?:?)",
    r"^\s*(meet\s+the\b)",
    r"^\s*(introducing\s+the\b)",
    r"^\s*(\d+[\.\)]\s*)",                # leading "1. " / "2) "
    r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]",  # emojis
    r"\bthat\b|\bwhich\b|\bwith\s+a\b",   # connective fluff (used carefully)
]

# Phrases we strip from middle/end
STRIP_PHRASES = [
    r"\s+for\s+(men\s+and\s+women|adults?|kids?|everyone|all\s+ages?|daily\s+use|everyday\s+use|home\s+use|professional\s+use)\s*$",
    r"\s+in\s+(minutes|seconds)\s*$",
    r"\s+(with\s+ease|effortlessly|easily)\s*$",
    r"^\s*the\s+",
    r"^\s*a\s+",
    r"^\s*an\s+",
]

# Stopwords for trimming long phrases
DROP_WORDS = {
    "and", "or", "the", "a", "an", "in", "on", "at", "to", "of", "with", "for",
    "your", "you", "their", "his", "her", "its", "all", "any", "some",
    "really", "very", "ultimate", "perfect", "amazing", "incredible",
    "premium", "advanced", "professional", "high-quality",
}


def clean_heading(text: str) -> str:
    """Normalize a markdown heading into a benefit-shaped fragment."""
    t = text.strip()
    # Remove markdown bold/italic markers
    t = re.sub(r"\*+", "", t)
    # Drop trailing colon, em-dashes converting to commas
    t = t.rstrip(":—-– .").strip()
    # Drop leading filler
    for pat in FILLER_PATTERNS:
        t = re.sub(pat, "", t, flags=re.IGNORECASE)
    for pat in STRIP_PHRASES:
        t = re.sub(pat, "", t, flags=re.IGNORECASE)
    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()
    # Title-case if SCREAMING; otherwise keep sentence-style
    if t.isupper():
        t = t.title()
    # Lowercase the first letter? No — keep as is so proper nouns stay capitalized.
    return t


def condense(text: str, max_words: int = 6) -> str:
    """Trim down to <=max_words by dropping stopwords from the END first."""
    words = text.split()
    if len(words) <= max_words:
        return text
    # Drop trailing stopwords first
    kept = words[:]
    while len(kept) > max_words and kept and kept[-1].lower().strip(",.") in DROP_WORDS:
        kept.pop()
    if len(kept) > max_words:
        kept = kept[:max_words]
    return " ".join(kept).rstrip(",.")


def is_usable(heading: str) -> bool:
    """Filter out specs/package sections and empty/junk."""
    if not heading:
        return False
    low = heading.lower()
    bad_prefixes = (
        "specifications", "specification", "package includes", "package contents",
        "what's included", "in the box", "size:", "color:", "material:",
        "customer reviews", "faq", "shipping", "returns",
    )
    if any(low.startswith(p) or p in low for p in bad_prefixes):
        return False
    if len(heading) < 3 or len(heading) > 120:
        return False
    return True


def extract_sections(md_text: str):
    """Return (title, source_url, list_of_section_headings)."""
    lines = md_text.splitlines()
    title = None
    source = None
    headings = []
    for line in lines:
        s = line.strip()
        if title is None and s.startswith("# "):
            title = s[2:].strip()
        elif s.startswith("**Source:**"):
            source = s.replace("**Source:**", "").strip()
        elif s.startswith("### "):
            h = s[4:].strip()
            headings.append(h)
    return title, source, headings


def benefits_for_pdp(md_text: str):
    title, source, headings = extract_sections(md_text)
    # Clean, filter, condense
    cleaned = []
    seen = set()
    for h in headings:
        if not is_usable(h):
            continue
        c = clean_heading(h)
        if not c:
            continue
        c = condense(c, max_words=6)
        # Skip if too short or duplicate after cleaning
        if len(c) < 4:
            continue
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(c)
        if len(cleaned) == 4:
            break
    return title, source, cleaned


def main():
    if not PDP_DIR.is_dir():
        print(f"ERROR: {PDP_DIR} not found", file=sys.stderr)
        sys.exit(1)

    # Preserve the existing first product entry (manually curated)
    existing_first = None
    if OUTPUT.exists():
        try:
            with open(OUTPUT) as f:
                data = json.load(f)
            if data.get("products"):
                existing_first = data["products"][0]
        except Exception:
            existing_first = None

    products = []
    skipped = []
    for md_file in sorted(PDP_DIR.glob("*.md")):
        handle = md_file.stem
        # Skip the manually-curated entry — re-add it at the end
        if existing_first and handle == existing_first.get("handle"):
            continue
        try:
            text = md_file.read_text(encoding="utf-8")
        except Exception as e:
            skipped.append((handle, f"read error: {e}"))
            continue
        title, source, benefits = benefits_for_pdp(text)
        if len(benefits) < 2:
            skipped.append((handle, f"only {len(benefits)} usable benefit(s)"))
            # Still include with what we have so list is complete
        if not title:
            title = handle.replace("-", " ").title()
        if not source:
            source = f"https://www.gearelevation.com/products/{handle}"
        products.append({
            "handle": handle,
            "title": title,
            "source_url": source,
            "benefits": benefits,
        })

    # Put the manually-curated entry first
    if existing_first:
        products.insert(0, existing_first)

    out = {
        "experiment": EXPERIMENT,
        "schema_version": 1,
        "products": products,
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    total = len(products)
    full = sum(1 for p in products if len(p["benefits"]) == 4)
    partial = total - full
    print(f"Wrote {OUTPUT}: {total} products ({full} with 4 benefits, {partial} with <4)")
    if skipped:
        print(f"Note: {len(skipped)} products had thin source content (e.g., gift card)")


if __name__ == "__main__":
    main()
