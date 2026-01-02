import telegram_bot
import mastodon_bot
import time
import datetime
import os
import re
import shutil
import asyncio
import logging
from typing import Optional, Tuple
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

#print("Imports successful")

# Firefox Profile Path
firefox_profile_path = "/home/sascha/.mozilla/firefox/rvkkf54c.Twitter"
geckodriver_path = "/usr/local/bin/geckodriver"

# Twitter Link
#twitter_link = "https://x.com/i/lists/1741534129215172901"

#eigene Liste:
twitter_link = "https://x.com/i/lists/1901917316708778158"

# File to store existing tweets
filename = "/home/sascha/bots/existing_tweets.txt"


# Logging configuration
logging.basicConfig(filename='/home/sascha/bots/twitter_bot.log', level=logging.ERROR)
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

# Stattdessen: Erstelle ein FirefoxProfile-Objekt und setze es in den Optionen
try:
    firefox_profile = FirefoxProfile(firefox_profile_path)
    firefox_options.profile = firefox_profile
except Exception as e:
    logging.error(f"twitter_bot: Fehler beim Setzen des Firefox Profil: {e}")

def expand_short_urls(urls):
    """
    Entfernt Hochkommata und prüft jede URL mittels eines HEAD-Requests.
    Falls es sich um einen Kurzlink handelt oder die URL weitergeleitet wird, wird die finale URL zurückgegeben.
    Nur funktionierende URLs (Status 2xx-3xx) werden in die Rückgabeliste aufgenommen.
    """
    expanded_urls = []
    seen: set[str] = set()
    headers = {"User-Agent": "Mozilla/5.0 (compatible; bot/1.0)"}
    for raw in urls:
        url = normalize_url(raw)
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            response = requests.head(url, allow_redirects=True, timeout=5, headers=headers)
            status_ok = response is not None and 200 <= response.status_code < 400

            # Einige Kurz-URL-Dienste (z.B. dlvr.it) blocken HEAD -> Fallback GET
            if (not status_ok) and ("dlvr.it" in url.lower()):
                response = requests.get(url, allow_redirects=True, timeout=8, headers=headers)
                status_ok = response is not None and 200 <= response.status_code < 400

            if status_ok:
                expanded_urls.append(response.url)
            else:
                logging.error(f"twitter_bot: Überprüfung URL {url} liefert ungültigen Status {getattr(response, 'status_code', 'unknown')}")
        except Exception as ex:
            logging.error(f"twitter_bot: Fehler beim Überprüfen der URL {url}: {ex}")
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
    if not cleaned:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", cleaned):
        cleaned = cleaned.lstrip("/")
        cleaned = f"https://{cleaned}"
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
        normalized = normalize_url(raw)
        if normalized:
            found.append(normalized)
        return ""

    cleaned = TEXT_URL_RX.sub(_replacer, text or "")
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned, dedupe_preserve_order(found)


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


def find_all_tweets(driver):
    """Finds all tweets from the page"""
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
        for i, tweet in enumerate(tweets):
            try:
                user, username = extract_user_fields(tweet)
                content = extract_content(tweet)

                try:
                    replies_element = tweet.find_element(By.CSS_SELECTOR, '[data-testid="reply"]')
                    replies = replies_element.text
                except StaleElementReferenceException:
                    raise
                except Exception as ex:
                    logging.warning(f"twitter_bot: Error finding replies element: {ex}")
                    replies = ""

                var_href = extract_status_url(tweet)

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
                extern_urls.extend(content_urls)

                # Links bereinigen, Schema ergänzen und deduplizieren
                extern_urls = dedupe_preserve_order([normalize_url(u) for u in extern_urls])

                # Neuer Aufruf: Erweitere Kurzlinks in extern_urls
                extern_urls = expand_short_urls(extern_urls)
                # Endgültig normalisieren + deduplizieren, falls expand_short_urls Duplikate erzeugt
                extern_urls = dedupe_preserve_order([normalize_url(u) for u in extern_urls])
                videos_as_string = str(videos).replace("[]", "").replace("'", "") if videos else ""

                if not images:
                    images_as_string = ""
                else:
                    images_as_string = str(images).replace("[]", "").replace("'", "")

                if not extern_urls:
                    extern_urls_as_string = ""
                else:
                    extern_urls_as_string = str(extern_urls).replace("[]", "").replace("'", "")

                tweet_data.append({
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
            except StaleElementReferenceException as ex:
                logging.warning(f"twitter_bot: Stale tweet element #{i}, übersprungen: {ex}")
                continue
            except Exception as ex:
                logging.error(f"twitter_bot: Unerwarteter Fehler bei Tweet #{i}: {ex}")
                continue

        return tweet_data
    except Exception as ex:
        logging.error(f"twitter_bot: Error finding tweets: {ex}")
        return []

def check_and_write_tweets(tweet_data):
    try:
        if not os.path.exists(filename):
            open(filename, "a").close()

        with open(filename, "r") as file:
            existing_tweets = file.read().splitlines()

        new_tweets = []
        for n, tweet in enumerate(tweet_data, start=1):
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
            #print(posted_time)

            if var_href not in existing_tweets:
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

                with open(filename, "a") as file:
                    file.write(var_href + "\n")

        return new_tweets
    except Exception as ex:
        logging.error(f"twitter_bot: Error checking and writing tweets: {ex}")
        return []

def trim_existing_tweets_file():
    try:
        with open(filename, "r") as file:
            lines = file.readlines()

        num_lines = len(lines)

        if num_lines > 100:
            with open(filename, "w") as file:
                file.writelines(lines[50:])
    except Exception as ex:
        logging.error(f"twitter_bot: Error trimming existing_tweets.txt file: {ex}")

async def main():
    #print("Entering main function")
    while True:
        driver = None
        try:
            logging.info("Starting Firefox WebDriver")
            service = FirefoxService(executable_path=geckodriver_path)
            driver = webdriver.Firefox(service=service, options=firefox_options)
            driver.get(twitter_link)
            logging.info("Navigated to Twitter link")
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
