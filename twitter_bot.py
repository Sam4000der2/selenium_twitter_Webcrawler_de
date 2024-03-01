import telegram_bot
import mastodon_bot
import time
import os
import asyncio
import logging
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from dateutil.parser import parse

#Zum aufrufen von nicht öffentlich sichtbaren Twitterseiten werden die gespeicherten Cookies von der Twitteranmeldung benötigt. Natürlich Optional
firefox_profile_path = "C:\\Users\\YOUR_USER\\AppData\\Roaming\\Mozilla\\Firefox\\Profiles\\YOUR_PROFILE.default-release"
#firefox_profile_path = "/home/YOUR_USER/.mozilla/firefox/YOUR_PROFILE.default-release"

twitter_link = "https://twitter.com/i/lists/1741534129215172901"

#filename = "existing_tweets.txt"

firefox_options = Options()
firefox_options.headless = True   # Öffnet den Browser sichtbar für den Benutzer

#Falls du ohne einloggen Twitter crawlen willst, bitte ausklammern:
firefox_profile = webdriver.FirefoxProfile(firefox_profile_path)
firefox_options.profile = firefox_profile


# Konfiguriere das Logging
logging.basicConfig(filename='twitter_bot.log', level=logging.ERROR)

#selenium erzeugt andauernd eine Arbeitskopie des Firefox Profils, die Funktion löscht diese wieder. Sonst wird der Speicherplatz schnell knapp.
#Falls ohne Einloggen bei Twitter gearbeitet werden will, kann diese Funktion samt Profil Option ausgeklammert werden.
def delete_temp_files():
    folder_path = '/tmp'
    for folder_name in os.listdir(folder_path):
        if folder_name.startswith('rust_mozprofile') or folder_name.startswith('tmp'):
            folder_full_path = os.path.join(folder_path, folder_name)
            try:
                os.rmdir(folder_full_path)
                print(f"Deleted folder: {folder_full_path}")
            except Exception as e:
                print(f"Error deleting folder {folder_full_path}: {e}")

def find_all_tweets(driver):
    """Finds all tweets from the page"""
    try:
        tweets = driver.find_elements(By.CSS_SELECTOR, '[data-testid="tweet"]')
        time.sleep(15)
        tweets = driver.find_elements(By.CSS_SELECTOR, '[data-testid="tweet"]')
        tweet_data = []
        for i, tweet in enumerate(tweets):
            tweet_parts = tweet.text.split("\n")
            
            user = tweet_parts[0]  # Der Benutzername ist der erste Teil des ersten Zeileninhalts
            username = tweet_parts[1]  # Der User ist der erste Teil des Benutzernamens

            content_element = tweet.find_element(By.CSS_SELECTOR, 'div[lang]')
            content = content_element.text
            
            replies_element = tweet.find_element(By.CSS_SELECTOR, '[data-testid="reply"]')
            replies = replies_element.text
            
            anchor = tweet.find_element(By.CSS_SELECTOR, "a[aria-label][dir]")
            var_href = anchor.get_attribute("href")
            
            timestamp = tweet.find_element(By.TAG_NAME, "time").get_attribute("datetime")
            
           # Zeitstempel parsen
            posted_time_utc = parse(timestamp)

            # Prüfen, ob die Zeitzone Sommerzeit (DST) ist
            is_dst = bool(datetime.datetime.now().astimezone().dst())

            # Lokale Zeitzone festlegen (hier als Beispiel Berlin)
            local_timezone = datetime.timezone(datetime.timedelta(hours=2 if is_dst else 1))  # MESZ (UTC+2) oder MEZ (UTC+1)

            # Zeitstempel in lokale Zeitzone konvertieren
            posted_time_local = posted_time_utc.astimezone(local_timezone)

            # Gewünschtes Format für Datum und Uhrzeit definieren
            desired_format = "%d.%m.%Y %H:%M"

            # Zeitstempel im gewünschten Format ausgeben
            posted_time = posted_time_local.strftime(desired_format)
                       
            image_element = tweet.find_elements(By.CSS_SELECTOR,'div[data-testid="tweetPhoto"]')
            images = []
            
            for image_div in image_element:
                href = image_div.find_element(By.TAG_NAME,
                                              "img").get_attribute("src")
                images.append(href)
                href = href.replace("&name=small", "")
        
            extern_url_elements = tweet.find_elements(By.CSS_SELECTOR, '[data-testid="card.wrapper"]')
            extern_urls = []
            for extern_url_element in extern_url_elements:
                href_0 = extern_url_element.find_element(By.TAG_NAME, 'a')
                href = href_0.get_attribute("href")
                extern_urls.append(href)
            
            # Regulärer Ausdruck, um URLs zu erkennen
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
        #Überprüfe, ob die Datei existiert und lese vorhandene Tweets
        if not os.path.exists(filename):
            # Wenn die Datei nicht existiert, erstelle sie
            open(filename, "a").close()  # Erstelle die Datei, falls sie nicht existiert

       # Öffne die Datei im Lese-Modus, um vorhandene Links zu überprüfen
        with open(filename, "r") as file:
            existing_tweets = file.read().splitlines()

        new_tweets = []
        # Überprüfe jeden Tweet in den Daten
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


            # Überprüfe, ob der Link bereits in den vorhandenen Tweets enthalten ist
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

                # Wenn nicht, schreibe den Link in die Datei
                with open(filename, "a") as file:
                    file.write(var_href + "\n")

        return new_tweets
    except Exception as ex:
        logging.error(f"Error checking and writing tweets: {ex}")
        return []


def trim_existing_tweets_file():
    try:
        # Öffne die Datei im Lese-Modus, um die Anzahl der Zeilen zu überprüfen
        with open(filename, "r") as file:
            lines = file.readlines()
        
        # Überprüfe die Anzahl der Zeilen
        num_lines = len(lines)
        
        if num_lines > 100:
            # Wenn mehr als 100 Zeilen vorhanden sind, lösche die ältesten 50 Zeilen
            with open(filename, "w") as file:
                file.writelines(lines[50:])
            
            #print("Trimmed existing_tweets.txt file.")
        #else:
            #print("No trimming needed for existing_tweets.txt file.")
    except Exception as ex:
        logging.error(f"Error trimming existing_tweets.txt file: {ex}")


async def main():
    while True:
        try:
            #Falls du ohne einloggen Twitter crawlen willst:
            #driver = webdriver.Firefox(options=firefox_options)
            
            driver = webdriver.Firefox(options=firefox_options, firefox_profile=firefox_profile_path)
            driver.get(twitter_link)
            tweet_data = find_all_tweets(driver)
            new_tweets = check_and_write_tweets(tweet_data)

            #print(new_tweets)

            # Aufruf der Funktion in telegram_bot.py
            await telegram_bot.main(new_tweets)

            # Aufruf der Funktion in mastodon_bot.py
            mastodon_bot.main(new_tweets)

            # Browser schließen
            driver.quit()

            trim_existing_tweets_file()

            #Falls du ohne einloggen Twitter crawlen willst, brauchst du die nicht mehr
            delete_temp_files()

            # Wartezeit, bevor die nächste Iteration beginnt
            await asyncio.sleep(60)   # Wartezeit in Sekunden (hier: 1 Minuten)

        except Exception as e:
            logging.error(f"An error occurred: {e}")
            # Fehlerbehandlung, z.B. Neustart des Browsers oder Wartezeit vor erneutem Versuch
            time.sleep(60)  # Wartezeit vor erneutem Versuch in Sekunden (hier: 1 Minute)
        
        
if __name__ == '__main__':
    #main()
    asyncio.run(main())



#Weitere css Selektoren findet ihr hier: https://github.com/shaikhsajid1111/twitter-scraper-selenium/blob/main/twitter_scraper_selenium/element_finder.py
