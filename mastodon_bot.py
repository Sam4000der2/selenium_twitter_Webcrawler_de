import asyncio
import os
import logging
import requests
from tempfile import NamedTemporaryFile  # nur einmal importieren
from PIL import Image
import io  # new import (if not already present)
import cairosvg  # new import for SVG conversion

import aiohttp
import aiofiles

from mastodon import Mastodon
from google import genai

# Initialize the Google Gemini client with the API key
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

# Configure logging
logging.basicConfig(
    filename='/home/YOURUSER/bots/mastodon_logfile.log',
    level=logging.ERROR,
    format='%(asctime)s %(levelname)s:%(message)s'
)
print("Logging configured")

instances = {
    'instance1': {'access_token': 'your_token', 'api_base_url': 'https://instance1.exempel.org'},
    'instance2': {'access_token': 'your_token', 'api_base_url': 'https://instance2.exempel.org'},
    'instance3': {'access_token': 'your_token', 'api_base_url': 'https://instance3.exempel.org'},
    'instance4': {'access_token': 'your_token', 'api_base_url': 'https://instance4.exempel.org'}
}



print("Instances configured")

def post_tweet(mastodon, message, username, instance_name):
    # Kürze den Nachrichtentext entsprechend den Anforderungen
    message_cut = truncate_text(message)

    try:
        if instance_name == "instance1":
            if any(sub in username for sub in ["Servicemeldung", "SBahnBerlin"]):
                mastodon.status_post(message_cut, visibility='public')
            else:
                mastodon.status_post(message_cut, visibility='unlisted')
        elif instance_name == "instance2":
            if any(sub in username for sub in ["Servicemeldung", "DB", "VBB", "bpol"]):
                mastodon.status_post(message_cut, visibility='public')
            else:
                mastodon.status_post(message_cut, visibility='unlisted')
        elif instance_name == "instance3":
            if any(sub in username for sub in ["Servicemeldung", "BVG"]):
                mastodon.status_post(message_cut, visibility='public')
            else:
                mastodon.status_post(message_cut, visibility='unlisted')
        elif instance_name == "instance4":
            if any(sub in username for sub in ["Servicemeldung", "VIZ"]):
                mastodon.status_post(message_cut, visibility='public')
        else:
            logging.error("Instanz nicht gefunden")
    except Exception as e:
        logging.error(f"Fehler beim Posten auf {instance_name}: {e}")

def truncate_text(text):
    # Ersetze alle '@' durch '#' und entferne doppelte '#'
    text = text.replace('@', '#').replace('##', '#')
    text = text.replace('https://x.com', 'x')
    # Kürze den Text, wenn er länger als 500 Zeichen ist
    return text[:500] if len(text) > 500 else text

def extract_hashtags(content, username):
    # Entferne ein eventuell vorhandenes führendes "@" aus dem Benutzernamen
    if username.startswith("@"):
        username = username[1:]
    hashtags = ""
    for word in content.split():
        if word.startswith("#") and len(word) > 1:
            word = word.replace('.', '').replace(',', '').replace(':', '').replace(';', '')
            hashtags += f" {word}_{username}"
    return hashtags

async def download_image(session, url):
    try:
        async with session.get(url) as response:
            if response.status == 200:
                # Erstelle einen temporären Pfad für das heruntergeladene Bild
                temp_file_path = os.path.join('/tmp', os.path.basename(url))
                async with aiofiles.open(temp_file_path, 'wb') as f:
                    # Warten auf das vollständige Lesen der Bilddaten
                    await f.write(await response.read())
                return temp_file_path
            else:
                logging.error(f"Fehler beim Herunterladen des Bildes: {url}")
                return None
    except Exception as e:
        logging.error(f"Fehler beim Herunterladen des Bildes: {e}")
        return None

async def generate_alt_text(client, image_path):
    try:
        response = client.models.generate_content(
            model='gemini-2.0-flash-exp',
            contents=[
                'Bitte genieriere mir für das Bild einen Alternativ Text (auch Alt-text oder Bildbeschreibung genannt). Bitte sehr umfangreich, aber maximal 1500 Zeichen. Bitte antworte ausschliesslich mit dem Alternativ Text als Antwort.',
                Image.open(image_path)
            ]
        )
        return response.text[:1500]
    except Exception as e:
        logging.error(f"Fehler beim Generieren des Alt-Texts: {e}")
        return ""

def prepare_image_for_upload(orig_image_bytes, ext):
    """
    Convert and process the image so it is in JPG/PNG format,
    resized to max 1280px and compressed below 8MB.
    """
    if ext == '.svg':
        orig_image_bytes = cairosvg.svg2png(bytestring=orig_image_bytes)
    elif ext not in ['.jpg', '.jpeg', '.png']:
        with Image.open(io.BytesIO(orig_image_bytes)) as img:
            output_io = io.BytesIO()
            img.convert('RGB').save(output_io, format='JPEG')
            orig_image_bytes = output_io.getvalue()
    processed_bytes = process_image_for_mastodon(orig_image_bytes)
    return processed_bytes

async def upload_media(mastodon, images, username):
    media_ids = []
    async with aiohttp.ClientSession() as session:
        for image_link in images:
            # Download image if not local
            if os.path.isfile(image_link):
                image_path = image_link
            else:
                image_path = await download_image(session, image_link)
            if image_path:
                try:
                    # Read the original image bytes.
                    async with aiofiles.open(image_path, 'rb') as image_file:
                        orig_image_bytes = await image_file.read()
                    ext = os.path.splitext(image_path)[1].lower()
                    # Process image once for both Gemini and Mastodon.
                    processed_bytes = prepare_image_for_upload(orig_image_bytes, ext)
                    # Write processed bytes to a temporary file.
                    temp_file = NamedTemporaryFile(delete=False, suffix=".jpg")
                    temp_file.write(processed_bytes)
                    temp_file.close()
                    # Generate alt text using the converted image.
                    image_description = await generate_alt_text(client, temp_file.name)
                    # Upload processed image to Mastodon.
                    media_info = await asyncio.to_thread(
                        mastodon.media_post,
                        io.BytesIO(processed_bytes),
                        description=image_description,
                        mime_type='image/jpeg'
                    )
                    media_ids.append(media_info['id'])
                except Exception as e:
                    logging.error(f"Fehler beim Media-Post: {e}")
                finally:
                    # Delete temporary files.
                    if 'temp_file' in locals() and os.path.isfile(temp_file.name):
                        os.remove(temp_file.name)
                    if (not os.path.isfile(image_link)) and os.path.isfile(image_path):
                        os.remove(image_path)
            else:
                logging.error("Kein Bildpfad erhalten, überspringe dieses Bild.")
    return media_ids

async def post_tweet_with_images(mastodon, message, images, username, instance_name):
    try:
        media_ids = await upload_media(mastodon, images, username)
    except Exception as e:
        logging.error(f"Fehler beim Hochladen der Medien: {e}")
        return

    try:
        message_cut = truncate_text(message)
    except Exception as e:
        logging.error(f"Fehler beim Kürzen der Nachricht: {e}")
        return

    try:
        if instance_name == "instance1":
            if "SBahnBerlin" in username:
                await mastodon.status_post(message_cut, media_ids=media_ids, visibility='public')
            else:
                await mastodon.status_post(message_cut, media_ids=media_ids, visibility='unlisted')
        elif instance_name == "instance2":
            await mastodon.status_post(message_cut, media_ids=media_ids, visibility='unlisted')
        elif instance_name == "instance3":
            if "BVG" in username:
                await mastodon.status_post(message_cut, media_ids=media_ids, visibility='public')
            else:
                await mastodon.status_post(message_cut, media_ids=media_ids, visibility='unlisted')
        elif instance_name == "instance4":
            if "VIZ" in username:
                await mastodon.status_post(message_cut, media_ids=media_ids, visibility='public')
            else:
                logging.error("Keine gültige Bedingung für instance4 gefunden.")
        else:
            logging.error("Instanz nicht gefunden")
    except Exception as e:
        logging.error(f"Allgemeiner Fehler beim Posten mit Bildern: {e}")

async def main(new_tweets):
    print("Entering main function")
    # Iteriere über alle konfigurierten Instanzen
    for instance_name, instance in instances.items():
        try:
            access_token = instance['access_token']
            api_base_url = instance['api_base_url']
            print(f"Processing instance: {instance_name}")
        except KeyError as e:
            logging.error(f"Fehler beim Zugriff auf die Instanz-Parameter für {instance_name}: {e}")
            continue

        try:
            mastodon = Mastodon(
                access_token=access_token,
                api_base_url=api_base_url
            )
            print(f"Created Mastodon object for {instance_name}")
        except Exception as e:
            logging.error(f"Fehler beim Erstellen des Mastodon-Objekts für {instance_name}: {e}")
            continue

        for n, tweet in enumerate(new_tweets, start=1):
            user = tweet['user']
            username = tweet['username']
            content = tweet['content']
            posted_time = tweet['posted_time']
            var_href = tweet['var_href']

            images = tweet['images']
            extern_urls = tweet['extern_urls']
            # images_as_string und extern_urls_as_string sind vorhanden, werden hier aber nicht weiterverwendet
            hashtags = extract_hashtags(content, username)
            # Erstelle die Nachricht mit zusätzlichen Informationen
            message = (
                f"#{username}:\n\n{content}\n\n#öpnv_berlin_bot\n\n"
                f"src: {var_href}\n{extern_urls}\n{posted_time}"
            )

            if not images:
                print(f"Posting tweet without images for {username}")
                post_tweet(mastodon, message, username, instance_name)
            else:
                print(f"Posting tweet with images for {username}")
                await post_tweet_with_images(mastodon, message, images, username, instance_name)

    print("Main function completed")

print("Script loaded")
