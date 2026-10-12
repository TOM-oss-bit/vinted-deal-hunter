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

# Każde wyszukiwanie ma osobny limit ceny w PLN.
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
        raise RuntimeError(f"Nie można odczytać historii ofert: {exc}")


def save_state(seen_ids):
    data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "seen": sorted(seen_ids),
    }
    STATE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def extract_items(data):
    if isinstance(data, list):
        if any(
            isinstance(x, dict)
            and any(k in x for k in ("listing_id", "id", "url"))
            for x in data
        ):
            return data

        for value in data:
            result = extract_items(value)
            if result:
                return result

        return []

    if isinstance(data, dict):
        for key in (
            "listings", "items", "results",
            "offers", "products", "data",
        ):
            if key in data:
                result = extract_items(data[key])
                if result:
                    return result

        if any(k in data for k in ("listing_id", "id")):
            return [data]

    return []


def get_listings(query, max_price):
    params = {
        "query": query,
        "marketplace": "pl",
        "page": 1,
        "limit": 20,
        "order": "newest_first",
    }

    if max_price > 0:
        params["price_to"] = max_price

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
                timeout=30,
            )
        except requests.RequestException as exc:
            print(f"[{query}] Błąd połączenia: {exc}")

            if attempt < MAX_ATTEMPTS - 1:
                delay = RETRY_DELAYS[attempt]
                print(f"Ponawiam za {delay} s.")
                time.sleep(delay)
                continue

            print(f"[{query}] Pomijam wyszukiwanie po błędach połączenia.")
            return None

        if response.status_code in (502, 503, 504):
            print(f"[{query}] Extractr HTTP {response.status_code}.")

            if attempt < MAX_ATTEMPTS - 1:
                delay = RETRY_DELAYS[attempt]
                print(f"Ponawiam za {delay} s.")
                time.sleep(delay)
                continue

            print(f"[{query}] API nadal nie odpowiada. Pomijam wyszukiwanie.")
            return None

        if response.status_code == 429:
            print(f"[{query}] HTTP 429 — limit zapytań. Pomijam wyszukiwanie.")
            return None

        if not response.ok:
            raise RuntimeError(
                f"[{query}] Extractr HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )

        try:
            data = response.json()
        except ValueError:
            raise RuntimeError(f"[{query}] Extractr zwrócił niepoprawny JSON.")

        items = extract_items(data)

        if not items:
            raise RuntimeError(
                f"[{query}] Odpowiedź nie zawiera rozpoznawalnych ofert."
            )

        listings = []

        for item in items:
            if not isinstance(item, dict):
                continue

            listing_id = (
                item.get("listing_id")
                or item.get("id")
                or item.get("item_id")
                or item.get("url")
            )

            if not listing_id:
                continue

            listings.append({
                "id": str(listing_id),
                "title": str(
                    item.get("title")
                    or item.get("name")
                    or "Oferta Vinted"
                ),
                "url": str(item.get("url") or item.get("share_url") or ""),
                "price": str(
                    item.get("price")
                    or item.get("total_price")
                    or item.get("discounted_price")
                    or "Brak danych"
                ),
                "currency": str(item.get("currency") or "PLN"),
                "brand": str(item.get("brand") or "Nie podano"),
                "size": str(item.get("size") or "Nie podano"),
                "condition": str(item.get("condition") or "Nie podano"),
                "image": str(
                    item.get("image") or item.get("thumbnail") or ""
                ),
                "query": query,
                "max_price": max_price,
            })

        if not listings:
            raise RuntimeError(
                f"[{query}] Nie udało się odczytać identyfikatorów ofert."
            )

        return listings

    return None


def send_discord(listing):
    embed = {
        "title": listing["title"][:256],
        "description": (
            f"**Cena:** {listing['price']} {listing['currency']}\n"
            f"**Marka:** {listing['brand']}\n"
            f"**Rozmiar:** {listing['size']}\n"
            f"**Stan:** {listing['condition']}\n"
            f"**Szukana fraza:** {listing['query']}\n"
            f"**Limit ceny:** {listing['max_price']} zł"
        )[:4000],
        "footer": {"text": "Vinted Deal Hunter • GitHub Actions"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if listing["url"].startswith(("https://", "http://")):
        embed["url"] = listing["url"]

    if listing["image"].startswith(("https://", "http://")):
        embed["thumbnail"] = {"url": listing["image"]}

    response = requests.post(
        WEBHOOK_URL,
        json={"embeds": [embed]},
        timeout=30,
    )

    if response.status_code not in (200, 204):
        raise RuntimeError(
            f"Discord HTTP {response.status_code}: "
            f"{response.text[:300]}"
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

        listings = get_listings(query, max_price)

        if listings is None:
            failed_searches += 1
        else:
            print(f"[{query}] Sprawdzone oferty: {len(listings)}")
            all_listings.extend(listings)

        if index < len(SEARCHES) - 1:
            time.sleep(3)

    if failed_searches == len(SEARCHES):
        print("Wszystkie wyszukiwania nieudane. Historia bez zmian.")
        return

    if known is None:
        if failed_searches:
            print(
                "Część wyszukiwań się nie udała. "
                "Nie zapisuję stanu początkowego; spróbuj ponownie."
            )
            return

        save_state({item["id"] for item in all_listings})
        print("Zapisano stan początkowy bez wysyłania alertów.")
        return

    sent = 0

    for listing in all_listings:
        if listing["id"] in known:
            continue

        send_discord(listing)
        known.add(listing["id"])
        save_state(known)

        sent += 1
        print(
            f"Wysłano alert: {listing['title']} "
            f"(wyszukiwanie: {listing['query']})"
        )

    save_state(known)

    print(f"\nŁącznie sprawdzone oferty: {len(all_listings)}")
    print(f"Wysłane alerty: {sent}")
    print(f"Nieudane wyszukiwania: {failed_searches}")


if __name__ == "__main__":
    main()
```
