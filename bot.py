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
PERSONA_PROMPT = """Sen 'Liber Turk'sun. Dijital bir istihbarat subayısın (Teşkilat-ı Mahsusa ruhuyla).

---
ADIM 1: FİLTRELEME (KRİTİK)
Önce haberi analiz et. Aşağıdaki konulardaysa SADECE 'NONE' yaz, başka hiçbir şey yazma:
- Spesifik ürün indirimleri (Black Friday, Prime Day, vb.)
- Video oyunu yamaları veya güncellemeleri
- Küçük çaplı gadget lansmanları
- ABD'ye özgü yerel haberler
- Eğlence/magazin haberleri

SADECE şu konularda tweet at:
- Makro-ekonomi (Fed, ECB, faiz, enflasyon)
- Büyük teknoloji şirketlerinin stratejik hamleleri (Apple, Google, Nvidia, Microsoft hisse/strateji)
- Yapay zeka regülasyonları
- Kripto piyasa hareketleri (Bitcoin, Ethereum, USDT)
- Teknoloji sektörünü etkileyen jeopolitik gelişmeler

---
ADIM 2: TÜRKİYE AÇISI (HAYATI ÖNEM)
Her global haberi Türkiye bağlamına oturt.
Sor: "Bu gelişme bir Türk'ün alım gücünü, kripto portföyünü veya geleceğini nasıl etkiler?"
- Fed faiz artırırsa -> Gelişmekte olan piyasalara (TR dahil) etkisini yorumla
- Petrol yükselirse -> TR'deki enflasyon etkisini belirt
- Dolar güçlenirse -> TL üzerindeki baskıyı analiz et
- Çip krizi varsa -> Türkiye'nin teknoloji bağımlılığını sorgula

---
ADIM 3: ÜSLUP VE STİL
- Ciddi, analitik, sinik ve 'derin devlet bilen' bir ton
- Çocuksu argo veya emoji YASAK
- Şu terimleri kullan: 'likidite krizi', 'küresel sermaye', 'operasyon', 'domino etkisi', 'hedge', 'pozisyon almak', 'manipülasyon'
- Tamamı küçük harf estetiği (büyük harf kullanma)
- Klişeler yasak: 'özetle', 'gelişme', 'son dakika'

---
ADIM 4: FORMAT
- Maximum 270 karakter
- Anlam bütünlüğü olan, tamamlanmış cümleler
- Yarım düşünce YASAK
- Eğer haber filtrelenecekse SADECE 'NONE' yaz"""


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
    """Generate a persona-driven tweet using Google Gemini. Returns None if filtered."""
    genai.configure(api_key=GEMINI_API_KEY)
    
    model = genai.GenerativeModel("gemini-3-flash-preview")
    
    user_prompt = f"""HABER BAŞLIĞI: {title}

HABER ÖZETİ: {summary}

KAYNAK: {source}

Önce filtrele, sonra uygunsa tarzında yorumla:"""
    
    response = model.generate_content(
        [
            {"role": "user", "parts": [PERSONA_PROMPT]},
            {"role": "model", "parts": ["anladım, filtreleme ve analiz için hazırım"]},
            {"role": "user", "parts": [user_prompt]},
        ]
    )
    
    tweet_text = response.text.strip()
    
    # Check if content was filtered
    if tweet_text.upper() == "NONE" or tweet_text == "" or len(tweet_text) < 10:
        return None
    
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
    
    # Find a suitable item to tweet (not in history and passes filtering)
    tweet_posted = False
    
    for entry in entries:
        if entry["link"] in history:
            continue
        
        print(f"📝 Checking item: {entry['title']}")
        
        # Generate tweet content (may return None if filtered)
        print("🤖 Analyzing with Gemini...")
        tweet_text = generate_tweet(
            entry["title"],
            entry["summary"],
            entry["source"],
        )
        
        # Check if content was filtered
        if tweet_text is None:
            print("⏭️ Content filtered (not relevant), adding to history and trying next...")
            history.append(entry["link"])
            save_history(history)
            continue
        
        print(f"💬 Generated tweet: {tweet_text}")
        
        # Post to Twitter
        print("🐦 Posting to Twitter...")
        try:
            response = post_to_twitter(tweet_text)
            print(f"✅ Tweet posted successfully! ID: {response.data['id']}")
            
            # Save to history
            history.append(entry["link"])
            save_history(history)
            print("💾 History updated")
            tweet_posted = True
            break  # Only post one tweet per run
            
        except Exception as e:
            print(f"❌ Error posting to Twitter: {e}")
            return
    
    if not tweet_posted:
        print("✅ No suitable items to post (all filtered or already posted)")


if __name__ == "__main__":
    main()
