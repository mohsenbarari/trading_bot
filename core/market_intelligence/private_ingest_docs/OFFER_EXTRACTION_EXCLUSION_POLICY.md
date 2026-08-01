# Group offer extraction exclusion policy

Applied in `offer_field_extractor_v2.py` before candidate extraction.  This is
a shadow/staging policy; it does not delete raw messages.

| Condition | Action | Reason |
| --- | --- | --- |
| Coin year `403`/`404`, `1403`/`1404`, or slash variants | Ignore | Sparse cohort; neither 86 nor low-date signal. |
| Thursday / cashier (`پنجشنبه`, common typo forms, or `کشیک`) | Ignore | Half-holiday market, outside current training scope. |
| No structured offer can be produced by the available parsers | Ignore | No synthetic price/product label is created. |
| Explicit commodity conflicts with decisive same-market price context | Review/ignore | A likely text typo is never silently relabeled or promoted. |
| Unnamed price overlaps multiple commodities and prior context is not decisive | Review/ignore | Parser default is not accepted as a market label when the price remains ambiguous. |

Ignored records remain only in the temporary shadow audit table
`offer_component_ignored`, with their reason.  They never enter the final
dataset, trade training, or coin-price model input.

Price context is strictly causal: only earlier offers from the same settlement
and trade form may be used. Explicit earlier observations are primary anchors;
an implicit cluster is secondary evidence and cannot override a close competing
explicit anchor. Because confirmed trades are linked only to accepted offers,
the same abstention gate also blocks their promotion.
