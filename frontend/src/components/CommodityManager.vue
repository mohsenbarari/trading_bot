<script setup lang="ts">
import { ref, onMounted, reactive, computed } from 'vue';
import { apiFetch } from '../utils/auth';
import LoadingSkeleton from './LoadingSkeleton.vue';

const props = defineProps<{
  apiBaseUrl: string;
  jwtToken: string | null;
}>();
const emit = defineEmits(['navigate']);

// --- اینترفیس‌ها ---
interface CommodityAlias {
  id: number;
  alias: string;
  commodity_id: number;
}
interface Commodity {
  id: number;
  name: string;
  aliases: CommodityAlias[];
}
interface FormState {
  name: string;
  aliasesText: string;
}

const LOCKED_IMAM_COMMODITY_NAME = 'امام';

// --- متغیرهای State ---
type ViewMode = 'list' | 'aliases' | 'add_commodity' | 'edit_commodity_name' | 'add_alias' | 'edit_alias' | 'delete_commodity' | 'delete_alias';
const viewMode = ref<ViewMode>('list');
const isLoading = ref(true);
const errorMessage = ref('');
const successMessage = ref('');
const commodities = ref<Commodity[]>([]);
const selectedCommodity = ref<Commodity | null>(null);
const selectedAlias = ref<CommodityAlias | null>(null);
const form = reactive<FormState>({ name: '', aliasesText: '' });
const selectedCommodityIsLockedImam = computed(() => selectedCommodity.value?.name === LOCKED_IMAM_COMMODITY_NAME);

// --- توابع کمکی ---
function resetMessages() {
  errorMessage.value = '';
  successMessage.value = '';
}
function resetForm() {
  form.name = '';
  form.aliasesText = '';
}

// نمایش صحیح خطاها (رفع مشکل [object Object])
function getErrorDetail(error: any, defaultMsg: string): string {
    const detail = error.detail || error.message;
    if (!detail) return defaultMsg;
    
    if (typeof detail === 'object') {
        try {
            return JSON.stringify(detail, null, 2);
        } catch (e) {
            return defaultMsg;
        }
    }
    return detail;
}

// --- 1. جریان اصلی (لیست کالاها) ---
async function fetchCommodities() {
  viewMode.value = 'list';
  isLoading.value = true;
  resetMessages();
  try {
    const response = await apiFetch(`/api/commodities/`);
    if (!response.ok) throw new Error('خطا در بارگیری لیست کالاها');
    commodities.value = await response.json();
  } catch (e: any) {
    errorMessage.value = getErrorDetail(e, 'خطای ناشناخته');
  } finally {
    isLoading.value = false;
  }
}

// --- 2. جریان مشاهده نام‌های مستعار ---
async function onManageAliases(commodity: Commodity, preserveMessages = false) {
  isLoading.value = true;
  if (!preserveMessages) {
    resetMessages();
  }
  try {
    const response = await apiFetch(`/api/commodities/${commodity.id}`);
    if (!response.ok) throw new Error('خطا در دریافت اطلاعات کالا');
    selectedCommodity.value = await response.json();
    viewMode.value = 'aliases';
  } catch (e: any) {
    errorMessage.value = getErrorDetail(e, 'خطای ناشناخته');
    viewMode.value = 'list';
  } finally {
    isLoading.value = false;
  }
}

// --- 3. افزودن کالای جدید ---
function onAddCommodityStart() {
  resetMessages();
  resetForm();
  viewMode.value = 'add_commodity';
}
async function onAddCommoditySubmit() {
  isLoading.value = true;
  resetMessages();
  try {
    const aliasList = form.aliasesText.split(/[،-]/)
                           .map(a => a.trim())
                           .filter(a => a.length > 0);
    
    const commodityName = form.name.trim();
    if (commodityName && !aliasList.includes(commodityName)) {
        aliasList.unshift(commodityName);
    }
    
    const payload = {
        commodity_data: { name: commodityName },
        aliases: aliasList
    };

    const response = await apiFetch(`/api/commodities/`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    
    const data = await response.json();
    
    if (!response.ok) {
        const errorObj = { detail: data.detail || 'خطا در افزودن کالا' }; 
        throw errorObj;
    }
    
    successMessage.value = `کالا «${data.name}» با موفقیت افزوده شد.`;
    await fetchCommodities(); 
  } catch (e: any) {
    errorMessage.value = getErrorDetail(e, 'خطای ناشناخته');
    viewMode.value = 'add_commodity'; 
  } finally {
    isLoading.value = false;
  }
}

// --- 4. ویرایش نام اصلی کالا ---
function onEditCommodityNameStart() {
  if (!selectedCommodity.value) return;
  if (selectedCommodityIsLockedImam.value) {
    resetMessages();
    errorMessage.value = 'نام کالای پیش فرض امام قابل ویرایش نیست. فقط نام های مستعار را مدیریت کنید.';
    return;
  }
  resetMessages();
  form.name = selectedCommodity.value.name;
  viewMode.value = 'edit_commodity_name';
}
async function onEditCommodityNameSubmit() {
  if (!selectedCommodity.value) return;
  isLoading.value = true;
  resetMessages();
  try {
    const response = await apiFetch(`/api/commodities/${selectedCommodity.value.id}`, {
      method: 'PUT',
      body: JSON.stringify({ name: form.name.trim() }),
    });
    const data = await response.json();
    if (!response.ok) {
         const errorObj = { detail: data.detail || 'خطا در ویرایش نام' };
         throw errorObj;
    }
    
    successMessage.value = `نام کالا با موفقیت به «${data.name}» تغییر یافت.`;
    await onManageAliases(data);
  } catch (e: any) {
    errorMessage.value = getErrorDetail(e, 'خطای ناشناخته');
    viewMode.value = 'edit_commodity_name';
  } finally {
    isLoading.value = false;
  }
}

// --- 5. افزودن نام مستعار ---
function onAddAliasStart() {
  if (!selectedCommodity.value) return;
  resetMessages();
  resetForm();
  viewMode.value = 'add_alias';
}
async function onAddAliasSubmit() {
  if (!selectedCommodity.value) return;
  isLoading.value = true;
  resetMessages();
  try {
    const aliasList = form.name.split(/[،\-]/)
                          .map(a => a.trim())
                          .filter(a => a.length > 0);

    if (aliasList.length === 0) {
      throw { detail: 'لطفاً حداقل یک نام مستعار وارد کنید.' };
    }

    const addedAliases: string[] = [];
    const failedAliases: string[] = [];

    for (const aliasName of aliasList) {
      const response = await apiFetch(`/api/commodities/${selectedCommodity.value.id}/aliases`, {
        method: 'POST',
        body: JSON.stringify({ alias: aliasName }),
      });
      const data = await response.json();
      if (response.ok) {
        addedAliases.push(data.alias);
      } else {
        failedAliases.push(`${aliasName}: ${data.detail || 'خطا'}`);
      }
    }

    if (addedAliases.length > 0) {
      successMessage.value = `نام‌های مستعار «${addedAliases.join('، ')}» با موفقیت افزوده شدند.`;
    }
    if (failedAliases.length > 0) {
      errorMessage.value = failedAliases.join('\n');
    }

    await onManageAliases(selectedCommodity.value, addedAliases.length > 0 || failedAliases.length > 0);
  } catch (e: any) {
    errorMessage.value = getErrorDetail(e, 'خطای ناشناخته');
    viewMode.value = 'add_alias';
  } finally {
    isLoading.value = false;
  }
}

// --- 6. ویرایش نام مستعار ---
function onEditAliasStart(alias: CommodityAlias) {
  if (!selectedCommodity.value) return;
  resetMessages();
  selectedAlias.value = alias;
  form.name = alias.alias;
  viewMode.value = 'edit_alias';
}
async function onEditAliasSubmit() {
  if (!selectedCommodity.value || !selectedAlias.value) return;
  isLoading.value = true;
  resetMessages();
  try {
     const response = await apiFetch(`/api/commodities/aliases/${selectedAlias.value.id}`, {
      method: 'PUT',
      body: JSON.stringify({ alias: form.name.trim() }),
    });
    const data = await response.json();
    if (!response.ok) {
         const errorObj = { detail: data.detail || 'خطا در ویرایش نام مستعار' };
         throw errorObj;
    }
    
    successMessage.value = `نام مستعار با موفقیت به «${data.alias}» تغییر یافت.`;
    await onManageAliases(selectedCommodity.value);
  } catch (e: any) {
    errorMessage.value = getErrorDetail(e, 'خطای ناشناخته');
    viewMode.value = 'edit_alias';
  } finally {
    isLoading.value = false;
  }
}

// --- 7. حذف کالا ---
function onDeleteCommodityStart() {
  if (!selectedCommodity.value) return;
  if (selectedCommodityIsLockedImam.value) {
    resetMessages();
    errorMessage.value = 'کالای پیش فرض امام قابل حذف نیست. فقط نام های مستعار را مدیریت کنید.';
    return;
  }
  resetMessages();
  viewMode.value = 'delete_commodity';
}
async function onDeleteCommodityConfirm() {
  if (!selectedCommodity.value) return;
  isLoading.value = true;
  resetMessages();
  try {
    const response = await apiFetch(`/api/commodities/${selectedCommodity.value.id}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
        const data = response.status !== 204 ? await response.json() : null;
        if (data) {
             const errorObj = { detail: data.detail || 'خطا در حذف کالا' };
             throw errorObj;
        }
    }
    
    successMessage.value = `کالا «${selectedCommodity.value.name}» با موفقیت حذف شد.`;
    await fetchCommodities();
  } catch (e: any) {
    errorMessage.value = getErrorDetail(e, 'خطای ناشناخته');
    await onManageAliases(selectedCommodity.value);
  } finally {
    isLoading.value = false;
  }
}

// --- 8. حذف نام مستعار ---
function onDeleteAliasStart(alias: CommodityAlias) {
  if (!selectedCommodity.value) return;
  resetMessages();
  selectedAlias.value = alias;
  viewMode.value = 'delete_alias';
}
async function onDeleteAliasConfirm() {
  if (!selectedCommodity.value || !selectedAlias.value) return;
  isLoading.value = true;
  resetMessages();
  try {
    const response = await apiFetch(`/api/commodities/aliases/${selectedAlias.value.id}`, {
      method: 'DELETE',
    });
    if (!response.ok) {
        const data = response.status !== 204 ? await response.json() : null;
        if (data) {
             const errorObj = { detail: data.detail || 'خطا در حذف نام مستعار' };
             throw errorObj;
        }
    }
    
    successMessage.value = `نام مستعار «${selectedAlias.value.alias}» با موفقیت حذف شد.`;
    await onManageAliases(selectedCommodity.value);
  } catch (e: any) {
    errorMessage.value = getErrorDetail(e, 'خطای ناشناخته');
    await onManageAliases(selectedCommodity.value);
  } finally {
    isLoading.value = false;
  }
}

// --- بارگیری اولیه ---
onMounted(fetchCommodities);

</script>

<template>
  <div class="commodity-manager ds-page-content">
    
    <div v-if="successMessage" class="ds-message success">{{ successMessage }}</div>
    <div v-if="errorMessage" class="ds-message danger">
       <pre class="error-pre">{{ errorMessage }}</pre>
    </div>

    <div v-if="viewMode === 'list'" class="ds-card">
      <div v-if="isLoading">
          <LoadingSkeleton :count="5" :height="60" />
      </div>
      <div v-else>
          <div v-if="commodities.length === 0" class="no-data">هیچ کالایی ثبت نشده است.</div>
          <div class="list-group">
            <button v-for="comm in commodities" :key="comm.id" @click="onManageAliases(comm)" class="list-item-btn">
              <span class="item-label">📦 {{ comm.name }}</span>
              <span class="chevron">&rsaquo;</span>
            </button>
          </div>
          <div class="card-footer">
            <button class="action-btn primary-soft" @click="onAddCommodityStart">
              ➕ افزودن کالای جدید
            </button>
          </div>
      </div>
    </div>

    <div v-if="viewMode === 'aliases' && selectedCommodity" class="ds-card">
      <div class="card-header">
        <button @click="fetchCommodities" class="back-icon-btn">
          <span>→</span>
        </button>
        <h2 class="card-title">{{ selectedCommodity.name }}</h2>
      </div>

      <div v-if="isLoading">
          <LoadingSkeleton :count="3" :height="50" />
      </div>
      <div v-else>
          <div v-if="selectedCommodity.aliases.length === 0" class="no-data">هیچ نام مستعاری ثبت نشده است.</div>
          <div class="alias-list">
            <div v-for="alias in selectedCommodity.aliases" :key="alias.id" class="alias-row">
              <span class="alias-text">{{ alias.alias }}</span>
              <div class="alias-actions">
                <button @click="onEditAliasStart(alias)" class="icon-btn edit">✏️</button>
                <button @click="onDeleteAliasStart(alias)" class="icon-btn delete">❌</button>
              </div>
            </div>
          </div>
          
          <div class="card-footer stacked">
            <button class="action-btn primary-soft" @click="onAddAliasStart">
              ➕ افزودن نام مستعار جدید
            </button>
            <p v-if="selectedCommodityIsLockedImam" class="locked-commodity-hint">
              کالای پیش فرض امام فقط از مسیر نام های مستعار قابل مدیریت است و تغییر نام یا حذف کامل ندارد.
            </p>
            <button v-if="!selectedCommodityIsLockedImam" class="action-btn secondary-soft" @click="onEditCommodityNameStart">
              ✏️ ویرایش نام اصلی کالا
            </button>
            <button v-if="!selectedCommodityIsLockedImam" class="action-btn danger-soft" @click="onDeleteCommodityStart">
              ❌ حذف کامل این کالا
            </button>
          </div>
      </div>
    </div>
    
    <div v-if="viewMode === 'add_commodity'" class="ds-card">
      <h2 class="card-title">افزودن کالای جدید</h2>
      <form @submit.prevent="onAddCommoditySubmit" class="manager-form">
        <div class="ds-form-group">
          <label class="ds-label">نام اصلی کالا</label>
          <input v-model="form.name" class="ds-input" type="text" placeholder="مثلاً: سکه امامی" required />
        </div>
        <div class="ds-form-group">
          <label class="ds-label">نام‌های مستعار (جدا با `،` یا `-`)</label>
          <input v-model="form.aliasesText" class="ds-input" type="text" placeholder="مثال: سکه جدید ، امامی - سکه بانکی" />
        </div>
        <div class="form-footer">
          <button type="submit" class="ds-btn primary" :disabled="isLoading">
            {{ isLoading ? 'در حال افزودن...' : 'افزودن کالا' }}
          </button>
          <button type="button" class="ds-btn secondary" @click="fetchCommodities" :disabled="isLoading">لغو</button>
        </div>
      </form>
    </div>

    <div v-if="viewMode === 'edit_commodity_name' && selectedCommodity" class="ds-card">
      <h2 class="card-title">ویرایش نام کالا</h2>
      <form @submit.prevent="onEditCommodityNameSubmit" class="manager-form">
        <div class="ds-form-group">
          <label class="ds-label">نام جدید برای «{{ selectedCommodity.name }}»</label>
          <input v-model="form.name" class="ds-input" type="text" required />
        </div>
        <div class="form-footer">
          <button type="submit" class="ds-btn primary" :disabled="isLoading">
            {{ isLoading ? 'در حال ذخیره...' : 'ذخیره نام' }}
          </button>
          <button type="button" class="ds-btn secondary" @click="onManageAliases(selectedCommodity)" :disabled="isLoading">لغو</button>
        </div>
      </form>
    </div>
    
    <div v-if="viewMode === 'add_alias' && selectedCommodity" class="ds-card">
      <h2 class="card-title">افزودن نام مستعار</h2>
      <form @submit.prevent="onAddAliasSubmit" class="manager-form">
        <div class="ds-form-group">
          <label class="ds-label">نام‌های مستعار (جدا با `،` یا `-`)</label>
          <input v-model="form.name" class="ds-input" type="text" placeholder="مثال: نیم تاریخ پایین ، نیم ت.پ" required />
        </div>
        <div class="form-footer">
          <button type="submit" class="ds-btn primary" :disabled="isLoading">
            {{ isLoading ? 'در حال افزودن...' : 'افزودن' }}
          </button>
          <button type="button" class="ds-btn secondary" @click="onManageAliases(selectedCommodity)" :disabled="isLoading">لغو</button>
        </div>
      </form>
    </div>
    
    <div v-if="viewMode === 'edit_alias' && selectedCommodity && selectedAlias" class="ds-card">
      <h2 class="card-title">ویرایش نام مستعار</h2>
      <form @submit.prevent="onEditAliasSubmit" class="manager-form">
        <div class="ds-form-group">
          <label class="ds-label">نام جدید برای «{{ selectedAlias.alias }}»</label>
          <input v-model="form.name" class="ds-input" type="text" required />
        </div>
        <div class="form-footer">
          <button type="submit" class="ds-btn primary" :disabled="isLoading">
            {{ isLoading ? 'در حال ذخیره...' : 'ذخیره' }}
          </button>
          <button type="button" class="ds-btn secondary" @click="onManageAliases(selectedCommodity)" :disabled="isLoading">لغو</button>
        </div>
      </form>
    </div>

    <div v-if="viewMode === 'delete_commodity' && selectedCommodity" class="ds-card confirm-card">
      <h2 class="card-title danger">حذف کالا</h2>
      <p class="confirm-text">⚠️ آیا از حذف کامل کالا **«{{ selectedCommodity.name }}»** مطمئن هستید؟ (تمام نام‌های مستعار آن نیز حذف خواهند شد)</p>
      <div class="form-footer">
        <button @click="onDeleteCommodityConfirm" :disabled="isLoading" class="ds-btn danger">
          {{ isLoading ? 'در حال حذف...' : ' بله، حذف کامل' }}
        </button>
        <button type="button" class="ds-btn secondary" @click="onManageAliases(selectedCommodity)" :disabled="isLoading">لغو</button>
      </div>
    </div>
    
    <div v-if="viewMode === 'delete_alias' && selectedCommodity && selectedAlias" class="ds-card confirm-card">
      <h2 class="card-title danger">حذف نام مستعار</h2>
      <p class="confirm-text">⚠️ آیا از حذف نام مستعار **«{{ selectedAlias.alias }}»** مطمئن هستید؟</p>
      <div class="form-footer">
        <button @click="onDeleteAliasConfirm" :disabled="isLoading" class="ds-btn danger">
          {{ isLoading ? 'در حال حذف...' : ' بله، حذف شود' }}
        </button>
        <button type="button" class="ds-btn secondary" @click="onManageAliases(selectedCommodity)" :disabled="isLoading">لغو</button>
      </div>
    </div>

  </div>
</template>

<style scoped>
.commodity-manager {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.error-pre {
  white-space: pre-wrap;
  margin: 0;
  font-family: inherit;
}

.no-data {
  text-align: center;
  color: var(--ds-text-placeholder);
  padding: 2rem 0;
  font-size: 0.9rem;
}

.locked-commodity-hint {
  margin: 0;
  padding: 0.85rem 1rem;
  border-radius: 14px;
  background: rgba(245, 158, 11, 0.12);
  color: var(--ds-text);
  line-height: 1.7;
  font-size: 0.88rem;
}

/* List Style */
.list-group {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.list-item-btn {
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem 1.25rem;
  background: var(--ds-bg-card);
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-lg);
  cursor: pointer;
  transition: all 0.2s;
}

.list-item-btn:hover {
  background: var(--ds-bg-hover);
  border-color: var(--ds-primary-300);
}

.list-item-btn .item-label {
  font-weight: 700;
  color: var(--ds-text-primary);
}

.list-item-btn .chevron {
  color: var(--ds-text-disabled);
  font-size: 1.2rem;
}

/* Card Header */
.card-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 1.5rem;
  padding-bottom: 0.75rem;
  border-bottom: 1px solid var(--ds-border-light);
}

.card-title {
  margin: 0;
  font-size: 1.1rem;
  font-weight: 800;
  color: var(--ds-text-primary);
}

.card-title.danger {
  color: var(--ds-danger-600);
}

.back-icon-btn {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--ds-bg-inset);
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-md);
  color: var(--ds-primary-600);
  font-weight: 900;
  cursor: pointer;
  transition: all 0.2s;
}

.back-icon-btn:hover {
  background: var(--ds-primary-50);
  border-color: var(--ds-primary-300);
}

/* Alias List */
.alias-list {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  margin-bottom: 1.5rem;
}

.alias-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem 1rem;
  background: var(--ds-bg-inset);
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-md);
}

.alias-text {
  font-weight: 600;
  color: var(--ds-text-secondary);
}

.alias-actions {
  display: flex;
  gap: 0.5rem;
}

.icon-btn {
  padding: 0.4rem 0.6rem;
  border-radius: var(--ds-radius-md);
  font-size: 0.9rem;
  transition: all 0.2s;
}

.icon-btn.edit {
  background: var(--ds-primary-50);
  color: var(--ds-primary-600);
}

.icon-btn.delete {
  background: var(--ds-danger-50);
  color: var(--ds-danger-600);
}

.icon-btn:active {
  transform: scale(0.9);
}

/* Footers */
.card-footer {
  margin-top: 1.5rem;
  padding-top: 1rem;
  border-top: 1px solid var(--ds-border-light);
}

.card-footer.stacked {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.action-btn {
  width: 100%;
  padding: 0.85rem;
  border-radius: var(--ds-radius-lg);
  font-weight: 700;
  font-size: 0.9rem;
  cursor: pointer;
  transition: all 0.2s;
}

.action-btn.primary-soft {
  background: var(--ds-primary-50);
  color: var(--ds-primary-700);
  border: 1px solid var(--ds-primary-100);
}

.action-btn.secondary-soft {
  background: var(--ds-bg-inset);
  color: var(--ds-text-secondary);
  border: 1px solid var(--ds-border-light);
}

.action-btn.danger-soft {
  background: var(--ds-danger-50);
  color: var(--ds-danger-700);
  border: 1px solid var(--ds-danger-100);
}

.action-btn:active {
  transform: scale(0.98);
}

/* Forms */
.manager-form {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.form-footer {
  display: flex;
  gap: 0.75rem;
  margin-top: 0.5rem;
}

.form-footer .ds-btn {
  flex: 1;
}

.confirm-text {
  font-size: 0.95rem;
  line-height: 1.6;
  color: var(--ds-text-secondary);
  margin-bottom: 1.5rem;
}
</style>