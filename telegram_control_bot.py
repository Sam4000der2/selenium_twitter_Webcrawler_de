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
    #Überprüfe, ob die Datei existiert und lese vorhandene Tweets
    if not os.path.exists(DATA_FILE):
        # Wenn die Datei nicht existiert, erstelle sie
        open(DATA_FILE, "a").close()  # Erstelle die Datei, falls sie nicht existiert

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
    if not rules:
        await add_exempel_command(bot, chat_id)
    else:
        filter_rules = load_filter_rules(chat_id)
        new_rules = set(filter(lambda x: x.strip(), rules))
        filter_rules.extend(new_rules)
        save_filter_rules(chat_id, filter_rules)
        await bot.send_message(chat_id=chat_id, text="Filter rules added.")

# Funktion zum Löschen von Filterregeln
async def delete_filter_rules(bot, message, chat_id):
    rules = message.split()[1:]  # Filterregeln aus der Nachricht extrahieren
    if not rules:
        await del_exempel_command(bot, chat_id)
    else:
        filter_rules = load_filter_rules(chat_id)
        to_remove = set(filter(lambda x: x.strip(), rules))
        filter_rules = [rule for rule in filter_rules if rule not in to_remove]
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
        
        
# Funktion für den /start-Befehl zum Speichern der Chat-ID
async def add_exempel_command(bot, chat_id):
    help_text = "Beispiel für die Funktion zum hinzufügen von Filterstichworten:\n\n"
    help_text += "/addfilterrules [dein Stichwort]\n"
    help_text += "/addfilterrules #S42\n"
    help_text += "/addfilterrules Heerstr\n"
    help_text += "/addfilterrules Alexanderplatz\n"
    help_text += "/addfilterrules #M4_BVG\n"
    help_text += "/addfilterrules #100_BVG\n"
    help_text += "/addfilterrules #U1_BVG\n"
    help_text += "\n"
    help_text += "In dem Beispiel schickt der Bot dir für die Linien U1, 100 (Bus), M4 (Tram) und S42 dir alle Nachichten weiter. Ausserdem für den Alexanderplatz und die Heerstr.\n"
    help_text += "\n"
    help_text += "Hinweis: Es handelt sich um einen Freitext. Solange der Stichwort in den ankommenden Tweets enthalten ist, kriegst du eine Nachricht."
    
    await bot.send_message(chat_id=chat_id, text=help_text)
    
async def del_exempel_command(bot, chat_id):
    help_text = "Beispiel für die Funktion zum löschen von Filterstichworten:\n\n"
    help_text += "/deletefilterrules [dein Stichwort]\n"
    help_text += "/deletefilterrules #S42\n"
    help_text += "/deletefilterrules Heerstr\n"
    help_text += "/deletefilterrules Alexanderplatz\n"
    help_text += "/deletefilterrules #M4_BVG\n"
    help_text += "/deletefilterrules #100_BVG\n"
    help_text += "/deletefilterrules #U1_BVG\n"
    help_text += "\n"
    help_text += "In dem Beispiel löscht der Bot die Suchbegriffe für die Linien U1, 100 (Bus), M4 (Tram) und S42. Ausserdem für den Alexanderplatz und die Heerstr.\n"
    help_text += "\n"
    help_text += "Das heisst du kriegst für die spezifischen Begriffe keine Nachichten mehr. Achtung achte auf die Schreibweise. \n"
    help_text += "\n"
    help_text += "Im Zweifelsfall nutze /showallrules um deine bisherigen Filterbegriffe aufzurufen. Sollten keine Begriffe festgelegt sein, bekommst du alle Nachichten weitergeleitet."
    
    await bot.send_message(chat_id=chat_id, text=help_text)

# Funktion für den /stop-Befehl zum Löschen der Chat-ID
async def stop_command(bot, chat_id):
    chat_ids = load_chat_ids()
    if str(chat_id) in chat_ids:
        del chat_ids[str(chat_id)]
        save_chat_ids(chat_ids)
        await bot.send_message(chat_id=chat_id, text="Bot stopped. Goodbye!")


# Funktion für den /hilfe-Befehl zum Anzeigen aller verfügbaren Befehle
async def help_command(bot, chat_id):
    help_text = "Der Bot leitet alle Tweets von #SbahnBerlin #BVG_Bus #BVG_UBahn #BVG_Tram #VIZ_Berlin an den Nutzer weiter.\n\n"
    help_text += "Außer der Nutzer nutzt die Filterbegriffe-Funktion des Bots. Dann werden nur entsprechende Tweets weitergeleitet.\n\n"
    help_text += "/start - Start the bot\n/stop - Stop the bot\n/addfilterrules [rules] - Add filter rules\n/deletefilterrules [rules] - Delete filter rules\n/deleteallrules - Delete all filter rules\n/showallrules - Show all filter rules\n"
    help_text += "/list - bietet eine ausführliche Anleitung für die Funktionen Filter hinzufügen und löschen."
    await bot.send_message(chat_id=chat_id, text=help_text)
    
    help_text = "Expertentipps:\n"
    help_text += "\n"
    help_text += "Dem Bot können mehre Stichworte gleichzeitig übergeben werden (getrennt durch Leerzeichen)\n"
    help_text += "\n"
    help_text += "Die Funktionen zum hinzufügen und löschen von Filterregeln unterstützten Kurzformen des Befehls.\n"
    help_text += "\n"
    help_text += "Bspw:\n"
    help_text += "/addrule [deine Stichworte]\n"
    help_text += "/delrule [deine Stichworte]\n"
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
        elif message.startswith('/add'):
            await add_filter_rules(bot, message, chat_id)
        elif message.startswith('/deleteallrules'):
            await delete_all_rules(bot, message, chat_id)
        elif message.startswith('/del'):
            await delete_filter_rules(bot, message, chat_id)
        elif message.startswith('/showallrules'):
            await show_all_rules(bot, message, chat_id)
        elif message.startswith('/list'):
            await add_exempel_command(bot, chat_id)
            await del_exempel_command(bot, chat_id)
        elif message.startswith('/'):
            await help_command(bot, chat_id)
        else:
            await start_command(bot, chat_id)
            await help_command(bot, chat_id)

# Ausführen des Bots
if __name__ == "__main__":
    asyncio.run(start_bot())
