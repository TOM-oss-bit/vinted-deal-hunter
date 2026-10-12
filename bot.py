
        try:
            listings = get_listings(query, max_price)
        except RuntimeError as exc:
            print(f"[{query}] Błąd wyszukiwania: {exc}")
            failed_searches += 1
            continue

        if listings is None:
            failed_searches += 1
            continue

        print(f"[{query}] Sprawdzone oferty: {len(listings)}")
        all_listings.extend(listings)
