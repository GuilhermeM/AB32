"""
Generate 4 concise benefit bullets per product in `benefits.json`
by reading each PDP markdown under `pdp content/` and calling
the Claude API.

Usage:
    pip install anthropic
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 summarize_benefits.py                 # process all products
    python3 summarize_benefits.py --limit 5       # test on 5 products first
    python3 summarize_benefits.py --overwrite     # re-summarize even existing
    python3 summarize_benefits.py --workers 16    # tune parallelism (default 8)

Behavior:
- Preserves the gold-standard porch banner entry (manually curated) unless --overwrite.
- Skips entries whose benefits already look manually-curated (short, no fluff markers).
  Use --overwrite to force regenerate.
- Writes benefits.json after every product (atomic) so a crash never loses progress.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List

import anthropic
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
PDP_DIR = ROOT / "pdp content"
BENEFITS_PATH = ROOT / "benefits.json"

MODEL = "claude-opus-4-7"
MAX_PDP_CHARS = 12000  # truncate very long PDPs (rare); keeps cost predictable

# Gold-standard handle — never re-summarize this one unless --overwrite is set.
GOLD_HANDLE = (
    "1-pair-patriotic-american-flag-porch-banners-4th-of-july-outdoor-decorations-"
    "durable-polyester-hanging-signs-for-veterans-memorial-day-labor-day-and-"
    "election-day-celebrations1746595748807"
)

SYSTEM_PROMPT = """\
You write product benefit bullets for an A/B test on Gear Elevation product pages.
The bullets sit below title and price to help shoppers grasp value in under one second.

Rules — follow exactly:
- Output 4 benefits per product.
- Each benefit is 3-6 words. Shorter is better.
- A benefit, not a feature. "Weather-resistant polyester" beats "100% polyester construction".
- Drawn from the actual PDP content provided. Do not invent claims.
- Unique to this product. No generic filler.
- No marketing fluff words: avoid "revolutionary", "ultimate", "amazing", "premium",
  "advanced", "unmatched", "incredible", "innovative".
- Sentence-cased fragment. No trailing period. No "and" lists ("X, Y and Z" -> pick one).
- Write in the same language as the PDP. If the PDP is in Portuguese, write Portuguese
  benefits. If English, English.

Reference example (gold standard):
Product: Patriotic American Flag Porch Banners
Benefits:
- Eye-catching vertical design
- Ready to hang, no setup
- Weather-resistant polyester
- Reusable every holiday

Notice: each is concrete, short, value-focused, drawn from the PDP."""


class Benefits(BaseModel):
    benefits: List[str] = Field(min_length=4, max_length=4)


def load_db() -> dict:
    with BENEFITS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


_write_lock = threading.Lock()


def save_db(db: dict) -> None:
    with _write_lock:
        tmp = BENEFITS_PATH.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
        os.replace(tmp, BENEFITS_PATH)


def read_pdp(handle: str) -> str | None:
    path = PDP_DIR / f"{handle}.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    if len(text) > MAX_PDP_CHARS:
        text = text[:MAX_PDP_CHARS] + "\n\n[...truncated]"
    return text


def looks_manually_curated(benefits: list[str]) -> bool:
    """Heuristic: already-good bullets are short and don't include fluff markers."""
    if not benefits or len(benefits) != 4:
        return False
    fluff = {
        "revolutionize", "revolutionary", "ultimate", "amazing", "premium",
        "advanced", "unmatched", "incredible", "innovative",
    }
    for b in benefits:
        words = b.lower().split()
        if len(words) > 6:
            return False
        if any(w.strip(",.!") in fluff for w in words):
            return False
        if b.endswith("..."):
            return False
        if b.rstrip().endswith(",") or b.rstrip().endswith("the"):
            return False
    return True


def summarize(client: anthropic.Anthropic, title: str, pdp_text: str) -> List[str]:
    user_prompt = f"""\
Product title: {title}

PDP content (markdown):
---
{pdp_text}
---

Write 4 benefit bullets for this product, following all rules in the system prompt.\
"""

    response = client.messages.parse(
        model=MODEL,
        max_tokens=1024,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": user_prompt}],
        output_format=Benefits,
    )
    return response.parsed_output.benefits


def process_one(
    client: anthropic.Anthropic,
    product: dict,
    overwrite: bool,
) -> tuple[str, list[str] | None, str]:
    """Returns (handle, new_benefits, status_msg)."""
    handle = product["handle"]
    title = product.get("title", "")

    if handle == GOLD_HANDLE and not overwrite:
        return handle, None, "skip (gold standard)"

    existing = product.get("benefits") or []
    if not overwrite and looks_manually_curated(existing):
        return handle, None, "skip (looks curated)"

    pdp_text = read_pdp(handle)
    if pdp_text is None:
        return handle, None, "skip (no pdp file)"

    try:
        benefits = summarize(client, title, pdp_text)
    except Exception as e:
        return handle, None, f"error: {type(e).__name__}: {e}"

    return handle, benefits, "ok"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="process first N products only")
    parser.add_argument("--overwrite", action="store_true", help="regenerate even good-looking entries")
    parser.add_argument("--workers", type=int, default=8, help="parallel API calls (default 8)")
    parser.add_argument("--only-handle", default=None, help="only process this single handle")
    args = parser.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: set ANTHROPIC_API_KEY in env", file=sys.stderr)
        return 2

    db = load_db()
    products = db["products"]

    if args.only_handle:
        products_to_process = [p for p in products if p["handle"] == args.only_handle]
    else:
        products_to_process = products[: args.limit] if args.limit else products

    print(f"Processing {len(products_to_process)} products (workers={args.workers})")
    print(f"Model: {MODEL}")

    client = anthropic.Anthropic()
    by_handle = {p["handle"]: p for p in db["products"]}

    counts = {"ok": 0, "skipped": 0, "error": 0}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_one, client, p, args.overwrite): p["handle"]
            for p in products_to_process
        }
        for i, fut in enumerate(as_completed(futures), 1):
            handle, benefits, status = fut.result()
            if benefits is not None:
                by_handle[handle]["benefits"] = benefits
                save_db(db)
                counts["ok"] += 1
                print(f"[{i}/{len(futures)}] OK    {handle[:60]}")
                for b in benefits:
                    print(f"           - {b}")
            elif status.startswith("error"):
                counts["error"] += 1
                print(f"[{i}/{len(futures)}] ERROR {handle[:60]} :: {status}")
            else:
                counts["skipped"] += 1
                print(f"[{i}/{len(futures)}] SKIP  {handle[:60]} :: {status}")

    print()
    print(f"Done. ok={counts['ok']} skipped={counts['skipped']} error={counts['error']}")
    print(f"Wrote: {BENEFITS_PATH}")
    return 0 if counts["error"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
