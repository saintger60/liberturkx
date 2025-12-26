#!/usr/bin/env python3
"""
LiberTurkX - Autonomous Tech News Bot
Fetches tech/finance/crypto news and posts persona-driven commentary to Twitter.
"""

import os
import json
import feedparser
import tweepy
import google.generativeai as genai
from datetime import datetime
from dateutil import parser as date_parser
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# API Configuration
TWITTER_API_KEY = os.environ.get("TWITTER_API_KEY")
TWITTER_API_SECRET = os.environ.get("TWITTER_API_SECRET")
TWITTER_ACCESS_TOKEN = os.environ.get("TWITTER_ACCESS_TOKEN")
TWITTER_ACCESS_SECRET = os.environ.get("TWITTER_ACCESS_SECRET")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# RSS Feed Sources
RSS_FEEDS = [
    "https://techcrunch.com/feed/",
    "https://www.theverge.com/rss/index.xml",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
]

# History file path
HISTORY_FILE = "history.json"

# Persona System Prompt
PERSONA_PROMPT = """Sen '@LiberturkX'sin. Teknoloji, finans ve kripto dünyasını yakından takip eden keskin bir yorumcusun.
GÖREV: Bu haberi oku ve kendi görüşünü net şekilde ifade et.
KURALLAR:
1. Sade ve anlaşılır Türkçe kullan.
2. Haberin ne hakkında olduğunu MUTLAKA belirt.
3. Keskin, eleştirel ve düşündürücü ol.
4. 'Özetle', 'Gelişme', 'Son dakika' gibi klişeler YASAK.
5. Emoji kullanma.
6. ÇOK ÖNEMLİ: Tweet MUTLAKA tamamlanmış bir cümle ile bitmeli. Yarım cümle YASAK.
7. KISA YAZ: Maximum 240 karakter. Daha uzun yazma."""


def load_history():
    """Load posting history from JSON file."""
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_history(history):
    """Save posting history to JSON file."""
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def fetch_rss_feeds():
    """Fetch and parse all RSS feeds, return sorted entries by date."""
    all_entries = []
    
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                # Parse publication date
                pub_date = None
                if hasattr(entry, "published"):
                    pub_date = date_parser.parse(entry.published)
                elif hasattr(entry, "updated"):
                    pub_date = date_parser.parse(entry.updated)
                else:
                    pub_date = datetime.now()
                
                # Get summary/description
                summary = ""
                if hasattr(entry, "summary"):
                    summary = entry.summary
                elif hasattr(entry, "description"):
                    summary = entry.description
                
                all_entries.append({
                    "title": entry.title,
                    "link": entry.link,
                    "summary": summary[:500],  # Limit summary length
                    "source": feed_url,
                    "pub_date": pub_date,
                })
        except Exception as e:
            print(f"Error fetching {feed_url}: {e}")
    
    # Sort by publication date (newest first)
    all_entries.sort(key=lambda x: x["pub_date"], reverse=True)
    return all_entries


def generate_tweet(title, summary, source):
    """Generate a persona-driven tweet using Google Gemini."""
    genai.configure(api_key=GEMINI_API_KEY)
    
    model = genai.GenerativeModel("gemini-3-flash-preview")
    
    user_prompt = f"""HABER BAŞLIĞI: {title}

HABER ÖZETİ: {summary}

KAYNAK: {source}

Şimdi bu haberi kendi tarzında yorumla:"""
    
    response = model.generate_content(
        [
            {"role": "user", "parts": [PERSONA_PROMPT]},
            {"role": "model", "parts": ["anladım, hazırım"]},
            {"role": "user", "parts": [user_prompt]},
        ]
    )
    
    tweet_text = response.text.strip()
    
    # Ensure max 280 characters (Twitter limit)
    if len(tweet_text) > 280:
        tweet_text = tweet_text[:277] + "..."
    
    return tweet_text


def post_to_twitter(tweet_text):
    """Post tweet to Twitter using API v2."""
    client = tweepy.Client(
        consumer_key=TWITTER_API_KEY,
        consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN,
        access_token_secret=TWITTER_ACCESS_SECRET,
    )
    
    response = client.create_tweet(text=tweet_text)
    return response


def main():
    """Main bot logic."""
    print("🚀 LiberTurkX Bot Starting...")
    
    # Validate API keys
    required_keys = [
        ("TWITTER_API_KEY", TWITTER_API_KEY),
        ("TWITTER_API_SECRET", TWITTER_API_SECRET),
        ("TWITTER_ACCESS_TOKEN", TWITTER_ACCESS_TOKEN),
        ("TWITTER_ACCESS_SECRET", TWITTER_ACCESS_SECRET),
        ("GEMINI_API_KEY", GEMINI_API_KEY),
    ]
    
    for key_name, key_value in required_keys:
        if not key_value:
            print(f"❌ Missing environment variable: {key_name}")
            return
    
    # Load history
    history = load_history()
    print(f"📜 Loaded {len(history)} items from history")
    
    # Fetch RSS feeds
    print("📡 Fetching RSS feeds...")
    entries = fetch_rss_feeds()
    print(f"📰 Found {len(entries)} news items")
    
    if not entries:
        print("❌ No entries found in RSS feeds")
        return
    
    # Find the latest item not in history
    new_entry = None
    for entry in entries:
        if entry["link"] not in history:
            new_entry = entry
            break
    
    if not new_entry:
        print("✅ No new items to post")
        return
    
    print(f"📝 New item found: {new_entry['title']}")
    
    # Generate tweet content
    print("🤖 Generating tweet with Gemini...")
    tweet_text = generate_tweet(
        new_entry["title"],
        new_entry["summary"],
        new_entry["source"],
    )
    print(f"💬 Generated tweet: {tweet_text}")
    
    # Post to Twitter
    print("🐦 Posting to Twitter...")
    try:
        response = post_to_twitter(tweet_text)
        print(f"✅ Tweet posted successfully! ID: {response.data['id']}")
        
        # Save to history
        history.append(new_entry["link"])
        save_history(history)
        print("💾 History updated")
        
    except Exception as e:
        print(f"❌ Error posting to Twitter: {e}")
        return


if __name__ == "__main__":
    main()
