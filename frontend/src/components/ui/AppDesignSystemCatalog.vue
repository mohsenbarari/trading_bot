<script setup lang="ts">
import { ref } from 'vue'
import AppButton from './AppButton.vue'
import AppCard from './AppCard.vue'
import AppDesignSystemScope from './AppDesignSystemScope.vue'
import AppErrorState from './AppErrorState.vue'
import AppFormField from './AppFormField.vue'
import AppInput from './AppInput.vue'
import AppLoadingState from './AppLoadingState.vue'
import AppListItem from './AppListItem.vue'
import AppStatusBadge from './AppStatusBadge.vue'

const semanticTokens = [
  { name: '--ui-v2-color-surface-page', label: 'سطح صفحه' },
  { name: '--ui-v2-color-surface-card', label: 'سطح کارت' },
  { name: '--ui-v2-color-surface-subtle', label: 'سطح ملایم' },
  { name: '--ui-v2-color-surface-brand-soft', label: 'سطح نرم برند' },
  { name: '--ui-v2-color-text-primary', label: 'متن اصلی' },
  { name: '--ui-v2-color-text-strong', label: 'متن پرقدرت' },
  { name: '--ui-v2-color-text-secondary', label: 'متن دوم' },
  { name: '--ui-v2-color-text-placeholder', label: 'متن راهنما' },
  { name: '--ui-v2-color-text-on-action', label: 'متن روی کنش' },
  { name: '--ui-v2-color-text-action', label: 'متن کنش' },
  { name: '--ui-v2-color-border-default', label: 'مرز پایه' },
  { name: '--ui-v2-color-border-focus', label: 'مرز تمرکز' },
  { name: '--ui-v2-color-action-primary', label: 'کنش اصلی' },
  { name: '--ui-v2-color-icon-brand', label: 'نماد برند' },
  { name: '--ui-v2-color-status-danger-bg', label: 'پس‌زمینه هشدار جدی' },
  { name: '--ui-v2-color-status-danger', label: 'هشدار جدی' },
  { name: '--ui-v2-color-status-warning-bg', label: 'پس‌زمینه هشدار' },
  { name: '--ui-v2-color-status-warning', label: 'هشدار' },
  { name: '--ui-v2-color-status-info-bg', label: 'پس‌زمینه اطلاع' },
  { name: '--ui-v2-color-status-info', label: 'اطلاع' },
  { name: '--ui-v2-color-status-success-bg', label: 'پس‌زمینه موفقیت' },
  { name: '--ui-v2-color-status-success', label: 'موفقیت' },
  { name: '--ui-v2-color-action-secondary', label: 'کنش دوم' },
  { name: '--ui-v2-color-action-disabled', label: 'کنش غیرفعال' },
  { name: '--ui-v2-color-action-danger', label: 'کنش حساس' },
  { name: '--ui-v2-color-text-disabled', label: 'متن غیرفعال' },
] as const

const typographyRoles = [
  {
    key: 'page',
    label: 'عنوان صفحه',
    sample: 'نمونه عنوان اصلی',
    size: '--ui-v2-type-page-size',
    line: '--ui-v2-type-page-line',
    weight: '--ui-v2-font-weight-bold',
  },
  {
    key: 'section',
    label: 'عنوان بخش',
    sample: 'نمونه عنوان بخش',
    size: '--ui-v2-type-section-size',
    line: '--ui-v2-type-section-line',
    weight: '--ui-v2-font-weight-bold',
  },
  {
    key: 'card',
    label: 'عنوان کارت',
    sample: 'نمونه عنوان کارت',
    size: '--ui-v2-type-card-size',
    line: '--ui-v2-type-card-line',
    weight: '--ui-v2-font-weight-semibold',
  },
  {
    key: 'body-medium',
    label: 'متن میانه',
    sample: 'این متن برای نمایش خوانایی بدنه است.',
    size: '--ui-v2-type-body-medium-size',
    line: '--ui-v2-type-body-medium-line',
    weight: '--ui-v2-font-weight-regular',
  },
  {
    key: 'body-small',
    label: 'متن کوچک',
    sample: 'نمونه متن کوچک و توضیحی',
    size: '--ui-v2-type-body-small-size',
    line: '--ui-v2-type-body-small-line',
    weight: '--ui-v2-font-weight-regular',
  },
  {
    key: 'label-medium',
    label: 'برچسب میانه',
    sample: 'برچسب ورودی نمونه',
    size: '--ui-v2-type-label-medium-size',
    line: '--ui-v2-type-label-medium-line',
    weight: '--ui-v2-font-weight-semibold',
  },
  {
    key: 'label-small',
    label: 'برچسب کوچک',
    sample: 'برچسب کمکی',
    size: '--ui-v2-type-label-small-size',
    line: '--ui-v2-type-label-small-line',
    weight: '--ui-v2-font-weight-medium',
  },
  {
    key: 'action-medium',
    label: 'متن کنش',
    sample: 'ادامه فرایند',
    size: '--ui-v2-type-action-medium-size',
    line: '--ui-v2-type-action-medium-line',
    weight: '--ui-v2-font-weight-semibold',
  },
  {
    key: 'caption',
    label: 'زیرنویس',
    sample: 'توضیح بسیار کوتاه',
    size: '--ui-v2-type-caption-size',
    line: '--ui-v2-type-caption-line',
    weight: '--ui-v2-font-weight-bold',
  },
  {
    key: 'avatar-initial',
    label: 'حرف نمایه',
    sample: 'ن',
    size: '--ui-v2-type-avatar-initial-size',
    line: '--ui-v2-type-avatar-initial-line',
    weight: '--ui-v2-font-weight-bold',
  },
] as const

const radiusRoles = [
  { key: 'compact', label: 'فشرده', token: '--ui-v2-radius-compact' },
  { key: 'control', label: 'کنترل', token: '--ui-v2-radius-control' },
  { key: 'card', label: 'کارت', token: '--ui-v2-radius-card' },
  { key: 'container', label: 'ظرف', token: '--ui-v2-radius-container' },
  { key: 'panel', label: 'پنل', token: '--ui-v2-radius-panel' },
  { key: 'full', label: 'کامل', token: '--ui-v2-radius-full' },
] as const

const iconRoles = [
  { key: 'small', label: 'کوچک', token: '--ui-v2-icon-size-small', size: '۱۶' },
  { key: 'control', label: 'کنترل', token: '--ui-v2-icon-size-control', size: '۲۰' },
  { key: 'large', label: 'بزرگ', token: '--ui-v2-icon-size-large', size: '۲۴' },
] as const

const responsiveWidths = [360, 375, 390, 414, 430, 1440] as const
const portalProofOpen = ref(false)
</script>

<template>
  <AppDesignSystemScope
    as="main"
    class="ui-v2-catalog"
    data-test="ui-v2-catalog"
    data-private-catalog="true"
    data-responsive-contract="360,375,390,414,430,1440"
    dir="rtl"
    aria-labelledby="ui-v2-catalog-title"
  >
    <header class="ui-v2-catalog__header" data-overflow-contract="header">
      <div class="ui-v2-catalog__heading-copy">
        <p class="ui-v2-catalog__eyebrow">نمونه داخلی و آزمایشی</p>
        <h1 id="ui-v2-catalog-title">کاتالوگ اجرایی سامانه طراحی نسخه دوم</h1>
        <p>
          این صفحه فقط قرارداد دیداری، رفتاری و دسترس‌پذیری پایه را نمایش می‌دهد و به هیچ مسیر
          محصولی متصل نیست.
        </p>
      </div>

      <nav class="ui-v2-catalog__nav" aria-label="فهرست نمونه‌ها">
        <a href="#ui-v2-foundations">پایه‌ها</a>
        <a href="#ui-v2-states">وضعیت‌ها</a>
        <a href="#ui-v2-behavior">رفتار</a>
        <a href="#ui-v2-responsive">واکنش‌گرایی</a>
      </nav>
    </header>

    <div class="ui-v2-catalog__content" data-overflow-contract="content">
      <section
        id="ui-v2-foundations"
        class="ui-v2-catalog__section"
        aria-labelledby="ui-v2-foundations-title"
      >
        <div class="ui-v2-catalog__section-heading">
          <div>
            <p class="ui-v2-catalog__eyebrow">پایه‌های مشترک</p>
            <h2 id="ui-v2-foundations-title">رنگ، نوشتار و هندسه</h2>
          </div>
          <AppStatusBadge tone="info">همگام با متغیرهای مرجع</AppStatusBadge>
        </div>

        <h3 class="ui-v2-catalog__subheading">رنگ‌های معنایی</h3>
        <div class="ui-v2-catalog__token-grid" data-test="semantic-token-grid">
          <article
            v-for="token in semanticTokens"
            :key="token.name"
            class="ui-v2-catalog__token"
            :data-token="token.name"
          >
            <span
              class="ui-v2-catalog__swatch"
              :style="{ backgroundColor: `var(${token.name})` }"
              aria-hidden="true"
            ></span>
            <span class="ui-v2-catalog__token-copy">
              <strong>{{ token.label }}</strong>
              <code dir="ltr">{{ token.name }}</code>
            </span>
          </article>
        </div>

        <h3 class="ui-v2-catalog__subheading">نقش‌های نوشتاری</h3>
        <div class="ui-v2-catalog__type-list" data-test="typography-role-list">
          <article
            v-for="role in typographyRoles"
            :key="role.key"
            class="ui-v2-catalog__type-row"
            :data-type-role="role.key"
          >
            <span class="ui-v2-catalog__type-label">{{ role.label }}</span>
            <span
              class="ui-v2-catalog__type-sample"
              :style="{
                fontSize: `var(${role.size})`,
                lineHeight: `var(${role.line})`,
                fontWeight: `var(${role.weight})`,
              }"
            >
              {{ role.sample }}
            </span>
          </article>
        </div>

        <h3 class="ui-v2-catalog__subheading">نقش‌های گردی</h3>
        <div class="ui-v2-catalog__radius-grid" data-test="radius-role-grid">
          <article
            v-for="role in radiusRoles"
            :key="role.key"
            class="ui-v2-catalog__radius"
            :data-radius-role="role.key"
            :style="{ borderRadius: `var(${role.token})` }"
          >
            <strong>{{ role.label }}</strong>
            <code dir="ltr">{{ role.token }}</code>
          </article>
        </div>

        <h3 class="ui-v2-catalog__subheading">مقیاس نماد</h3>
        <div class="ui-v2-catalog__icon-grid" data-test="icon-scale-contract">
          <article
            v-for="role in iconRoles"
            :key="role.key"
            class="ui-v2-catalog__icon-proof"
            :data-icon-size="role.key"
            :data-icon-token="role.token"
          >
            <svg aria-hidden="true" viewBox="0 0 24 24" fill="none">
              <path d="M5 12.5 9.2 17 19 7" stroke="currentColor" stroke-width="2" />
            </svg>
            <span>
              <strong>{{ role.label }}</strong>
              <code dir="ltr">{{ role.size }}px · {{ role.token }}</code>
            </span>
          </article>
        </div>
      </section>

      <section
        id="ui-v2-states"
        class="ui-v2-catalog__section"
        aria-labelledby="ui-v2-states-title"
      >
        <div class="ui-v2-catalog__section-heading">
          <div>
            <p class="ui-v2-catalog__eyebrow">ماتریس قابل اجرا</p>
            <h2 id="ui-v2-states-title">وضعیت‌های پایه مؤلفه‌ها</h2>
          </div>
          <AppStatusBadge tone="success">پنج وضعیت مرجع</AppStatusBadge>
        </div>

        <div class="ui-v2-catalog__state-grid" data-test="catalog-state-grid">
          <AppCard data-catalog-state="normal">
            <h3>عادی</h3>
            <p>کنش آماده استفاده است.</p>
            <AppButton>ادامه</AppButton>
          </AppCard>

          <AppCard data-catalog-state="loading" tone="info">
            <h3>در حال انجام</h3>
            <AppLoadingState label="در حال آماده‌سازی نمونه" />
            <AppButton loading>در حال انجام</AppButton>
          </AppCard>

          <AppCard data-catalog-state="disabled">
            <h3>غیرفعال</h3>
            <p>کنش تا فراهم‌شدن پیش‌نیاز در دسترس نیست.</p>
            <AppButton disabled>ادامه</AppButton>
          </AppCard>

          <AppCard data-catalog-state="error" tone="danger">
            <h3>خطا</h3>
            <AppFormField label="ورودی نمونه" error="این ورودی نیاز به بازبینی دارد.">
              <template #default="{ id, describedby, invalid }">
                <AppInput
                  :id="id"
                  model-value=""
                  :invalid="invalid"
                  :aria-describedby="describedby"
                  placeholder="مقدار نمونه"
                />
              </template>
            </AppFormField>
            <AppErrorState title="نمونه کامل نشد" message="ورودی را بازبینی و دوباره تلاش کنید." />
          </AppCard>

          <AppCard data-catalog-state="destructive" tone="danger">
            <h3>حساس</h3>
            <p>کنش حساس باید برچسب روشن و تأیید مستقل داشته باشد.</p>
            <AppButton variant="danger">حذف نمونه</AppButton>
          </AppCard>
        </div>

        <AppCard class="ui-v2-catalog__list-card" data-test="list-contract">
          <h3>فهرست مرجع</h3>
          <ul class="ui-v2-catalog__list" aria-label="فهرست نمونه">
            <li>
              <AppListItem
                title="پرونده نمونه یک"
                description="آخرین بررسی: امروز"
                meta="آماده"
                interactive
              />
            </li>
            <li>
              <AppListItem title="پرونده نمونه دو" description="نیازمند بازبینی" meta="در انتظار" />
            </li>
          </ul>
        </AppCard>
      </section>

      <section
        id="ui-v2-behavior"
        class="ui-v2-catalog__section"
        aria-labelledby="ui-v2-behavior-title"
      >
        <div class="ui-v2-catalog__section-heading">
          <div>
            <p class="ui-v2-catalog__eyebrow">رفتار و دسترس‌پذیری</p>
            <h2 id="ui-v2-behavior-title">تمرکز، حرکت و پنجره مستقل</h2>
          </div>
        </div>

        <div class="ui-v2-catalog__behavior-grid">
          <AppCard data-test="focus-proof">
            <h3>تمرکز صفحه‌کلید</h3>
            <p>با کلید جهش، حلقه سه‌پیکسلی مرجع دور کنترل دیده می‌شود.</p>
            <AppButton class="ui-v2-catalog__focus-proof">کنترل تمرکزپذیر</AppButton>
          </AppCard>

          <AppCard
            class="ui-v2-motion-micro"
            data-ui-v2-motion="essential"
            data-motion-duration="140"
          >
            <h3>حرکت کوتاه</h3>
            <p>برای بازخورد کوچک، مدت مرجع ۱۴۰ میلی‌ثانیه است.</p>
          </AppCard>

          <AppCard
            class="ui-v2-motion-state"
            data-ui-v2-motion="decorative"
            data-motion-duration="180"
          >
            <h3>تغییر وضعیت</h3>
            <p>برای تغییر وضعیت، مدت مرجع ۱۸۰ میلی‌ثانیه است.</p>
          </AppCard>
        </div>

        <details class="ui-v2-catalog__disclosure" data-test="reduced-motion-disclosure">
          <summary>رفتار در حالت کاهش حرکت</summary>
          <p>
            با فعال‌شدن ترجیح کاهش حرکت، مدت‌ها به حداقل می‌رسند، حرکت تزئینی حذف می‌شود و محتوا
            همچنان قابل استفاده می‌ماند.
          </p>
        </details>

        <AppCard class="ui-v2-catalog__portal-card" data-test="portal-dialog-proof">
          <h3>اثبات محدوده پنجره مستقل</h3>
          <p>نمونه زیر محدوده صریح پنجره را بدون تغییر میزبان سراسری فعال می‌کند.</p>
          <AppButton
            variant="secondary"
            data-test="portal-proof-toggle"
            :aria-expanded="portalProofOpen"
            aria-controls="ui-v2-portal-proof"
            @click="portalProofOpen = !portalProofOpen"
          >
            {{ portalProofOpen ? 'بستن نمونه' : 'بازکردن نمونه' }}
          </AppButton>

          <AppDesignSystemScope
            v-if="portalProofOpen"
            id="ui-v2-portal-proof"
            as="aside"
            kind="portal"
            class="ui-v2-catalog__portal"
            data-test="catalog-portal-scope"
          >
            <section role="dialog" aria-modal="false" aria-labelledby="ui-v2-portal-title">
              <h4 id="ui-v2-portal-title">پنجره نمونه</h4>
              <p>این محتوا فقط قرارداد محدوده مستقل را بررسی می‌کند.</p>
              <div class="ui-v2-catalog__dialog-actions">
                <AppButton variant="secondary" @click="portalProofOpen = false">انصراف</AppButton>
                <AppButton @click="portalProofOpen = false">تأیید نمونه</AppButton>
              </div>
            </section>
          </AppDesignSystemScope>
        </AppCard>
      </section>

      <section
        id="ui-v2-responsive"
        class="ui-v2-catalog__section"
        aria-labelledby="ui-v2-responsive-title"
        data-test="responsive-contract"
      >
        <div class="ui-v2-catalog__section-heading">
          <div>
            <p class="ui-v2-catalog__eyebrow">قرارداد عرض</p>
            <h2 id="ui-v2-responsive-title">نمایش بدون سرریز افقی</h2>
          </div>
        </div>

        <div class="ui-v2-catalog__viewport-grid">
          <article
            v-for="width in responsiveWidths"
            :key="width"
            class="ui-v2-catalog__viewport"
            :data-responsive-width="width"
          >
            <strong dir="ltr">{{ width }}px</strong>
            <span>{{ width === 1440 ? 'نمای گسترده' : 'نمای فشرده' }}</span>
          </article>
        </div>

        <div class="ui-v2-catalog__overflow-contract" data-overflow-contract="responsive-proof">
          <strong>قاعده سرریز</strong>
          <span>
            همه ستون‌ها حداقل عرض صفر، بیشینه عرض کامل و شکست ایمن متن دارند؛ بنابراین محتوای نمونه
            در عرض‌های مرجع از قاب خارج نمی‌شود.
          </span>
        </div>
      </section>
    </div>
  </AppDesignSystemScope>
</template>

<style>
:where([data-ui-system='v2'], [data-ui-system='v2-portal']).ui-v2-catalog {
  box-sizing: border-box;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  min-height: 100%;
  overflow-x: clip;
  background: var(--ui-v2-color-surface-page);
  color: var(--ui-v2-color-text-primary);
  font-family: var(--ui-v2-font-family);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__header,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__content {
  box-sizing: border-box;
  width: min(100%, var(--ui-v2-layout-content-max));
  max-width: 100%;
  min-width: 0;
  margin-inline: auto;
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__header {
  display: grid;
  gap: var(--ui-v2-spacing-20);
  padding: var(--ui-v2-spacing-32) var(--ui-v2-spacing-20);
  background: var(--ui-v2-color-surface-card);
  border: var(--ui-v2-stroke-standard) solid var(--ui-v2-color-border-default);
  border-block-start: 0;
  border-radius: 0 0 var(--ui-v2-radius-panel) var(--ui-v2-radius-panel);
  box-shadow: var(--ui-v2-effect-card);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__heading-copy,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__token-copy,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__content,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__section,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__state-grid,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__behavior-grid {
  min-width: 0;
  max-width: 100%;
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__heading-copy h1 {
  margin: 0;
  color: var(--ui-v2-color-text-strong);
  font-size: var(--ui-v2-type-page-size);
  font-weight: var(--ui-v2-font-weight-bold);
  line-height: var(--ui-v2-type-page-line);
  overflow-wrap: anywhere;
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal'])
  .ui-v2-catalog__heading-copy
  > p:last-child {
  max-width: var(--ui-v2-layout-reading-max);
  margin: var(--ui-v2-spacing-8) 0 0;
  color: var(--ui-v2-color-text-secondary);
  font-size: var(--ui-v2-type-body-medium-size);
  line-height: var(--ui-v2-type-body-medium-line);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__eyebrow {
  margin: 0 0 var(--ui-v2-spacing-4);
  color: var(--ui-v2-color-text-action);
  font-size: var(--ui-v2-type-label-small-size);
  font-weight: var(--ui-v2-font-weight-semibold);
  line-height: var(--ui-v2-type-label-small-line);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__nav {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ui-v2-spacing-8);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__nav a {
  min-height: var(--ui-v2-size-target-min);
  padding-inline: var(--ui-v2-spacing-16);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: var(--ui-v2-stroke-standard) solid var(--ui-v2-color-border-default);
  border-radius: var(--ui-v2-radius-full);
  background: var(--ui-v2-color-surface-subtle);
  color: var(--ui-v2-color-text-action);
  font-size: var(--ui-v2-type-label-medium-size);
  font-weight: var(--ui-v2-font-weight-semibold);
  line-height: var(--ui-v2-type-label-medium-line);
  text-decoration: none;
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__content {
  display: grid;
  gap: var(--ui-v2-spacing-24);
  padding: var(--ui-v2-spacing-24) var(--ui-v2-spacing-20) var(--ui-v2-spacing-32);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__section {
  box-sizing: border-box;
  padding: var(--ui-v2-spacing-24);
  border: var(--ui-v2-stroke-standard) solid var(--ui-v2-color-border-default);
  border-radius: var(--ui-v2-radius-panel);
  background: var(--ui-v2-color-surface-card);
  box-shadow: var(--ui-v2-effect-card);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__section-heading {
  display: flex;
  min-width: 0;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--ui-v2-spacing-16);
  margin-block-end: var(--ui-v2-spacing-20);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__section-heading h2 {
  margin: 0;
  color: var(--ui-v2-color-text-strong);
  font-size: var(--ui-v2-type-section-size);
  font-weight: var(--ui-v2-font-weight-bold);
  line-height: var(--ui-v2-type-section-line);
  overflow-wrap: anywhere;
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__subheading {
  margin: var(--ui-v2-spacing-24) 0 var(--ui-v2-spacing-12);
  color: var(--ui-v2-color-text-primary);
  font-size: var(--ui-v2-type-card-size);
  font-weight: var(--ui-v2-font-weight-semibold);
  line-height: var(--ui-v2-type-card-line);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__token-grid,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__radius-grid,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__icon-grid,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__state-grid,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__behavior-grid,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__viewport-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(var(--ui-v2-layout-card-min), 1fr));
  gap: var(--ui-v2-spacing-12);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__token {
  box-sizing: border-box;
  display: flex;
  min-width: 0;
  align-items: center;
  gap: var(--ui-v2-spacing-12);
  padding: var(--ui-v2-spacing-12);
  border: var(--ui-v2-stroke-standard) solid var(--ui-v2-color-border-default);
  border-radius: var(--ui-v2-radius-card);
  background: var(--ui-v2-color-surface-subtle);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__swatch {
  width: var(--ui-v2-size-target-min);
  height: var(--ui-v2-size-target-min);
  flex: 0 0 auto;
  border: var(--ui-v2-stroke-standard) solid var(--ui-v2-color-border-default);
  border-radius: var(--ui-v2-radius-control);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__token-copy {
  display: grid;
  gap: var(--ui-v2-spacing-2);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__token-copy strong,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__radius strong,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__viewport strong {
  color: var(--ui-v2-color-text-primary);
  font-size: var(--ui-v2-type-label-medium-size);
  line-height: var(--ui-v2-type-label-medium-line);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__token-copy code,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__radius code {
  color: var(--ui-v2-color-text-secondary);
  font-size: var(--ui-v2-type-caption-size);
  line-height: var(--ui-v2-type-caption-line);
  overflow-wrap: anywhere;
  white-space: normal;
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__type-list {
  display: grid;
  min-width: 0;
  border: var(--ui-v2-stroke-standard) solid var(--ui-v2-color-border-default);
  border-radius: var(--ui-v2-radius-card);
  overflow: hidden;
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__type-row {
  display: grid;
  min-width: 0;
  grid-template-columns: minmax(var(--ui-v2-layout-type-label-min), 0.35fr) minmax(0, 1fr);
  align-items: center;
  gap: var(--ui-v2-spacing-16);
  padding: var(--ui-v2-spacing-12) var(--ui-v2-spacing-16);
  background: var(--ui-v2-color-surface-card);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal'])
  .ui-v2-catalog__type-row
  + .ui-v2-catalog__type-row {
  border-block-start: var(--ui-v2-stroke-standard) solid var(--ui-v2-color-border-default);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__type-label {
  color: var(--ui-v2-color-text-secondary);
  font-size: var(--ui-v2-type-label-small-size);
  line-height: var(--ui-v2-type-label-small-line);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__type-sample {
  min-width: 0;
  color: var(--ui-v2-color-text-strong);
  overflow-wrap: anywhere;
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__radius {
  box-sizing: border-box;
  display: grid;
  min-width: 0;
  min-height: calc(var(--ui-v2-size-cta-min) * 2);
  align-content: center;
  gap: var(--ui-v2-spacing-4);
  padding: var(--ui-v2-spacing-16);
  border: var(--ui-v2-stroke-standard) solid var(--ui-v2-color-border-default);
  background: var(--ui-v2-color-surface-brand-soft);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__icon-proof {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: var(--ui-v2-spacing-12);
  padding: var(--ui-v2-spacing-12);
  border: var(--ui-v2-stroke-standard) solid var(--ui-v2-color-border-default);
  border-radius: var(--ui-v2-radius-card);
  background: var(--ui-v2-color-surface-card);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__icon-proof svg {
  flex: 0 0 auto;
  color: var(--ui-v2-color-icon-brand);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal'])
  .ui-v2-catalog__icon-proof[data-icon-size='small']
  svg {
  width: var(--ui-v2-icon-size-small);
  height: var(--ui-v2-icon-size-small);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal'])
  .ui-v2-catalog__icon-proof[data-icon-size='control']
  svg {
  width: var(--ui-v2-icon-size-control);
  height: var(--ui-v2-icon-size-control);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal'])
  .ui-v2-catalog__icon-proof[data-icon-size='large']
  svg {
  width: var(--ui-v2-icon-size-large);
  height: var(--ui-v2-icon-size-large);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__icon-proof span,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__icon-proof code {
  display: grid;
  min-width: 0;
  color: var(--ui-v2-color-text-secondary);
  font-size: var(--ui-v2-type-caption-size);
  line-height: var(--ui-v2-type-caption-line);
  overflow-wrap: anywhere;
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__list-card {
  display: grid;
  gap: var(--ui-v2-spacing-12);
  margin-block-start: var(--ui-v2-spacing-16);
  padding: var(--ui-v2-spacing-16);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__list {
  display: grid;
  gap: var(--ui-v2-spacing-8);
  margin: 0;
  padding: 0;
  list-style: none;
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__list > li {
  min-width: 0;
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-list-item {
  box-sizing: border-box;
  display: grid;
  width: 100%;
  min-width: 0;
  min-height: var(--ui-v2-size-target-min);
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--ui-v2-spacing-12);
  padding: var(--ui-v2-spacing-12);
  border: var(--ui-v2-stroke-standard) solid var(--ui-v2-color-border-default);
  border-radius: var(--ui-v2-radius-control);
  background: var(--ui-v2-color-surface-card);
  color: var(--ui-v2-color-text-primary);
  font: inherit;
  text-align: start;
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-list-item__copy {
  display: grid;
  min-width: 0;
  gap: var(--ui-v2-spacing-2);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-list-item__copy strong {
  color: var(--ui-v2-color-text-strong);
  font-size: var(--ui-v2-type-label-medium-size);
  line-height: var(--ui-v2-type-label-medium-line);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-list-item__copy span,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-list-item__trailing {
  color: var(--ui-v2-color-text-secondary);
  font-size: var(--ui-v2-type-label-small-size);
  line-height: var(--ui-v2-type-label-small-line);
  overflow-wrap: anywhere;
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__state-grid > .ui-card,
:where([data-ui-system='v2'], [data-ui-system='v2-portal'])
  .ui-v2-catalog__behavior-grid
  > .ui-card,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__portal-card {
  box-sizing: border-box;
  display: grid;
  min-width: 0;
  align-content: start;
  gap: var(--ui-v2-spacing-12);
  margin: 0;
  padding: var(--ui-v2-spacing-16);
  border: var(--ui-v2-stroke-standard) solid var(--ui-v2-color-border-default);
  border-radius: var(--ui-v2-radius-card);
  background: var(--ui-v2-color-surface-subtle);
  box-shadow: none;
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__state-grid h3,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__behavior-grid h3,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__portal-card h3,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__portal h4 {
  margin: 0;
  color: var(--ui-v2-color-text-strong);
  font-size: var(--ui-v2-type-card-size);
  font-weight: var(--ui-v2-font-weight-semibold);
  line-height: var(--ui-v2-type-card-line);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__state-grid p,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__behavior-grid p,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__portal-card p,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__portal p {
  margin: 0;
  color: var(--ui-v2-color-text-secondary);
  font-size: var(--ui-v2-type-body-small-size);
  line-height: var(--ui-v2-type-body-small-line);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-button {
  box-sizing: border-box;
  display: inline-flex;
  width: fit-content;
  max-width: 100%;
  min-height: var(--ui-v2-size-cta-min);
  align-items: center;
  justify-content: center;
  gap: var(--ui-v2-spacing-8);
  padding: var(--ui-v2-spacing-8) var(--ui-v2-spacing-16);
  border: var(--ui-v2-stroke-standard) solid transparent;
  border-radius: var(--ui-v2-radius-control);
  background: var(--ui-v2-color-action-primary);
  color: var(--ui-v2-color-text-on-action);
  font: inherit;
  font-size: var(--ui-v2-type-action-medium-size);
  font-weight: var(--ui-v2-font-weight-semibold);
  line-height: var(--ui-v2-type-action-medium-line);
  cursor: pointer;
  transition-property: color, background-color, border-color, opacity, transform;
  transition-duration: var(--ui-v2-motion-micro);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-button--secondary,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-button--ghost {
  border-color: var(--ui-v2-color-border-default);
  background: var(--ui-v2-color-action-secondary);
  color: var(--ui-v2-color-text-action);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-button--danger {
  background: var(--ui-v2-color-action-danger);
  color: var(--ui-v2-color-text-on-action);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-button:disabled {
  border-color: var(--ui-v2-color-border-default);
  background: var(--ui-v2-color-action-disabled);
  color: var(--ui-v2-color-text-disabled);
  cursor: not-allowed;
  opacity: 1;
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-button__spinner,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-loading-state__spinner {
  box-sizing: border-box;
  width: var(--ui-v2-spacing-16);
  height: var(--ui-v2-spacing-16);
  border: var(--ui-v2-stroke-standard) solid currentColor;
  border-inline-start-color: transparent;
  border-radius: var(--ui-v2-radius-full);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-loading-state {
  display: inline-flex;
  align-items: center;
  gap: var(--ui-v2-spacing-8);
  color: var(--ui-v2-color-status-info);
  font-size: var(--ui-v2-type-body-small-size);
  line-height: var(--ui-v2-type-body-small-line);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-form-field {
  display: grid;
  min-width: 0;
  gap: var(--ui-v2-spacing-4);
  color: var(--ui-v2-color-text-primary);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-form-field__label {
  font-size: var(--ui-v2-type-label-medium-size);
  font-weight: var(--ui-v2-font-weight-semibold);
  line-height: var(--ui-v2-type-label-medium-line);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-input {
  box-sizing: border-box;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  min-height: var(--ui-v2-size-target-min);
  padding: var(--ui-v2-spacing-8) var(--ui-v2-spacing-12);
  border: var(--ui-v2-stroke-standard) solid var(--ui-v2-color-border-default);
  border-radius: var(--ui-v2-radius-control);
  background: var(--ui-v2-color-surface-card);
  color: var(--ui-v2-color-text-primary);
  font: inherit;
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-input.is-invalid {
  border-color: var(--ui-v2-color-status-danger);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-form-field__error {
  color: var(--ui-v2-color-status-danger);
  font-size: var(--ui-v2-type-body-small-size);
  line-height: var(--ui-v2-type-body-small-line);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-empty-state {
  box-sizing: border-box;
  display: grid;
  gap: var(--ui-v2-spacing-4);
  padding: var(--ui-v2-spacing-12);
  border: var(--ui-v2-stroke-standard) solid var(--ui-v2-color-status-danger);
  border-radius: var(--ui-v2-radius-control);
  background: var(--ui-v2-color-status-danger-bg);
  color: var(--ui-v2-color-status-danger);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-empty-state h2,
:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-empty-state p {
  margin: 0;
  color: inherit;
  font-size: var(--ui-v2-type-body-small-size);
  line-height: var(--ui-v2-type-body-small-line);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-status-badge {
  display: inline-flex;
  width: fit-content;
  max-width: 100%;
  align-items: center;
  min-height: var(--ui-v2-spacing-24);
  padding: var(--ui-v2-spacing-2) var(--ui-v2-spacing-8);
  border-radius: var(--ui-v2-radius-full);
  background: var(--ui-v2-color-surface-subtle);
  color: var(--ui-v2-color-text-secondary);
  font-size: var(--ui-v2-type-caption-size);
  font-weight: var(--ui-v2-font-weight-bold);
  line-height: var(--ui-v2-type-caption-line);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-status-badge--info {
  background: var(--ui-v2-color-status-info-bg);
  color: var(--ui-v2-color-status-info);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-status-badge--success {
  background: var(--ui-v2-color-status-success-bg);
  color: var(--ui-v2-color-status-success);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__disclosure {
  margin-block: var(--ui-v2-spacing-16);
  padding: var(--ui-v2-spacing-12) var(--ui-v2-spacing-16);
  border: var(--ui-v2-stroke-standard) solid var(--ui-v2-color-border-default);
  border-radius: var(--ui-v2-radius-control);
  background: var(--ui-v2-color-surface-brand-soft);
  color: var(--ui-v2-color-text-primary);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__disclosure summary {
  min-height: var(--ui-v2-size-target-min);
  display: flex;
  align-items: center;
  color: var(--ui-v2-color-text-action);
  font-weight: var(--ui-v2-font-weight-semibold);
  cursor: pointer;
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__disclosure p {
  margin: var(--ui-v2-spacing-8) 0 0;
  color: var(--ui-v2-color-text-secondary);
  font-size: var(--ui-v2-type-body-small-size);
  line-height: var(--ui-v2-type-body-small-line);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__portal-card {
  margin-block-start: var(--ui-v2-spacing-16);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__portal {
  box-sizing: border-box;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  padding: var(--ui-v2-spacing-20);
  border: var(--ui-v2-stroke-standard) solid var(--ui-v2-color-border-focus);
  border-radius: var(--ui-v2-radius-panel);
  background: var(--ui-v2-color-surface-card);
  box-shadow: var(--ui-v2-effect-overlay);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__portal section {
  display: grid;
  min-width: 0;
  gap: var(--ui-v2-spacing-12);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__dialog-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--ui-v2-spacing-8);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__viewport {
  box-sizing: border-box;
  display: grid;
  min-width: 0;
  gap: var(--ui-v2-spacing-4);
  padding: var(--ui-v2-spacing-16);
  border: var(--ui-v2-stroke-standard) solid var(--ui-v2-color-border-default);
  border-radius: var(--ui-v2-radius-card);
  background: var(--ui-v2-color-surface-subtle);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__viewport span {
  color: var(--ui-v2-color-text-secondary);
  font-size: var(--ui-v2-type-body-small-size);
  line-height: var(--ui-v2-type-body-small-line);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__overflow-contract {
  box-sizing: border-box;
  display: grid;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  gap: var(--ui-v2-spacing-4);
  margin-block-start: var(--ui-v2-spacing-16);
  padding: var(--ui-v2-spacing-16);
  overflow-wrap: anywhere;
  border: var(--ui-v2-stroke-standard) solid var(--ui-v2-color-border-default);
  border-radius: var(--ui-v2-radius-container);
  background: var(--ui-v2-color-surface-brand-soft);
  color: var(--ui-v2-color-text-secondary);
  font-size: var(--ui-v2-type-body-small-size);
  line-height: var(--ui-v2-type-body-small-line);
}

:where([data-ui-system='v2'], [data-ui-system='v2-portal'])
  .ui-v2-catalog__overflow-contract
  strong {
  color: var(--ui-v2-color-text-primary);
}

@media (max-width: 430px) {
  :where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__header,
  :where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__content {
    padding-inline: var(--ui-v2-spacing-12);
  }

  :where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__section {
    padding: var(--ui-v2-spacing-16);
    border-radius: var(--ui-v2-radius-container);
  }

  :where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__section-heading {
    flex-direction: column;
  }

  :where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__token-grid,
  :where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__radius-grid,
  :where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__state-grid,
  :where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__behavior-grid,
  :where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__viewport-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  :where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__type-row {
    grid-template-columns: minmax(0, 1fr);
    gap: var(--ui-v2-spacing-4);
  }
}

@media (min-width: 900px) {
  :where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__header {
    grid-template-columns: minmax(0, 1fr) auto;
    align-items: end;
    padding-inline: var(--ui-v2-spacing-32);
  }

  :where([data-ui-system='v2'], [data-ui-system='v2-portal']) .ui-v2-catalog__content {
    padding-inline: var(--ui-v2-spacing-32);
  }
}
</style>
