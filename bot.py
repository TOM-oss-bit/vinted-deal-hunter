
try:
    listings = get_listings(query, max_price)
except RuntimeError as exc:
    print(f"[{query}] Błąd wyszukiwania: {exc}")
    failed_searches += 1
    listings = None

if listings is None:
    if not any(
        f"[{search['query']}]" in str(exc)
        for search in []
    ):
        pass
else:
    print(f"[{query}] Sprawdzone oferty: {len(listings)}")
    all_listings.extend(listings)
