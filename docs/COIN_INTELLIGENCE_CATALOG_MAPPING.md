# Coin Intelligence — Catalog Mapping Boundary

`coin_catalog.py` is the only boundary that may turn a product-neutral
coin-inference candidate into a site-local `commodity_id`.

It deliberately queries only:

```text
commodities.name == candidate.commodity_name
```

The equality is exact. `commodity_aliases`, text-parser aliases, fuzzy search,
case folding, a fallback Imam ID, and catalog ordering are all prohibited.

Every candidate must resolve to exactly one current catalog row. A zero match,
multiple rows, invalid ID, or returned name that differs from the canonical name
returns `ABSTAIN / CATALOG_CANONICAL_NAME_UNAVAILABLE` for the complete
decision. This includes `CONFIRM`: a UI must never show one candidate whose
eventual ID cannot be safely stored.

The mapper is read-only and does not create offers, write audit records, or
enable a feature. P6 owns submit-time receipt validation, explicit user
confirmation, and minimal append-only decision audit.
