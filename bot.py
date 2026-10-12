
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

QUERY = os.getenv("VINTED_QUERY", "Salomon ACS").strip()
MAX_PRICE = int(os.getenv("MAX_PRICE", "0"))

MAX_ATTEMPTS = 4
RETRY_DELAYS = [10, 20, 40]


def load_state():
    if not STATE_FILE.exists():
        return None

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return set(str(x) for x in data.get("seen", []))
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError(
            f"Nie można odczytać historii ofert: {exc}"
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


def extract_items(data):
    if isinstance(data, dict):
        for key in ("listings", "items", "results", "offers", "products"):
            value = data.get(key)
            if isinstance(value, list):
                return value

        value = data.get("data")
        if isinstance(value, (dict, list)):
            result = extract_items(value)
            if result:
                return result

        if any(k in data for k in ("listing_id", "id")):
            return [data]

    elif isinstance(data, list):
        if all(isinstance(item, dict) for item in data):
            if not data or any(
                k in item for item in data
                for k in ("listing_id", "id", "url")
            ):
                return data

        for value in data:
            result = extract_items(value)
            if result:
                return result

    return []


def get_listings():
    params = {
        "query": QUERY,
        "marketplace": "pl",
        "page": 1,
        "limit": 20,
        "order": "newest_first",
    }

    if MAX_PRICE > 0:
        params["price_to"] = MAX_PRICE

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
            print(f"Błąd połączenia: {exc}")

            if attempt < MAX_ATTEMPTS - 1:
                delay = RETRY_DELAYS[attempt]
                print(f"Ponawiam za {delay} s.")
                time.sleep(delay)
                continue

            print("Nie udało się połączyć z Extractr.")
            return None

        if response.status_code in (502, 503, 504):
            print(f"Extractr HTTP {response.status_code}.")

            if attempt < MAX_ATTEMPTS - 1:
                delay = RETRY_DELAYS[attempt]
                print(f"Ponawiam za {delay} s.")
                time.sleep(delay)
                continue

            print("API nadal nie odpowiada. Pomijam ten skan.")
            return None

        if response.status_code == 429:
            print("Extractr HTTP 429: limit zapytań. Pomijam skan.")
            return None

        if not response.ok:
            raise RuntimeError(
                f"Extractr HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )

        try:
            data = response.json()
        except ValueError:
            raise RuntimeError("Extractr zwrócił niepoprawny JSON.")

        items = extract_items(data)

        if not items:
            raise RuntimeError(
                "Odpowiedź API nie zawiera rozpoznawalnych ofert. "
                "Historia nie została zmieniona."
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
                "url": str(
                    item.get("url")
                    or item.get("share_url")
                    or ""
                ),
                "price": str(
                    item.get("price")
                    or item.get("total_price")
                    or item.get("discounted_price")
                    or "Brak danych"
                ),
                "currency": str(item.get("currency") or "PLN"),
                "brand": str(item.get("brand") or "Nie podano"),
                "size": str(item.get("size") or "Nie podano"),
                "condition": str(
                    item.get("condition") or "Nie podano"
                ),
                "image": str(
                    item.get("image")
                    or item.get("thumbnail")
                    or ""
                ),
            })

        if not listings:
            raise RuntimeError(
                "API odpowiedziało, ale nie udało się odczytać "
                "identyfikatorów ofert. Historia zachowana."
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
            f"**Wyszukiwanie:** {QUERY}"
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

    print(f"Szukam ofert: {QUERY}")

    listings = get_listings()

    if listings is None:
        print(
            "Skan nieudany. Historia ofert nie została zmieniona. "
            "Spróbuj ponownie przy kolejnym uruchomieniu."
        )
        return

    print(f"Sprawdzone oferty: {len(listings)}")

    known = load_state()

    # Pierwszy przebieg zapisuje stan początkowy bez alertów.
    if known is None:
        save_state({item["id"] for item in listings})
        print(
            "Zapisano stan początkowy. "
            "Nowe oferty będą zgłaszane przy kolejnych skanach."
        )
        return

    sent = 0

    for listing in listings:
        if listing["id"] in known:
            continue

        send_discord(listing)
        known.add(listing["id"])
        save_state(known)

        sent += 1
        print(f"Wysłano alert: {listing['title']}")

    # Zapisujemy również historię, gdy nie znaleziono nowych ofert.
    save_state(known)
    print(f"Wysłane alerty: {sent}")


if __name__ == "__main__":
    main()

