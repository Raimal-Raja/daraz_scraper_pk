import argparse
import csv
from pathlib import Path
from selenium.webdriver.common.by import By
from browser_helpers import BASE_URL, Navigator, category_links, create_driver, wait_for_elements


def main():
    parser = argparse.ArgumentParser(description='Collect public Pakistan category names')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--driver-path')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent / 'Scraped_data' / 'pakistan_subcategories.csv')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output exists; choose a new --output path')
    driver = create_driver(args.headless, args.driver_path)
    rows = []
    try:
        driver.set_page_load_timeout(45)
        navigator = Navigator(driver)
        navigator.load_robots()
        navigator.get(BASE_URL)
        categories = category_links(driver)
        for category, links in categories:
            for link in links:
                navigator.get(link)
                headings = wait_for_elements(driver, 'h1', 'category heading')
                rows.append({'Category': category, 'SubCategory Name': headings[0].text, 'SubCategory Link': link})
                args.output.parent.mkdir(parents=True, exist_ok=True)
                with args.output.open('w', encoding='utf-8-sig', newline='') as handle:
                    writer = csv.DictWriter(handle, fieldnames=['Category', 'SubCategory Name', 'SubCategory Link'])
                    writer.writeheader()
                    writer.writerows(rows)
        print(f'Saved {len(rows)} categories to {args.output}')
    finally:
        driver.quit()


if __name__ == '__main__':
    main()
