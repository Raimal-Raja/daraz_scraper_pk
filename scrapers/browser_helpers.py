"""Browser navigation, Pakistan URL validation, and access-challenge detection."""
import json
import re
import time
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

BASE_URL = 'https://www.daraz.pk/'
CATEGORY_SELECTOR = '.lzd-site-menu-root > li > a, .lzd-site-nav-menu-dropdown > ul > li > a, a.card-categories-li'
PRODUCT_SELECTOR = "[data-qa-locator='product-item'] a[href*='-i'], a[href*='/products/'][href*='-i']"


class AccessBlocked(RuntimeError):
    pass


class RobotsDenied(RuntimeError):
    pass


def pakistan_url(url, strip_query=False):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname not in {'www.daraz.pk', 'daraz.pk', 'www.daraz.com.pk', 'daraz.com.pk'} or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError(f'Expected an HTTPS Daraz Pakistan URL: {url}')
    return urlunsplit(('https', 'www.daraz.pk', parts.path or '/', '' if strip_query else parts.query, ''))


def page_url(url, page):
    parts = urlsplit(pakistan_url(url))
    query = {key: value for key, value in parse_qsl(parts.query) if key not in {'page', 'from', 'sort'}}
    query.update(page=str(page), sort='order')
    return urlunsplit(parts._replace(query=urlencode(query)))


def block_reason(url, title, text):
    path = urlsplit(url).path.lower()
    if any(word in path for word in ('captcha', '/punish', '/challenge', '/verify', '/user/login')):
        return 'verification URL'
    heading = title.lower()
    if any(word in heading for word in ('access denied', 'just a moment', 'security verification', 'robot check')):
        return 'access challenge title'
    # Look at visible text, not bundled scripts that can mention CAPTCHA on ordinary pages.
    content = text.lower()
    for phrase in ('slide to verify', 'verify you are human', 'verify that you are human', 'unusual traffic', 'too many requests', 'access denied', 'please complete the captcha', 'press and hold to confirm'):
        if phrase in content:
            return phrase
    return None


def check_access(driver):
    body = driver.find_elements(By.TAG_NAME, 'body')
    reason = block_reason(driver.current_url, driver.title, body[0].text if body else '')
    if reason:
        raise AccessBlocked(f'{reason} at {driver.current_url}; stopped without bypassing the challenge')
    frames = driver.find_elements(By.CSS_SELECTOR, "iframe[src*='captcha'], iframe[src*='challenge'], #nc_1_wrapper, .nc-container")
    if any(frame.is_displayed() for frame in frames):
        raise AccessBlocked(f'Visible verification widget at {driver.current_url}; stopped')


def wait_for_elements(driver, selector, description, timeout=30):
    def ready(browser):
        check_access(browser)
        return browser.find_elements(By.CSS_SELECTOR, selector)
    try:
        return WebDriverWait(driver, timeout).until(ready)
    except TimeoutException as exc:
        raise RuntimeError(f'No {description} after {timeout}s at {driver.current_url}; empty results or unsupported layout') from exc


def category_links(driver):
    categories = wait_for_elements(driver, CATEGORY_SELECTOR, 'categories')
    result = []
    seen = set()
    for index, category in enumerate(categories, 1):
        name = (category.get_attribute('textContent') or '').strip()
        children = driver.find_elements(By.CSS_SELECTOR, f'ul.Level_1_Category_No{index} > li > a')
        links = []
        for element in children or [category]:
            link = element.get_attribute('href')
            if link:
                try:
                    link = pakistan_url(link, strip_query=True)
                except ValueError:
                    continue
                if link not in seen:
                    links.append(link)
                    seen.add(link)
        if links:
            result.append((name, links))
    if not result:
        raise RuntimeError('No usable Pakistan category links')
    return result


def create_driver(headless=False, driver_path=None):
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument('--headless=new')
    options.add_argument('--window-size=1440,1000')
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    return webdriver.Chrome(service=Service(driver_path) if driver_path else Service(), options=options)


class Navigator:
    def __init__(self, driver, delay=2):
        self.driver = driver
        self.delay = delay
        self.last_request = None
        self.disallowed = []
        self.responses = []
        self.robots_loaded = False

    def allowed(self, url):
        target = urlsplit(url).path
        if urlsplit(url).query:
            target += '?' + urlsplit(url).query
        for pattern in self.disallowed:
            end = pattern.endswith('$')
            pattern = pattern[:-1] if end else pattern
            regex = '^' + re.escape(pattern).replace(r'\*', '.*') + ('$' if end else '')
            if re.search(regex, target):
                return False
        return True

    def load_robots(self):
        self.get(BASE_URL + 'robots.txt', robots=True)
        text = self.driver.find_element(By.TAG_NAME, 'body').text
        if 'user-agent:' not in text.lower():
            raise RuntimeError('robots.txt could not be read; stopping before scraping')
        active = False
        for line in text.splitlines():
            line = line.split('#', 1)[0].strip()
            if ':' not in line:
                continue
            key, value = [part.strip() for part in line.split(':', 1)]
            if key.lower() == 'user-agent':
                active = value == '*'
            elif active and key.lower() == 'disallow' and value:
                self.disallowed.append(value)
        self.robots_loaded = True
        return text

    def get(self, url, robots=False):
        url = pakistan_url(url)
        if not robots and (not self.robots_loaded or not self.allowed(url)):
            raise RobotsDenied(f'robots.txt does not permit this URL: {url}')
        if self.last_request is not None:
            time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        self.driver.get(url)
        check_access(self.driver)
        pakistan_url(self.driver.current_url)
        for entry in self.driver.get_log('performance'):
            event = json.loads(entry['message'])['message']
            params = event.get('params', {})
            if event.get('method') != 'Network.responseReceived' or params.get('type') != 'Document':
                continue
            response = params['response']
            response_url = response.get('url', '')
            if urlsplit(response_url).hostname not in {'www.daraz.pk', 'daraz.pk'}:
                continue
            status = int(response['status'])
            self.responses.append({'url': response_url, 'status': status})
            if status in (401, 403, 429):
                raise AccessBlocked(f'HTTP {status} at {response_url}; run stopped')
            if status >= 400:
                raise RuntimeError(f'HTTP {status} at {response_url}')
        check_access(self.driver)
