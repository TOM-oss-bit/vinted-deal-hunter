
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

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
DB_PATH = "deals.db"
MAX_AGE_HOURS = 24

CATALOG = {
    "Nike": [
        "Nike TN", "Nike Shox", "Nike Air Max",
        "Nike Vomero", "Nike P-6000", "Nike Dunk"
    ],
    "Adidas": [
        "Adidas Samba", "Adidas Gazelle",
        "Adidas Campus", "Adidas Spezial"
    ],
    "The North Face": [
        "The North Face Nuptse", "The North Face 1996"
    ],
    "Ralph Lauren": [
        "Polo Ralph Lauren", "Ralph Lauren quarter zip"
    ],
    "Moncler": ["Moncler jacket"],
    "Stone Island": ["Stone Island sweatshirt", "Stone Island jacket"],
    "New Balance": ["New Balance 2002R", "New Balance 1906R"],
    "Salomon": ["Salomon XT-6", "Salomon ACS"],
    "Carhartt WIP": ["Carhartt WIP jacket", "Carhartt WIP pants"],
    "Arc'teryx": ["Arc'teryx jacket", "Arc'teryx shell"],
}


# =========================
# KLUCZE
# =========================

def get_secret(name):
    value = os.getenv(name)

    if value:
        return value.strip()

    try:
        value = st.secrets.get(name, "")
        return str(value).strip() if value else ""
    except Exception:
        return ""


# =========================
# BAZA DANYCH
# =========================

def connect():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS filters (
            model TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 1,
            max_price REAL NOT NULL DEFAULT 300,
            keywords TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen (
            item_id TEXT PRIMARY KEY,
            seen_at TEXT NOT NULL
        )
    """)

    for brand, models in CATALOG.items():
        for model in models:
            conn.execute(
                """
                INSERT OR IGNORE INTO filters
                (model, enabled, max_price, keywords)
                VALUES (?, 1, 300, '')
                """,
                (model,),
            )

    conn.commit()
    return conn


def get_filters():
    conn = connect()
    rows = conn.execute(
        "SELECT model, enabled, max_price, keywords FROM filters ORDER BY model"
    ).fetchall()
    conn.close()

    return {
        row[0]: {
            "enabled": bool(row[1]),
            "max_price": float(row[2]),
            "keywords": row[3] or "",
        }
        for row in rows
    }


def save_filter(model, enabled, max_price, keywords):
    conn = connect()
    conn.execute(
        """
        INSERT INTO filters (model, enabled, max_price, keywords)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(model) DO UPDATE SET
            enabled=excluded.enabled,
            max_price=excluded.max_price,
            keywords=excluded.keywords
        """,
        (model, int(enabled), float(max_price), keywords),
    )
    conn.commit()
    conn.close()


def already_seen(item_id):
    conn = connect()
    found = conn.execute(
        "SELECT 1 FROM seen WHERE item_id = ?",
        (str(item_id),),
    ).fetchone()
    conn.close()
    return found is not None


def mark_seen(item_id):
    conn = connect()
    conn.execute(
        "INSERT OR IGNORE INTO seen (item_id, seen_at) VALUES (?, ?)",
        (str(item_id), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


# =========================
# DATY OFERT
# =========================

DATE_FIELDS = (
    "published_at",
    "created_at",
    "upload_date",
    "createdAt",
    "publishedAt",
    "created",
    "updated_at",
    "timestamp",
    "created_ts",
)


def parse_listing_date(item):
    """
    Zwraca datę UTC, jeśli odpowiedź API zawiera rozpoznawane pole.
    Nie zgaduje daty na podstawie kolejności wyników.
    """
    raw = None

    for key in DATE_FIELDS:
        if item.get(key) is not None:
            raw = item[key]
            break

    if raw is None:
        return None

    try:
        if isinstance(raw, (int, float)):
            # Obsługa sekund lub milisekund Unix.
            stamp = float(raw)
            if stamp > 10_000_000_000:
                stamp /= 1000
            return datetime.fromtimestamp(stamp, tz=timezone.utc)

        value = str(raw).strip()

        if value.isdigit():
            stamp = float(value)
            if stamp > 10_000_000_000:
                stamp /= 1000
            return datetime.fromtimestamp(stamp, tz=timezone.utc)

        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = parsedate_to_datetime(value)

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)

    except (ValueError, TypeError, OverflowError, OSError):
        return None


def is_recent(item):
    published = parse_listing_date(item)

    if published is None:
        return False

    now = datetime.now(timezone.utc)
    return now - timedelta(hours=MAX_AGE_HOURS) <= published <= now + timedelta(minutes=5)


# =========================
# API EXTRACTR
# =========================

def extract_items(data):
    """Obsługuje kilka typowych formatów odpowiedzi JSON."""
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    for key in ("items", "results", "listings", "data"):
        value = data.get(key)

        if isinstance(value, list):
            return value

        if isinstance(value, dict):
            for nested_key in ("items", "results", "listings"):
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    return nested

    return []


def load_extractr(query, api_key, max_price):
    params = {
        "query": query,
        "marketplace": "pl",
        "page": 1,
        "limit": 96,
        "order": "newest_first",
        "currency": "PLN",
        "price_to": max_price,
    }

    url = API_URL + "?" + urlencode(params)
    request = Request(
        url,
        headers={
            "X-API-Key": api_key,
            "Accept": "application/json",
            "User-Agent": "VintedDealHunter/1.0",
        },
        method="GET",
    )

    # Krótkie ponowienia wyłącznie dla błędów serwera.
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
                return extract_items(data)

        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")

            if error.code == 429:
                match = re.search(r"Retry in\s+(\d+)s", body, re.I)
                wait_seconds = int(match.group(1)) if match else 30
                raise RuntimeError(
                    f"LIMIT EXTRACTR: odczekaj co najmniej "
                    f"{wait_seconds} sekund przed kolejnym skanem."
                ) from None

            if error.code in (502, 503, 504) and attempt < 2:
                time.sleep(2 ** attempt)
                continue

            raise RuntimeError(
                f"Extractr HTTP {error.code}: {body[:250]}"
            ) from None

        except URLError as error:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(
                f"Nie można połączyć się z Extractr: {error.reason}"
            ) from None

        except (json.JSONDecodeError, UnicodeDecodeError):
            raise RuntimeError(
                "Extractr zwrócił odpowiedź, której nie można odczytać jako JSON."
            ) from None

    return []


# =========================
# DISCORD
# =========================

def send_discord(webhook, item, model):
    title = str(
        item.get("title")
        or item.get("name")
        or item.get("catalog_title")
        or model
    )

    price = (
        item.get("price")
        or item.get("total_item_price")
        or item.get("discounted_price")
        or "Nie podano"
    )

    url = (
        item.get("url")
        or item.get("item_url")
        or item.get("web_url")
        or item.get("share_url")
        or ""
    )

    payload = {
        "embeds": [{
            "title": title[:250],
            "url": url if str(url).startswith("https://") else None,
            "description": f"Model: {model}\nCena: {price} PLN",
            "color": 5763719,
            "footer": {"text": "Vinted Deal Hunter • nowe oferty"},
        }]
    }

    request = Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urlopen(request, timeout=15) as response:
        return response.status


# =========================
# SKANOWANIE
# =========================

def scan(webhook, api_key, filters):
    sent = 0
    recent_count = 0
    no_date_count = 0
    errors = []
    processed_ids = set()

    active = [
        (model, settings)
        for model, settings in filters.items()
        if settings["enabled"]
    ]

    if not active:
        return {
            "sent": 0,
            "recent": 0,
            "no_date": 0,
            "errors": ["Nie włączono żadnego modelu."],
        }

    for model, settings in active:
        try:
            items = load_extractr(
                model,
                api_key,
                settings["max_price"],
            )

        except RuntimeError as error:
            message = str(error)
            errors.append(f"{model}: {message}")

            # Wspólny limit providera: przerwij, nie wysyłaj
            # kolejnych zapytań, gdy pojawi się 429.
            if "LIMIT EXTRACTR" in message:
                break

            continue

        for item in items:
            if not isinstance(item, dict):
                continue

            # Wymagamy prawdziwej daty publikacji.
            if parse_listing_date(item) is None:
                no_date_count += 1
                continue

            if not is_recent(item):
                continue

            recent_count += 1

            title = str(
                item.get("title")
                or item.get("name")
                or item.get("catalog_title")
                or ""
            )

            raw_price = (
                item.get("price")
                or item.get("total_item_price")
                or item.get("discounted_price")
            )

            try:
                price = float(
                    str(raw_price).replace(" ", "").replace(",", ".")
                    .replace("PLN", "").replace("zł", "")
                )
            except (TypeError, ValueError):
                continue

            if price > settings["max_price"] or price < 0:
                continue

            keywords = [
                word.strip().lower()
                for word in settings["keywords"].split(",")
                if word.strip()
            ]

            if keywords and not any(word in title.lower() for word in keywords):
                continue

            item_id = str(
                item.get("id")
                or item.get("item_id")
                or item.get("url")
                or item.get("item_url")
                or ""
            ).strip()

            if not item_id:
                continue

            if item_id in processed_ids or already_seen(item_id):
                continue

            processed_ids.add(item_id)

            try:
                send_discord(webhook, item, model)
                mark_seen(item_id)
                sent += 1
            except Exception as error:
                errors.append(f"Discord ({model}): {str(error)[:180]}")

    return {
        "sent": sent,
        "recent": recent_count,
        "no_date": no_date_count,
        "errors": errors,
    }


# =========================
# INTERFEJS
# =========================

st.title("🔎 Vinted Deal Hunter")
st.caption("Wyszukiwanie nowych ofert • limit wieku: 24 godziny")

api_key = get_secret("EXTRACTR_API_KEY")

if not api_key:
    st.error(
        "Brakuje EXTRACTR_API_KEY. Dodaj klucz w ustawieniach "
        "Secrets aplikacji Streamlit."
    )
    st.stop()

filters = get_filters()

with st.sidebar:
    st.header("⚙️ Ustawienia")

    webhook = st.text_input(
        "Discord webhook URL",
        type="password",
        help="Adres webhooka Discord. Nie udostępniaj go publicznie.",
    )

    st.divider()
    st.subheader("Modele i limity cen")

    for brand, models in CATALOG.items():
        with st.expander(brand, expanded=False):
            for model in models:
                settings = filters[model]

                enabled = st.checkbox(
                    model,
                    value=settings["enabled"],
                    key=f"enabled_{model}",
                )

                max_price = st.number_input(
                    f"Maksymalna cena — {model}",
                    min_value=0.0,
                    max_value=100000.0,
                    value=float(settings["max_price"]),
                    step=10.0,
                    key=f"price_{model}",
                )

                keywords = st.text_input(
                    f"Słowa w tytule — {model}",
                    value=settings["keywords"],
                    key=f"keywords_{model}",
                    help="Opcjonalnie, oddziel przecinkami.",
                )

                save_filter(model, enabled, max_price, keywords)

    st.caption(
        "Wskazówka: zacznij od jednego lub dwóch modeli, "
        "aby ograniczyć liczbę zapytań do Extractr."
    )

st.info(
    "Bot wysyła tylko oferty, dla których API zwróci rozpoznawaną datę "
    "z ostatnich 24 godzin. Oferty bez daty są pomijane."
)

if st.button("🔍 Sprawdź nowe oferty", type="primary", use_container_width=True):
    if not webhook.startswith("https://"):
        st.error("Wpisz prawidłowy adres Discord webhook.")
    else:
        with st.spinner("Sprawdzam nowe oferty…"):
            result = scan(webhook, api_key, get_filters())

        st.success(f"Wysłano na Discord: {result['sent']} ofert.")
        st.write(f"Sprawdzone oferty z ostatnich 24 h: {result['recent']}")
        st.write(f"Oferty pominięte z powodu braku daty: {result['no_date']}")

        if result["errors"]:
            for error in result["errors"]:
                st.warning(error)

st.caption(
    "Uwaga: dokładne filtrowanie po czasie zależy od pola daty zwracanego "
    "przez API Extractr. Jeśli API używa innej nazwy pola, trzeba ją dodać "
    "do DATE_FIELDS."
)
