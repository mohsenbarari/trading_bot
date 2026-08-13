# مدل عرضهٔ محدود تیمی — Stage 8

این سند برنامه است، نه دستور اجرا. هیچ staging یا productionای از روی آن شروع نمی‌شود.
`ACCEPTANCE_MATRIX.json` نیز فقط draft رهگیری outcomeهای موردانتظار guard است و نتیجهٔ
اجرای پذیرش تیمی محسوب نمی‌شود.

## پیش‌شرط تبدیل draft به پذیرش

- هر اجرای واقعی باید route، access profile، viewport، state، interaction و environment
  مشخص داشته باشد.
- هر outcome اجراشده باید `evidenceRef` پایدار و source binding همان اجرا را ثبت کند.
- ارجاع به evidence مراحل قبلی فقط traceability است و به‌تنهایی سلول Stage 8 را pass نمی‌کند.
- زیبایی و یکپارچگی UI/UX باید با sign-off صریح مالک ثبت شود؛ سبز بودن test فنی جای آن نیست.

## ترتیب

1. **تیم آزمایشی روی همین branch**
   فقط access profileهای داخلی مشخص‌شده توسط مالک. runtime محصول عمومی عوض نمی‌شود.

2. **مشاهده چند روزه**
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
