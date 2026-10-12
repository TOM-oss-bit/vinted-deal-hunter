
import json
import os
import re
import time
import unicodedata
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
    text = unicodedata.normalize("NFKD", text)
    text = "".join(
        char for char in text
        if not unicodedata.combining(char)
    )
    text = text.replace("ł", "l")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def has_phrase(text, phrases):
    padded = f" {text} "
    return any(
        f" {normalize(phrase)} " in padded
        for phrase in phrases
    )


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
        # Musi być marka Nike.
        if not has_phrase(text, ["nike"]):
            return False

        # Ubrania i akcesoria — wykluczamy w wielu językach.
        clothing_words = [
            "tee", "t shirt", "shirt", "shirts",
            "sweatshirt", "hoodie", "sweater", "jumper",
            "blouse", "shorts", "trousers", "pants",
            "jacket", "coat", "cap", "hat", "bag",
            "backpack", "socks", "dress", "skirt",
            "koszulka", "koszulki", "bluza", "bluzka",
            "spodnie", "spodenki", "kurtka", "czapka",
            "torba", "plecak", "skarpetki", "sukienka",
            "spodnica", "wiatrowka", "spodnica",
            "tricko", "triko", "mikina", "bunda",
            "kalhoty", "kratasy", "cepice", "kosile",
            "majica", "hlace", "jakna", "camiseta",
            "sudadera", "chaqueta", "pantalones",
            "camisa", "gorra", "falda", "calcetines",
            "maglietta", "felpa", "giacca", "pantaloni",
            "cappello", "gonna", "calzini",
            "pull", "veste", "pantalon", "jupe",
            "bonnet", "chaussettes", "hemd", "pullover",
            "jacke", "hose", "muetze", "socken",
            "shirtje", "trui", "broek", "jas", "rok",
            "sokken", "camisola", "calcas", "casaco",
            "gorro", "meias", "handschuhe", "handschoen",
            "portefeuille", "wallet", "keychain",
            "porte cle", "phone case", "etui"
        ]

        if has_phrase(title, clothing_words):
            return False

        # Słowa związane z butami w różnych językach.
        shoe_words = [
            # Polski
            "buty", "butow", "butach", "obuwie",
            "sneakersy", "adidasy", "but sportowy",

            # Czeski i słowacki
            "boty", "tenisky", "obuv", "sportovni boty",
            "sportovni obuv", "sportove tenisky",

            # Angielski
            "shoes", "shoe", "sneakers", "sneaker",
            "trainers", "trainer", "footwear",
            "running shoes",

            # Niemiecki
            "schuhe", "turnschuhe", "sportschuhe",

            # Francuski
            "chaussures", "baskets", "chaussure",

            # Włoski
            "scarpe", "scarpa",

            # Hiszpański
            "zapatillas", "zapatos", "deportivas",

            # Portugalski
            "sapatos", "sapatilhas", "tenis",

            # Holenderski
            "schoenen", "sportschoenen",

            # Rumuński
            "pantofi", "adidasi",

            # Węgierski
            "cipo", "cipok",

            # Bałkańskie
            "tenisice", "patike", "obutev",

            # Turecki
            "ayakkabi", "spor ayakkabi",

            # Inne
            "krossovki", "sneaker shoes"
        ]

        has_shox = has_phrase(title, ["shox"])
        has_shoes = has_phrase(title, shoe_words)

        # Akceptujemy Nike Shox w dowolnym języku.
        # Akceptujemy też "Boty Nike" lub "Nike 38,5",
        # jeśli opis zawiera określenie obuwia albo rozmiar.
        if has_shox:
            return True

        if has_shoes:
            return True

        # Krótkie tytuły typu "Nike 38,5" albo "Nike 44".
        if re.search(r"\b\d{2}(?:\s+\d)?(?:\s+\d{1,2})?\b", title):
            return True

        return False

    if query == "Moncler Maya":
        if "moncler" not in text or "maya" not in title:
            return False

        blocked = [
            "etui", "case", "pokrowiec", "wieszak",
            "hanger", "metka", "tag", "torba",
        ]
        if has_phrase(title, blocked):
            return False

        jacket_words = [
            "kurtka", "jacket", "puffer", "parka",
            "kurtke", "kurtki", "chaqueta", "giacca",
            "doudoune", "jacke", "jas", "abrigo",
        ]
        return has_phrase(title, jacket_words)

    if query == "Ralph Lauren Quarter Zip":
        brands = [
            "ralph lauren", "polo ralph lauren", "chaps"
        ]
        if not any(normalize(name) in text for name in brands):
            return False

        zip_phrases = [
            "quarter zip", "1 4 zip", "half zip",
            "quarterzip", "halfzip", "quarter zip sweater",
            "zip neck", "zip neckline", "half zip jumper",
            "pullover quarter zip",
        ]
        if not any(
            normalize(phrase) in title
            for phrase in zip_phrases
        ):
            return False

        blocked = [
            "traders", "tommy hilfiger", "karta", "card",
            "sukienka", "dress", "spodnica", "skirt",
        ]
        if has_phrase(title, blocked):
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
                f"[{query}] Niepoprawna odpowiedź JSON."
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
                price = float(
                    str(item.get("price")).replace(",", ".")
                )
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
                "condition": str(item.get("condition") or "Nie podano"),
                "image": str(
                    item.get("image") or item.get("photo_url") or ""
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
