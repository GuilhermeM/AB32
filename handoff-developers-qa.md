**Project:** Gear Elevation A/B test — Benefits panel variant
**Experiment ID:** `GE|PDP|AB032`
**Deliverable:** `variant-ge-ab032.js` (single IIFE, paste into Varify.io)
**Audience:** Engineers reviewing the code, QA analysts validating the variant before launch.
**Source of truth for this doc:** `documentation.md` (full project history) and `briefing.md` (AB test hypothesis).

---

## 1. The idea, in one paragraph

PDPs on gearelevation.com lean on long, unstructured descriptions. Users skim, miss the value proposition, and bounce (~78% bounce rate). The hypothesis: surfacing **4 short, value-focused benefit bullets** near the top of each PDP — between the title/rating area and the price/Add-to-Cart box — will reduce cognitive load and lift add-to-cart and conversion. The bullets are pre-curated for 493 products and ship as static data inside the variant code.

This document covers what was built, why it was built that way, and how to QA it.

---

## 2. Success metrics (from `briefing.md`)

- ↑ Add-to-cart rate
- ↑ Final conversion rate
- ↓ Bounce rate

Targeting rule: pages whose URL contains `/products/`. Variant assignment and traffic split are handled by Varify, not by this code.

---

## 3. High-level architecture

```
Varify.io
   ↓ injects
variant-ge-ab032.js  (IIFE)
   ├── Data:        BENEFITS_MAP (493 handles → 4 bullets)
   ├── Anchor:      .product-block--price  (beforebegin)
   ├── Idempotent:  #ge-ab032-benefits guard
   ├── SPA:         MutationObserver, throttled + capped
   ├── Tracking:    dataLayer.push(ge_ab032_variant_rendered)
   └── Safety:      try/catch + console.warn, never breaks page
   ↓ renders
<div id="ge-ab032-benefits"> in the PDP info column
```

**Key architectural choices and why:**

| Choice | Rationale |
|---|---|
| Inline `BENEFITS_MAP` (~125 KB) in the IIFE | No runtime fetch, no CDN failure mode, no flash-of-no-bullets. Strategy A from the spec. |
| Vanilla JS, no jQuery, no external assets | Snippet must be portable across themes and survive Shopify's script-defer pipeline. |
| BEM-scoped CSS under `#ge-ab032-benefits` | Avoids theme collisions without `!important`. |
| `MutationObserver` with 1/sec throttle, 10-render or 30s cap | Re-renders on SPA variant changes; can't run away. |
| `try/catch` at the boot boundary | Variant must never break the page, even if a Shopify theme update changes selectors. |

---

## 4. The DOM anchor — the one thing that changed from the spec

The original spec (Section 3 of `documentation.md`) said to insert **after** `div.price.product__price`. On the live theme, that selector resolves *inside* the gray price/Popular-Add-Ons wrapper, which placed the panel inside the gray box rather than above it (visible regression vs. the Figma preview).

**Final anchor priority** (in `findAnchor()`):

1. `.product-block.product-block--price` with `beforebegin` — panel sits **above** the gray box as a direct child of `.product-details`.
2. `.product-block.product-block--title` with `afterend` — fallback when the price block is missing.
3. `h1.product-title` with `afterend` — last-resort fallback.

The PDP info column is built from `.product-block--*` siblings in this order:

```
.product-block--title     ← product name, "few left" notice
.product-block--@app      ← Loox rating widget
.product-block--dynamic_list
.product-block--price     ← gray box: price, Popular Add-Ons, color/qty, ATC
.product-block--form      ← variant selectors, quantity, Add to Cart button
```

Inserting `beforebegin` of `--price` slots the panel between the rating area and the gray container.

---

## 5. Data: `BENEFITS_MAP`

- 493 entries; each value is a `string[4]`.
- Source: curated in earlier sessions from each PDP's actual content. See `documentation.md` SESSION 3/4 handoffs for the curation history.
- Quality bar (3–6 word, value-focused, no fluff, English-only) is locked. Do not mutate the bullets without re-reading the relevant PDP markdown in `pdp content/`.
- File size impact: ~125 KB compact JSON literal embedded as `var BENEFITS_MAP = {...};`.

### Truncated-handle gotcha (important)

Shopify auto-truncates URL handles >120 chars. The live PDP URL may carry the **full** untruncated handle (e.g. `...-fk0301747018588026`) while `BENEFITS_MAP` keys use the **truncated/hashed** form (e.g. `...-f-07faac4e`).

`lookupBenefits()` handles this with a two-step lookup:

1. Exact match in `BENEFITS_MAP` → use it.
2. Take the first 80 characters of the page handle, scan `BENEFITS_MAP` keys for any that start with the same prefix → use that.
3. Otherwise bail silently.

Verified against a real truncated-handle product (`1pc-portable-camera-...`) in jsdom.

---

## 6. Handle resolution

`resolveHandle()` tries these sources in order and returns the first hit:

1. `window.ShopifyAnalytics.meta.product.handle` (most reliable, theme-set)
2. `window.location.pathname.match(/\/products\/([^/?#]+)/)[1]` (URL fallback)
3. `[data-product-handle]` element's dataset (last-resort fallback)

If none resolve, the IIFE logs `[GE-AB032] handle not found` and bails — better to show nothing than the wrong bullets.

---

## 7. Visual spec (from `style.css` tokens + Figma previews)

| Token | Value |
|---|---|
| Font family | `Assistant`, system fallback |
| Bullet text size | 18px desktop / 16px mobile |
| Bullet text color | `#000` |
| Bullet font weight | 400 |
| Line height | 21px desktop / 20px mobile |
| Check icon | Inlined SVG, 20×20, dark slate circle `#354C5E` with white checkmark |
| Row gap (icon ↔ text) | 8px |
| Row gap (bullet ↔ bullet) | 12px |
| Container margin | `12px 0 16px` (top tightened from spec's 16px to hug the rating area) |
| Container padding | 0 (no card background) |

Responsive: flex column on all viewports, text wraps naturally, icon stays at the top via `align-items: center`.

---

## 8. Failure modes — what the IIFE refuses to do

The IIFE will bail (silently or with a `console.warn`) if any of:

- `location.pathname` doesn't include `/products/`.
- `#ge-ab032-benefits` already exists in the DOM (idempotency).
- `resolveHandle()` returns null.
- `lookupBenefits()` returns null (no exact match and no prefix match).
- `findAnchor()` returns null (theme changed all known selectors).
- Any uncaught exception inside `boot()` → caught by top-level try/catch, logged with `[GE-AB032] error:` prefix.

The IIFE will never throw a page-breaking error. This is a hard requirement — Varify variants run on production traffic.

---

## 9. Tracking

The IIFE pushes a single event to `window.dataLayer` per page load:

```js
{ event: 'ge_ab032_variant_rendered', product_handle: '<resolved-handle>', bullet_count: 4 }
```

Pushed inside `render()` after the DOM injection succeeds. Skipped if rendering bails. Pushed once per handle change (re-render on SPA navigation re-pushes if the handle is different).

Varify will track variant exposure separately. This dataLayer push exists for funnel debugging in GTM/GA.

---

## 10. File inventory

| File | Purpose | Touch this? |
|---|---|---|
| `variant-ge-ab032.js` | The IIFE — paste this into Varify | No, regenerate via build script |
| `benefits.json` | Curated bullets (source of truth) | Edit if bullets need revision |
| `documentation.md` | Full project history & session handoffs | Append handoff sections only |
| `briefing.md` | AB test hypothesis | Reference only |
| `local-page.html` | Saved PDP HTML used for selector verification | Reference only |
| `preview-desktop.png` / `preview-mobile.png` | Figma mockups | Reference only |
| `icon-check.svg` | Source of the inlined check icon | Reference only |
| `style.css` | Figma's generated CSS (token reference) | Reference only |
| `handoff-developers-qa.md` / `.pdf` | This document | Update if architecture changes |

---

## 11. QA checklist — run all of these on a live PDP

QA should treat the IIFE as production code. Test on **at least 3 different products**: one with short bullets, one with the longest bullet in the catalog, one with a truncated-handle slug (any URL ending in `...-<8-hex-chars>`).

### Functional checks

- [ ] Panel appears between the title/rating area and the gray price box (NOT inside the gray box).
- [ ] Exactly 4 bullets render.
- [ ] Bullets match `benefits.json` for the current product's handle.
- [ ] Check icon is visible left of each bullet, dark slate circle with white checkmark.
- [ ] No console errors.
- [ ] No layout shift on existing PDP elements.

### Idempotency

- [ ] Paste the IIFE into the console a second time → panel does not duplicate.
- [ ] Hard refresh the page → panel still appears, no duplicate.

### Responsive

- [ ] Resize browser to 375px width → text shrinks to 16px / 20px line-height, icons stay aligned, no horizontal scroll.
- [ ] No bullet wraps awkwardly. Test the longest bullet (e.g. `9-axis tracking captures every hit`).

### Tracking

- [ ] After successful render, `window.dataLayer` contains an object with `event: 'ge_ab032_variant_rendered'`, the resolved `product_handle`, and `bullet_count: 4`.

### Failure-mode safety

- [ ] Load the IIFE on the homepage or a collection page → no panel injected, no console errors.
- [ ] Pick a product whose handle is NOT in `BENEFITS_MAP` → no panel, `console.warn` with `[GE-AB032] no benefits for handle:`.

### Variant selection (SPA test)

- [ ] On a PDP with color/size variants, switch variants several times → panel stays in place, no duplicates, no flicker.

### Truncated-handle product

- [ ] Open a PDP whose URL ends in `...-<8-hex-chars>` (e.g. one of the `feice-fashion-...-f-07faac4e` family) → panel still renders with the right bullets, even though the URL handle differs from the map key.

### Visual regression

- [ ] Compare to `preview-desktop.png` and `preview-mobile.png`. Spacing within ~2px, color hex-exact (`#000` text, `#354C5E` icon background).

---

## 12. Open questions for the user — answered before final ship

These are defaults applied in the IIFE header comment block. **Confirm or override before launch.**

| # | Question | Default | Where to change |
|---|---|---|---|
| 1 | Varify snippet size limit | Assume ~133 KB is accepted (Strategy A) | Switch to Strategy B (hosted JSON) if rejected |
| 2 | dataLayer event name | `ge_ab032_variant_rendered` | `EVENT_NAME` constant |
| 3 | Non-English locales | Show on all locales (English bullets) | No-op in code; add locale check to disable |
| 4 | Missing handle behavior | Silent bail + `console.warn` | `render()` early returns |
| 5 | Per-bullet click tracking | Not implemented | Out of scope for v1 |

---

## 13. Code review checklist (for the engineer reviewing the IIFE)

- [ ] All code wrapped in IIFE; no globals leak.
- [ ] `'use strict'` at top.
- [ ] No `!important` in CSS.
- [ ] All selectors namespaced under `#ge-ab032-benefits`.
- [ ] No `document.write`, no inline event handlers.
- [ ] Top-level `try/catch` wraps the boot path.
- [ ] `MutationObserver` is disconnected after MAX_RENDERS or OBSERVER_MAX_MS.
- [ ] No external network calls, no `<img>` or `<link>` to remote assets.
- [ ] SVG icon is inlined, not fetched.
- [ ] Element creation uses `document.createElement` + `textContent` for bullet text (no `innerHTML` for user-controlled data). `innerHTML` is used only for the static SVG constant.
- [ ] All `console.*` calls are wrapped in `try/catch` (some Shopify themes clobber console).

---

## 14. Rebuild process — for future maintenance

If `benefits.json` changes:

```bash
# Build script reads benefits.json + /tmp/ab32_iife_template.js, writes variant-ge-ab032.js
python3 /tmp/ab32_build.py
```

If the IIFE template changes (anchor, styles, tracking, etc.):

1. Edit `/tmp/ab32_iife_template.js` (the template uses `__BENEFITS_MAP__` as the data placeholder).
2. Re-run the build script above.
3. Re-run `node --check variant-ge-ab032.js` to confirm syntax.
4. Re-run the QA checklist above on a live PDP.

To rebuild this PDF:

```bash
pandoc handoff-developers-qa.md -s -o /tmp/handoff.html \
  --metadata title="GE PDP AB032 — Developer & QA Handoff" \
  --css=/dev/null
weasyprint /tmp/handoff.html handoff-developers-qa.pdf
```

---

## 15. Glossary

- **PDP** — Product Detail Page (`/products/<handle>` on Shopify).
- **IIFE** — Immediately-Invoked Function Expression. The variant code is wrapped as `(function(){ ... })();` so it can't leak globals.
- **Varify** — Third-party A/B testing tool. Handles traffic split and variant injection.
- **Handle** — Shopify's URL-safe product slug. Some are truncated/hashed when very long.
- **Anchor** — The DOM element the IIFE inserts the panel relative to.

---

*End of handoff. Questions: refer to `documentation.md` SESSION 5 BUILD COMPLETE for the latest project state.*
