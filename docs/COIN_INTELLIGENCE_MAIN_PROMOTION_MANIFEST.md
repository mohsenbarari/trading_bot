# Coin intelligence — P0 انتقال به `main`

## وضعیت

**Promotion branch:** `candidate/coin-commodity-inference-promotion`

**Baseline `main`:** `540b2c0c933406368866ffce17a58f5124bfbef8`
**وضعیت:** `P0 COMPLETE — MANIFEST ONLY`

این manifest مشخص می‌کند کدام بخش‌های
`candidate/coin-price-intelligence` برای قابلیت محصولی «تشخیص کالا از قیمت»
مرجع هستند. این فایل اجازهٔ انتقال یا فعال‌سازی نمی‌دهد؛ هر بخش با وضعیت
`REWRITE` باید در همین برنچ از نو و با قرارداد محصولی نوشته شود.

## قواعد انتقال

1. هیچ commit بزرگی از برنچ پژوهشی cherry-pick نمی‌شود. انتقال در patchهای
   کوچک و testable انجام خواهد شد.
2. `main` فعلی فاقد اثرات اجرایی معماری سه‌سروره است. فقط migrationهای آن
   به‌عنوان compatibility constraint در نظر گرفته می‌شوند؛ کد/تنظیمات/worker
   سه‌سروره از برنچ پژوهشی وارد این برنچ نمی‌شوند.
3. Bundle Shadow هیچ‌گاه با تغییر یک flag به bundle محصولی تبدیل نمی‌شود.
   artifact محصولی باید version، checksum، status و promotion approval مستقل
   داشته باشد.
4. Parser گروه‌ها فقط برای دادهٔ خارجی است. `Offer` و `Trade` پروژه مستقیماً
   بعد از commit و بدون parse متن وارد adapter بازار می‌شوند.
5. sourceهای خارجی از هم جدا می‌مانند؛ تتر جایگزین قیمت هرات نیست.

## INCLUDE — قابل انتقال با کمترین تغییر

| مؤلفه | مسیر کاندید | مقصد/نقش در Promotion | شرط انتقال |
| --- | --- | --- | --- |
| قراردادهای پایه | `core/market_intelligence/contracts.py` | قراردادهای immutable Snapshot، status و abstention | حذف واژه/semantic صرفاً Shadow در API محصول |
| Ranker | `core/market_intelligence/ranker.py` | مقایسهٔ قیمت آفر با bandهای canonical | تست کامل ambiguity، overlap و freshness |
| خوانندهٔ Snapshot | `core/market_intelligence/snapshot.py` | خواندن اتمیک و local-first Snapshot | runtime path فقط خارج از checkout |
| validation bundle | `core/market_intelligence/bundle.py` | checksum، schema و catalog validation | status محصولی مستقل از `SHADOW_NOT_PROMOTED` |
| لنگر/رژیم پایه | `anchor_transfer.py`, `low_date.py`, `regime.py` | هرات/تتر، تاریخ پایین و regime پایه | قرارداد P1 و تست point-in-time |
| producer پایه | `producer.py` | ساخت bandهای CASH/TOMORROW | وابسته به Market Store canonical |
| تست عددی پایه | `tests/test_coin_intelligence_ranker.py`, `tests/test_coin_intelligence_snapshot.py`, `tests/test_coin_intelligence_herat_bridge.py` | regression عددی | fixtureها بدون دادهٔ خام/secret |

## REWRITE — منطق قابل استفاده، اما نه با شکل فعلی

| حوزه | مرجع فعلی | بازنویسی لازم |
| --- | --- | --- |
| سرویس inference | `service.py`, `shadow.py` | `CommodityInferenceService` محصولی با `AUTO_SELECT`/`CONFIRM`/`ABSTAIN` و receipt معتبر؛ نه `CoinIntelligenceShadowService` |
| artifact و Snapshot publishing | `pipeline.py`, `scripts/build_coin_intelligence_snapshot.py`, `scripts/run_coin_intelligence_shadow_cycle.py` | worker محصولی، health/rollback و bundle status محصولی؛ بدون acknowledgement Shadow |
| storage عمومی تلگرام | `telegram_collector/storage.py` | schema واحد شامل `price_events` و `external_market_observations`؛ حذف دو schema موازی |
| Collector عمومی | `telegram_collector/*`, `scripts/collect_coin_market_telegram.py` | daemon/scheduler production با secret injection، checkpoint، health و policy retention |
| کانال آبشدهٔ جدید | `scripts/coin_intelligence_private_ingest/gold_*`, `promote_account1_gold_to_main_market_db.py` | worker lifecycle دائمی؛ حفظ physical raw و minute paper weighted؛ نه script دستی |
| گروه‌های سکه | `group_offer_parser.py`, `group_trade_parser.py`, `group_commodity_context.py`, `conversation_quality.py`, private ingest scripts | ingress تولیدی، raw retention سه‌روزه، parser/quality/linking و projection نرمال‌شده |
| تتر و IME | `apps/coin_rate_estimator/telegram_price_collector/external_collectors.py` | external-market adapter مستقل؛ انتقال فقط collector/normalization، نه dashboard یا DB legacy |
| آفر/معامله پروژه | `project_events.py`, `job_queue.py`, `ledger.py` | PostgreSQL outbox پایدار و worker idempotent؛ رویدادهای open/cancel/expire/partial/completed |
| بات و parser مشترک | `bot/utils/offer_parser.py` | حذف default پنهان امام از مسیر inference؛ حفظ fallback policy صریح و استفاده از receipt |
| API و WebApp | `api/routers/offers.py`, `frontend/*` | API contract جدید، submit server-side، preview/confirm UI و E2E؛ هیچ implementation کامل آن در برنچ پژوهشی نیست |
| audit محصول | مدل‌ها/ledger Shadow | جدول مینیمال جدا، privacy-minimized و غیرپژوهشی |
| config و deployment | `config/coin-*.env.example`, `requirements-market-intelligence.txt` | config کوچک و disabled-by-default، volume path و secret boundaries؛ بدون تنظیمات سه‌سروره |

## DEFER — در برنچ پژوهشی باقی می‌ماند

- `online_residual_v1.py`, `residual_research.py` و اسکریپت‌های CatBoost/PySR؛
- `coin_relationship_challenger.py`, `melted_relationship_challenger.py`,
  `relationship_ledger.py` و خروجی‌های discovery؛
- `features_v2.py`, `feature_store_v2.py`, `basis_v2.py`, `hybrid_v2.py`,
  `low_date_v2.py`, `regime_v2.py` تا زمانی که promotion جداگانه تصویب شود؛
- Gemma، `gemma_parser.py` و `Dockerfile.coin-intelligence-gemma`؛
- forecast آیندهٔ آبشده، تقویم پیش‌بینی و roadmap چندافق؛
- مدل‌های Shadow، گزارش‌های پژوهشی و training automation.

## EXCLUDE — نباید وارد این Promotion شود

- `apps/coin_rate_estimator/` شامل dashboard مستقل، login اپراتوری، تحلیل کاربر
  و web server مستقل؛
- کدها، scriptها، composeها، configها، migrationها و documentهای معماری
  سه‌سروره/Writer-Witness/DR؛
- تغییرات نامرتبط queue تلگرام و API/trade delivery؛
- raw exportها، session تلگرام، API key، phone، SQLite runtime DB، model artifact
  runtime، log و training dataset؛
- هر جدول `coin_intelligence_shadow_*` صرفاً برای پژوهش، مگر migration جدید
  و مستقل audit محصول در P5 به‌طور صریح تصویب شود.

## گراف وابستگی و ترتیب انتقال

```text
P1 canonical market contract
    ├── P2 external/public/private collectors ─┐
    └── P3 project PostgreSQL outbox adapter ───┼──> P4 Snapshot / anchors / regime
                                                │             │
                                                │             └──> P5 authoritative inference
                                                │                         │
                                                └─────────────────────────┴──> P6 Bot + API + WebApp
                                                                                  │
                                                                                  └──> P7 shadow-visible rollout
```

P2 و P3 فقط پس از تثبیت schema P1 می‌توانند به موازات هم توسعه یابند. P5
به Snapshot معتبر P4 نیاز دارد. P6 هرگز نباید implementation inference را
در client تکرار کند؛ server مرجع تصمیم است.

## Baseline test contract

پیش از نخستین کد P1، این خانواده‌ها روی commit baseline اجرا و نتیجه ثبت
شده‌اند:

```text
tests.test_manual_offer_validation
tests.test_offer_creation_service
tests.test_offers_router_create_guards
tests.test_offers_router_create_success
tests.test_offers_router_reads
tests.test_offers_router_expire
tests.test_trades_router_authoritative_guards
tests.test_trades_router_authoritative_success
tests.test_bot_trade_create_text_offer_parse_flow
tests.test_migration_smoke
```

تست‌ها فقط با environment ساختگیِ فاقد endpoint و credential واقعی اجرا
می‌شوند. هر مرحله باید این baseline را به‌علاوهٔ testهای افزودهٔ خودش دوباره
اجرا کند.
