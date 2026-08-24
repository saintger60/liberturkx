#!/usr/bin/env python3
"""
LiberTurkX - Autonomous Tech News Bot
Fetches tech/finance/crypto news, generates persona-driven commentary with
Gemini and publishes it to an RSS feed (feed.xml). dlvr.it watches the feed
and cross-posts new items to X — X API'nin ücretli krediyle çalışan yazma
ucu kullanılmıyor.
"""

import json
import os
import re
import socket
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

import feedparser
from dateutil import parser as date_parser
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Load environment variables
load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# RSS Feed Sources
RSS_FEEDS = [
    "https://techcrunch.com/feed/",
    "https://www.theverge.com/rss/index.xml",
    # Sondaki '/' 308 redirect'e dönüşüyor ve feedparser boş liste alıyor.
    "https://www.coindesk.com/arc/outboundfeeds/rss",
]

HISTORY_FILE = "history.json"
FEED_ITEMS_FILE = "feed_items.json"  # feed'in kaynak verisi (feed.xml bundan render edilir)
FEED_XML_FILE = "feed.xml"
FEED_MAX_ITEMS = 20
# dlvr.it free plan ~10 post/ay geçiriyor; feed'i bundan hızlı doldurmak
# aylık tavanı ilk günlerde tüketir. 72 saat aralık = ~10 post/ay.
MIN_HOURS_BETWEEN_POSTS = 72
# Ücretsiz Gemini katmanı model başına günlük istekle sınırlı; tavan olmadan
# tek tur tüm günlük kotayı tüketebilir.
MAX_GEMINI_CALLS_PER_RUN = 3
HISTORY_MAX_ENTRIES = 1000
# Preview modeller 2 hafta bildirimle emekli edilebiliyor; stable modelde kal.
GEMINI_MODEL = "gemini-3.5-flash"
TWEET_MAX_CHARS = 270
FEED_TIMEOUT_SECONDS = 20

FEED_TITLE = "Liber Turk"
FEED_LINK = "https://x.com/liberturkx"
FEED_DESCRIPTION = "eski usül istihbarat bitti, yeni cephe veri madenciliği."

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


def load_feed_items():
    """Load published feed items (newest last)."""
    if os.path.exists(FEED_ITEMS_FILE):
        with open(FEED_ITEMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def hours_since_last_feed_item(feed_items, now):
    """Return hours elapsed since the newest feed item, or None if feed is empty."""
    if not feed_items:
        return None
    last = date_parser.parse(feed_items[-1]["published_at"])
    return (now - last).total_seconds() / 3600


def render_feed_xml(feed_items):
    """Render feed items (newest last) into an RSS 2.0 document, newest first."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "<channel>",
        f"<title>{escape(FEED_TITLE)}</title>",
        f"<link>{escape(FEED_LINK)}</link>",
        f"<description>{escape(FEED_DESCRIPTION)}</description>",
        "<language>tr</language>",
    ]
    for item in reversed(feed_items):
        pub_date = format_datetime(date_parser.parse(item["published_at"]))
        lines += [
            "<item>",
            f"<title>{escape(item['text'])}</title>",
            f"<description>{escape(item['text'])}</description>",
            f"<link>{escape(item['news_link'])}</link>",
            f"<guid isPermaLink=\"false\">{escape(item['news_link'])}</guid>",
            f"<pubDate>{pub_date}</pubDate>",
            "</item>",
        ]
    lines += ["</channel>", "</rss>"]
    return "\n".join(lines) + "\n"


def publish_to_feed(feed_items, tweet_text, news_link, now):
    """Append a new item, prune old ones and write both feed files."""
    feed_items.append({
        "text": tweet_text,
        "news_link": news_link,
        "published_at": now.isoformat(),
    })
    feed_items = feed_items[-FEED_MAX_ITEMS:]
    with open(FEED_ITEMS_FILE, "w", encoding="utf-8") as f:
        json.dump(feed_items, f, ensure_ascii=False, indent=2)
    with open(FEED_XML_FILE, "w", encoding="utf-8") as f:
        f.write(render_feed_xml(feed_items))
    return feed_items


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


def main():
    """Main bot logic."""
    print("🚀 LiberTurkX Bot Starting...")

    if not GEMINI_API_KEY:
        print("❌ Missing environment variable: GEMINI_API_KEY")
        sys.exit(1)

    now = datetime.now(timezone.utc)

    # Pacing kapısı: dlvr.it free planı ~10 post/ay geçiriyor. Pencere
    # kapalıyken RSS ve Gemini'ye hiç dokunmadan çık — kota harcanmasın.
    feed_items = load_feed_items()
    elapsed = hours_since_last_feed_item(feed_items, now)
    if elapsed is not None and elapsed < MIN_HOURS_BETWEEN_POSTS:
        remaining = MIN_HOURS_BETWEEN_POSTS - elapsed
        print(f"⏳ Pacing window closed ({elapsed:.1f}h since last post, "
              f"{remaining:.1f}h remaining) — skipping run")
        return

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
    queued = False

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

        # Feed'e yaz; dlvr.it feed'i izleyip X'e basacak.
        print("📤 Publishing to RSS feed...")
        publish_to_feed(feed_items, tweet_text, entry["link"], now)

        history.append(entry["link"])
        history_set.add(entry["link"])
        save_history(history)
        print("✅ Queued for X via dlvr.it, history updated")
        queued = True
        break  # Pencere başına tek post

    if not queued:
        print("✅ No suitable items to queue (all filtered or already posted)")


if __name__ == "__main__":
    main()
