# Group offer extraction exclusion policy

Applied in `offer_field_extractor_v2.py` before candidate extraction.  This is
a shadow/staging policy; it does not delete raw messages.

| Condition | Action | Reason |
| --- | --- | --- |
| Coin year `403`/`404`, `1403`/`1404`, or slash variants | Ignore | Sparse cohort; neither 86 nor low-date signal. |
| Thursday / cashier (`پنجشنبه`, common typo forms, or `کشیک`) | Ignore | Half-holiday market, outside current training scope. |
| No structured offer can be produced by the available parsers | Ignore | No synthetic price/product label is created. |

Ignored records remain only in the temporary shadow audit table
`offer_component_ignored`, with their reason.  They never enter the final
dataset, trade training, or coin-price model input.
