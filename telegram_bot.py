import asyncio
import os
import telegram
import json
import logging
import re
import time
import state_store

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


_X_LINK_PREFIX = re.compile(r"(?i)\bhttps?://x\.com")
_X_LINK_BARE = re.compile(r"(?i)\bx\.com(?=/)")
RETRY_DELAYS_SECONDS = [60, 120, 180]
MAX_EXTRA_SEND_RETRIES = len(RETRY_DELAYS_SECONDS)


def replace_x_links_with_nitter(text: str) -> str:
    if not text:
        return ""
    replaced = _X_LINK_PREFIX.sub("https://nitter.net", text)
    replaced = _X_LINK_BARE.sub("https://nitter.net", replaced)
    return replaced


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


def _build_retry_payload(chat_id: int, message: str) -> dict:
    return {
        "chat_id": int(chat_id),
        "message": str(message or ""),
    }


def _enqueue_telegram_retry(chat_id: int, message: str, error_text: str):
    state_store.enqueue_failed_delivery(
        channel="telegram",
        target=str(chat_id),
        payload=_build_retry_payload(chat_id=chat_id, message=message),
        max_retries=MAX_EXTRA_SEND_RETRIES,
        first_delay_seconds=RETRY_DELAYS_SECONDS[0],
        last_error=error_text,
    )


async def _process_pending_telegram_retries(bot):
    pending = state_store.get_due_failed_deliveries("telegram", limit=200)
    if not pending:
        return

    for entry in pending:
        delivery_id = int(entry.get("id", 0))
        payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
        chat_id_raw = payload.get("chat_id", entry.get("target"))
        message = str(payload.get("message") or "")
        try:
            chat_id = int(chat_id_raw)
        except Exception:
            state_store.mark_failed_delivery_exhausted(
                delivery_id,
                attempt_count=int(entry.get("attempt_count", 0)) + 1,
                last_error=f"Ungültige chat_id im Retry-Payload: {chat_id_raw}",
            )
            logging.error(f"telegram_bot: Retry-Job {delivery_id} verworfen (ungültige chat_id={chat_id_raw}).")
            continue

        ok, err_txt = await send_telegram_message(bot, chat_id, message)
        if ok:
            state_store.remove_failed_delivery(delivery_id)
            logging.info(f"telegram_bot: Retry-Job {delivery_id} erfolgreich an chat_id={chat_id}.")
            continue

        prev_attempt_count = int(entry.get("attempt_count", 0))
        max_retries = max(1, int(entry.get("max_retries", MAX_EXTRA_SEND_RETRIES)))
        new_attempt_count = prev_attempt_count + 1
        if new_attempt_count >= max_retries:
            state_store.mark_failed_delivery_exhausted(
                delivery_id,
                attempt_count=new_attempt_count,
                last_error=err_txt,
            )
            logging.error(
                f"telegram_bot: Retry-Job {delivery_id} ausgeschöpft "
                f"(chat_id={chat_id}, versuche={new_attempt_count}/{max_retries}): {err_txt}"
            )
            continue

        delay_index = min(new_attempt_count, len(RETRY_DELAYS_SECONDS) - 1)
        next_retry_at = int(time.time()) + RETRY_DELAYS_SECONDS[delay_index]
        state_store.schedule_failed_delivery_retry(
            delivery_id,
            attempt_count=new_attempt_count,
            next_retry_at=next_retry_at,
            last_error=err_txt,
        )
        logging.warning(
            f"telegram_bot: Retry-Job {delivery_id} erneut geplant "
            f"(chat_id={chat_id}, versuche={new_attempt_count}/{max_retries}, "
            f"next_in={RETRY_DELAYS_SECONDS[delay_index]}s): {err_txt}"
        )


async def send_telegram_message(bot, chat_id, message):
    try:
        await bot.send_message(chat_id=chat_id, text=message)
        return True, ""
    except Exception as e:
        err_txt = str(e)
        logging.warning(f"telegram_bot: Fehler in def send_telegram_message (chat_id={chat_id}): {err_txt}")
        return False, err_txt


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

    state_store.prune_failed_deliveries()
    await _process_pending_telegram_retries(bot)

    my_filter = load_data()

    try:
        for n, tweet in enumerate(new_tweets, start=1):
            username = tweet['username']
            content = replace_x_links_with_nitter(tweet['content'])
            posted_time = tweet['posted_time']
            var_href = replace_x_links_with_nitter(tweet['var_href'])
            images = tweet['images']
            extern_urls_as_string = replace_x_links_with_nitter(tweet['extern_urls_as_string'])

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
                    ok, err_txt = await send_telegram_message(bot, chat_id, message)
                    if not ok:
                        _enqueue_telegram_retry(chat_id, message, err_txt)

                elif username == "me_1234_me":
                    # admin ist bereits int oder None
                    target_admin = admin if admin is not None else chat_id
                    ok, err_txt = await send_telegram_message(bot, target_admin, message)
                    if not ok:
                        _enqueue_telegram_retry(target_admin, message, err_txt)

                elif not keywords:
                    ok, err_txt = await send_telegram_message(bot, chat_id, message)
                    if not ok:
                        _enqueue_telegram_retry(chat_id, message, err_txt)

                else:
                    keywordincontent = any(keyword in content for keyword in keywords)
                    if keywordincontent:
                        ok, err_txt = await send_telegram_message(bot, chat_id, message)
                        if not ok:
                            _enqueue_telegram_retry(chat_id, message, err_txt)
                        # await send_telegram_picture(bot, chat_id, images)

    except Exception as e:
        logging.error(f"telegram_bot: Fehler in grosser for-Schleife Main: {e}")


if __name__ == '__main__':
    # asyncio.run(main(tweet_data))
    print("This script should be imported and not run directly.")
