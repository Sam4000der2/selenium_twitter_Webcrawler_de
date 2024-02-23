import asyncio
from mastodon import Mastodon

# Anpassbare Variablen
api_base_url = 'https://berlin.social'  # Die Basis-URL Ihrer Mastodon-Instanz
access_token = '65WOTPaUDA1aSoxcCBzGk9eQTul84w2YvPWoEG5SfE4'  # Ihr Access-Token

def post_tweet(mastodon, message):
    # Veröffentliche den Tweet auf Mastodon
    message_cut = truncate_text(message)
    mastodon.status_post(message_cut, visibility='unlisted')
    
def truncate_text(text):
    # Ersetze alle '@' Zeichen durch '#'
    text = text.replace('@', '#')
    # Entferne doppelte '#'
    text = text.replace('##', '#')
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

def post_tweet_with_images(mastodon, message, images):
    # Veröffentliche den Beitrag mit einem oder mehreren Bildern auf Mastodon
    message_cut = truncate_text(message)
    
    # Lade die Bilder hoch und erhalte die Media-IDs
    media = []
    for image_link in images:
        media = mastodon.media_post(image_link)
    
    # Veröffentliche den Beitrag mit den angehängten Bildern
    mastodon.status_post(message_cut, media_ids=[media['id']], visibility='unlisted')




def main(new_tweets):
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
        hashtags = extract_hashtags(content, username)
        message = f"#{username}:\n\n{content}\n\n#öpnv_berlin_bot\n\nscr: {var_href}"
        
        post_tweet(mastodon, message)
        
        #if not images:
            #print("")
            #post_tweet(mastodon, message)
        #else:
            #post_tweet_with_images(mastodon, message, images)

# Hauptprogramm (z.B. wo der Twitter-Bot aufgerufen wird)
if __name__ == "__main__":
    #new_tweets = [...]  # Hier kommen die neuen Tweets
    #asyncio.run(main(new_tweets))
    print("This script should be imported and not run directly.")
