"""Collect and validate public Daraz Pakistan product data."""
import argparse
import csv
import json
import re
import sys
import math
import logging
import os
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from selenium.webdriver.common.by import By
from browser_helpers import (
    BASE_URL, PRODUCT_SELECTOR, AccessBlocked, RobotsDenied, Navigator,
    category_links, create_driver, pakistan_url, page_url, wait_for_elements,
)

columns = ['Category', 'SubCategory', 'Title', 'Original Price', 'Discount Price',
           'Discount', 'Seller Name', 'Number of Ratings', 'Positive Seller Ratings',
           'Ship On Time', 'Chat Response Rate', 'Delivery Type', 'Cash On Delivery',
           'Flagship Store', 'Product URL', 'Currency', 'Scraped At', 'Validation Warnings']
OPTIONAL = ['Original Price', 'Discount', 'Seller Name', 'Number of Ratings',
            'Positive Seller Ratings', 'Ship On Time', 'Chat Response Rate',
            'Delivery Type', 'Cash On Delivery', 'Flagship Store']
logger = logging.getLogger('daraz.products')


class PakistanFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created, ZoneInfo('Asia/Karachi')).strftime('%Y-%m-%d %H:%M:%S %z')


def configure_logging(log_dir=None):
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    formatter = PakistanFormatter('%(asctime)s | %(levelname)s | %(message)s')
    terminal = logging.StreamHandler(sys.stdout)
    terminal.setFormatter(formatter)
    logger.addHandler(terminal)
    log_path = None
    if log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(ZoneInfo('Asia/Karachi')).strftime('%Y%m%d_%H%M%S_%f')
        log_path = log_dir / f'pakistan_scraper_{stamp}.log'
        file_handler = logging.FileHandler(log_path, encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return log_path


def now():
    return datetime.now(ZoneInfo('Asia/Karachi')).isoformat(timespec='seconds')


def text(root, selector):
    for element in root.find_elements(By.CSS_SELECTOR, selector):
        value = element.text.strip()
        if value:
            return value
    return ''


def parse_money(value):
    value = re.sub(r'^(?:Rs\.?|PKR)\s*', '', value.strip(), flags=re.I)
    if not re.fullmatch(r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?', value):
        raise ValueError(f'Invalid PKR price: {value!r}')
    amount = Decimal(value.replace(',', ''))
    if amount <= 0:
        raise ValueError('Price must be positive')
    return str(amount)


def parse_percent(value):
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)%\s*', value)
    if not match:
        return None
    number = float(match.group(1))
    return number if 0 <= number <= 100 else None


def parse_rating_count(value):
    match = re.fullmatch(r'\s*(?:Ratings?\s+)?(\d{1,3}(?:,\d{3})+|\d+)(?:\s+Ratings?)?\s*', value, re.I)
    return int(match.group(1).replace(',', '')) if match else None


def validate_row(row):
    errors = []
    if not row.get('Title', '').strip():
        errors.append('Missing title')
    for field in ('Category', 'SubCategory'):
        if not str(row.get(field) or '').strip():
            errors.append(f'Missing {field}')
    try:
        pakistan_url(row.get('Product URL', ''))
        product_key(row['Product URL'])
    except ValueError:
        errors.append('Invalid product domain')
    if row.get('Currency') != 'PKR':
        errors.append('Currency must be PKR')
    try:
        stamp = datetime.fromisoformat(row.get('Scraped At') or '')
        if stamp.utcoffset() is None:
            raise ValueError()
    except ValueError:
        errors.append('Missing or invalid timestamp')
    try:
        current = Decimal(str(row['Discount Price']))
        if not current.is_finite() or current <= 0:
            raise ValueError()
        original_value = row.get('Original Price')
        if original_value not in (None, ''):
            original = Decimal(str(original_value))
            if not original.is_finite() or original < current:
                errors.append('Original price is below sale price')
    except (KeyError, InvalidOperation, ValueError):
        errors.append('Invalid price')
    for field in ('Discount', 'Positive Seller Ratings', 'Ship On Time', 'Chat Response Rate'):
        value = row.get(field)
        if value not in (None, ''):
            try:
                if not 0 <= float(value) <= 100:
                    raise ValueError()
            except (ValueError, TypeError):
                errors.append(f'{field} outside 0..100')
    count = row.get('Number of Ratings')
    if count is not None and (not isinstance(count, int) or count < 0):
        errors.append('Invalid rating count')
    if errors:
        raise ValueError('; '.join(errors))
    return [f'Missing {field}' for field in OPTIONAL if row.get(field) in (None, '')]


def product_key(url):
    match = re.search(r'/products/[^?]*-i(\d+)(?:[-.]|$)', url)
    if not match:
        raise ValueError(f'Invalid product URL: {url}')
    return match.group(1)


def scrape_product_data(category, sub_category, product_link, driver, navigator):
    navigator.get(product_link)
    wait_for_elements(driver, '.pdp-mod-product-badge-title', 'product title')
    wait_for_elements(driver, '.pdp-price_color_orange', 'product price')
    row = dict.fromkeys(columns)
    row.update({'Category': category, 'SubCategory': sub_category,
                'Title': text(driver, '.pdp-mod-product-badge-title'),
                'Discount Price': parse_money(text(driver, '.pdp-product-price .pdp-price_color_orange')),
                'Product URL': pakistan_url(driver.current_url, strip_query=True),
                'Currency': 'PKR', 'Scraped At': now()})
    original = text(driver, '.pdp-product-price .origin-block .pdp-price')
    if original:
        row['Original Price'] = parse_money(original)
        row['Discount'] = round(100 * (1 - float(Decimal(row['Discount Price']) / Decimal(row['Original Price']))), 2)
    row['Seller Name'] = text(driver, '.seller-name__detail-name, .seller-name__detail a') or None
    row['Number of Ratings'] = parse_rating_count(text(driver, '.pdp-review-summary__link'))
    metric_fields = {'positive seller ratings': 'Positive Seller Ratings',
                     'ship on time': 'Ship On Time', 'chat response rate': 'Chat Response Rate'}
    for block in driver.find_elements(By.CSS_SELECTOR, '.pdp-seller-info-pc .info-content'):
        label = text(block, '.seller-info-title').lower()
        if label in metric_fields:
            row[metric_fields[label]] = parse_percent(text(block, '.seller-info-value'))
    delivery = text(driver, '.delivery-option-item_type_standard .delivery-option-item__title')
    if delivery:
        row['Delivery Type'] = delivery
    cod = text(driver, '.delivery-option-item_type_COD .delivery-option-item__title')
    if cod:
        row['Cash On Delivery'] = cod
    # A missing badge cannot establish that a seller is not a flagship store.
    badges = driver.find_elements(By.CSS_SELECTOR, '.seller-name__detail img[alt]')
    if any('darazmall' in (badge.get_attribute('alt') or '').lower() for badge in badges):
        row['Flagship Store'] = 'Yes'
    breadcrumbs = driver.find_elements(By.CSS_SELECTOR, '#J_breadcrumb a')
    if breadcrumbs:
        row['Category'] = breadcrumbs[0].text.strip() or category
        row['SubCategory'] = breadcrumbs[-1].text.strip() or sub_category
    warnings = validate_row(row)
    row['Validation Warnings'] = '; '.join(warnings)
    return row


def append_row(path, row, first=False):
    """Checkpoint one validated product before requesting the next product."""
    with path.open('w' if first else 'a', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        if handle.tell() == 0:
            writer.writeheader()
        writer.writerow(row)
        handle.flush()
        os.fsync(handle.fileno())


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    log_path = configure_logging(Path(__file__).resolve().parents[1] / 'logs')
    logger.info('Log file: %s', log_path)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default=BASE_URL)
    parser.add_argument('--url', help='Public Pakistan category URL (search /catalog/ is disallowed by robots.txt)')
    parser.add_argument('--pages', type=int, default=1)
    parser.add_argument('--max-products', type=int, default=20)
    parser.add_argument('--delay', type=float, default=2, help='Minimum seconds between navigations (at least 2)')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--driver-path')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    if args.pages < 1 or args.max_products < 1 or not math.isfinite(args.delay) or args.delay < 2:
        parser.error('--pages and --max-products must be positive; --delay must be at least 2')
    try:
        base_url = pakistan_url(args.base_url)
        selected_url = pakistan_url(args.url) if args.url else None
    except ValueError as exc:
        parser.error(str(exc))
    stamp = datetime.now(ZoneInfo('Asia/Karachi')).strftime('%Y%m%d_%H%M%S')
    output = args.output or Path(__file__).resolve().parent / 'Scraped_data' / f'pakistan_products_{stamp}.csv'
    report_path = output.with_suffix('.report.json')
    if (output.exists() or report_path.exists()) and not args.overwrite:
        parser.error(f'Output already exists: {output}; choose another path or use --overwrite')
    output.parent.mkdir(parents=True, exist_ok=True)
    rows, failures, seen = [], [], set()
    attempts = 0
    report = {'started_at': now(), 'domain': BASE_URL, 'status': 'running',
              'requested_max_products': args.max_products, 'requested_pages_per_category': args.pages,
              'category_scope': 'explicit URL' if selected_url else 'homepage cards; not a complete category hierarchy',
              'output': str(output.resolve())}
    driver = None
    navigator = None
    try:
        logger.info('Starting Pakistan scrape | target=%s products | pages/category=%s | CSV=%s', args.max_products, args.pages, output.resolve())
        driver = create_driver(args.headless, args.driver_path)
        driver.set_page_load_timeout(45)
        navigator = Navigator(driver, args.delay)
        robots = navigator.load_robots()
        logger.info('robots.txt loaded; navigation delay=%.1fs', args.delay)
        output.with_suffix('.robots.txt').write_text(robots, encoding='utf-8')
        if selected_url:
            categories = [('Selected category', [selected_url])]
        else:
            navigator.get(base_url)
            categories = category_links(driver)
        report['categories_discovered'] = len(categories)
        logger.info('Found %s Pakistan categories', len(categories))
        for category, links in categories:
            for link in links:
                for page in range(1, args.pages + 1):
                    listing = page_url(link, page)
                    logger.info('Category=%s | page=%s/%s | %s', category, page, args.pages, listing)
                    try:
                        navigator.get(listing)
                        elements = wait_for_elements(driver, PRODUCT_SELECTOR, 'product links')
                    except RobotsDenied as exc:
                        failures.append({'url': listing, 'reason': str(exc)})
                        logger.warning('Skipped listing: %s', exc)
                        break
                    except AccessBlocked:
                        raise
                    except RuntimeError as exc:
                        failures.append({'url': listing, 'reason': str(exc)})
                        logger.warning('Skipped listing: %s', exc)
                        break
                    product_links = []
                    for element in elements:
                        try:
                            url = pakistan_url(element.get_attribute('href') or '', strip_query=True)
                            key = product_key(url)
                        except ValueError:
                            continue
                        if key not in seen:
                            seen.add(key)
                            product_links.append(url)
                    if not product_links:
                        logger.info('No new product IDs on this page; ending category')
                        break
                    logger.info('Found %s new product links', len(product_links))
                    for url in product_links:
                        attempts += 1
                        if attempts > args.max_products * 3:
                            raise RuntimeError('Product attempt limit reached; review failures before another run')
                        try:
                            logger.info('Extracting product %s | %s', attempts, url)
                            row = scrape_product_data(category, link, url, driver, navigator)
                        except AccessBlocked:
                            raise
                        except (ValueError, RuntimeError) as exc:
                            failures.append({'url': url, 'reason': str(exc)})
                            logger.warning('Skipped product %s: %s', url, exc)
                            continue
                        append_row(output, row, first=not rows)
                        rows.append(row)
                        logger.info('Saved %s/%s to CSV | %s | PKR %s', len(rows), args.max_products, row['Title'][:100], row['Discount Price'])
                        if row.get('Validation Warnings'):
                            logger.warning('Product %s: %s', product_key(url), row['Validation Warnings'])
                        if len(rows) >= args.max_products:
                            report['status'] = 'completed_limit'
                            return
        if not rows:
            raise RuntimeError('No valid products collected; see report')
        report['status'] = 'completed_scope'
    except BaseException as exc:
        report['status'] = 'blocked' if isinstance(exc, AccessBlocked) else 'failed'
        report['error'] = str(exc)
        logger.error('Scrape %s: %s', report['status'], exc)
        raise
    finally:
        report.update(finished_at=now(), rows_saved=len(rows), products_attempted=attempts, failures=failures,
                      missing_fields=dict(Counter(field for row in rows for field in OPTIONAL if row.get(field) in (None, ''))),
                      responses=navigator.responses if navigator else [])
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
        if driver:
            driver.quit()
        logger.info('Finished: %s | saved=%s | skipped=%s | CSV=%s', report['status'], len(rows), len(failures), output.resolve())
        logger.info('Report: %s', report_path)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        print(f'Scraper failed: {exc}', file=sys.stderr)
        sys.exit(1)
