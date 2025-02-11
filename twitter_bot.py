import telegram_bot
import mastodon_bot
import time
import datetime
import os
import re
import shutil
import asyncio
import logging
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.common.by import By
from dateutil.parser import parse
import pytz

# Firefox Profile Path
firefox_profile_path = "/home/youruser/.mozilla/firefox/j2ylnh5d.twitter/"
geckodriver_path = "/usr/local/bin/geckodriver"

# Twitter Link
twitter_link = "https://x.com/i/lists/1741534129215172901"

# File to store existing tweets
filename = "/home/youruser/bots/existing_tweets.txt"

# Set Firefox options
firefox_options = Options()
firefox_options.add_argument("--headless")
firefox_options.add_argument(f"-profile")
firefox_options.add_argument(firefox_profile_path)

# Logging configuration
logging.basicConfig(filename='/home/youruser/bots/twitter_bot.log', level=logging.ERROR)

def delete_temp_files():
    folder_path = '/tmp'
    for folder_name in os.listdir(folder_path):
        if folder_name.startswith('rust_mozprofile') or folder_name.startswith('tmp'):
            folder_full_path = os.path.join(folder_path, folder_name)
            try:
                shutil.rmtree(folder_full_path)
                print(f"Deleted folder: {folder_full_path}")
            except Exception as e:
                logging.error(f"Error deleting folder {folder_full_path}: {e}")

def find_all_tweets(driver):
    """Finds all tweets from the page"""
    try:
        tweets = driver.find_elements(By.CSS_SELECTOR, '[data-testid="tweet"]')
        time.sleep(15)
        tweets = driver.find_elements(By.CSS_SELECTOR, '[data-testid="tweet"]')
        tweet_data = []
        for i, tweet in enumerate(tweets):
            tweet_parts = tweet.text.split("\n")

            user = tweet_parts[0]
            username = tweet_parts[1]

            content_element = tweet.find_element(By.CSS_SELECTOR, 'div[lang]')
            content = content_element.text

            replies_element = tweet.find_element(By.CSS_SELECTOR, '[data-testid="reply"]')
            replies = replies_element.text

            anchor = tweet.find_element(By.CSS_SELECTOR, "a[aria-label][dir]")
            var_href = anchor.get_attribute("href")

            timestamp = tweet.find_element(By.TAG_NAME, "time").get_attribute("datetime")

            posted_time_utc = parse(timestamp).replace(tzinfo=pytz.utc)
            local_timezone = pytz.timezone('Europe/Berlin')
            posted_time_local = posted_time_utc.astimezone(local_timezone)
            desired_format = "%d.%m.%Y %H:%M"
            posted_time = posted_time_local.strftime(desired_format)
            #print(posted_time)

            image_element = tweet.find_elements(By.CSS_SELECTOR, 'div[data-testid="tweetPhoto"]')
            images = []
            for image_div in image_element:
                href = image_div.find_element(By.TAG_NAME, "img").get_attribute("src")
                parts = href.split("jpg") if "jpg" in href else href.split("png")
                href = parts[0]
                href += "jpg"
                images.append(href)

            extern_url_elements = tweet.find_elements(By.CSS_SELECTOR, '[data-testid="card.wrapper"]')
            extern_urls = []
            for extern_url_element in extern_url_elements:
                href_0 = extern_url_element.find_element(By.TAG_NAME, 'a')
                href = href_0.get_attribute("href")
                extern_urls.append(href)

            url_pattern = re.compile(r"https?://\S+")
            content = url_pattern.sub('', content)

            url_pattern = re.compile(r"http?://\S+")
            content = url_pattern.sub('', content)

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
                "extern_urls": extern_urls,
                "images_as_string": images_as_string,
                "extern_urls_as_string": extern_urls_as_string
            })

        return tweet_data
    except Exception as ex:
        logging.error(f"Error finding tweets: {ex}")
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
            extern_urls = tweet['extern_urls']
            images_as_string = tweet['images_as_string']
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
                    "extern_urls": extern_urls,
                    "images_as_string": images_as_string,
                    "extern_urls_as_string": extern_urls_as_string
                })

                with open(filename, "a") as file:
                    file.write(var_href + "\n")

        return new_tweets
    except Exception as ex:
        logging.error(f"Error checking and writing tweets: {ex}")
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
        logging.error(f"Error trimming existing_tweets.txt file: {ex}")

async def main():
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
                logging.error(f"An error occurred in telegram_bot: {e}")

            try:
                mastodon_bot.main(new_tweets)
            except Exception as e:
                logging.error(f"An error occurred in mastodon_bot: {e}")

            trim_existing_tweets_file()
            delete_temp_files()

            await asyncio.sleep(60)
        except Exception as e:
            logging.error(f"An error occurred: {e}")
            if driver:
                driver.quit()
            time.sleep(60)
        finally:
            if driver:
                driver.quit()

if __name__ == '__main__':
    asyncio.run(main())
