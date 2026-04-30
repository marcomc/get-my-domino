# Domino Site Structure Notes

This note documents the current `rivistadomino.it` page structure from the
point of view of `get-my-domino`, so maintainers can adjust the scraper
quickly if the site layout, domain paths, or selectors change.

## Purpose

The crawler does not use an official API. It relies on a few stable entry
points plus HTML selectors and URL patterns:

- subscriber issue index pages to discover private issue URLs
- issue detail pages to discover ordered article lists, issue metadata, and the
  issue cover image
- article pages to extract cleaned article bodies and author bylines
- weekly feed archive pages to discover recurring `La settimana di Domino`
  posts

The most important implementation points are:

- [src/get_my_domino/web.py](../src/get_my_domino/web.py)
- [src/get_my_domino/extract.py](../src/get_my_domino/extract.py)
- [src/get_my_domino/config.py](../src/get_my_domino/config.py)

## Crawl Entry Points

The current configured defaults are:

- `base_url = "https://www.rivistadomino.it/"`
- `magazine_index_url = "https://www.rivistadomino.it/mio-account/my_domino/"`
- `feed_index_url = "https://www.rivistadomino.it/blog/category/la-settimana-di-domino/"`

The process is:

1. Authenticate against the WordPress/WooCommerce account area.
2. Load `magazine_index_url` and keep only links matching
   `issue_link_patterns`, currently `?sfoglia=1`.
3. Load each issue page and extract ordered article links from the issue tab.
4. Load each article page and extract clean readable content.
5. Separately, load `feed_index_url` and walk pagination for recurring feed
   posts.

## Authentication And Session Reuse

`WebClient.authenticate()` supports two paths:

- saved cookies from `auth_session_path`
- credential login by parsing the login form and submitting configured field
  names

The session check is simple and intentional: after loading
`auth_login_url = "https://www.rivistadomino.it/mio-account/"`, the client
looks for the presence of the login form again. If the form is still present,
the cookie session is considered expired.

If Domino changes login markup, these methods are the first place to inspect:

- `WebClient._find_login_form()`
- `WebClient._form_payload()`
- `WebClient._contains_login_form()`

## Issue Discovery

Issue discovery starts from the subscriber `my_domino` page, not from a public
catalog.

Implementation:

- `WebClient.discover_issues()`
- `extract.extract_links()`

Current assumptions:

- issue links are discoverable as normal `<a href="...">` elements
- the issue URL contains a marker captured by `issue_link_patterns`
- today that marker is `?sfoglia=1`, because the issue page must open in the
  readable subscriber view instead of the bare storefront product page

If the site changes:

- first try adjusting `issue_link_patterns` in config
- if the issue index stops exposing ordinary anchors, update
  `discover_issues()` or add a dedicated issue extractor

## Issue Page Parsing

Issue detail parsing lives in `WebClient._extract_issue()`.

The current selectors and logic are:

- title: `h1.product_title, h1.entry-title, h1`
- summary block: `.summary, .entry-summary`
- issue code: regex `NN/YYYY` extracted from summary text and stored as `YYYY-NN`
- article panel: `#tab-articles`
- cover image candidates:
  - `.woocommerce-product-gallery__image img`
  - `.product img`
  - `.summary img`
  - `meta[property='og:image']`

When `#tab-articles` exists, the parser walks only `h3` and `a` elements
inside that panel:

- each `h3` becomes the current section/group label
- each `<a class="article_title">` becomes one ordered article
- article order is assigned sequentially in DOM order
- article dates are inferred from `/blog/YYYY/MM/DD/...` in the article URL

When `#tab-articles` does not exist, the code falls back to generic link
extraction over the whole issue page using `article_link_patterns`.

This is the main contract the rest of the CLI depends on:

- section headings come from `h3`
- chapter order comes from the order of `a.article_title`
- issue cover art comes from one of the selectors above
- summary text is cleaned to remove store boilerplate like Amazon and print
  availability copy

If issue pages are redesigned, inspect these helpers first:

- `WebClient._extract_issue()`
- `WebClient._issue_cover_image_url()`
- `WebClient._issue_summary_description()`

## Weekly Feed Discovery

Weekly feed discovery lives in `WebClient.discover_feed_articles()`.

Current assumptions:

- the feed is a paginated WordPress category archive
- article URLs still look like `/blog/YYYY/MM/DD/slug/`
- the next page is discoverable through one of:
  - `<link rel="next" href="...">`
  - `<a class="next" href="...">`

The feed walker:

- starts at `feed_index_url`
- extracts links matching `feed_article_link_patterns`
- deduplicates URLs across pages
- follows `_next_page_url()` until `max_pages` is reached or pagination stops

If the feed changes shape, adjust:

- `feed_index_url`
- `feed_article_link_patterns`
- `WebClient._next_page_url()`

## Generic Link Extraction

`extract.extract_links()` is the generic anchor collector used by issue
discovery and feed discovery.

Important behavior:

- every candidate URL is normalized by `normalize_url()`
- trailing slashes are removed, but the query string is preserved
- the include/skip filter checks three strings together:
  - raw `href`
  - anchor text
  - normalized absolute URL
- duplicate URLs are dropped after normalization

This means many site changes can be handled by config only:

- `issue_link_patterns`
- `article_link_patterns`
- `feed_article_link_patterns`
- `skip_link_patterns`

## Article Extraction

Article extraction lives in `extract.extract_article()`.

The process is:

1. Parse the full page with BeautifulSoup.
2. Detect the author before removing boilerplate.
3. Remove known non-content selectors from `REMOVABLE_SELECTORS`.
4. Pick the first matching content root from `content_selectors`.
5. Prefer an `h1` or `h2` inside the chosen content root as the article title.
6. Export cleaned HTML plus double-newline-separated text.

Current default `content_selectors` are:

- `article`
- `main`
- `.entry-content`

Current author extraction tries, in order:

- `[rel='author']`
- `.author`
- `.byline`
- `.entry-author`
- `.post-author`
- fallback scan of `p`, `h3`, `h4`, and `span`

Author strings are normalized by `_clean_author()`, which strips prefixes like
`di`, `by`, `da`, `diretta da`, and `diretto da`.

If Domino changes article markup, the safest first move is to override
`content_selectors`. If that is not enough, update:

- `REMOVABLE_SELECTORS`
- `_extract_author()`
- `_clean_author()`

## Config Knobs Before Code Changes

Prefer config changes first when the site changes but the general structure is
still the same.

The most useful knobs are:

- `magazine_index_url`
- `feed_index_url`
- `issue_link_patterns`
- `article_link_patterns`
- `feed_article_link_patterns`
- `skip_link_patterns`
- `content_selectors`
- `auth_*` login field names and URLs

Code changes are usually only needed when:

- issue links are no longer ordinary anchors
- section/article order is no longer represented by `#tab-articles`
- cover images move to different markup
- login forms stop matching the current field discovery
- article content can no longer be isolated with selector changes alone

## Regression Tests That Lock This In

The main tests describing the current structure are in
[tests/test_cli.py](../tests/test_cli.py):

- `test_feed_articles_follow_pagination_and_deduplicate`
- `test_issue_articles_keep_month_groups_dates_and_order`
- `test_catalog_expands_selected_issue_grouped_by_section`
- `test_download_issue_all_can_package_issue_audiobook`
- `test_extract_article_reads_byline_author`
- `test_extract_article_normalizes_domino_directed_by_author`

When the site changes, update both the scraper code and these structural tests
so future regressions are explicit.

## Fast Debug Checklist

When discovery breaks, check in this order:

1. Run `get-my-domino login --browser` and confirm the saved session still
   reaches `mio-account` without showing the login form.
2. Open the relevant page in a browser and confirm the current URL still
   matches the configured index URL or link patterns.
3. Inspect whether issue links still contain `?sfoglia=1`.
4. Inspect whether the issue page still exposes `#tab-articles`, `h3` section
   headings, and `a.article_title` links.
5. Inspect whether the cover image now lives under a different selector.
6. Inspect whether article content is still under `article`, `main`, or
   `.entry-content`.
7. Update config-only selectors or patterns first; patch parser code second.
8. Re-run `uv run pytest -q` after updating the structural tests.

## Practical Rule

If the domain structure changes but the HTML semantics remain close to the
current WordPress/WooCommerce setup, start in config. If the DOM structure
changes, start in `web.py` for issue/feed discovery and in `extract.py` for
article parsing.
