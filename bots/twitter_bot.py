import sys
from pathlib import Path

if __package__ in {None, ""}:
    _PROJECT_ROOT = Path(__file__).resolve().parents[1]
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))

from modules import telegram_bot_module as telegram_bot
from modules import mastodon_bot_module as mastodon_bot
import time
import re
import asyncio
import os
import logging
from logging.handlers import WatchedFileHandler
from typing import Optional, Tuple
from urllib.parse import urljoin, urlparse
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.firefox.firefox_profile import FirefoxProfile  # Neuer Import
from selenium.common.exceptions import StaleElementReferenceException
from dateutil.parser import parse
import pytz
import requests  # Neuer Import für URL-Erweiterung
from modules import state_store_module as state_store
from modules.url_safety_module import validate_outbound_url
from modules.paths_module import LOG_FILE, LOG_LEVEL

#print("Imports successful")

DEFAULT_TWITTER_LIST_URL = "https://x.com/i/lists/1901917316708778158"
DEFAULT_GECKODRIVER_PATH = "/usr/local/bin/geckodriver"

# Runtime-Konfiguration aus ENV (Legacy-Namen als Fallback).
firefox_profile_path = (
    os.environ.get("TWITTER_FIREFOX_PROFILE_PATH")
    or os.environ.get("FIREFOX_PROFILE_PATH")
    or ""
).strip()
geckodriver_path = (
    os.environ.get("TWITTER_GECKODRIVER_PATH")
    or os.environ.get("GECKODRIVER_PATH")
    or DEFAULT_GECKODRIVER_PATH
).strip()
twitter_link = (
    os.environ.get("TWITTER_LIST_URL")
    or os.environ.get("TWITTER_LINK")
    or DEFAULT_TWITTER_LIST_URL
).strip()

HISTORY_LIMIT = 100
HISTORY_TRIM_TO = 50

# Logging configuration
logging.basicConfig(
    handlers=[WatchedFileHandler(LOG_FILE)],
    level=LOG_LEVEL,
    force=True,
)
for _noisy_logger in ("httpx", "httpcore", "urllib3", "telegram"):
    logging.getLogger(_noisy_logger).setLevel(logging.WARNING)
#print("Logging configured")

# Set Firefox options
try:
    firefox_options = Options()
except Exception as e:
    logging.error(f"Fehler bei firefox_options = Options: {e}")
try:
    firefox_options.add_argument("--headless")
except Exception as e:
    logging.error(f"twitter_bot: Fehler bei firefox_options.add_argument headless: {e}")

# Optionales Firefox-Profil (für eingeloggte X-Session/Cookies).
if firefox_profile_path:
    try:
        firefox_profile = FirefoxProfile(firefox_profile_path)
        firefox_options.profile = firefox_profile
    except Exception as e:
        logging.warning(
            f"twitter_bot: Firefox-Profil konnte nicht gesetzt werden "
            f"({firefox_profile_path}): {e}"
        )
else:
    logging.info("twitter_bot: Kein Firefox-Profil konfiguriert, starte ohne Profil.")

def expand_short_urls(urls):
    """
    Entfernt Hochkommata und prüft jede URL mittels eines HEAD-Requests.
    Falls es sich um einen Kurzlink handelt oder die URL weitergeleitet wird, wird die finale URL zurückgegeben.
    Nur funktionierende URLs (Status 2xx-3xx) werden in die Rückgabeliste aufgenommen.
    """
    expanded_urls = []
    seen: set[str] = set()
    headers = {"User-Agent": "Mozilla/5.0 (compatible; bot/1.0)"}
    redirect_statuses = {301, 302, 303, 307, 308}
    max_redirects = 5
    for raw in urls:
        url = normalize_url(raw)
        if not url or url in seen:
            continue
        seen.add(url)
        is_safe_url, reason = validate_outbound_url(url, allowed_schemes=("https",))
        if not is_safe_url:
            logging.warning(f"twitter_bot: URL aus Sicherheitsgründen verworfen ({reason}): {url}")
            continue

        current_url = url
        use_get = False
        response = None
        is_blocked_redirect = False
        final_status = None
        try:
            for _ in range(max_redirects + 1):
                if response is not None:
                    response.close()
                    response = None

                if use_get:
                    response = requests.get(
                        current_url,
                        allow_redirects=False,
                        timeout=8,
                        headers=headers,
                        stream=True,
                    )
                else:
                    response = requests.head(
                        current_url,
                        allow_redirects=False,
                        timeout=5,
                        headers=headers,
                    )

                final_status = getattr(response, "status_code", None)
                status_code = final_status if isinstance(final_status, int) else None
                if status_code in redirect_statuses:
                    location = (response.headers.get("Location") or "").strip()
                    if not location:
                        final_status = "redirect-without-location"
                        break
                    next_url = normalize_url(urljoin(current_url, location))
                    is_safe_next, next_reason = validate_outbound_url(
                        next_url,
                        allowed_schemes=("https",),
                    )
                    if not is_safe_next:
                        logging.warning(
                            f"twitter_bot: Redirect-Ziel aus Sicherheitsgründen verworfen "
                            f"({next_reason}): {next_url}"
                        )
                        is_blocked_redirect = True
                        break
                    current_url = next_url
                    use_get = False
                    continue

                status_ok = isinstance(status_code, int) and 200 <= status_code < 400
                should_retry_get = (
                    (not status_ok)
                    and (not use_get)
                    and (status_code not in (429,))
                )
                if should_retry_get:
                    use_get = True
                    continue

                if status_ok:
                    expanded_urls.append(current_url)
                break

            else:
                final_status = "too-many-redirects"

            if is_blocked_redirect:
                continue

            is_valid_result = isinstance(final_status, int) and 200 <= final_status < 400
            if not is_valid_result:
                log_fn = logging.warning
                if isinstance(final_status, int) and final_status >= 500:
                    log_fn = logging.error
                log_fn(
                    f"twitter_bot: Überprüfung URL {url} liefert ungültigen Status {final_status}"
                )
        except Exception as ex:
            logging.error(f"twitter_bot: Fehler beim Überprüfen der URL {url}: {ex}")
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
    # Nach Auflösung erneut normalisieren und deduplizieren
    return dedupe_preserve_order([normalize_url(u) for u in expanded_urls])


def normalize_url(url: str) -> str:
    """
    Stellt sicher, dass URLs bereinigt werden und ein Schema haben (default https://).
    Entfernt typische Satzzeichen/Quotes am Rand.
    """
    cleaned = (url or "").strip()
    cleaned = cleaned.strip(".,;:!?()[]{}<>\"'…")
    cleaned = cleaned.replace("%E2%80%A6", "")
    cleaned = re.sub(r"^https:/(?!/)", "https://", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^http:/(?!/)", "http://", cleaned, flags=re.IGNORECASE)
    if not cleaned:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", cleaned):
        cleaned = cleaned.lstrip("/")
        cleaned = f"https://{cleaned}"
    if cleaned.startswith("http://"):
        cleaned = "https://" + cleaned[len("http://") :]
    return cleaned


def dedupe_preserve_order(items):
    seen = set()
    result = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


# Erfasst http/https, www., sowie nackte Domains mit optionalem Pfad
TEXT_URL_RX = re.compile(
    r"(?P<url>(?:https?://|www\.)[^\s]+|(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s]*)?)",
    re.IGNORECASE
)


def extract_urls_from_text(text: str) -> tuple[str, list[str]]:
    """
    Entfernt alle erkannten Links aus dem Text und gibt den bereinigten Text + URL-Liste zurück.
    Ergänzt fehlendes Schema mit https://.
    """
    found: list[str] = []

    def _replacer(match):
        raw = match.group("url")
        trimmed_raw = raw.rstrip(".,;:!?")
        if trimmed_raw.endswith(("…", "...", "%E2%80%A6")):
            # Link ist im Tweet-Text abgeschnitten (Ellipsis) -> überspringen
            return ""

        normalized = normalize_url(trimmed_raw)
        if normalized:
            found.append(normalized)
        return ""

    cleaned = TEXT_URL_RX.sub(_replacer, text or "")
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned, dedupe_preserve_order(found)


HASHTAG_HOSTS = {
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
    "m.twitter.com",
}


def is_hashtag_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        path = (parsed.path or "").lower()
        return host in HASHTAG_HOSTS and path.startswith("/hashtag/")
    except Exception:
        return False


def pick_username(user: str, username: str) -> tuple[str, str]:
    """
    Stellt sicher, dass als username der Eintrag ohne Leerzeichen genutzt wird.
    Wenn das zweite Feld (username) Leerzeichen enthält, aber das erste (user) nicht,
    werden die beiden getauscht. Bleibt bei der Originalreihenfolge, wenn beide gültig sind
    oder keiner ohne Leerzeichen gefunden wird.
    """
    def has_space(s: str) -> bool:
        return bool(re.search(r"\s", s or ""))

    if username and not has_space(username):
        return user, username
    if user and not has_space(user):
        return username, user
    return user, username


def extract_user_fields(tweet) -> Tuple[str, str]:
    """
    Liest Anzeigename + Handle robuster aus dem User-Block.
    """
    try:
        name_block = tweet.find_element(By.CSS_SELECTOR, "[data-testid='User-Name']")
        parts = name_block.text.split("\n")
        display = parts[0] if parts else ""
        handle = ""
        for part in parts[1:]:
            if part.startswith("@"):
                handle = part.lstrip("@")
                break
        if not handle and len(parts) > 1:
            handle = parts[1]
        return pick_username(display, handle)
    except StaleElementReferenceException:
        # Weiter oben wird erneut versucht; hier bewusst durchreichen
        raise
    except Exception as ex:
        logging.warning(f"twitter_bot: Konnte User-Feld nicht auslesen: {ex}")
        return "", ""


def extract_status_url(tweet) -> str:
    """
    Holt die Status-URL aus dem Link-Block (stabiler als generische Anchor-Suche).
    """
    try:
        anchor = tweet.find_element(By.CSS_SELECTOR, "a[href*='/status/'][role='link']")
        return anchor.get_attribute("href") or ""
    except StaleElementReferenceException:
        raise
    except Exception:
        try:
            anchor = tweet.find_element(By.CSS_SELECTOR, "a[aria-label][dir]")
            return anchor.get_attribute("href") or ""
        except Exception:
            return ""


def extract_content(tweet) -> str:
    selectors = [
        '[data-testid="tweetText"]',
        'div[lang]'
    ]
    for selector in selectors:
        try:
            elements = tweet.find_elements(By.CSS_SELECTOR, selector)
            texts = [el.text for el in elements if el.text]
            if texts:
                return "\n".join(texts).strip()
        except StaleElementReferenceException:
            raise
        except Exception:
            continue
    return ""


def extract_text_link_hrefs(tweet, status_url: str) -> list:
    """
    Liest echte hrefs aus dem Textblock aus (verhindert gekürzte/ellipsis-Links).
    Filtert den eigenen Status-Link heraus.
    """
    try:
        anchors = tweet.find_elements(By.CSS_SELECTOR, '[data-testid="tweetText"] a[href]')
        urls = []
        for anchor in anchors:
            href = anchor.get_attribute("href") or ""
            if not href:
                continue
            if status_url and href.startswith(status_url):
                continue
            if href.startswith("http://") or href.startswith("https://"):
                urls.append(href)
        return urls
    except StaleElementReferenceException:
        raise
    except Exception as ex:
        logging.warning(f"twitter_bot: Konnte Text-Link-hrefs nicht auslesen: {ex}")
        return []


def extract_images(tweet) -> list:
    try:
        images = []
        image_elements = tweet.find_elements(By.CSS_SELECTOR, "div[data-testid='tweetPhoto'] img")
        for img in image_elements:
            href = img.get_attribute("src") or ""
            if href:
                images.append(href)
        return images
    except StaleElementReferenceException:
        raise
    except Exception as ex:
        logging.error(f"twitter_bot: Error finding image elements: {ex}")
        return []


def extract_videos(tweet) -> list:
    """
    Sammelt Video-Quellen aus dem Tweet (sofern vorhanden).
    """
    try:
        videos = []
        # Direktes <video>-Element
        for vid in tweet.find_elements(By.TAG_NAME, "video"):
            src = vid.get_attribute("src") or ""
            if src:
                videos.append(src)
        # Fallback über Container
        for video_div in tweet.find_elements(By.CSS_SELECTOR, "div[data-testid='videoPlayer']"):
            try:
                vid = video_div.find_element(By.TAG_NAME, "video")
                src = vid.get_attribute("src") or ""
                if src:
                    videos.append(src)
            except Exception:
                continue
        return videos
    except StaleElementReferenceException:
        raise
    except Exception as ex:
        logging.error(f"twitter_bot: Error finding video elements: {ex}")
        return []


def extract_external_urls(tweet) -> list:
    extern_urls = []
    try:
        extern_url_elements = tweet.find_elements(By.CSS_SELECTOR, '[data-testid="card.wrapper"]')
        for extern_url_element in extern_url_elements:
            try:
                href_0 = extern_url_element.find_element(By.TAG_NAME, 'a')
                href = href_0.get_attribute("href")
                if href:
                    extern_urls.append(href)
            except Exception:
                continue
    except StaleElementReferenceException:
        raise
    except Exception as ex:
        logging.error(f"twitter_bot: Error finding external URL elements: {ex}")
    return extern_urls


def collect_tweet_data(tweet, index: int) -> Optional[dict]:
    """
    Extrahiert alle benötigten Felder aus einem Tweet-Element.
    Wirft bei StaleElementReferenceException nach oben, damit der Aufrufer neu laden kann.
    """
    try:
        user, username = extract_user_fields(tweet)
        content = extract_content(tweet)

        var_href = extract_status_url(tweet)

        text_link_hrefs = extract_text_link_hrefs(tweet, var_href)

        try:
            timestamp = tweet.find_element(By.TAG_NAME, "time").get_attribute("datetime")
            posted_time_utc = parse(timestamp).replace(tzinfo=pytz.utc)
            local_timezone = pytz.timezone('Europe/Berlin')
            posted_time_local = posted_time_utc.astimezone(local_timezone)
            desired_format = "%d.%m.%Y %H:%M"
            posted_time = posted_time_local.strftime(desired_format)
        except StaleElementReferenceException:
            raise
        except Exception as ex:
            logging.warning(f"twitter_bot: Error finding timestamp: {ex}")
            posted_time = ""

        images = extract_images(tweet)
        videos = extract_videos(tweet)
        extern_urls = extract_external_urls(tweet)
        if videos:
            extern_urls.extend(videos)

        # Alle Links aus dem Text entfernen (auch ohne Schema) und zu extern_urls verschieben
        content, content_urls = extract_urls_from_text(content)
        extern_urls.extend(text_link_hrefs)
        extern_urls.extend(content_urls)

        # Links bereinigen, Schema ergänzen und deduplizieren
        extern_urls = dedupe_preserve_order([normalize_url(u) for u in extern_urls])
        extern_urls = [u for u in extern_urls if not is_hashtag_url(u)]

        # Neuer Aufruf: Erweitere Kurzlinks in extern_urls
        extern_urls = expand_short_urls(extern_urls)
        # Endgültig normalisieren + deduplizieren, falls expand_short_urls Duplikate erzeugt
        extern_urls = dedupe_preserve_order([normalize_url(u) for u in extern_urls])
        extern_urls = [u for u in extern_urls if not is_hashtag_url(u)]
        videos_as_string = str(videos).replace("[]", "").replace("'", "") if videos else ""

        if not images:
            images_as_string = ""
        else:
            images_as_string = str(images).replace("[]", "").replace("'", "")

        if not extern_urls:
            extern_urls_as_string = ""
        else:
            extern_urls_as_string = str(extern_urls).replace("[]", "").replace("'", "")

        return {
            "user": user,
            "username": username,
            "content": content,
            "posted_time": posted_time,
            "var_href": var_href,
            "images": images,
            "videos": videos,
            "extern_urls": extern_urls,
            "images_as_string": images_as_string,
            "videos_as_string": videos_as_string,
            "extern_urls_as_string": extern_urls_as_string
        }
    except StaleElementReferenceException:
        raise
    except Exception as ex:
        logging.error(f"twitter_bot: Unerwarteter Fehler bei Tweet #{index}: {ex}")
        return None


def find_all_tweets(driver):
    """Finds all tweets from the page with a retry on stale elements."""
    try:
        try:
            tweets = WebDriverWait(driver, 20).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, '[data-testid="tweet"]'))
            )
        except Exception:
            time.sleep(10)
            tweets = driver.find_elements(By.CSS_SELECTOR, '[data-testid="tweet"]')
        tweets = list(tweets or [])
        tweet_data = []
        for i in range(len(tweets)):
            for attempt in range(2):
                try:
                    tweet = tweets[i] if i < len(tweets) else None
                    if tweet is None:
                        raise StaleElementReferenceException("Tweet element missing after refresh")
                    data = collect_tweet_data(tweet, i)
                    if data:
                        tweet_data.append(data)
                    break
                except StaleElementReferenceException as ex:
                    logging.warning(f"twitter_bot: Stale tweet element #{i} (Versuch {attempt + 1}), versuche erneut: {ex}")
                    time.sleep(0.3)
                    tweets = driver.find_elements(By.CSS_SELECTOR, '[data-testid="tweet"]')
            else:
                logging.warning(f"twitter_bot: Tweet #{i} nach 2 Versuchen übersprungen (stale).")

        return tweet_data
    except Exception as ex:
        logging.error(f"twitter_bot: Error finding tweets: {ex}")
        return []

def _normalize_user_key(tweet: dict) -> str:
    username = (tweet.get("username") or "").strip()
    user_display = (tweet.get("user") or "").strip()
    return username or user_display or "unknown"


def _load_history_map() -> dict[str, list[str]]:
    raw = state_store.load_nitter_history()
    history_map: dict[str, list[str]] = {}
    if isinstance(raw, dict):
        for user, urls in raw.items():
            if not isinstance(urls, list):
                continue
            cleaned = [(u or "").strip() for u in urls if isinstance(u, str) and (u or "").strip()]
            if cleaned:
                history_map[str(user)] = cleaned[-HISTORY_LIMIT:]
    return history_map


def _save_history_map(history_map: dict[str, list[str]]):
    trimmed_map: dict[str, list[str]] = {}
    for user, urls in (history_map or {}).items():
        if not isinstance(urls, list):
            continue
        trimmed_map[user] = urls[-HISTORY_TRIM_TO:]
    state_store.save_nitter_history(trimmed_map)


def check_and_write_tweets(tweet_data):
    try:
        history_map = _load_history_map()
        seen = {u for urls in history_map.values() for u in urls}

        new_tweets = []
        for tweet in tweet_data or []:
            user = tweet['user']
            username = tweet['username']
            content = tweet['content']
            posted_time = tweet['posted_time']
            var_href = tweet['var_href']
            images = tweet['images']
            videos = tweet.get('videos', [])
            extern_urls = tweet['extern_urls']
            images_as_string = tweet['images_as_string']
            videos_as_string = tweet.get('videos_as_string', "")
            extern_urls_as_string = tweet['extern_urls_as_string']

            user_key = _normalize_user_key(tweet)

            if var_href not in seen:
                new_tweets.append({
                    "user": user,
                    "username": username,
                    "content": content,
                    "posted_time": posted_time,
                    "var_href": var_href,
                    "images": images,
                    "videos": videos,
                    "extern_urls": extern_urls,
                    "images_as_string": images_as_string,
                    "videos_as_string": videos_as_string,
                    "extern_urls_as_string": extern_urls_as_string
                })

                history_map.setdefault(user_key, []).append(var_href)
                seen.add(var_href)

        _save_history_map(history_map)
        return new_tweets
    except Exception as ex:
        logging.error(f"twitter_bot: Error checking and writing tweets: {ex}")
        return []


def trim_existing_tweets_file():
    try:
        history_map = _load_history_map()
        _save_history_map(history_map)
    except Exception as ex:
        logging.error(f"twitter_bot: Error trimming existing tweets history: {ex}")

async def main():
    #print("Entering main function")
    while True:
        driver = None
        try:
            logging.debug("Starting Firefox WebDriver")
            service = FirefoxService(executable_path=geckodriver_path)
            driver = webdriver.Firefox(service=service, options=firefox_options)
            driver.get(twitter_link)
            logging.debug("Navigated to Twitter link")
            tweet_data = find_all_tweets(driver)
            new_tweets = check_and_write_tweets(tweet_data)

            try:
                await telegram_bot.main(new_tweets)
            except Exception as e:
                logging.error(f"twitter_bot: An error occurred in telegram_bot: {e}")

            try:
                await mastodon_bot.main(new_tweets)
            except Exception as e:
                logging.error(f"twitter_bot: An error occurred in mastodon_bot: {e}")

            trim_existing_tweets_file()

            await asyncio.sleep(60)
        except Exception as e:
            logging.error(f"twitter_bot: An error occurred: {e}")
            if driver:
                driver.quit()
            time.sleep(60)
        finally:
            if driver:
                driver.quit()

if __name__ == '__main__':
    #print("Starting Twitter bot")
    asyncio.run(main())
