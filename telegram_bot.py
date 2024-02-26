import asyncio
import os
import telegram
from telegram.ext import Updater
import json

# Telegram-Bot-Parameter
bot_token = "API:TOKEN"

#Die Datei erstezt die alte my_filter Liste
DATA_FILE = 'data.json'

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as file:
            return read_json_to_dict(file)
    else:
        return {"chat_id": {}, "keywords": {}}

def read_json_to_dict(json_file):
    data_dict = []
    json_data = json_file.read()  # Hier wird der Inhalt des TextIOWrapper-Objekts gelesen
    data = json.loads(json_data)

    chat_ids = data.get("chat_ids", {})
    filter_rules = data.get("filter_rules", {})

    for chat_id, keywords in filter_rules.items():
        # Überprüfen, ob die chat_id in chat_ids vorhanden ist
        if chat_id in chat_ids:
            entry = {"chat_id": int(chat_id), "keywords": keywords}
            data_dict.append(entry)

    return data_dict


async def send_telegram_message(bot, chat_id, message):
    await bot.send_message(chat_id=chat_id, text=message)
    
async def send_telegram_picture (bot, chat_id, images):
    for image_url in images:
        if image_url != "":
            await bot.send_photo(chat_id, image_url)
        

async def main(new_tweets):
    # Initialisiere den Telegram-Bot
    bot = telegram.Bot(token=bot_token)
    my_filter = load_data()
    
    # Ausgabe der Tweet-Texte
    for n, tweet in enumerate(new_tweets, start=1):
        user = tweet['user']
        username = tweet['username']
        content = tweet['content']
        posted_time = tweet['posted_time']
        var_href = tweet['var_href']
        images = tweet['images']
        message = f"{username} hat einen neuen Tweet veröffentlicht:\n\n{content}\n\nTweet abgesetzt um: {posted_time}\n\nLink zum Tweet: {var_href}"
        message = message.replace('@', '#')
        
        for entries in my_filter:
            
            chat_id = entries["chat_id"]
            keywords = entries["keywords"]

            if not keywords:
                await send_telegram_message(bot, chat_id, message)
            else:
                keywordincontent = False
                for keyword in keywords:
                    if keyword in content:  # Überprüfe, ob das Keyword enthalten ist
                        keywordincontent = True
                if keywordincontent:
                    await send_telegram_message(bot, chat_id, message)
                    #await send_telegram_picture(bot, chat_id, images)
        

if __name__ == '__main__':
    #asyncio.run(main(tweet_data))
    print("This script should be imported and not run directly.")
