
import os
import re
import time
import sqlite3
from datetime import datetime, timezone

import requests
import streamlit as st


# =========================
# USTAWIENIA
# =========================

st.set_page_config(
    page_title="Vinted Deal Hunter",
    page_icon="🔎",
    layout="wide",
)

API_URL = "https://api.extractr.dev/vinted/search"
DB_PATH = "vinted_deals.db"
REQUEST_TIMEOUT = 30
PAUSE_BETWEEN_QUERIES = 42

CATALOG = [
    "Salomon ACS",
    "Salomon XT-6",
    "Salomon XT-4",
    "Salomon Speedcross",
    "Nike Shox",
    "Nike Air Max",
    "Adidas Campus",
    "Adidas Samba",
    "New Balance 2002R",
    "New Balance 1906R",
    "Asics Gel-Kayano",
    "Asics Gel-NYC",
]


# =========================
# SECRETS
# =========================

def get_secret(*names):
    for name in names:
        try:
            value = st.secrets.get(name, "")
        except Exception:
            value = ""

        if value:
            return str(value).strip()

        value = os.getenv(name, "")
        if value:
            return str(value).strip()

    return ""


# =========================
# BAZA DANYCH
# =========================

def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database():
    with db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen (
                listing_id TEXT PRIMARY KEY,
                title TEXT,
                url TEXT,
                first_seen TEXT,
                notified INTEGER DEFAULT 1,
                discord_error TEXT
            )
            """
        )

        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(seen)").fetchall()
        }

        # Obsługa starszej wersji bazy danych.
        if "title" not in columns:
            conn.execute("ALTER TABLE seen ADD COLUMN title TEXT")

        if "url" not in columns:
            conn.execute("ALTER TABLE seen ADD COLUMN url TEXT")

        if "first_seen" not in columns:
            conn.execute("ALTER TABLE seen ADD COLUMN first_seen TEXT")

        if "notified" not in columns:
            # Oferty zapisane przez starszą wersję traktujemy jako już
            # obsłużone, aby nie wysłać nagle wielu starych alertów.
            conn.execute(
                "ALTER TABLE seen ADD COLUMN notified INTEGER DEFAULT 1"
            )

        if "discord_error" not in columns:
            conn.execute(
                "ALTER TABLE seen ADD COLUMN discord_error TEXT"
            )


def is_known(listing_id):
    with db_connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM seen WHERE listing_id = ?",
            (str(listing_id),),
        ).fetchone()
    return row is not None


def save_listing(listing):
    listing_id = str(listing.get("listing_id", "")).strip()
    if not listing_id:
        return False

    with db_connect() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO seen
                (listing_id, title, url, first_seen, notified, discord_error)
            VALUES (?, ?, ?, ?, 1, NULL)
            """,
            (
                listing_id,
                str(listing.get("title", "")),
                str(listing.get("url", "")),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    return cursor.rowcount > 0


def count_saved():
    with db_connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS total FROM seen").fetchone()
    return int(row["total"])


# =========================
# EXTRACTR
# =========================

def extract_items(data):
    """Odnajduje listę ofert także w zagnieżdżonej odpowiedzi JSON."""
    if isinstance(data, list):
        if not data:
            return []

        if any(
            isinstance(item, dict)
            and any(key in item for key in ("listing_id", "id", "url"))
            for item in data
        ):
            return data

        for item in data:
            result = extract_items(item)
            if result:
                return result

        return []

    if isinstance(data, dict):
        # Najbardziej prawdopodobne nazwy pól zawierających oferty.
        for key in (
            "listings",
            "items",
            "results",
            "offers",
            "products",
            "data",
        ):
            if key in data:
                result = extract_items(data[key])
                if result:
                    return result

        # Niektóre odpowiedzi mogą zawierać pojedynczą ofertę.
        if any(key in data for key in ("listing_id", "id")):
            return [data]

    return []


def normalize_listing(item):
    if not isinstance(item, dict):
        return None

    listing_id = (
        item.get("listing_id")
        or item.get("id")
        or item.get("item_id")
    )

    url = item.get("url") or item.get("share_url") or ""
    title = item.get("title") or item.get("name") or "Oferta Vinted"

    # Jeśli API nie udostępnia ID, spróbuj użyć URL.
    if not listing_id and url:
        listing_id = url

    if not listing_id:
        return None

    price = (
        item.get("price")
        or item.get("total_price")
        or item.get("discounted_price")
        or "Brak danych"
    )

    currency = item.get("currency") or "PLN"
    brand = item.get("brand") or "Nie podano"
    size = item.get("size") or "Nie podano"
    condition = item.get("condition") or "Nie podano"
    image = item.get("image") or item.get("thumbnail") or ""

    return {
        "listing_id": str(listing_id),
        "title": str(title),
        "url": str(url),
        "price": str(price),
        "currency": str(currency),
        "brand": str(brand),
        "size": str(size),
        "condition": str(condition),
        "image": str(image),
    }


def load_extractr(query, api_key, max_price):
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
        "X-API-Key": api_key,
        "Accept": "application/json",
    }

    response = requests.get(
        API_URL,
        params=params,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code == 429:
        raise RuntimeError(
            "LIMIT 429: Extractr ograniczył liczbę zapytań. "
            "Poczekaj przed kolejnym skanem."
        )

    if not response.ok:
        # Nie pokazujemy nagłówków, klucza API ani innych sekretów.
        body = response.text[:500]
        raise RuntimeError(
            f"Extractr HTTP {response.status_code}: {body}"
        )

    try:
        data = response.json()
    except ValueError:
        raise RuntimeError(
            "Extractr zwrócił odpowiedź, która nie jest poprawnym JSON-em."
        )

    items = extract_items(data)

    if not items:
        if isinstance(data, dict):
            keys = ", ".join(str(key) for key in list(data.keys())[:20])
            details = f"Klucze odpowiedzi: {keys or '(brak)'}"
        else:
            details = f"Typ odpowiedzi: {type(data).__name__}"

        raise RuntimeError(
            "Odpowiedź Extractr nie zawiera rozpoznanej listy ofert. "
            + details
        )

    normalized = []
    for item in items:
        listing = normalize_listing(item)
        if listing:
            normalized.append(listing)

    return normalized


# =========================
# DISCORD
# =========================

def send_discord(webhook_url, listing, query):
    if not webhook_url:
        raise RuntimeError(
            "Brak webhooka Discord. Dodaj DISCORD_WEBHOOK_URL "
            "w Streamlit Secrets."
        )

    embed = {
        "title": listing["title"][:256],
        "url": listing["url"] or None,
        "description": (
            f"**Cena:** {listing['price']} {listing['currency']}\n"
            f"**Marka:** {listing['brand']}\n"
            f"**Rozmiar:** {listing['size']}\n"
            f"**Stan:** {listing['condition']}\n"
            f"**Wyszukiwanie:** {query}"
        )[:4000],
        "footer": {"text": "Vinted Deal Hunter"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if listing["image"].startswith("https://") or listing["image"].startswith("http://"):
        embed["thumbnail"] = {"url": listing["image"]}

    payload = {"embeds": [embed]}

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Nie udało się połączyć z Discordem: {exc}"
        )

    if response.status_code not in (200, 204):
        body = response.text[:500]
        raise RuntimeError(
            f"Discord HTTP {response.status_code}: {body}"
        )


# =========================
# SKANOWANIE
# =========================

def scan_queries(queries, api_key, webhook_url, max_price):
    checked = 0
    new_offers = 0
    sent = 0
    errors = []
    rate_limited = False

    for index, query in enumerate(queries):
        query = query.strip()
        if not query:
            continue

        try:
            listings = load_extractr(query, api_key, max_price)
            checked += len(listings)

            for listing in listings:
                listing_id = listing["listing_id"]

                if is_known(listing_id):
                    continue

                new_offers += 1

                # Jeśli wysyłka nie powiedzie się, oferta NIE zostanie
                # zapisana jako obsłużona. Kolejny skan spróbuje ponownie.
                try:
                    send_discord(webhook_url, listing, query)
                except Exception as exc:
                    message = f"{query} — {listing['title']}: {exc}"
                    errors.append(message)
                    continue

                # Zapis dopiero po potwierdzonej wysyłce.
                if save_listing(listing):
                    sent += 1

        except Exception as exc:
            message = f"{query}: {exc}"
            errors.append(message)

            if "429" in str(exc) or "LIMIT" in str(exc).upper():
                rate_limited = True
                break

        if index < len(queries) - 1 and not rate_limited:
            time.sleep(PAUSE_BETWEEN_QUERIES)

    return {
        "checked": checked,
        "new": new_offers,
        "sent": sent,
        "errors": errors,
        "rate_limited": rate_limited,
    }


# =========================
# INICJALIZACJA
# =========================

def seed_database(queries, api_key, max_price):
    saved = 0
    errors = []
    rate_limited = False

    for index, query in enumerate(queries):
        query = query.strip()
        if not query:
            continue

        try:
            listings = load_extractr(query, api_key, max_price)

            for listing in listings:
                # Inicjalizacja celowo nie wysyła alertów.
                if save_listing(listing):
                    saved += 1

        except Exception as exc:
            errors.append(f"{query}: {exc}")
            if "429" in str(exc) or "LIMIT" in str(exc).upper():
                rate_limited = True
                break

        if index < len(queries) - 1 and not rate_limited:
            time.sleep(PAUSE_BETWEEN_QUERIES)

    return saved, errors


# =========================
# INTERFEJS
# =========================

initialize_database()

st.title("🔎 Vinted Deal Hunter")
st.write("Monitor ofert Vinted i powiadomienia Discord.")

st.info(
    "Bot zapamiętuje identyfikatory ofert i wykrywa nowe pozycje. "
    "To nie jest ścisły filtr ostatnich 24 godzin, ponieważ odpowiedź "
    "Extractr może nie zawierać daty publikacji."
)

api_key = get_secret("EXTRACTR_API_KEY")

webhook_url = get_secret(
    "DISCORD_WEBHOOK_URL",
    "DISCORD_WEBHOOK",
    "DISCORD_WEBHOOK_URL_VINTED",
)

with st.sidebar:
    st.header("⚙️ Ustawienia")

    selected_models = st.multiselect(
        "Modele do monitorowania",
        options=CATALOG,
        default=["Salomon ACS"],
    )

    custom_queries_text = st.text_area(
        "Własne wyszukiwania (jedno na linię)",
        placeholder="np.\nSalomon XT-6 42\nNike Shox",
    )

    max_price = st.number_input(
        "Maksymalna cena w PLN (0 = bez limitu)",
        min_value=0,
        max_value=100000,
        value=0,
        step=50,
    )

    st.caption(
        "Przy wielu wyszukiwaniach skanowanie może potrwać dłużej, "
        "ponieważ aplikacja robi przerwy, aby ograniczyć ryzyko limitu API."
    )

queries = list(selected_models)
custom_queries = [
    line.strip()
    for line in custom_queries_text.splitlines()
    if line.strip()
]

for query in custom_queries:
    if query not in queries:
        queries.append(query)

col1, col2 = st.columns(2)

with col1:
    scan_button = st.button(
        "🔍 Skanuj teraz",
        type="primary",
        use_container_width=True,
    )

with col2:
    seed_button = st.button(
        "🧹 Zapisz aktualne oferty bez alertów",
        use_container_width=True,
    )

if not api_key:
    st.error(
        "Nie znaleziono EXTRACTR_API_KEY. "
        "Dodaj go w ustawieniach Secrets aplikacji Streamlit."
    )

if not webhook_url:
    st.warning(
        "Nie znaleziono webhooka Discord. "
        "Sprawdź nazwę sekretu DISCORD_WEBHOOK_URL."
    )

if scan_button:
    if not api_key:
        st.error("Nie można skanować bez EXTRACTR_API_KEY.")
    elif not queries:
        st.warning("Wybierz przynajmniej jeden model lub wpisz własne wyszukiwanie.")
    else:
        with st.spinner("Skanuję oferty..."):
            result = scan_queries(
                queries,
                api_key,
                webhook_url,
                int(max_price),
            )

        st.subheader("📊 Wynik skanowania")

        a, b, c = st.columns(3)
        a.metric("Sprawdzone oferty", result["checked"])
        b.metric("Nowe oferty", result["new"])
        c.metric("Wysłane na Discord", result["sent"])

        if result["sent"] > 0:
            st.success(f"Wysłano {result['sent']} powiadomień na Discord.")

        if result["new"] > result["sent"]:
            st.warning(
                f"{result['new'] - result['sent']} nowych ofert nie otrzymało "
                "powiadomienia. Nie zostały zapisane jako obsłużone, więc "
                "kolejny skan może spróbować ponownie."
            )

        if result["rate_limited"]:
            st.warning(
                "Wykryto limit API. Skanowanie przerwano, aby nie wykonywać "
                "dalszych zapytań. Poczekaj przed kolejną próbą."
            )

        if result["errors"]:
            with st.expander("⚠️ Szczegóły błędów", expanded=True):
                for error in result["errors"]:
                    st.error(error)
        else:
            st.success("Nie wykryto błędów.")

if seed_button:
    if not api_key:
        st.error("Nie można pobrać ofert bez EXTRACTR_API_KEY.")
    elif not queries:
        st.warning("Wybierz przynajmniej jeden model lub wpisz własne wyszukiwanie.")
    else:
        with st.spinner("Zapisuję aktualne oferty bez wysyłania alertów..."):
            saved, seed_errors = seed_database(
                queries,
                api_key,
                int(max_price),
            )

        st.success(f"Zapisano {saved} nowych identyfikatorów bez alertów.")

        if seed_errors:
            with st.expander("⚠️ Szczegóły błędów", expanded=True):
                for error in seed_errors:
                    st.error(error)

st.divider()
st.caption(f"Zapamiętane identyfikatory ofert: {count_saved()}")
