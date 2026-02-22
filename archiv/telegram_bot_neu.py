import asyncio
import os
import telegram
import logging
import re

import state_store

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


_X_LINK_PREFIX = re.compile(r"(?i)\bhttps?://x\.com")
_X_LINK_BARE = re.compile(r"(?i)\bx\.com(?=/)")


def replace_x_links_with_nitter(text: str) -> str:
    if not text:
        return ""
    replaced = _X_LINK_PREFIX.sub("https://nitter.net", text)
    replaced = _X_LINK_BARE.sub("https://nitter.net", replaced)
    return replaced


def load_data():
    data_dict = []
    try:
        data = state_store.load_telegram_data()
        chat_ids = data.get("chat_ids", {})
        filter_rules = data.get("filter_rules", {})
        for chat_id, keywords in filter_rules.items():
            if chat_id in chat_ids:
                try:
                    numeric_id = int(chat_id)
                except Exception:
                    continue
                data_dict.append({"chat_id": numeric_id, "keywords": keywords})
    except Exception as e:
        logging.error(f"telegram_bot: Fehler in def load_data: {e}")
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
            content = replace_x_links_with_nitter(tweet['content'])
            posted_time = tweet['posted_time']
            var_href = replace_x_links_with_nitter(tweet['var_href'])
            images = tweet['images']
            extern_urls_as_string = replace_x_links_with_nitter(tweet['extern_urls_as_string'])

            parts = [
                f"{username} hat einen neuen Tweet veröffentlicht:",
                "",
                extern_urls_as_string.strip() if extern_urls_as_string.strip() else "",
                content,
                "",
                f"Tweet vom: {posted_time}",
                "",
                f"Link zum Tweet: {var_href}",
            ]
            message = "\n".join([p for p in parts if p != ""]).replace('@', '#')

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
