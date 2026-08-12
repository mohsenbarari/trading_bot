# P5-A — ranker محصولیِ کالا از روی قیمت

این لایه فقط Snapshot اتمیک و rate-ready را می‌خواند و هیچ Store/Offer/Trade
یا شبکه‌ای را نمی‌نویسد. ورودی قیمت در واحد پروژه و settlement مشخص است؛
خروجی فقط canonical `commodity_code` و نام طبیعی canonical است، نه
`commodity_id` پایگاه‌داده.

- یک بازهٔ HIGH/MEDIUM یکتا: `AUTO_SELECT`؛
- چند بازهٔ هم‌پوشان یا fallback LOW: `CONFIRM` همراه تمام گزینه‌ها؛
- snapshot کهنه/ناموجود/غیرمعتبر یا قیمت خارج بازه: `ABSTAIN`.

هر نتیجه receipt SHA-256 همان Snapshot و generated timestamp را دارد. P6 در
زمان submit باید receipt/freshness را دوباره کنترل و سپس name را فقط با
`commodities.name` همان site دقیقاً تطبیق دهد. alias و PostgreSQL ID به این
ranker راه ندارند.
