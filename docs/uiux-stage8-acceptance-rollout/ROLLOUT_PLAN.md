# مدل عرضهٔ محدود تیمی — Stage 8

این سند برنامه است، نه دستور اجرا. هیچ staging یا productionای از روی آن شروع نمی‌شود.
`ACCEPTANCE_MATRIX.json` اکنون ۲۷۰ outcome موردانتظار guard و چهار canonicalization
کامپوننتی را رهگیری می‌کند. چهار slice 8A در `STAGE8A_EXECUTION_RECEIPTS.json`، یک slice
8B در `STAGE8B_TYPOGRAPHY_EXECUTION_RECEIPT.json`، یک slice مستقل auth-containment در
`STAGE8_AUTH_VIEWPORT_CONTAINMENT_EXECUTION_RECEIPT.json` و یک rebaseline مستقل current
directory/profile در `STAGE8_DIRECTORY_PROFILE_REBASELINE_EXECUTION_RECEIPT.json` و یک receipt
postcommit roving-focus در `STAGE8_ROVING_WORKSPACE_FOCUS_EXECUTION_RECEIPT.json`، یک receipt
long-text wrapping در `STAGE8_SETTINGS_NOTIFICATIONS_WRAP_EXECUTION_RECEIPT.json` و یک receipt
Account Hub singleton در `STAGE8_ACCOUNT_HUB_SINGLETON_EXECUTION_RECEIPT.json` ثبت شده‌اند؛
هیچ‌کدام نتیجهٔ اجرای پذیرش تیمی یا authority عرضه نیستند.

## پیش‌شرط تبدیل draft به پذیرش

- هر اجرای واقعی باید route، access profile، viewport، state، interaction و environment
  مشخص داشته باشد.
- هر outcome اجراشده باید `evidenceRef` پایدار و source binding همان اجرا را ثبت کند.
- ارجاع به evidence مراحل قبلی فقط traceability است و به‌تنهایی سلول Stage 8 را pass نمی‌کند.
- evidence محدود 8A باید با شناسهٔ خود باقی بماند و هرگز به `executedFullMatrixCellCount` یا
  sign-off مالک تبدیل نشود.
- receipt invitation-presentation تا وقتی DELETE روی transport واقعی و بدون artifact تشخیصی
  مستقل بازتأیید نشود، `nonpromotable` می‌ماند و فقط end-state محلی mock را توصیف می‌کند.
- receipt 8B typography فقط bridge route-vnode با `protection=NONE` را توصیف می‌کند؛ base
  `font-sans` و FULL/MIXED را تغییر نمی‌دهد و هرگز به `executedFullMatrixCellCount` یا sign-off
  مالک تبدیل نمی‌شود.
- receipt auth-containment فقط opt-in صریحِ public/focused `AuthFlowShell` را توصیف می‌کند؛
  SystemRecovery credentialed بدون modifier و با daily navigation می‌ماند. این receipt هرگز
  routeهای FULL/MIXED، root/global cascade یا `executedFullMatrixCellCount` را گسترش نمی‌دهد.
- rebaseline current directory/profile فقط receipt جداگانهٔ source-bound `601b4005` است؛ receipt
  تاریخی `4415b743` را overwrite، promote یا به acceptance تبدیل نمی‌کند. ۴۰ scenario محدود آن
  و diagnosticهای recovery طبقه‌بندی‌شده به `executedFullMatrixCellCount` افزوده نمی‌شوند.
- receipt postcommit roving-focus فقط reveal صریحِ keyboard selection در filter/detail stripهای
  workspace customer/accountant را با `End` و دو viewport توصیف می‌کند. ۸ scenario آن، دو
  scroll intentional strip و diagnosticهای صفر به `executedFullMatrixCellCount` افزوده نمی‌شوند.
- receipt long-text wrapping حساب فقط containment متن synthetic در security/notifications را با
  ۱۲ scenario local/synthetic توصیف می‌کند و به `executedFullMatrixCellCount` افزوده نمی‌شود.
- receipt Account Hub singleton فقط topology action-grid برای normal/accountant در دو viewport،
  چهار scenario، journey محدود keyboard/click و diagnosticهای صفر را توصیف می‌کند و به
  `executedFullMatrixCellCount` افزوده نمی‌شود.
- مرجع Figma زنده فقط برای هم‌راستایی design است؛ پیش از اجرای browser قابل‌تکرار، شاهد runtime
  یا visual freeze نیست.
- زیبایی و یکپارچگی UI/UX باید با sign-off صریح مالک ثبت شود؛ سبز بودن test فنی جای آن نیست.

## ترتیب

1. **تکمیل evidence قابل‌تکرار روی همین branch**
   اول ۳۰ مسیر × ۹ profile و dimensionهای لازم با receipt مستقل، سپس فقط access profileهای
   داخلی مشخص‌شده توسط مالک. runtime محصول عمومی عوض نمی‌شود.

2. **مشاهده چند روزه پس از اجازهٔ جداگانه**
   خطا، بازیابی، کیبورد، zoom، copy اطلاعات، و عدم نشت به بازار/پیام‌رسان ثبت می‌شود.

3. **گسترش مرحله‌ای**
   فقط پس از اجازهٔ صریح مالک. هر موج باید rollback مستقل داشته باشد.

4. **حذف adapter قدیمی**
   فقط وقتی وابستگی نمانده و revert هر Stage جدا ممکن است.

## ممنوع

- merge به `main` بدون دستور جدا
- production deploy
- staging deploy خودسرانه
- Sites به‌عنوان محصول
- overwrite hashهای freeze بازار/پیام‌رسان

## Rollback

هر Stage 0–7 با revert commit همان Stage برمی‌گردد. سطح محافظت‌شده با `guard:ui` و hashهای این بسته کنترل می‌شود.
