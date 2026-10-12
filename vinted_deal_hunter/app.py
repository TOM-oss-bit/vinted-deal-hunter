
import os
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

# Automatyczne ponawianie tylko przy przejściowych błędach serwera.
MAX_SERVER_ATTEMPTS = 3
RETRY_DELAYS = [5, 15]

CATALOG = [
    
CATALOG = [
    # Buty
    "Salomon ACS",
    "Salomon XT-6",
    "Salomon XT-4",
    "Salomon Speedcross",
    "Nike Shox",
    "Nike Air Max",
    "Nike Air Force 1",
    "Nike Dunk",
    "Nike P-6000",
    "Nike Vomero",
    "Adidas Campus",
    "Adidas Samba",
    "Adidas Spezial",
    "Adidas Gazelle",
    "Adidas Yeezy",
    "New Balance 2002R",
    "New Balance 1906R",
    "New Balance 9060",
    "Asics Gel-Kayano",
    "Asics Gel-NYC",
    "Asics Gel-1130",
    "Puma Speedcat",
    "Mizuno Wave Rider",
    "On Running",
    "Hoka Clifton",

    # Bluzy i ubrania
    "Nike Tech Fleece",
    "Nike Tech",
    "Nike Hoodie",
    "Adidas Originals",
    "Adidas Essentials",
    "Ralph Lauren Hoodie",
    "Ralph Lauren Quarter Zip",
    "Polo Ralph Lauren",
    "Stone Island",
    "C.P. Company",
    "Carhartt",
    "Carhartt WIP",
    "Trapstar",
    "Supreme",
    "Stussy",
    "Essentials Fear of God",
    "Represent",
    "Palm Angels",
    "Bape",
    "Gallery Dept",

    # Kurtki
    "Moncler Maya",
    "The North Face Nuptse",
    "The North Face",
    "Arc'teryx",
    "Canada Goose",
    "Nike Puffer Jacket",
    "Adidas Track Jacket",

    # Spodnie
    "Nike Tech Fleece Pants",
    "Adidas Track Pants",
    "Carhartt Cargo Pants",
    "Dickies 874",
]

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

        if "title" not in columns:
            conn.execute("ALTER TABLE seen ADD COLUMN title TEXT")

        if "url" not in columns:
            conn.execute("ALTER TABLE seen ADD COLUMN url TEXT")

        if "first_seen" not in columns:
            conn.execute("ALTER TABLE seen ADD COLUMN first_seen TEXT")

        if "notified" not in columns:
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
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM seen"
        ).fetchone()

    return int(row["total"])


# =========================
# ODCZYT ODPOWIEDZI EXTRACTR
# =========================

def extract_items(data):
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

    return {
        "listing_id": str(listing_id),
        "title": str(title),
        "url": str(url),
        "price": str(price),
        "currency": str(item.get("currency") or "PLN"),
        "brand": str(item.get("brand") or "Nie podano"),
        "size": str(item.get("size") or "Nie podano"),
        "condition": str(item.get("condition") or "Nie podano"),
        "image": str(
            item.get("image") or item.get("thumbnail") or ""
        ),
    }


# =========================
# EXTRACTR Z PONAWIANIEM
# =========================

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

    last_error = None

    for attempt in range(MAX_SERVER_ATTEMPTS):
        try:
            response = requests.get(
                API_URL,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )

        except requests.Timeout:
            last_error = (
                f"Przekroczono limit czasu ({REQUEST_TIMEOUT} s)."
            )

            if attempt < MAX_SERVER_ATTEMPTS - 1:
                delay = RETRY_DELAYS[attempt]
                time.sleep(delay)
                continue

            raise RuntimeError(
                f"Extractr nie odpowiedział po "
                f"{MAX_SERVER_ATTEMPTS} próbach. {last_error}"
            )

        except requests.RequestException as exc:
            last_error = f"Błąd połączenia: {exc}"

            if attempt < MAX_SERVER_ATTEMPTS - 1:
                delay = RETRY_DELAYS[attempt]
                time.sleep(delay)
                continue

            raise RuntimeError(
                f"Nie udało się połączyć z Extractr. {last_error}"
            )

        # Limit API: nie ponawiamy zapytania.
        if response.status_code == 429:
            raise RuntimeError(
                "LIMIT 429: Extractr ograniczył liczbę zapytań. "
                "Poczekaj przed kolejnym skanem."
            )

        # Przejściowe błędy serwera: ponawiamy ograniczoną liczbę razy.
        if response.status_code in (502, 503, 504):
            last_error = (
                f"HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )

            if attempt < MAX_SERVER_ATTEMPTS - 1:
                delay = RETRY_DELAYS[attempt]
                time.sleep(delay)
                continue

            raise RuntimeError(
                f"Extractr nadal zwraca błąd serwera po "
                f"{MAX_SERVER_ATTEMPTS} próbach. {last_error}"
            )

        if not response.ok:
            raise RuntimeError(
                f"Extractr HTTP {response.status_code}: "
                f"{response.text[:500]}"
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
                keys = ", ".join(
                    str(key) for key in list(data.keys())[:20]
                )
                details = f"Klucze odpowiedzi: {keys or '(brak)'}"
            else:
                details = f"Typ odpowiedzi: {type(data).__name__}"

            raise RuntimeError(
                "Nie udało się znaleźć listy ofert w odpowiedzi Extractr. "
                + details
            )

        normalized = []

        for item in items:
            listing = normalize_listing(item)
            if listing:
                normalized.append(listing)

        return normalized

    raise RuntimeError(
        f"Nie udało się pobrać ofert. {last_error or ''}"
    )


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

    if listing["url"].startswith(("https://", "http://")):
        embed["url"] = listing["url"]

    if listing["image"].startswith(("https://", "http://")):
        embed["thumbnail"] = {"url": listing["image"]}

    try:
        response = requests.post(
            webhook_url,
            json={"embeds": [embed]},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Nie udało się połączyć z Discordem: {exc}"
        )

    if response.status_code not in (200, 204):
        raise RuntimeError(
            f"Discord HTTP {response.status_code}: "
            f"{response.text[:500]}"
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
            listings = load_extractr(
                query,
                api_key,
                max_price,
            )

            checked += len(listings)

            for listing in listings:
                listing_id = listing["listing_id"]

                if is_known(listing_id):
                    continue

                new_offers += 1

                try:
                    send_discord(
                        webhook_url,
                        listing,
                        query,
                    )
                except Exception as exc:
                    errors.append(
                        f"{query} — {listing['title']}: {exc}"
                    )
                    continue

                if save_listing(listing):
                    sent += 1

        except Exception as exc:
            errors.append(f"{query}: {exc}")

            if (
                "429" in str(exc)
                or "LIMIT" in str(exc).upper()
            ):
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
# ZAPIS BEZ ALERTÓW
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
            listings = load_extractr(
                query,
                api_key,
                max_price,
            )

            for listing in listings:
                if save_listing(listing):
                    saved += 1

        except Exception as exc:
            errors.append(f"{query}: {exc}")

            if (
                "429" in str(exc)
                or "LIMIT" in str(exc).upper()
            ):
                rate_limited = True
                break

        if index < len(queries) - 1 and not rate_limited:
            time.sleep(PAUSE_BETWEEN_QUERIES)

    return saved, errors


# =========================
# INTERFEJS STREAMLIT
# =========================

initialize_database()

st.title("🔎 Vinted Deal Hunter")
st.write("Monitor ofert Vinted i powiadomienia Discord.")

st.info(
    "Bot zapamiętuje identyfikatory ofert i wykrywa wcześniej "
    "nieznane pozycje. To nie jest ścisły filtr ostatnich 24 godzin, "
    "ponieważ API może nie udostępniać daty publikacji."
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
        placeholder="Salomon XT-6 42\nNike Shox",
    )

    max_price = st.number_input(
        "Maksymalna cena w PLN (0 = bez limitu)",
        min_value=0,
        max_value=100000,
        value=0,
        step=50,
    )

    st.caption(
        "Błędy 502/503/504 są ponawiane automatycznie. "
        "Przy wielu wyszukiwaniach bot robi przerwy między zapytaniami."
    )

queries = list(selected_models)

for line in custom_queries_text.splitlines():
    query = line.strip()
    if query and query not in queries:
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
        "Brak EXTRACTR_API_KEY. Dodaj go w ustawieniach Secrets "
        "aplikacji Streamlit."
    )

if not webhook_url:
    st.warning(
        "Brak webhooka Discord. Sprawdź sekret DISCORD_WEBHOOK_URL."
    )

if scan_button:
    if not api_key:
        st.error("Nie można skanować bez EXTRACTR_API_KEY.")
    elif not queries:
        st.warning("Wybierz model lub wpisz własne wyszukiwanie.")
    else:
        with st.spinner(
            "Skanuję oferty. W razie błędu serwera bot spróbuje ponownie..."
        ):
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
            st.success(
                f"Wysłano {result['sent']} powiadomień na Discord."
            )

        if result["new"] > result["sent"]:
            st.warning(
                "Nie wszystkie nowe oferty otrzymały alert. "
                "Sprawdź szczegóły błędów poniżej."
            )

        if result["rate_limited"]:
            st.warning(
                "Extractr zwrócił limit 429. Skanowanie zostało "
                "przerwane. Poczekaj przed kolejną próbą."
            )

        if result["errors"]:
            with st.expander(
                "⚠️ Szczegóły błędów",
                expanded=True,
            ):
                for error in result["errors"]:
                    st.error(error)
        else:
            st.success("Nie wykryto błędów.")

if seed_button:
    if not api_key:
        st.error("Nie można pobrać ofert bez EXTRACTR_API_KEY.")
    elif not queries:
        st.warning("Wybierz model lub wpisz własne wyszukiwanie.")
    else:
        with st.spinner(
            "Zapisuję aktualne oferty bez wysyłania alertów..."
        ):
            saved, seed_errors = seed_database(
                queries,
                api_key,
                int(max_price),
            )

        st.success(
            f"Zapisano {saved} nowych identyfikatorów bez alertów."
        )

        if seed_errors:
            with st.expander(
                "⚠️ Szczegóły błędów",
                expanded=True,
            ):
                for error in seed_errors:
                    st.error(error)

st.divider()
st.caption(
    f"Zapamiętane identyfikatory ofert: {count_saved()}"
)
