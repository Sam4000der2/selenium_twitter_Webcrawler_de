import asyncio
import os
import json
import telegram
from telegram.ext import Updater, CommandHandler, MessageHandler

# Telegram secret access bot token
BOT_TOKEN = "api:token"

# Dateiname für Chat-IDs und Filterregeln
DATA_FILE = 'data.json'

# Funktion zum Laden der Daten aus der Datei
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as file:
            return json.load(file)
    else:
        return {"chat_ids": {}, "filter_rules": {}}

# Funktion zum Speichern der Daten in die Datei
def save_data(data):
    with open(DATA_FILE, 'w') as file:
        json.dump(data, file)

# Funktion zum Laden der Chat-IDs aus den Daten
def load_chat_ids():
    data = load_data()
    return data["chat_ids"]

# Funktion zum Speichern der Chat-IDs in die Daten
def save_chat_ids(chat_ids):
    data = load_data()
    data["chat_ids"] = chat_ids
    save_data(data)

# Funktion zum Laden der Filterregeln aus den Daten
def load_filter_rules(chat_id):
    data = load_data()
    return data["filter_rules"].get(str(chat_id), [])

# Funktion zum Speichern der Filterregeln in die Daten
def save_filter_rules(chat_id, filter_rules):
    data = load_data()
    data["filter_rules"][str(chat_id)] = filter_rules
    save_data(data)

# Funktion zum Hinzufügen von Filterregeln
async def add_filter_rules(bot, message, chat_id):
    rules = message.split()[1:]  # Filterregeln aus der Nachricht extrahieren
    filter_rules = load_filter_rules(chat_id)
    filter_rules.extend(rules)
    save_filter_rules(chat_id, filter_rules)
    await bot.send_message(chat_id=chat_id, text="Filter rules added.")

# Funktion zum Löschen von Filterregeln
async def delete_filter_rules(bot, message, chat_id):
    rules = message.split()[1:]  # Filterregeln aus der Nachricht extrahieren
    filter_rules = load_filter_rules(chat_id)
    for rule in rules:
        if rule in filter_rules:
            filter_rules.remove(rule)
    save_filter_rules(chat_id, filter_rules)
    await bot.send_message(chat_id=chat_id, text="Filter rules deleted.")

# Funktion zum Löschen aller Filterregeln
async def delete_all_rules(bot, message, chat_id):
    save_filter_rules(chat_id, [])
    await bot.send_message(chat_id=chat_id, text="All filter rules deleted.")

# Funktion zum Anzeigen aller Filterregeln
async def show_all_rules(bot,message, chat_id):
    filter_rules = load_filter_rules(chat_id)
    if filter_rules:
        await bot.send_message(chat_id=chat_id, text="Filter rules:\n" + '\n'.join(filter_rules))
    else:
        await bot.send_message(chat_id=chat_id, text="No filter rules found.")
        
# Funktion für den /start-Befehl zum Speichern der Chat-ID
async def start_command(bot, chat_id):
    chat_ids = load_chat_ids()
    if str(chat_id) not in chat_ids:
        chat_ids[str(chat_id)] = True
        save_chat_ids(chat_ids)
        await bot.send_message(chat_id=chat_id, text="Bot started. Welcome!")

# Funktion für den /stop-Befehl zum Löschen der Chat-ID
async def stop_command(bot, chat_id):
    chat_ids = load_chat_ids()
    if str(chat_id) in chat_ids:
        del chat_ids[str(chat_id)]
        save_chat_ids(chat_ids)
        await bot.send_message(chat_id=chat_id, text="Bot stopped. Goodbye!")


# Funktion für den /hilfe-Befehl zum Anzeigen aller verfügbaren Befehle
async def help_command(bot, chat_id):
    help_text = "/start - Start the bot\n/stop - Stop the bot\n/addfilterrules [rules] - Add filter rules\n/deletefilterrules [rules] - Delete filter rules\n/deleteallrules - Delete all filter rules\n/showallrules - Show all filter rules"
    await bot.send_message(chat_id=chat_id, text=help_text)

# Funktion zum Starten des Bots
async def start_bot():
    bot = telegram.Bot(token=BOT_TOKEN)
    update_id = None
    while True:
        updates = await bot.get_updates(offset=update_id)
        for update in updates:
            update_id = update.update_id + 1
            await process_update(bot, update)

# Funktion zum Verarbeiten eines Updates
async def process_update(bot, update):
    if update.message:
        message = update.message.text
        chat_id = update.message.chat.id
        if message.startswith('/start'):
            await start_command(bot, chat_id)
            await help_command(bot, chat_id)
        elif message.startswith('/stop'):
            await stop_command(bot, chat_id)
        elif message.startswith('/hilfe'):
            await help_command(bot, chat_id)
        elif message.startswith('/addfilterrules'):
            await add_filter_rules(bot, message, chat_id)
        elif message.startswith('/deletefilterrules'):
            await delete_filter_rules(bot, message, chat_id)
        elif message.startswith('/deleteallrules'):
            await delete_all_rules(bot, message, chat_id)
        elif message.startswith('/showallrules'):
            await show_all_rules(bot, message, chat_id)
        elif message.startswith('/'):
            await help_command(bot, chat_id)
        else:
            await start_command(bot, chat_id)
            await help_command(bot, chat_id)

# Ausführen des Bots
if __name__ == "__main__":
    asyncio.run(start_bot())
