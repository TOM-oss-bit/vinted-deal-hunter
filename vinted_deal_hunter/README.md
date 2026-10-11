# Vinted Deal Hunter — projekt startowy

Panel w Streamlit do wybierania konkretnych modeli, osobnych limitów, alternatywnych nazw, deduplikacji i alertów Discord.

## Uruchomienie lokalne
Wymaga Python 3.10+.

```bash
python -m venv .venv
```
Windows:
```powershell
.venv\\Scripts\\activate
```
macOS/Linux:
```bash
source .venv/bin/activate
```
Następnie:
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Co działa
- katalog 54 modeli z podanymi limitami startowymi dla 9 wybranych modeli;
- włączanie/wyłączanie modeli i edycja limitów;
- dodawanie i późniejsza edycja własnych modeli oraz alternatywnych fraz;
- odczyt listy ofert z JSON;
- deduplikacja po identyfikatorze ogłoszenia;
- wysyłka alertów przez Discord webhook;
- lokalna historia SQLite.

## Co wymaga integracji
Projekt **nie łączy się bezpośrednio z Vinted**. Aby wyszukiwać prawdziwe, nowe ogłoszenia, trzeba podłączyć dozwolone źródło danych w formacie JSON. Nie omija zabezpieczeń ani ograniczeń Vinted. `feed.json` jest pusty i nie udaje prawdziwego źródła ofert.

Przycisk „Sprawdź teraz” uruchamia jednorazowy skan. Monitorowanie 24/7 wymaga dodatkowego procesu harmonogramu i stabilnego źródła danych; samo wdrożenie panelu nie uruchamia jeszcze skanera w tle.

## Discord
Utwórz webhook w ustawieniach kanału Discord i wklej jego URL do panelu. Nie udostępniaj tego adresu — pozwala wysyłać wiadomości na kanał.
