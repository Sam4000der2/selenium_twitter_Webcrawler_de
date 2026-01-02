import asyncio
import os
import telegram
import json
import logging

# DATA_FILE = 'data.json'
DATA_FILE = '/home/sascha/bots/data.json'

# Configure logging
logging.basicConfig(
    filename='/home/sascha/bots/twitter_bot.log',
    level=logging.WARNING,
    format='%(asctime)s %(levelname)s:%(message)s'
)

# Secrets aus ENV (global + local):
#   telegram_token  -> Bot Token
#   telegram_admin  -> Admin Chat-ID
BOT_TOKEN = os.environ.get("telegram_token")
admin_env = os.environ.get("telegram_admin")

# Admin-Chat-ID robust parsen (Telegram liefert ints)
try:
    admin = int(admin_env) if admin_env is not None and str(admin_env).strip() != "" else None
except Exception:
    admin = None

if not BOT_TOKEN:
    logging.error("telegram_bot: ENV 'telegram_token' ist nicht gesetzt.")
if admin is None:
    logging.error("telegram_bot: ENV 'telegram_admin' ist nicht gesetzt oder keine gültige Zahl.")


def load_data():
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as file:
                data = read_json_to_dict(file)
                return data if data is not None else []
        else:
            return []
    except Exception as e:
        logging.error(f"telegram_bot: Fehler in def load_data: {e}")
        return []


def read_json_to_dict(json_file):
    data_dict = []
    try:
        json_data = json_file.read()
        if not json_data or not json_data.strip():
            return []
        data = json.loads(json_data)

        chat_ids = data.get("chat_ids", {})
        filter_rules = data.get("filter_rules", {})

        for chat_id, keywords in filter_rules.items():
            if chat_id in chat_ids:
                entry = {"chat_id": int(chat_id), "keywords": keywords}
                data_dict.append(entry)
    except json.JSONDecodeError as e:
        logging.error(f"telegram_bot: Ungültige JSON in {DATA_FILE}: {e}")
    except Exception as e:
        logging.error(f"telegram_bot: Fehler in def read_json_to_dict: {e}")

    return data_dict


async def send_telegram_message(bot, chat_id, message):
    try:
        await bot.send_message(chat_id=chat_id, text=message)
    except Exception as e:
        logging.error(f"telegram_bot: Fehler in def send_telegram_message: {e}")


async def send_telegram_picture(bot, chat_id, images):
    try:
        for image_url in images:
            if image_url != "":
                await bot.send_photo(chat_id, image_url)
    except Exception as e:
        logging.error(f"telegram_bot: Fehler in def send_telegram_picture: {e}")


async def main(new_tweets):
    # Initialisiere den Telegram-Bot
    if not BOT_TOKEN:
        logging.error("telegram_bot: Start abgebrochen – ENV 'telegram_token' fehlt.")
        return

    try:
        bot = telegram.Bot(token=BOT_TOKEN)  # <-- FIX: BOT_TOKEN statt bot_token
    except Exception as e:
        logging.error(f"telegram_bot: Fehler in bot = telegram.Bot: {e}")
        return

    my_filter = load_data()

    try:
        for n, tweet in enumerate(new_tweets, start=1):
            username = tweet['username']
            content = tweet['content']
            posted_time = tweet['posted_time']
            var_href = tweet['var_href'].replace('x.com', 'nitter.net')
            images = tweet['images']
            extern_urls_as_string = tweet['extern_urls_as_string']

            message = (
                f"{username} hat einen neuen Tweet veröffentlicht:\n\n"
                f"{content}\n\n"
                f"Tweet vom: {posted_time}\n\n"
                f"Link zum Tweet: {var_href}\n\n"
                f"{extern_urls_as_string}"
            ).replace('@', '#')

            for entries in my_filter:
                chat_id = entries["chat_id"]
                keywords = entries["keywords"]

                if username == "Servicemeldung":
                    await send_telegram_message(bot, chat_id, message)

                elif username == "me_1234_me":
                    # admin ist bereits int oder None
                    target_admin = admin if admin is not None else chat_id
                    await send_telegram_message(bot, target_admin, message)

                elif not keywords:
                    await send_telegram_message(bot, chat_id, message)

                else:
                    keywordincontent = any(keyword in content for keyword in keywords)
                    if keywordincontent:
                        await send_telegram_message(bot, chat_id, message)
                        # await send_telegram_picture(bot, chat_id, images)

    except Exception as e:
        logging.error(f"telegram_bot: Fehler in grosser for-Schleife Main: {e}")


if __name__ == '__main__':
    # asyncio.run(main(tweet_data))
    print("This script should be imported and not run directly.")
