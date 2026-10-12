
import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError

import streamlit as st

ROOT = Path(__file__).parent
DB = ROOT / "deals.db"
API_URL = "https://api.extractr.dev/vinted/search"

CATALOG = {
    "Nike": [
        "Nike Tech Fleece", "Nike Air Max 95", "Nike Air Max Plus (TN)",
        "Nike Shox TL", "Nike Dunk Low", "Nike Air Force 1",
        "Nike Air Jordan 1", "Nike Air Jordan 4", "Nike P-6000",
        "Nike Vomero 5"
    ],
    "Adidas": [
        "Adidas Campus 00s", "Adidas Samba OG", "Adidas Gazelle",
        "Adidas Handball Spezial", "Adidas Firebird Track Top",
        "Adidas Adicolor Classics", "Adidas Yeezy 350"
    ],
    "The North Face": [
        "TNF Nuptse 700", "TNF Denali Fleece", "TNF Mountain Jacket",
        "TNF Himalayan Parka", "TNF 1996 Retro Nuptse"
    ],
    "Ralph Lauren": [
        "Ralph Lauren Quarter Zip", "Ralph Lauren Polo Bear",
        "Ralph Lauren Cable Knit", "Ralph Lauren Pima Polo",
        "Ralph Lauren Track Jacket"
    ],
    "Moncler": [
        "Moncler Maya", "Moncler Montbeliard", "Moncler Grenoble",
        "Moncler Acorus", "Moncler polo z logo"
    ],
    "Stone Island": [
        "Stone Island Soft Shell-R", "Stone Island Ghost Piece",
        "Stone Island overshirt", "Stone Island sweatshirt badge",
        "Stone Island cargo pants"
    ],
    "New Balance": [
        "New Balance 2002R", "New Balance 1906R", "New Balance 9060",
        "New Balance 530", "New Balance 990v6"
    ],
    "Salomon": [
        "Salomon XT-6", "Salomon ACS Pro", "Salomon XT-4",
        "Salomon Speedcross 3"
    ],
    "Carhartt WIP": [
        "Carhartt Detroit Jacket", "Carhartt Active Jacket",
        "Carhartt Double Knee", "Carhartt WIP Nimbus Pullover"
    ],
    "Arc'teryx": [
        "Arc'teryx Beta AR", "Arc'teryx Atom Hoody",
        "Arc'teryx Gamma MX", "Arc'teryx Alpha SV"
    ],
}

DEFAULTS = {
    "Nike Tech Fleece": 30,
    "TNF Nuptse 700": 100,
    "Ralph Lauren Quarter Zip": 70,
    "Moncler Maya": 160,
    "Nike Shox TL": 139,
    "Nike Air Jordan 4": 50,
    "Moncler Montbeliard": 140,
    "Stone Island Soft Shell-R": 130,
    "New Balance 2002R": 50,
}


def get_api_key():
    """Odczytuje klucz ze zmiennej środowiskowej lub sekretów Streamlit."""
    key = os.environ.get("EXTRACTR_API_KEY", "").strip()

    if key:
        return key

    try:
        return str(st.secrets.get("EXTRACTR_API_KEY", "")).strip()
    except Exception:
        return ""


def connect():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE IF NOT EXISTS filters(
            model TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL,
            max_price REAL NOT NULL,
            keywords TEXT NOT NULL DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS seen(
            listing_id TEXT PRIMARY KEY,
            title TEXT,
            url TEXT,
            price REAL,
            model TEXT,
            seen_at TEXT
        )
    """)
    c.commit()
    return c


def init():
    c = connect()
    for brand, models in CATALOG.items():
        for model in models:
            c.execute(
                """INSERT OR IGNORE INTO filters
                   (model, enabled, max_price, keywords)
                   VALUES (?, ?, ?, '')""",
                (model, int(model in DEFAULTS), DEFAULTS.get(model, 100)),
            )
    c.commit()
    c.close()


def get_filters():
    c = connect()
    rows = [dict(x) for x in c.execute(
        "SELECT * FROM filters ORDER BY model"
    )]
    c.close()
    return {x["model"]: x for x in rows}


def save(model, enabled, price, keywords):
    c = connect()
    c.execute("""
        INSERT INTO filters(model, enabled, max_price, keywords)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(model) DO UPDATE SET
            enabled=excluded.enabled,
            max_price=excluded.max_price,
            keywords=excluded.keywords
    """, (model, int(enabled), float(price), keywords))
    c.commit()
    c.close()


def send_discord(webhook, item, model):
    payload = {
        "content": (
            f"🔎 **Okazja: {model}**\n"
            f"**{item['title']}**\n"
            f"Cena: **{item['price']:.2f} zł**\n"
            f"{item['url']}"
        )
    }

    req = Request(
        webhook,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "DealHunter/1.0",
        },
        method="POST",
    )

    with urlopen(req, timeout=15) as response:
        return response.status


def load_feed(url):
    if url.strip():
        req = Request(
            url.strip(),
            headers={"User-Agent": "DealHunter/1.0"},
        )
        with urlopen(req, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    else:
        fp = ROOT / "feed.json"
        if not fp.exists():
            return []
        data = json.loads(fp.read_text(encoding="utf-8"))

    return data.get("items", []) if isinstance(data, dict) else data


def load_extractr(query, api_key, max_price):
    """Pobiera jedną stronę wyników z polskiego Vinted."""
    params = {
        "query": query,
        "marketplace": "pl",
        "page": 1,
        "limit": 96,
        "order": "newest_first",
        "currency": "PLN",
        "price_to": max_price,
    }

    req = Request(
        API_URL + "?" + urlencode(params),
        headers={
            "X-API-Key": api_key,
            "Accept": "application/json",
            "User-Agent": "DealHunter/1.0",
        },
        method="GET",
    )

    try:
        with urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        if e.code == 401:
            raise RuntimeError(
                "Extractr odrzucił klucz API (401). "
                "Sprawdź sekret EXTRACTR_API_KEY."
            ) from None
        if e.code == 402:
            raise RuntimeError(
                "Extractr: niewystarczająca liczba kredytów."
            ) from None
        raise RuntimeError(
            f"Extractr HTTP {e.code}: {detail[:500]}"
        ) from None
    except URLError as e:
        raise RuntimeError(
            f"Nie można połączyć się z Extractr: {e.reason}"
        ) from None

    if not isinstance(data, dict):
        raise RuntimeError("Extractr zwrócił nieoczekiwany format JSON.")

    if data.get("error"):
        raise RuntimeError(
            "Extractr: " + str(data["error"])[:500]
        )

    listings = data.get("listings", [])
    if not isinstance(listings, list):
        raise RuntimeError(
            "Brak listy listings w odpowiedzi Extractr."
        )

    results = []

    for raw in listings:
        try:
            price = float(raw.get("price"))
        except (TypeError, ValueError):
            continue

        results.append({
            "id": str(
                raw.get("listing_id")
                or raw.get("id")
                or raw.get("url")
                or ""
            ),
            "title": str(raw.get("title") or ""),
            "url": str(raw.get("url") or ""),
            "price": price,
            "currency": str(raw.get("currency") or "PLN"),
            "size": str(raw.get("size") or ""),
            "condition": str(raw.get("condition") or ""),
        })

    return results


def scan(feed_url, webhook, api_key):
    filters = [
        f for f in get_filters().values()
        if f["enabled"]
    ]

    if not filters:
        raise ValueError("Włącz przynajmniej jeden model.")

    if api_key:
        # Jedno zapytanie na każdy aktywny model.
        # Każde udane zapytanie kosztuje 3 kredyty.
        listings = []
        errors = []
        for f in filters:
            try:
                results = load_extractr(
                    f["model"], api_key, f["max_price"]
                )
                listings.extend(results)
            except Exception as e:
                errors.append(f"{f['model']}: {e}")

        # Nie traktuj błędów API jako pustych wyników.
        if errors and not listings:
            raise RuntimeError("\n".join(errors))

    else:
        if not feed_url.strip():
            raise ValueError(
                "Brak klucza EXTRACTR_API_KEY. "
                "Dodaj sekret do środowiska aplikacji "
                "albo podaj adres źródła JSON."
            )
        listings = load_feed(feed_url)
        errors = []

    if not isinstance(listings, list):
        raise ValueError(
            "Źródło JSON musi być listą lub obiektem z polem items."
        )

    found = []
    c = connect()

    try:
        for raw in listings:
            try:
                item = {
                    "id": str(
                        raw.get("id")
                        or raw.get("listing_id")
                        or raw.get("url")
                        or ""
                    ),
                    "title": str(raw.get("title") or ""),
                    "url": str(raw.get("url") or ""),
                    "price": float(raw.get("price", 0)),
                    "currency": str(raw.get("currency") or "PLN"),
                }

                if not item["id"] or not item["title"] or not item["url"]:
                    continue

                # Ceny z polskiego rynku powinny być w PLN.
                if item["currency"].upper() != "PLN":
                    continue

                if c.execute(
                    "SELECT 1 FROM seen WHERE listing_id=?",
                    (item["id"],),
                ).fetchone():
                    continue

                title = item["title"].casefold()

                for f in filters:
                    terms = [
                        f["model"].casefold(),
                        *[
                            x.strip().casefold()
                            for x in f["keywords"].split(",")
                            if x.strip()
                        ],
                    ]

                    if (
                        item["price"] <= float(f["max_price"])
                        and any(term in title for term in terms)
                    ):
                        # Przy błędzie Discorda nie oznaczaj oferty
                        # jako obsłużonej — kolejny skan może ją ponowić.
                        if webhook.strip():
                            try:
                                send_discord(webhook.strip(), item, f["model"])
                            except Exception as e:
                                errors.append(
                                    f"Discord ({item['title']}): {e}"
                                )
                                continue

                        c.execute(
                            """INSERT OR IGNORE INTO seen
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (
                                item["id"],
                                item["title"],
                                item["url"],
                                item["price"],
                                f["model"],
                                datetime.now(timezone.utc).isoformat(),
                            ),
                        )

                        found.append({**item, "model": f["model"]})
                        break

            except Exception as e:
                errors.append(str(e))

        c.commit()
    finally:
        c.close()

    return len(listings), found, errors


# -------------------- INTERFEJS --------------------

st.set_page_config(
    page_title="Vinted Deal Hunter",
    page_icon="🔎",
    layout="wide",
)

init()
st.title("🔎 Vinted Deal Hunter")
st.caption(
    "Prawdziwe oferty z Extractr • limity cenowe • "
    "historia • Discord"
)

api_key = get_api_key()

with st.sidebar:
    st.header("Połączenia")

    if api_key:
        st.success("Klucz Extractr został wykryty.")
    else:
        st.warning(
            "Nie wykryto EXTRACTR_API_KEY w środowisku aplikacji."
        )

    feed_url = st.text_input(
        "Adres źródła JSON (opcjonalnie)",
        placeholder="https://twoj-dostawca.example/feed.json",
        help="Używany tylko wtedy, gdy nie skonfigurowano klucza Extractr.",
    )

    webhook = st.text_input(
        "Discord webhook URL",
        type="password",
    )

    if st.button(
        "🔍 Sprawdź teraz",
        type="primary",
        use_container_width=True,
    ):
        try:
            total, found, errors = scan(
                feed_url, webhook, api_key
            )
            st.session_state["scan"] = (total, found, errors)
            st.success(
                f"Sprawdzono {total} pozycji; "
                f"nowych dopasowań: {len(found)}."
            )
        except Exception as e:
            st.error(f"Błąd skanowania: {e}")

    if st.button("Wyślij test Discord", use_container_width=True):
        try:
            if not webhook.strip():
                raise ValueError("Wpisz Discord webhook URL.")

            send_discord(
                webhook.strip(),
                {
                    "title": "Test połączenia",
                    "price": 0,
                    "url": "https://www.vinted.pl/",
                },
                "Test",
            )
            st.success("Wysłano wiadomość testową.")
        except Exception as e:
            st.error(f"Nie udało się wysłać: {e}")


t1, t2, t3 = st.tabs([
    "🎯 Modele i ceny",
    "📬 Historia ofert",
    "📘 Podłączenie źródła",
])

with t1:
    st.write(
        "Włącz modele, ustaw limit ceny i opcjonalne "
        "alternatywne frazy oddzielone przecinkami."
    )

    current = get_filters()

    for brand, models in CATALOG.items():
        active_count = sum(
            1 for m in models
            if current.get(m, {}).get("enabled", 0)
        )

        with st.expander(
            f"{brand} · {active_count} aktywnych",
            expanded=brand in [
                "Nike", "The North Face", "Ralph Lauren", "Moncler"
            ],
        ):
            for model in models:
                f = current.get(
                    model,
                    {"enabled": 0, "max_price": 100, "keywords": ""},
                )
                a, b, c = st.columns([3, 1, 3])

                with a:
                    enabled = st.checkbox(
                        model,
                        value=bool(f["enabled"]),
                        key="en_" + model,
                    )
                with b:
                    price = st.number_input(
                        "Limit zł",
                        min_value=0.0,
                        value=float(f["max_price"]),
                        step=10.0,
                        key="pr_" + model,
                    )
                with c:
                    keywords = st.text_input(
                        "Alternatywne frazy",
                        value=f["keywords"],
                        key="kw_" + model,
                    )

                save(model, enabled, price, keywords)

    known_models = {
        m for models in CATALOG.values() for m in models
    }
    custom_rows = [
        f for name, f in get_filters().items()
        if name not in known_models
    ]

    if custom_rows:
        with st.expander("🧩 Twoje własne modele", expanded=True):
            for f in custom_rows:
                a, b, c = st.columns([3, 1, 3])

                with a:
                    enabled = st.checkbox(
                        f["model"],
                        value=bool(f["enabled"]),
                        key="custom_en_" + f["model"],
                    )
                with b:
                    price = st.number_input(
                        "Limit zł",
                        min_value=0.0,
                        value=float(f["max_price"]),
                        step=10.0,
                        key="custom_pr_" + f["model"],
                    )
                with c:
                    keywords = st.text_input(
                        "Alternatywne frazy",
                        value=f["keywords"],
                        key="custom_kw_" + f["model"],
                    )

                save(f["model"], enabled, price, keywords)

    with st.expander("➕ Dodaj własny model"):
        with st.form("add_model"):
            name = st.text_input("Nazwa modelu / fraza")
            price = st.number_input(
                "Limit ceny (zł)",
                min_value=0.0,
                value=100.0,
                step=10.0,
            )
            keywords = st.text_input(
                "Alternatywne nazwy (przecinki)"
            )
            enabled = st.checkbox("Aktywny", value=True)
            submitted = st.form_submit_button("Dodaj model")

            if submitted:
                if name.strip():
                    save(name.strip(), enabled, price, keywords)
                    st.success(
                        "Model dodany. Odśwież widok, aby zobaczyć zmiany."
                    )
                else:
                    st.error("Wpisz nazwę modelu.")


with t2:
    c = connect()
    rows = [
        dict(r) for r in c.execute(
            "SELECT * FROM seen ORDER BY seen_at DESC LIMIT 250"
        )
    ]
    c.close()

    if rows:
        st.dataframe(
            rows,
            use_container_width=True,
            hide_index=True,
            column_config={
                "url": st.column_config.LinkColumn("Link do oferty")
            },
        )
    else:
        st.info("Brak zapisanych dopasowań.")

    if "scan" in st.session_state:
        total, found, errors = st.session_state["scan"]

        if found:
            st.subheader("Nowe dopasowania")
            st.dataframe(
                found,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "url": st.column_config.LinkColumn("Link")
                },
            )

        for error in errors:
            st.warning(error)


with t3:
    st.markdown("""
### Extractr API

Aplikacja korzysta z endpointu:

`https://api.extractr.dev/vinted/search`

Klucz jest przekazywany w nagłówku `X-API-Key`.
Nie umieszczaj go w kodzie ani w pliku `feed.json`.

### Sekret API

Jeśli uruchamiasz aplikację przez GitHub Actions,
workflow musi przekazać sekret do środowiska:

`EXTRACTR_API_KEY`

Jeśli korzystasz z Streamlit Community Cloud,
dodaj klucz również w ustawieniach Secrets tej aplikacji.
Sekrety GitHub Actions nie są automatycznie dostępne
w niezależnie uruchomionej aplikacji Streamlit.

### Koszt

Każde udane zapytanie wyszukiwania kosztuje 3 kredyty.
Wykonanie skanu dla wielu aktywnych modeli może oznaczać
wiele osobnych zapytań.

### Historia

Baza `deals.db` zapamiętuje już obsłużone ogłoszenia.
Jeśli środowisko hostingowe usuwa lokalne pliki przy restarcie,
historia SQLite może nie być trwała.

### Automatyzacja

Przycisk „Sprawdź teraz” wykonuje jeden skan.
Automatyczne sprawdzanie w tle wymaga dodatkowego harmonogramu.
""")

    c=connect(); c.execute('INSERT INTO filters VALUES(?,?,?,?) ON CONFLICT(model) DO UPDATE SET enabled=excluded.enabled,max_price=excluded.max_price,keywords=excluded.keywords',(model,int(enabled),float(price),keywords)); c.commit(); c.close()

def send_discord(webhook, item, model):
    payload={'content':f"🔎 **Okazja: {model}**\n**{item['title']}**\nCena: **{item['price']:.2f} zł**\n{item['url']}"}
    req=Request(webhook,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','User-Agent':'DealHunter/1.0'},method='POST')
    with urlopen(req,timeout=15) as r: return r.status

def load_feed(url):
    if url.strip():
        req=Request(url.strip(),headers={'User-Agent':'DealHunter/1.0'})
        with urlopen(req,timeout=15) as r: data=json.loads(r.read().decode('utf-8'))
    else:
        fp=ROOT/'feed.json'
        if not fp.exists(): return []
        data=json.loads(fp.read_text(encoding='utf-8'))
    return data.get('items',[]) if isinstance(data,dict) else data

def scan(feed_url, webhook):
    listings=load_feed(feed_url); filters=[f for f in get_filters().values() if f['enabled']]
    if not isinstance(listings,list): raise ValueError('Źródło JSON musi być listą lub obiektem z polem items.')
    found=[]; errors=[]; c=connect()
    for raw in listings:
        try:
            item={'id':str(raw.get('id') or raw.get('url') or ''),'title':str(raw.get('title') or ''),'url':str(raw.get('url') or ''),'price':float(raw.get('price',0))}
            if not item['id'] or not item['title'] or not item['url']: continue
            for f in filters:
                title=item['title'].casefold()
                terms=[f['model'].casefold()]+[x.strip().casefold() for x in f['keywords'].split(',') if x.strip()]
                if item['price']<=f['max_price'] and any(t in title for t in terms):
                    if c.execute('SELECT 1 FROM seen WHERE listing_id=?',(item['id'],)).fetchone(): break
                    try:
                        if webhook.strip(): send_discord(webhook.strip(),item,f['model'])
                    except Exception as e: errors.append(f"Discord: {e}")
                    c.execute('INSERT OR IGNORE INTO seen VALUES(?,?,?,?,?,?)',(item['id'],item['title'],item['url'],item['price'],f['model'],datetime.now(timezone.utc).isoformat()))
                    found.append({**item,'model':f['model']}); break
        except Exception as e: errors.append(str(e))
    c.commit(); c.close(); return len(listings),found,errors

st.set_page_config(page_title='Vinted Deal Hunter',page_icon='🔎',layout='wide')
init()
st.title('🔎 Vinted Deal Hunter')
st.caption('Wiele modeli • osobne limity cen • deduplikacja • Discord')
with st.sidebar:
    st.header('Połączenia')
    feed_url=st.text_input('Adres źródła JSON (opcjonalnie)',placeholder='https://twoj-dostawca.example/feed.json')
    webhook=st.text_input('Discord webhook URL',type='password')
    st.warning('Aplikacja nie pobiera bezpośrednio danych z Vinted. Wymaga źródła JSON, z którego korzystanie jest dozwolone.')
    if st.button('🔍 Sprawdź teraz',type='primary',use_container_width=True):
        try:
            total,found,errors=scan(feed_url,webhook); st.session_state['scan']=(total,found,errors); st.success(f'Sprawdzono {total} pozycji; nowych dopasowań: {len(found)}.')
        except Exception as e: st.error(f'Błąd źródła: {e}')
    if st.button('Wyślij test Discord',use_container_width=True):
        try: send_discord(webhook,{'title':'Test połączenia','price':0,'url':'https://www.vinted.pl/'},'Test'); st.success('Wysłano wiadomość testową.')
        except Exception as e: st.error(f'Nie udało się wysłać: {e}')

t1,t2,t3=st.tabs(['🎯 Modele i ceny','📬 Historia ofert','📘 Podłączenie źródła'])
with t1:
    st.write('Włącz wybrane modele, ustaw limit i opcjonalne alternatywne frazy (oddziel przecinkami).')
    current=get_filters()
    for brand,models in CATALOG.items():
        with st.expander(f'{brand} · {sum(1 for m in models if current.get(m,{}).get("enabled",0))} aktywnych',expanded=brand in ['Nike','The North Face','Ralph Lauren','Moncler']):
            for model in models:
                f=current.get(model,{'enabled':0,'max_price':100,'keywords':''})
                a,b,c=st.columns([3,1,3])
                with a: enabled=st.checkbox(model,value=bool(f['enabled']),key='en_'+model)
                with b: price=st.number_input('Limit zł',min_value=0.0,value=float(f['max_price']),step=10.0,key='pr_'+model)
                with c: keywords=st.text_input('Alternatywne frazy',value=f['keywords'],key='kw_'+model,help='Wpisz frazy oddzielone przecinkami')
                save(model,enabled,price,keywords)
    known_models={m for models in CATALOG.values() for m in models}
    custom_rows=[f for name,f in get_filters().items() if name not in known_models]
    if custom_rows:
        with st.expander('🧩 Twoje własne modele',expanded=True):
            for f in custom_rows:
                a,b,c=st.columns([3,1,3])
                with a: enabled=st.checkbox(f['model'],value=bool(f['enabled']),key='custom_en_'+f['model'])
                with b: price=st.number_input('Limit zł',min_value=0.0,value=float(f['max_price']),step=10.0,key='custom_pr_'+f['model'])
                with c: keywords=st.text_input('Alternatywne frazy',value=f['keywords'],key='custom_kw_'+f['model'])
                save(f['model'],enabled,price,keywords)
    with st.expander('➕ Dodaj własny model'):
        with st.form('add_model'):
            name=st.text_input('Nazwa modelu / fraza'); price=st.number_input('Limit ceny (zł)',min_value=0.0,value=100.0,step=10.0); keywords=st.text_input('Alternatywne nazwy (przecinki)'); enabled=st.checkbox('Aktywny',value=True); submitted=st.form_submit_button('Dodaj model')
            if submitted:
                if name.strip(): save(name.strip(),enabled,price,keywords); st.success('Model dodany. Odśwież widok, aby zobaczyć zmiany.')
                else: st.error('Wpisz nazwę modelu.')
with t2:
    c=connect(); rows=[dict(r) for r in c.execute('SELECT * FROM seen ORDER BY seen_at DESC LIMIT 250')]; c.close()
    if rows: st.dataframe(rows,use_container_width=True,hide_index=True,column_config={'url':st.column_config.LinkColumn('Link do oferty')})
    else: st.info('Brak dopasowań. Podłącz źródło ofert i kliknij „Sprawdź teraz”.')
    if 'scan' in st.session_state:
        total,found,errors=st.session_state['scan']
        if found: st.subheader('Nowe dopasowania'); st.dataframe(found,use_container_width=True,hide_index=True,column_config={'url':st.column_config.LinkColumn('Link')})
        for e in errors: st.warning(e)
with t3:
    st.markdown('''### To, co trzeba podłączyć
Ta aplikacja obsługuje konfigurację, filtrowanie, historię i Discord, ale nie ma gotowego konektora do Vinted. Potrzebne jest dozwolone źródło ofert: np. oficjalny feed/API lub dostawca uprawniony do udostępniania danych. Nie omija zabezpieczeń, logowania ani limitów serwisu.

Wklej URL feedu JSON w panelu bocznym albo umieść plik `feed.json` obok `app.py`. Oczekiwany format:
```json
[
  {"id":"unikalne-id","title":"Nike Tech Fleece hoodie","url":"https://www.vinted.pl/items/...","price":29.0,"currency":"PLN","size":"M","condition":"Bardzo dobry"}
]
```
Może to być też obiekt `{ "items": [...] }`. Przykładowy `feed.json` w paczce zawiera wyłącznie dane demonstracyjne, nie prawdziwe ogłoszenia.

### Discord
W Discordzie utwórz webhook w ustawieniach kanału, a jego adres wklej w polu po lewej. Traktuj ten adres jak hasło i nikomu go nie wysyłaj.

### Uwaga o pracy 24/7
Przycisk „Sprawdź teraz” wykonuje pojedyncze sprawdzenie. Automatyczne skanowanie w tle co określony czas wymaga osobnego procesu harmonogramu oraz źródła danych. Sam hosting panelu nie oznacza jeszcze działającego monitora 24/7.''')
