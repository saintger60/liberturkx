#!/usr/bin/env python3
"""
LiberTurkX - Autonomous Tech News Bot
Fetches tech/finance/crypto news and posts persona-driven commentary to Twitter.
"""

import json
import os
import re
import socket
import sys
from datetime import datetime, timezone

import feedparser
import tweepy
from dateutil import parser as date_parser
from dotenv import load_dotenv
from google import genai
from google.genai import types

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
    # Sondaki '/' 308 redirect'e dönüşüyor ve feedparser boş liste alıyor.
    "https://www.coindesk.com/arc/outboundfeeds/rss",
]

HISTORY_FILE = "history.json"
# Ücretsiz Gemini katmanı model başına günde 20 istekle sınırlı (Pasifik gece
# yarısında sıfırlanır); tavan olmadan tek tur tüm günlük kotayı tüketebilir.
MAX_GEMINI_CALLS_PER_RUN = 3
HISTORY_MAX_ENTRIES = 1000
# Preview modeller 2 hafta bildirimle emekli edilebiliyor; stable modelde kal.
GEMINI_MODEL = "gemini-3.5-flash"
TWEET_MAX_CHARS = 270
FEED_TIMEOUT_SECONDS = 20

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
    """Save posting history to JSON file, keeping only the newest entries."""
    trimmed = history[-HISTORY_MAX_ENTRIES:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)


def parse_entry_date(entry):
    """Return a timezone-aware publication date for a feed entry."""
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if not raw:
            continue
        try:
            parsed = date_parser.parse(raw)
        except (ValueError, OverflowError):
            continue
        # Naive ve aware tarihler karışırsa sort TypeError fırlatır; hepsini aware yap.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return datetime.now(timezone.utc)


def fetch_rss_feeds():
    """Fetch and parse all RSS feeds, return sorted entries by date."""
    all_entries = []
    socket.setdefaulttimeout(FEED_TIMEOUT_SECONDS)

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries:
                status = getattr(feed, "status", "?")
                print(f"⚠️ Feed returned no entries (status {status}): {feed_url}")
            for entry in feed.entries:
                try:
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
                        "pub_date": parse_entry_date(entry),
                    })
                except Exception as e:
                    print(f"Skipping malformed entry in {feed_url}: {e}")
        except Exception as e:
            print(f"Error fetching {feed_url}: {e}")

    # Sort by publication date (newest first)
    all_entries.sort(key=lambda x: x["pub_date"], reverse=True)
    return all_entries


def postprocess_tweet(tweet_text):
    """Normalize Gemini output. Returns None if the item was filtered."""
    tweet_text = (tweet_text or "").strip()

    # Model 'NONE' cevabını noktalama/tırnak süsleriyle döndürebilir.
    normalized = re.sub(r"[^a-z]", "", tweet_text.lower())
    if normalized == "none" or len(tweet_text) < 10:
        return None

    if len(tweet_text) > TWEET_MAX_CHARS:
        cut = tweet_text[: TWEET_MAX_CHARS - 1]
        if " " in cut:
            cut = cut[: cut.rfind(" ")]
        tweet_text = cut + "…"

    return tweet_text


def generate_tweet(client, title, summary, source):
    """Generate a persona-driven tweet using Gemini. Returns None if filtered."""
    user_prompt = f"""HABER BAŞLIĞI: {title}

HABER ÖZETİ: {summary}

KAYNAK: {source}

Önce filtrele, sonra uygunsa tarzında yorumla:"""

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_prompt,
        config=types.GenerateContentConfig(system_instruction=PERSONA_PROMPT),
    )

    return postprocess_tweet(response.text)


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

    missing = [name for name, value in required_keys if not value]
    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        sys.exit(1)

    # Load history
    history = load_history()
    history_set = set(history)
    print(f"📜 Loaded {len(history)} items from history")

    # Fetch RSS feeds
    print("📡 Fetching RSS feeds...")
    entries = fetch_rss_feeds()
    print(f"📰 Found {len(entries)} news items")

    if not entries:
        print("❌ No entries found in RSS feeds")
        return

    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    gemini_calls = 0
    seen_titles = set()
    tweet_posted = False

    for entry in entries:
        if entry["link"] in history_set:
            continue

        # Aynı haber birden fazla feed'de yayınlanabiliyor; kotayı iki kez harcama.
        title_key = entry["title"].strip().lower()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        if gemini_calls >= MAX_GEMINI_CALLS_PER_RUN:
            print(f"⛔ Gemini limit reached ({MAX_GEMINI_CALLS_PER_RUN} calls), stopping this run")
            break

        print(f"📝 Checking item: {entry['title']}")
        print("🤖 Analyzing with Gemini...")
        gemini_calls += 1
        try:
            tweet_text = generate_tweet(
                gemini_client,
                entry["title"],
                entry["summary"],
                entry["source"],
            )
        except Exception as e:
            # Kota/servis hatası: turu temiz bitir ki biriken history commit edilebilsin.
            print(f"⚠️ Gemini error, ending run: {e}")
            break

        # Check if content was filtered
        if tweet_text is None:
            print("⏭️ Content filtered (not relevant), adding to history and trying next...")
            history.append(entry["link"])
            history_set.add(entry["link"])
            save_history(history)
            continue

        print(f"💬 Generated tweet: {tweet_text}")

        # Post to Twitter
        print("🐦 Posting to Twitter...")
        try:
            response = post_to_twitter(tweet_text)
        except Exception as e:
            # Görünür başarısızlık: 402 sessizce yutulduğu için 45 gün fark edilmemişti.
            print(f"❌ Error posting to Twitter: {e}")
            sys.exit(1)

        print(f"✅ Tweet posted successfully! ID: {response.data['id']}")

        # Save to history
        history.append(entry["link"])
        history_set.add(entry["link"])
        save_history(history)
        print("💾 History updated")
        tweet_posted = True
        break  # Only post one tweet per run

    if not tweet_posted:
        print("✅ No suitable items to post (all filtered or already posted)")


if __name__ == "__main__":
    main()
