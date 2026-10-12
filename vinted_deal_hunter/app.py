
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import streamlit as st

st.set_page_config(page_title="Vinted Deal Hunter", page_icon="🔎")

API_URL = "https://api.extractr.dev/vinted/search"

CATALOG = [
    "Nike TN",
    "Nike Shox",
    "Nike Air Max",
    "Adidas Samba",
    "Adidas Gazelle",
    "The North Face Nuptse",
    "Polo Ralph Lauren",
    "Moncler jacket",
    "Stone Island sweatshirt",
    "New Balance 2002R",
    "Salomon ACS",
    "Salomon XT-6",
    "Carhartt WIP jacket",
    "Arc'teryx jacket",
]


def get_secret(name):
    value = os.getenv(name)
    if value:
        return value.strip()

    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def extract_items(data):
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("items", "results", "listings", "data"):
            value = data.get(key)

            if isinstance(value, list):
                return value

            if isinstance(value, dict):
                for nested in ("items", "results", "listings"):
                    if isinstance(value.get(nested), list):
                        return value[nested]

    return []


def load_extractr(query, api_key):
    params = {
        "query": query,
        "marketplace": "pl",
        "page": 1,
        "limit": 10,
        "order": "newest_first",
        "currency": "PLN",
    }

    request = Request(
        API_URL + "?" + urlencode(params),
        headers={
            "X-API-Key": api_key,
            "Accept": "application/json",
            "User-Agent": "VintedDealHunter/1.0",
        },
    )

    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(
                    response.read().decode("utf-8")
                )

        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")

            if error.code == 429:
                match = re.search(r"Retry in\s+(\d+)s", body, re.I)
                wait = int(match.group(1)) if match else 40
                raise RuntimeError(
                    f"Limit Extractr: odczekaj {wait} sekund."
                ) from None

            if error.code in (502, 503, 504) and attempt < 2:
                time.sleep(2 ** attempt)
                continue

            raise RuntimeError(
                f"Extractr HTTP {error.code}: {body[:300]}"
            ) from None

        except URLError as error:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue

            raise RuntimeError(
                f"Błąd połączenia: {error.reason}"
            ) from None

    raise RuntimeError("Nie udało się pobrać odpowiedzi API.")


st.title("🔎 Vinted Deal Hunter")
st.caption("Diagnostyka API — sprawdzamy, jakie dane zwraca Extractr.")

api_key = get_secret("EXTRACTR_API_KEY")

if not api_key:
    st.error(
        "Brak EXTRACTR_API_KEY. Dodaj go w Secrets aplikacji Streamlit."
    )
    st.stop()

model = st.selectbox("Wybierz model do sprawdzenia", CATALOG)

if st.button("Sprawdź odpowiedź API", type="primary"):
    try:
        with st.spinner("Pobieram przykładowe wyniki…"):
            data = load_extractr(model, api_key)

        items = extract_items(data)

        st.write("Liczba znalezionych ofert:", len(items))

        if items:
            st.subheader("Pierwsza oferta — pełne pola odpowiedzi")
            st.json(items[0])

            st.subheader("Nazwy pól")
            st.code(", ".join(str(key) for key in items[0].keys()))
        else:
            st.warning(
                "Nie znaleziono listy ofert w odpowiedzi. "
                "Poniżej pełna odpowiedź API."
            )
            st.json(data)

    except Exception as error:
        st.error(str(error))
