# ماتریس ضرورت محتوا — Stage 5 Customer & Accountant Workspaces

وضعیت: **`stage5_complete_policy_implemented_browser_validated_sites_proven`**؛ preview Sites فقط evidence خصوصی owner-only است و محصول را deploy نکرده است.

قاعده: هر واحد همیشه‌نمایان باید یک تصمیم، اقدام، وضعیت اثرگذار یا ریسک واقعی را روشن کند. عرض desktop مجوز افزودن KPI یا metadata نیست.

| سطح / state | Keep | On demand / شرطی | Remove / ممنوع | دلیل |
| --- | --- | --- | --- | --- |
| row مشتری | نام رابطه، disambiguator لازم، status اثرگذار، affordance ورود | pending deadline/SMS فقط در queue | count کل/active/tier، آمار، نشست، backend/server | scan سریع و حداقل افشا |
| row حسابدار | نام رابطه، account name لازم، status اثرگذار، affordance ورود | duty فقط در detail | count، session count، IP/home-server، metadata نقش | task-first |
| pending queue | identity، deadline، SMS state، copy، cancel | count فقط برای queue actionable | تکرار deadline/status، bot/API metadata | اقدام محدود به دعوت باز |
| create | fieldهای لازم، validation و submit state | detail کمک فقط کنار field لازم | KPI ظرفیت و توضیح permission تکراری | جلوگیری از فرم متراکم |
| customer finance | draft و جدول before/after، پیام future-only | history/statistics پس از tab/action | بازنویسی گذشته، metadata معامله در review | جلوگیری از mutation مبهم |
| accountant duty | یک textarea و save feedback | متن موجود داخل همان field | کارت duplicate «شرح فعلی» | یک منبع حقیقت |
| customer history | ردیف‌های لازم معامله و empty/error مستقل | stats برای بازهٔ انتخابی | total ساختگی، route/server metadata | دادهٔ پرتراکم فقط در مقصد on-demand |
| sessions | device، platform، last activity، primary signal، terminate | refresh و recovery | IP، home-server، API/backend، نشست‌های relation دیگر | privacy و action محلی |
| cancel pending | نام، پیامد revoke، CTA صریح | strong confirm متناسب با action | copy «قطع ارتباط» | capability دقیق |
| delete relation-only | نام، اثر بستن relation، CTA صریح | refresh پس از `409` | ادعای حذف user | تفاوت orphan relation با حساب live |
| delete account | نام، cascade واقعی، strong typed confirmation | جزئیات فقط در dialog | euphemism «قطع ارتباط»، CTA عمومی | جلوگیری از حذف ناخواسته |
| loading اولیه | skeleton/label ساختاری | — | identity یا status فرضی | truth before data |
| retained refresh error | دادهٔ قبلی، notice و retry | freshness فقط وقتی اثر دارد | جایگزینی با empty/zero | حفظ context |
| true empty | پیام کوتاه و create action مجاز | — | آموزش طولانی و KPI صفر | اقدام بعدی روشن |
| search/filter empty | عبارت/filter و clear action | — | create CTA به‌عنوان درمان جست‌وجو | empty معنایی مستقل |
| missing/terminal detail | status truthful و back معتبر | detail terminal owner-only | spinner بی‌نهایت یا blank | deep-link recovery |
| success/failure | outcome کنار action و receipt مرتبط | toast ثانویه فقط کمک | پاک‌کردن draft/context در failure | بازخورد قابل اعتماد |

## invariantهای کمی

- list/detail در عرض `<900px`: دقیقاً یکی visible؛ در `>=900px`: master/detail adaptive.
- target تعاملی: حداقل `44×44`؛ CTA اصلی: `48px`.
- pending deadline و SMS state: حداکثر یک ارائهٔ اصلی در context.
- count مجاز: فقط pending action queue؛ count تزئینی relation/tool/route ممنوع.
- metadata تازهٔ `home_server`، IP، backend/API و route داخلی: صفر.

پذیرش policy به تست‌های unit/integration و browser run `uiux-stage5-browser-20260811T100859948Z` متکی است. inventory کمی مستقل برای تک‌تک واحدهای DOM بستهٔ نهایی ساخته نشده؛ عدد ساختگی ادعا نمی‌شود.
