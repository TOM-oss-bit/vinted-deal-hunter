
import json
import os
import re
import time
from pathlib import Path
from datetime import datetime, timezone

import requests

API_URL = "https://api.extractr.dev/vinted/search"
STATE_FILE = Path("seen_listings.json")

API_KEY = os.getenv("EXTRACTR_API_KEY", "").strip()
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

SEARCHES = [
    {"query": "Nike Shox", "max_price": 100},
    {"query": "Moncler Maya", "max_price": 150},
    {"query": "Ralph Lauren Quarter Zip", "max_price": 30},
]

MAX_ATTEMPTS = 4
RETRY_DELAYS = [10, 20, 40]


def normalize(text):
    text = str(text or "").lower()
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"[^a-z0-9ąćęłńóśźż]+", " ", text)
    return " ".join(text.split())


def load_state():
    if not STATE_FILE.exists():
        return None

    try:
        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
        return set(str(x) for x in data.get("seen", []))
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(
            f"Nie można odczytać historii: {exc}"
        )


def save_state(seen_ids):
    data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "seen": sorted(seen_ids),
    }
    STATE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def matches_search(item, query):
    title = normalize(item.get("title", ""))
    brand = normalize(item.get("brand", ""))
    text = f"{title} {brand}"

    if query == "Nike Shox":
        if "nike" not in text or "shox" not in title:
            return False

        clothing_words = [
            "kurtka", "kurtkę", "kurtki", "bluza", "bluzy",
            "koszulka", "koszulki", "t shirt", "spodenki",
            "spodnie", "czapka", "czapki", "hoodie",
            "sweatshirt", "jacket", "shirt", "shorts",
            "trousers", "card", "rookie", "autograph",
            "patch", "wiatrówka", "wiatrowka"
        ]

        if any(word in title for word in clothing_words):
            return False

        shoe_words = [
            "buty", "sneakers", "sneaker", "shoes",
            "obuwie", "tenisówki", "tenisky", "trainers",
            "trampki", "sneakerów", "sneakersy"
        ]

        # Tytuł musi wyraźnie wskazywać na obuwie.
        if not any(word in title for word in shoe_words):
            return False

        return True

    if query == "Moncler Maya":
        if "moncler" not in text or "maya" not in title:
            return False

        blocked = [
            "etui", "case", "pokrowiec", "wieszak",
            "hanger", "metka", "tag", "torba"
        ]

        if any(word in title for word in blocked):
            return False

        jacket_words = [
            "kurtka", "jacket", "puffer", "down jacket",
            "parka", "kurtkę", "kurtki"
        ]

        return any(word in title for word in jacket_words)

    if query == "Ralph Lauren Quarter Zip":
        brands = ["ralph lauren", "polo ralph lauren", "chaps"]

        if not any(brand_name in text for brand_name in brands):
            return False

        zip_phrases = [
            "quarter zip", "1 4 zip", "half zip",
            "quarterzip", "halfzip"
        ]

        if not any(phrase in title for phrase in zip_phrases):
            return False

        blocked = [
            "traders", "tommy hilfiger", "karta", "card",
            "sukienka", "dress", "spódnica", "skirt"
        ]

        if any(word in title for word in blocked):
            return False

        return True

    return False


def get_listings(search):
    query = search["query"]
    max_price = search["max_price"]

    params = {
        "query": query,
        "marketplace": "pl",
        "page": 1,
        "limit": 96,
        "order": "newest_first",
        "price_to": max_price,
        "currency": "PLN",
    }

    headers = {
        "X-API-Key": API_KEY,
        "Accept": "application/json",
    }

    for attempt in range(MAX_ATTEMPTS):
        try:
            response = requests.get(
                API_URL,
                params=params,
                headers=headers,
                timeout=45,
            )
        except requests.RequestException as exc:
            print(f"[{query}] Błąd połączenia: {exc}")
            if attempt < MAX_ATTEMPTS - 1:
                delay = RETRY_DELAYS[attempt]
                print(f"Ponawiam za {delay} s.")
                time.sleep(delay)
                continue
            return None

        if response.status_code in (429, 502, 503, 504):
            print(f"[{query}] Extractr HTTP {response.status_code}")
            if attempt < MAX_ATTEMPTS - 1:
                delay = RETRY_DELAYS[attempt]
                print(f"Ponawiam za {delay} s.")
                time.sleep(delay)
                continue
            return None

        if not response.ok:
            raise RuntimeError(
                f"[{query}] HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )

        try:
            data = response.json()
        except ValueError:
            raise RuntimeError(
                f"[{query}] API zwróciło niepoprawny JSON."
            )

        if not isinstance(data, dict):
            raise RuntimeError(
                f"[{query}] Nieoczekiwany format odpowiedzi."
            )

        items = data.get("listings")
        if not isinstance(items, list):
            raise RuntimeError(
                f"[{query}] Brak listy listings."
            )

        results = []

        for item in items:
            if not isinstance(item, dict):
                continue

            listing_id = item.get("listing_id") or item.get("id")
            if not listing_id:
                continue

            try:
                price = float(item.get("price"))
            except (TypeError, ValueError):
                continue

            if price < 0 or price > max_price:
                continue

            if not matches_search(item, query):
                print(
                    f"[{query}] Odrzucono: "
                    f"{item.get('title', 'bez tytułu')}"
                )
                continue

            results.append({
                "id": str(listing_id),
                "title": str(item.get("title") or "Oferta Vinted"),
                "url": str(item.get("url") or ""),
                "price": price,
                "brand": str(item.get("brand") or "Nie podano"),
                "size": str(item.get("size") or "Nie podano"),
                "condition": str(
                    item.get("condition") or "Nie podano"
                ),
                "image": str(
                    item.get("image")
                    or item.get("photo_url")
                    or ""
                ),
                "query": query,
                "max_price": max_price,
            })

        return results

    return None


def send_discord(listing):
    embed = {
        "title": listing["title"][:256],
        "description": (
            f"**Cena:** {listing['price']:.2f} zł\n"
            f"**Marka:** {listing['brand']}\n"
            f"**Rozmiar:** {listing['size']}\n"
            f"**Stan:** {listing['condition']}\n"
            f"**Wyszukiwanie:** {listing['query']}\n"
            f"**Limit:** {listing['max_price']} zł"
        ),
        "footer": {"text": "Vinted Deal Hunter"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if listing["url"].startswith("https://"):
        embed["url"] = listing["url"]

    if listing["image"].startswith("https://"):
        embed["thumbnail"] = {"url": listing["image"]}

    for attempt in range(6):
        try:
            response = requests.post(
                WEBHOOK_URL,
                json={"embeds": [embed]},
                timeout=30,
            )
        except requests.RequestException as exc:
            if attempt == 5:
                raise
            delay = min(2 ** attempt, 15)
            print(f"Błąd połączenia Discord: {exc}")
            time.sleep(delay)
            continue

        if response.status_code in (200, 204):
            return

        if response.status_code == 429:
            try:
                retry_after = float(
                    response.json().get("retry_after", 2)
                )
            except (ValueError, TypeError):
                retry_after = 2

            delay = max(retry_after, 1) + 0.5
            print(f"Discord 429 — czekam {delay:.1f} s.")
            time.sleep(delay)
            continue

        if response.status_code >= 500 and attempt < 5:
            delay = min(2 ** attempt, 15)
            print(f"Discord HTTP {response.status_code}.")
            time.sleep(delay)
            continue

        raise RuntimeError(
            f"Discord HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    raise RuntimeError("Nie udało się wysłać alertu.")


def main():
    if not API_KEY:
        raise RuntimeError("Brak sekretu EXTRACTR_API_KEY.")

    if not WEBHOOK_URL:
        raise RuntimeError("Brak sekretu DISCORD_WEBHOOK_URL.")

    known = load_state()
    all_listings = []
    failed_searches = 0

    for index, search in enumerate(SEARCHES):
        print(
            f"\nSzukam: {search['query']} | "
            f"limit: {search['max_price']} zł"
        )

        try:
            listings = get_listings(search)
        except RuntimeError as exc:
            print(f"Błąd wyszukiwania: {exc}")
            listings = None

        if listings is None:
            failed_searches += 1
            print("To wyszukiwanie pominięto.")
        else:
            print(f"Pasujące oferty po filtrze: {len(listings)}")
            all_listings.extend(listings)

        if index < len(SEARCHES) - 1:
            time.sleep(3)

    if failed_searches == len(SEARCHES):
        print("Wszystkie wyszukiwania nieudane. Historia bez zmian.")
        return

    if known is None:
        if failed_searches:
            print(
                "Część wyszukiwań nieudana. "
                "Nie inicjuję historii."
            )
            return

        save_state({item["id"] for item in all_listings})
        print("Zapisano historię początkową. Alertów nie wysłano.")
        return

    sent = 0
    failed_alerts = 0

    for listing in all_listings:
        if listing["id"] in known:
            continue

        try:
            send_discord(listing)
        except (RuntimeError, requests.RequestException) as exc:
            print(f"Błąd alertu: {exc}")
            failed_alerts += 1
            continue

        known.add(listing["id"])
        save_state(known)
        sent += 1

        print(f"Wysłano alert: {listing['title']}")
        time.sleep(1.2)

    save_state(known)

    print(f"\nŁącznie pasujących ofert: {len(all_listings)}")
    print(f"Wysłane alerty: {sent}")
    print(f"Nieudane wyszukiwania: {failed_searches}")
    print(f"Nieudane alerty: {failed_alerts}")


if __name__ == "__main__":
    main()
