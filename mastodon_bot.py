import asyncio
from mastodon import Mastodon
import os
import requests
from tempfile import NamedTemporaryFile
from PIL import Image  # Correct import for PIL.Image

from google import genai

import tempfile
import cv2

instances = {
    'instance1': {'access_token': 'your_token', 'api_base_url': 'https://instance1.exempel.org'},
    'instance2': {'access_token': 'your_token', 'api_base_url': 'https://instance2.exempel.org'},
    'instance3': {'access_token': 'your_token', 'api_base_url': 'https://instance3.exempel.org'},
    'instance4': {'access_token': 'your_token', 'api_base_url': 'https://instance4.exempel.org'}
}



def post_tweet(mastodon, message, username, instance_name):
    # Veröffentliche den Tweet auf Mastodon
    message_cut = truncate_text(message)

    if instance_name == "instance1":
        try:
            if any(substring in username for substring in ["Servicemeldung", "SBahnBerlin"]):
                mastodon.status_post(message_cut, visibility='public')
            else:
                mastodon.status_post(message_cut, visibility='unlisted')
        except Exception as e:
            print(f"Fehler beim Posten auf instance1: {e}")
    elif instance_name == "instance2":
        try:
            if any(substring in username for substring in ["Servicemeldung", "DB", "VBB", "bpol"]):
                mastodon.status_post(message_cut, visibility='public')
            else:
                mastodon.status_post(message_cut, visibility='unlisted')
        except Exception as e:
            print(f"Fehler beim Posten auf instance2: {e}")
    elif instance_name == "instance3":
        try:
            if any(substring in username for substring in ["Servicemeldung", "BVG"]):
                mastodon.status_post(message_cut, visibility='public')
            else:
                mastodon.status_post(message_cut, visibility='unlisted')
        except Exception as e:
            print(f"Fehler beim Posten auf instance3: {e}")
    elif instance_name == "instance4":
        try:
            if any(substring in username for substring in ["Servicemeldung", "VIZ"]):
                mastodon.status_post(message_cut, visibility='public')
        except Exception as e:
            print(f"Fehler beim Posten auf instance4: {e}")
    else:
        print("Instanz nicht gefunden")

    
    
def truncate_text(text):
    # Ersetze alle '@' Zeichen durch '#'
    text = text.replace('@', '#')
    # Entferne doppelte '#'
    text = text.replace('##', '#')
    text = text.replace('https://x.com', 'x')
    # Prüfe, ob der Text länger als 500 Zeichen ist
    if len(text) > 500:
        return text[:500]
    else:
        return text


    
def extract_hashtags(content, username):
    # Entferne "@"-Symbol aus dem Benutzernamen, falls vorhanden
    if username.startswith("@"):
        username = username[1:]
    
    # Suche nach Hashtags im Inhalt
    hashtags = ""
    words = content.split()
    for word in words:
        if word.startswith("#") and len(word) > 1:
            word = word.replace('.', '')
            word = word.replace(',', '')
            word = word.replace(':', '')
            word = word.replace(';', '')
            hashtag_with_username = f"{word}_{username}"
            hashtags += " " + hashtag_with_username
            
    return hashtags

def upload_media(mastodon, images, username):
    media_ids = []
    
    for image_link in images:
        # Bild herunterladen und temporär speichern
        with NamedTemporaryFile(delete=False) as tmp_file:
            response = requests.get(image_link)
            tmp_file.write(response.content)
            image_path = tmp_file.name
       
        # Upscaling des Bildes mit OpenCV
        image = cv2.imread(image_path)
        
        # Konvertiere das Bild in den erforderlichen Datentyp
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        
        upscaled_image = cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR)

        # Kontrastanpassung
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        image_contrast = clahe.apply(upscaled_image)

        # Schärfen
        image_sharpened = cv2.GaussianBlur(image_contrast, (0,0), 3)
        image_sharpened = cv2.addWeighted(image_contrast, 1.5, image_sharpened, -0.5, 0)

        # Speichern des hochskalierten und bearbeiteten Bildes im temporären Ordner
        tmp_upscaled_path = os.path.join(os.path.dirname(image_path), 'upscaled_image.jpg')
        cv2.imwrite(tmp_upscaled_path, image_sharpened)

        client = genai.Client()  # Ensure the client is initialized correctly

        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=[
                'Bitte genieriere mir für das Bild einen Alternativ Text (auch Alt-text oder Bildbeschreibung genannt). Bitte sehr umfangreich, aber maximal 1500 Zeichen. Bitte antworte ausschliesslich mit dem Alternativ Text als Antwort.',
                Image.open(tmp_upscaled_path)
            ]
        )

        # Textextraktion aus dem Bild
        image_description = response.text
        
        if len(image_description) > 1500:
            image_description = image_description[:1500]
    
        if "VIZ" in username:
            # Media-Post mit Bildbeschreibung durchführen
            try:
                with open(image_path, 'rb') as image_file:
                    media_info = mastodon.media_post(image_file, description=image_description, mime_type='image/jpeg')
                    media_ids.append(media_info['id'])
            except Exception as e:
                print(f"Fehler beim Media-Post für VIZ: {e}")
        else:
            # Media-Post mit Bildbeschreibung durchführen
            try:
                with open(image_path, 'rb') as image_file:
                    media_info = mastodon.media_post(image_file, description=image_description, mime_type='image/jpeg')
                    media_ids.append(media_info['id'])
            except Exception as e:
                print(f"Fehler beim Media-Post: {e}")

    
        # Temporäre Datei löschen
        os.unlink(image_path)
        os.unlink(tmp_upscaled_path)
    
    return media_ids


def post_tweet_with_images(mastodon, message, images, username, instance_name):
    try:
        media_ids = upload_media(mastodon, images, username)
    except Exception as e:
        print(f"Fehler beim Hochladen der Medien: {e}")
        return

    try:
        message_cut = truncate_text(message)
    except Exception as e:
        print(f"Fehler beim Kürzen der Nachricht: {e}")
        return

    try:
        if instance_name == "instance1":
            try:
                if "SBahnBerlin" in username:
                    mastodon.status_post(message_cut, media_ids=media_ids, visibility='public')
                else:
                    mastodon.status_post(message_cut, media_ids=media_ids, visibility='unlisted')
            except Exception as e:
                print(f"Fehler beim Posten auf instance1: {e}")
        elif instance_name == "instance2":
            try:
                mastodon.status_post(message_cut, media_ids=media_ids, visibility='unlisted')
            except Exception as e:
                print(f"Fehler beim Posten auf instance2: {e}")
        elif instance_name == "instance3":
            try:
                if "BVG" in username:
                    mastodon.status_post(message_cut, media_ids=media_ids, visibility='public')
                else:
                    mastodon.status_post(message_cut, media_ids=media_ids, visibility='unlisted')
            except Exception as e:
                print(f"Fehler beim Posten auf instance3: {e}")
        elif instance_name == "instance4":
            try:
                if "VIZ" in username:
                    mastodon.status_post(message_cut, media_ids=media_ids, visibility='public')
            except Exception as e:
                print(f"Fehler beim Posten auf instance4: {e}")
        else:
            print("Instanz nicht gefunden")
    except Exception as e:
        print(f"Allgemeiner Fehler: {e}")

        


def main(new_tweets):
    for instance_name, instance in instances.items():
        try:
            access_token = instance['access_token']
            api_base_url = instance['api_base_url']
        except KeyError as e:
            print(f"Fehler beim Zugriff auf die Instanz-Parameter für {instance_name}: {e}")
            continue

        try:
            mastodon = Mastodon(
                access_token=access_token,
                api_base_url=api_base_url
            )

            for n, tweet in enumerate(new_tweets, start=1):
                user = tweet['user']
                username = tweet['username']
                content = tweet['content']
                posted_time = tweet['posted_time']
                var_href = tweet['var_href']

                images = tweet['images']
                extern_urls = tweet['extern_urls']
                images_as_string = tweet['images_as_string']
                extern_urls_as_string = tweet['extern_urls_as_string']

                hashtags = extract_hashtags(content, username)
                message = f"#{username}:\n\n{content}\n\n#öpnv_berlin_bot\n\nsrc: {var_href}\n{extern_urls_as_string}\n{posted_time}"

                if not images:
                    post_tweet(mastodon, message, username, instance_name)
                else:
                    post_tweet_with_images(mastodon, message, images, username, instance_name)

        except Exception as e:
            print(f"Fehler beim Erstellen des Mastodon-Objekts für {instance_name}: {e}")
            continue
        


# Hauptprogramm (z.B. wo der Twitter-Bot aufgerufen wird)
if __name__ == "__main__":
    # Example usage of main function
    new_tweets = []  # Placeholder for new tweets
    asyncio.run(main(new_tweets))
