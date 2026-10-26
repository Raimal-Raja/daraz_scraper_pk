# Pakistan scraper review — 26 October 2026

## Current data and verification

The live runs collected **25 unique products** from `https://www.daraz.pk/`:
20 Bedding & Bath products and 5 Computers & Laptops products. This is a bounded
sample of default product variants, not a complete Pakistan catalog or all
categories. The homepage exposed 16 category cards; those cards can change.

Combined export: `scrapers/Scraped_data/pakistan_current_20261026.csv`.
Independent validation: the adjacent `.validation.json`.
The two `pakistan_products_20261026_*.report.json` files retain individual run
timestamps, document HTTP responses, failures, and missing-field counts.
The live document responses recorded in these runs were HTTP 200, with no
detected access challenge and no rejected product rows.

The `.com.pk` hostname returned `ERR_SSL_UNRECOGNIZED_NAME_ALERT` in the initial
browser check. `www.daraz.pk` worked. Input legacy Pakistan URLs are normalized
to the working domain. Bangladesh URLs are rejected.

## Validation coverage

16 automated tests pass using the installed Selenium package and bundled Python.
All five Python source files were syntax checked. An independent standard-library
CSV validator accepted all 25 exported rows with no errors, checking:

- Required title, category, subcategory, product URL, currency, price, timestamp.
- HTTPS Pakistan URL and product ID, with duplicate IDs rejected independently
  of product slug or SKU.
- Positive finite PKR sale prices; original prices at least equal to sale prices.
- Discount arithmetic when original prices are available.
- Seller/discount percentages in 0–100; rating counts as nonnegative integers.
- Current timestamps within 24 hours with Pakistan's UTC+05:00 offset.
- UTF-8 CSV round-trip and explicit blanks for unavailable values.

The independent validator checks freshness relative to the time it runs, so
this dated export will intentionally fail that check after 24 hours.

Missing values in the combined export: Chat Response Rate 25, Flagship Store 25,
Ship On Time 2, Original Price 1, Discount 1, Delivery Type 1, Cash On Delivery 1.
A missing badge cannot establish that a seller is not a flagship store.
Unavailable metrics are not replaced with zero, and missing delivery information
does not imply that COD is unavailable. Delivery details depend on the site's
default location; prices reflect the default variant and can change.

## Bot/access detection review

These are observable checks and tested failure paths, not a claim to know every
private rule Daraz applies.

| Mechanism | Handling / evidence |
| --- | --- |
| robots.txt | Fetched and saved before each run. Current `/catalog/` and `/*from=` exclusions are enforced; wildcard matching is unit tested. Public category URLs omit homepage tracking queries. |
| Request frequency | One browser, sequential navigation, at least 2 seconds between navigation starts, bounded product/page limits, and an attempt limit of three times the product target. |
| HTTP access denial/rate limiting | Document responses 401, 403, and 429 stop the run and retain saved rows. All three status paths are unit tested with synthetic responses; production returned 200 during these runs. No intentional load or rate-limit probing was performed. |
| CAPTCHA/slider widgets | Visible CAPTCHA/challenge frames and known slider containers stop the run. Visibility handling is unit tested; no live challenge was encountered. |
| Verification interstitials | Known verification URLs, challenge titles, and visible phrases such as “slide to verify” or “unusual traffic” stop the run. Classifier behavior is unit tested. |
| Hidden scripts mentioning CAPTCHA | Detection uses visible text/widgets, rather than treating any occurrence in JavaScript source as a block. |
| IP reputation, TLS/HTTP fingerprints, browser fingerprints, behavioral scoring, session reputation, honeypots | Whether and how these are applied by Daraz was not established. Public page inspection and successful requests cannot prove their absence. No fingerprint spoofing, stealth patches, proxy rotation, or behavioral evasion is implemented. |
| Novel/localized challenges | May not match the current indicators. Missing-content errors and HTTP failures still end or flag affected work. Detection is not exhaustive. |

CAPTCHA is intended to prevent automation; the scraper stops rather than solving
or bypassing it. See [Selenium's CAPTCHA guidance](https://www.selenium.dev/documentation/test_practices/discouraged/captchas/).

## Operational fixes

Both scrapers use Pakistan URLs and the shared browser helpers. Product parsing
uses semantic CSS classes instead of generated block IDs, understands PKR prices,
and reads seller metrics by label. Pagination replaces its existing page value.
One browser is reused and closed in `finally`. Each validated product is appended
to one CSV and flushed and synced to disk before the next product is requested.
Pakistan timestamped terminal logs show progress, saved counts, warnings, errors,
and the final output path. The append/header and immediate logging checks bring
the current automated test count to 18 (the original data review used 16).
Existing output files
require an explicit `--overwrite`; normal runs receive timestamped filenames.
The report is written on completion or handled run failure. A hard process kill
can interrupt report creation; previously checkpointed CSV rows remain available.

The local `.env` launcher could not find its original Python installation in this
execution session. Verification used bundled Python 3.12 with the existing
`.env/Lib/site-packages` and cached matching ChromeDriver. No environment or
dependency installation was performed.

## Run again

From the project root with a working Python environment:

```powershell
python scrapers/product_data_scraper.py --headless --max-products 20 --pages 1
python scrapers/product_data_scraper.py --url https://www.daraz.pk/traditional-laptops/ --max-products 5
python -m unittest discover -s tests -v
python tests/validate_export.py PATH_TO_NEW_CSV
```

Use `--driver-path` if ChromeDriver is already installed and automatic Selenium
Manager lookup is unavailable. The scraper does not bypass TLS errors or disable
certificate checks.
