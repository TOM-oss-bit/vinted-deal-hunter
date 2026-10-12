
import os
import re
import time
import sqlite3
from datetime import datetime, timezone

import requests
import streamlit as st


# =========================
# KONFIGURACJA
# =========================

st.set_page_config(
    page_title="Vinted Deal Hunter",
    page_icon="🔎",
    layout="wide",
)

API_URL = "https://api.extractr.dev/vinted/search"
DB_PATH = "vinted_deals.db"
RESULTS_PER_QUERY = 20
REQUEST_DELAY_SECONDS = 42
REQUEST_TIMEOUT_SECONDS = 30

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
# SEKRETY
# =========================

def get_secret(name, default=""):
    try:
        value = st.secrets.get(name, default)
        if value:
            return str(value).strip()
    except Exception:
        pass

    return os.getenv(name, default).strip()


def get_discord_webhook():
    return (
        get_secret("DISCORD_WEBHOOK_URL")
        or get_secret("DISCORD_WEBHOOK")
        or get_secret("DISCORD_WEBHOOK_URL_VINTED")
    )


# =========================
# BAZA DANYCH
# =========================

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            listing_id TEXT PRIMARY KEY,
            title TEXT,
            url TEXT,
            price REAL,
            first_seen TEXT,
            query TEXT
        )
    """)
    conn.commit()
    return conn


def safe_price(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def get_seen_ids():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT listing_id FROM seen"
        ).fetchall()
        return {str(row[0]) for row in rows}
    finally:
        conn.close()


def save_listing(item, query):
    listing_id = str(item.get("listing_id", "")).strip()

    if not listing_id:
        return False

    conn = get_connection()

    try:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO seen
            (listing_id, title, url, price, first_seen, query)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            listing_id,
            str(item.get("title", "")),
            str(item.get("url", "")),
            safe_price(item.get("price")),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            query,
        ))

        conn.commit()
        return cursor.rowcount > 0

    finally:
        conn.close()


# =========================
# EXTRACTR API
# =========================

class RateLimitError(Exception):
    def __init__(self, wait_seconds=60):
        self.wait_seconds = wait_seconds
        super().__init__(
            f"Extractr ograniczył liczbę zapytań. "
            f"Odczekaj około {wait_seconds} sekund."
        )


class ExtractrError(Exception):
    pass


def extract_items(data):
    if isinstance(data, list):
        return [
            item for item in data
            if isinstance(item, dict)
        ]

    if isinstance(data, dict):
        for key in ("listings", "items", "results", "data"):
            value = data.get(key)

            if isinstance(value, list):
                return [
                    item for item in value
                    if isinstance(item, dict)
                ]

            if isinstance(value, dict):
                for nested_key in ("listings", "items", "results"):
                    nested = value.get(nested_key)

                    if isinstance(nested, list):
                        return [
                            item for item in nested
                            if isinstance(item, dict)
                        ]

    return []


def load_extractr(query, api_key, max_price=None):
    headers = {
        "X-API-Key": api_key,
        "Accept": "application/json",
    }

    params = {
        "query": query,
        "marketplace": "pl",
        "page": 1,
        "limit": RESULTS_PER_QUERY,
        "order": "newest_first",
    }

    if max_price is not None:
        params["price_to"] = float(max_price)

    try:
        response = requests.get(
            API_URL,
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise ExtractrError(
            f"Błąd połączenia z Extractr: {exc}"
        ) from exc

    if response.status_code == 429:
        wait_seconds = 60

        match = re.search(
            r"(?:retry in|wait)\s*(\d+)\s*s?",
            response.text,
            re.IGNORECASE,
        )

        if match:
            wait_seconds = max(1, int(match.group(1)))

        retry_header = response.headers.get("Retry-After")

        if retry_header and retry_header.isdigit():
            wait_seconds = max(
                wait_seconds,
                int(retry_header),
            )

        raise RateLimitError(wait_seconds)

    if response.status_code in (401, 403):
        raise ExtractrError(
            "Extractr odrzucił klucz API. Sprawdź "
            "EXTRACTR_API_KEY w Streamlit Secrets."
        )

    if response.status_code >= 400:
        raise ExtractrError(
            f"Extractr zwrócił HTTP {response.status_code}: "
            f"{response.text[:400]}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise ExtractrError(
            "Extractr zwrócił nieprawidłową odpowiedź JSON."
        ) from exc

    items = extract_items(data)

    if not items and isinstance(data, dict) and data.get("error"):
        raise ExtractrError(
            f"Extractr zgłosił błąd: {data['error']}"
        )

    return items


# =========================
# DISCORD
# =========================

def send_discord(item, query, webhook_url):
    title = str(item.get("title") or "Oferta Vinted")
    url = str(item.get("url") or "")
    image_url = str(
        item.get("image")
        or item.get("thumbnail")
        or ""
    )

    brand = str(item.get("brand") or "Nie podano")
    size = str(item.get("size") or "Nie podano")
    condition = str(item.get("condition") or "Nie podano")
    currency = str(item.get("currency") or "PLN")

    price = safe_price(item.get("price"))
    total_price = safe_price(item.get("total_price"))

    price_text = (
        f"{price:.2f} {currency}"
        if price is not None
        else f"Nie podano {currency}"
    )

    fields = [
        {
            "name": "Cena",
            "value": price_text,
            "inline": True,
        },
        {
            "name": "Marka",
            "value": brand[:100],
            "inline": True,
        },
        {
            "name": "Rozmiar",
            "value": size[:100],
            "inline": True,
        },
        {
            "name": "Stan",
            "value": condition[:100],
            "inline": True,
        },
        {
            "name": "Wyszukiwana fraza",
            "value": query[:100],
            "inline": True,
        },
    ]

    if total_price is not None:
        fields.append({
            "name": "Cena z opłatą",
            "value": f"{total_price:.2f} {currency}",
            "inline": True,
        })

    embed = {
        "title": title[:250],
        "color": 0x5865F2,
        "fields": fields,
        "footer": {
            "text": "Vinted Deal Hunter"
        },
        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(timespec="seconds"),
    }

    if url.startswith("https://"):
        embed["url"] = url

    if image_url.startswith("https://"):
        embed["thumbnail"] = {
            "url": image_url
        }

    payload = {
        "content": "🔎 **Nowa oferta wykryta na Vinted!**",
        "embeds": [embed],
    }

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=15,
        )
    except requests.RequestException as exc:
        return False, f"Błąd połączenia z Discordem: {exc}"

    if response.status_code not in (200, 204):
        return False, (
            f"Discord zwrócił HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )

    return True, "OK"


# =========================
# SKANOWANIE
# =========================

def scan_queries(queries, api_key, webhook_url, max_price):
    seen_ids = get_seen_ids()

    sent_count = 0
    new_count = 0
    checked_count = 0
    errors = []
    rate_limited = False

    for index, query in enumerate(queries):
        query = query.strip()

        if not query:
            continue

        if index > 0:
            time.sleep(REQUEST_DELAY_SECONDS)

        try:
            items = load_extractr(
                query=query,
                api_key=api_key,
                max_price=max_price,
            )

        except RateLimitError as exc:
            errors.append(str(exc))
            rate_limited = True
            break

        except ExtractrError as exc:
            errors.append(f"{query}: {exc}")
            continue

        checked_count += len(items)

        for item in items:
            listing_id = str(
                item.get("listing_id", "")
            ).strip()

            if not listing_id or listing_id in seen_ids:
                continue

            new_count += 1

            inserted = save_listing(item, query)
            seen_ids.add(listing_id)

            if not inserted:
                continue

            if webhook_url:
                success, message = send_discord(
                    item,
                    query,
                    webhook_url,
                )

                if success:
                    sent_count += 1
                else:
                    errors.append(
                        f"Discord — "
                        f"{item.get('title', listing_id)}: "
                        f"{message}"
                    )

    return {
        "sent": sent_count,
        "new": new_count,
        "checked": checked_count,
        "errors": errors,
        "rate_limited": rate_limited,
    }


def initialize_database(queries, api_key, max_price):
    initialized = 0
    checked = 0
    errors = []
    rate_limited = False

    for index, query in enumerate(queries):
        if index > 0:
            time.sleep(REQUEST_DELAY_SECONDS)

        try:
            items = load_extractr(
                query,
                api_key,
                max_price,
            )

        except RateLimitError as exc:
            errors.append(str(exc))
            rate_limited = True
            break

        except ExtractrError as exc:
            errors.append(f"{query}: {exc}")
            continue

        checked += len(items)

        for item in items:
            if item.get("listing_id"):
                if save_listing(item, query):
                    initialized += 1

    return {
        "initialized": initialized,
        "checked": checked,
        "errors": errors,
        "rate_limited": rate_limited,
    }


# =========================
# INTERFEJS STREAMLIT
# =========================

st.title("🔎 Vinted Deal Hunter")
st.caption(
    "Monitor ofert Vinted i powiadomienia Discord."
)

api_key = get_secret("EXTRACTR_API_KEY")
webhook_url = get_discord_webhook()

if not api_key:
    st.error(
        "Brakuje EXTRACTR_API_KEY w Streamlit Secrets. "
        "Dodaj klucz w ustawieniach aplikacji."
    )
    st.stop()

with st.sidebar:
    st.header("⚙️ Ustawienia")

    selected_queries = st.multiselect(
        "Modele do monitorowania",
        options=CATALOG,
        default=["Salomon ACS"],
    )

    custom_queries = st.text_area(
        "Dodatkowe frazy — jedna na linię",
        placeholder="Salomon XT-6 42\nNike Shox",
    )

    max_price_input = st.number_input(
        "Maksymalna cena w PLN (0 = bez limitu)",
        min_value=0.0,
        value=0.0,
        step=10.0,
    )

    max_price = (
        max_price_input
        if max_price_input > 0
        else None
    )

    if webhook_url:
        st.success("Webhook Discord skonfigurowany")
    else:
        st.warning(
            "Brak webhooka Discord. Alerty nie będą wysyłane."
        )

st.info(
    "Extractr nie zwraca daty publikacji w pokazanych polach. "
    "Bot zapamiętuje identyfikatory ofert i wykrywa te, których "
    "wcześniej nie widział. To nie jest ścisły filtr ostatnich 24 godzin."
)

st.warning(
    "Przy pierwszym uruchomieniu użyj przycisku inicjalizacji, "
    "aby zapisać aktualne oferty bez wysyłania alertów."
)

col1, col2 = st.columns(2)

with col1:
    initialize_clicked = st.button(
        "🧱 Zainicjuj bazę bez alertów",
        use_container_width=True,
    )

with col2:
    scan_clicked = st.button(
        "🔍 Skanuj i wysyłaj alerty",
        type="primary",
        use_container_width=True,
    )

queries = list(selected_queries)

for line in custom_queries.splitlines():
    line = line.strip()

    if line and line not in queries:
        queries.append(line)

if initialize_clicked:
    if not queries:
        st.error(
            "Wybierz przynajmniej jeden model "
            "lub wpisz własną frazę."
        )
    else:
        with st.spinner(
            "Zapisuję istniejące oferty bez alertów..."
        ):
            result = initialize_database(
                queries,
                api_key,
                max_price,
            )

        st.success(
            f"Inicjalizacja zakończona. Zapisano "
            f"{result['initialized']} nowych identyfikatorów "
            f"spośród {result['checked']} sprawdzonych ofert."
        )

        for error in result["errors"]:
            st.error(error)

        if result["rate_limited"]:
            st.warning(
                "Osiągnięto limit API. Odczekaj wskazany czas "
                "przed kolejną próbą."
            )

if scan_clicked:
    if not queries:
        st.error(
            "Wybierz przynajmniej jeden model "
            "lub wpisz własną frazę."
        )
    else:
        with st.spinner("Skanuję oferty..."):
            result = scan_queries(
                queries=queries,
                api_key=api_key,
                webhook_url=webhook_url,
                max_price=max_price,
            )

        st.subheader("📊 Wynik skanowania")

        col1, col2, col3 = st.columns(3)

        col1.metric(
            "Sprawdzone oferty",
            result["checked"],
        )

        col2.metric(
            "Nowe oferty",
            result["new"],
        )

        col3.metric(
            "Wysłane na Discord",
            result["sent"],
        )

        if result["rate_limited"]:
            st.warning(
                "Extractr ograniczył liczbę zapytań. "
                "Bot zatrzymał dalsze skanowanie. "
                "Odczekaj przed kolejną próbą."
            )

        if result["errors"]:
            with st.expander("Szczegóły błędów"):
                for error in result["errors"]:
                    st.error(error)

        if not result["new"] and not result["errors"]:
            st.info(
                "Nie znaleziono ofert z nowymi identyfikatorami. "
                "Spróbuj ponownie później."
            )

try:
    conn = get_connection()

    total_seen = conn.execute(
        "SELECT COUNT(*) FROM seen"
    ).fetchone()[0]

    conn.close()

    st.divider()
    st.caption(
        f"Zapamiętane identyfikatory ofert: {total_seen}"
    )

except Exception as exc:
    st.warning(
        f"Nie udało się odczytać bazy danych: {exc}"
    )
