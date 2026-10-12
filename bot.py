```python
import json
import os
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


def load_state():
    if not STATE_FILE.exists():
        return None

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return set(str(x) for x in data.get("seen", []))
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(f"Nie można odczytać historii: {exc}")


def save_state(seen_ids):
    data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "seen": sorted(seen_ids),
    }
    STATE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_listings(query, max_price):
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

        if response.status_code in (502, 503, 504):
            print(f"[{query}] Extractr HTTP {response.status_code}")
            if attempt < MAX_ATTEMPTS - 1:
                delay = RETRY_DELAYS[attempt]
                print(f"Ponawiam za {delay} s.")
                time.sleep(delay)
                continue
            return None

        if response.status_code == 429:
            print(f"[{query}] Limit zapytań API (429).")
            return None

        if not response.ok:
            raise RuntimeError(
                f"[{query}] HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )

        try:
            data = response.json()
        except ValueError:
            raise RuntimeError(f"[{query}] API zwróciło niepoprawny JSON.")

        if not isinstance(data, dict):
            raise RuntimeError(f"[{query}] Nieoczekiwany format odpowiedzi.")

        items = data.get("listings")

        if not isinstance(items, list):
            raise RuntimeError(
                f"[{query}] Brak listy 'listings'. "
                f"Pola odpowiedzi: {list(data.keys())}"
            )

        results = []

        for item in items:
            if not isinstance(item, dict):
                continue

            listing_id = item.get("listing_id") or item.get("id")
            if not listing_id:
                continue

            price = item.get("price")
            try:
                numeric_price = float(price)
            except (TypeError, ValueError):
                print(
                    f"[{query}] Pomijam ofertę bez prawidłowej ceny: "
                    f"{item.get('title', 'Oferta')}"
                )
                continue

            # Dodatkowe zabezpieczenie limitu ceny.
            if numeric_price > max_price:
                continue

            results.append({
                "id": str(listing_id),
                "title": str(item.get("title") or "Oferta Vinted"),
                "url": str(item.get("url") or ""),
                "price": numeric_price,
                "currency": str(item.get("currency") or "PLN"),
                "brand": str(item.get("brand") or "Nie podano"),
                "size": str(item.get("size") or "Nie podano"),
                "condition": str(item.get("condition") or "Nie podano"),
                "image": str(item.get("image") or item.get("photo_url") or ""),
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

    response = requests.post(
        WEBHOOK_URL,
        json={"embeds": [embed]},
        timeout=30,
    )

    if response.status_code not in (200, 204):
        raise RuntimeError(
            f"Discord HTTP {response.status_code}: {response.text[:300]}"
        )


def main():
    if not API_KEY:
        raise RuntimeError("Brak sekretu EXTRACTR_API_KEY.")

    if not WEBHOOK_URL:
        raise RuntimeError("Brak sekretu DISCORD_WEBHOOK_URL.")

    known = load_state()
    all_listings = []
    failed_searches = 0

    for index, search in enumerate(SEARCHES):
        query = search["query"]
        max_price = search["max_price"]

        print(f"\nSzukam: {query} | limit: {max_price} zł")

        try:
            listings = get_listings(query, max_price)
        except RuntimeError as exc:
            print(f"[{query}] Błąd: {exc}")
            failed_searches += 1
            listings = None

        if listings is None:
            failed_searches += 1
            print(f"[{query}] Pomijam to wyszukiwanie.")
        else:
            print(f"[{query}] Sprawdzone oferty: {len(listings)}")
            all_listings.extend(listings)

        if index < len(SEARCHES) - 1:
            time.sleep(3)

    if failed_searches == len(SEARCHES):
        print("Wszystkie wyszukiwania nieudane. Historia bez zmian.")
        return

    # Pierwsze uruchomienie: zapisz znalezione oferty,
    # ale nie wysyłaj alertów dla starych ogłoszeń.
    if known is None:
        if failed_searches:
            print("Część zapytań nieudana. Nie inicjuję historii.")
            return

        save_state({item["id"] for item in all_listings})
        print("Zapisano historię początkową. Alertów nie wysłano.")
        return

    sent = 0

    for listing in all_listings:
        if listing["id"] in known:
            continue

        try:
            send_discord(listing)
        except (RuntimeError, requests.RequestException) as exc:
            print(f"Błąd wysyłania alertu: {exc}")
            continue

        known.add(listing["id"])
        save_state(known)
        sent += 1
        print(f"Wysłano alert: {listing['title']}")

    save_state(known)

    print(f"\nŁącznie sprawdzone oferty: {len(all_listings)}")
    print(f"Wysłane alerty: {sent}")
    print(f"Nieudane wyszukiwania: {failed_searches}")


if __name__ == "__main__":
    main()
```
