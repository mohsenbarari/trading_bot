# Public Profile Release Note

این feature در scope تعریف شده roadmap بسته شده است.

- public contract اکنون بدون نشت `role` و با contextهای accountant/customer کنترل شده ارائه می شود.
- `PublicProfile.vue` اکنون presence مشترک با messenger، block UX capability-aware، project-users directory، و history filter/export هم راستا با backend را ارائه می کند.
- history export برای web و bot روی service مشترک `trade_history_export_service.py` تکیه می کند و `PDF` bot دیگر placeholder نیست.
- validation نهایی closure:
  - backend focused bundle: `47/47` سبز
  - frontend unit (`PublicProfile.test.ts`): `27/27` سبز
  - Playwright browser matrix (`trade-history-accountant.spec.ts`): `18/18` سبز

نتیجه: public profile در این release از نظر scope، تست، و مستندسازی بسته است.