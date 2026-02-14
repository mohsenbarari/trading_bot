<script setup lang="ts">
import { ref, onMounted, reactive, computed } from 'vue';
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

const API_HEADERS = computed(() => ({
  'Content-Type': 'application/json',
  Authorization: `Bearer ${props.jwtToken}`,
}));

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
    const response = await fetch(`${props.apiBaseUrl}/api/commodities/`, { headers: API_HEADERS.value });
    if (!response.ok) throw new Error('خطا در بارگیری لیست کالاها');
    commodities.value = await response.json();
  } catch (e: any) {
    errorMessage.value = getErrorDetail(e, 'خطای ناشناخته');
  } finally {
    isLoading.value = false;
  }
}

// --- 2. جریان مشاهده نام‌های مستعار ---
async function onManageAliases(commodity: Commodity) {
  isLoading.value = true;
  resetMessages();
  try {
    const response = await fetch(`${props.apiBaseUrl}/api/commodities/${commodity.id}`, { headers: API_HEADERS.value });
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

// --- 3. افزودن کالای جدید (با Payload اصلاح شده) ---
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
    
    // فرمت صحیح برای API
    const payload = {
        commodity_data: { name: commodityName },
        aliases: aliasList
    };

    const response = await fetch(`${props.apiBaseUrl}/api/commodities/`, {
      method: 'POST',
      headers: API_HEADERS.value,
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
  resetMessages();
  form.name = selectedCommodity.value.name;
  viewMode.value = 'edit_commodity_name';
}
async function onEditCommodityNameSubmit() {
  if (!selectedCommodity.value) return;
  isLoading.value = true;
  resetMessages();
  try {
    const response = await fetch(`${props.apiBaseUrl}/api/commodities/${selectedCommodity.value.id}`, {
      method: 'PUT',
      headers: API_HEADERS.value,
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
    const response = await fetch(`${props.apiBaseUrl}/api/commodities/${selectedCommodity.value.id}/aliases`, {
      method: 'POST',
      headers: API_HEADERS.value,
      body: JSON.stringify({ alias: form.name.trim() }),
    });
    const data = await response.json();
    if (!response.ok) {
         const errorObj = { detail: data.detail || 'خطا در افزودن نام مستعار' };
         throw errorObj;
    }

    successMessage.value = `نام مستعار «${data.alias}» با موفقیت افزوده شد.`;
    await onManageAliases(selectedCommodity.value);
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
     const response = await fetch(`${props.apiBaseUrl}/api/commodities/aliases/${selectedAlias.value.id}`, {
      method: 'PUT',
      headers: API_HEADERS.value,
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
  resetMessages();
  viewMode.value = 'delete_commodity';
}
async function onDeleteCommodityConfirm() {
  if (!selectedCommodity.value) return;
  isLoading.value = true;
  resetMessages();
  try {
    const response = await fetch(`${props.apiBaseUrl}/api/commodities/${selectedCommodity.value.id}`, {
      method: 'DELETE',
      headers: API_HEADERS.value,
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
    const response = await fetch(`${props.apiBaseUrl}/api/commodities/aliases/${selectedAlias.value.id}`, {
      method: 'DELETE',
      headers: API_HEADERS.value,
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
  <div class="commodity-manager-container">
    
    <div v-if="successMessage" class="message success">{{ successMessage }}</div>
    <div v-if="errorMessage" class="message error">
       <pre style="white-space: pre-wrap; margin: 0;">{{ errorMessage }}</pre>
    </div>
    <!-- Removed generic spinner container -->

    <div v-if="viewMode === 'list'" class="card">
      <div class="header-row">
        <h2 class="page-title">مدیریت کالاها</h2>
        <button class="back-button" @click="$emit('navigate', 'admin_panel')">🔙</button>
      </div>

      <div v-if="isLoading">
          <LoadingSkeleton :count="5" :height="60" />
      </div>
      <div v-else>
          <div v-if="commodities.length === 0" class="no-data">هیچ کالایی ثبت نشده است.</div>
          <div class="button-list">
            <button v-for="comm in commodities" :key="comm.id" @click="onManageAliases(comm)" class="list-button">
              <span>📦 {{ comm.name }}</span>
              <span>&rsaquo;</span>
            </button>
          </div>
          <hr class="divider" />
          <button class="list-button add-button" @click="onAddCommodityStart">
            <span>➕ افزودن کالای جدید</span>
          </button>
      </div>
    </div>

    <div v-if="viewMode === 'aliases' && selectedCommodity" class="card">
      <div class="header-row">
        <h2 class="page-title">مدیریت: {{ selectedCommodity.name }}</h2>
        <button @click="fetchCommodities" class="back-button">🔙</button>
      </div>

      <div v-if="isLoading">
          <LoadingSkeleton :count="3" :height="50" />
      </div>
      <div v-else>
          <div v-if="selectedCommodity.aliases.length === 0" class="no-data">هیچ نام مستعاری ثبت نشده است.</div>
          <div class="alias-list">
            <div v-for="alias in selectedCommodity.aliases" :key="alias.id" class="alias-item">
              <span>{{ alias.alias }}</span>
              <div class="alias-actions">
                <button @click="onEditAliasStart(alias)" class="action-btn edit">✏️</button>
                <button @click="onDeleteAliasStart(alias)" class="action-btn delete">❌</button>
              </div>
            </div>
          </div>
          <hr class="divider" />
          <div class="button-list stacked">
            <button class="list-button add-button" @click="onAddAliasStart">
              <span>➕ افزودن نام مستعار جدید</span>
            </button>
            <button class="list-button edit-button" @click="onEditCommodityNameStart">
              <span>✏️ ویرایش نام اصلی کالا</span>
            </button>
            <button class="list-button delete-button" @click="onDeleteCommodityStart">
              <span>❌ حذف کامل این کالا</span>
            </button>
          </div>
      </div>
    </div>
    
    <div v-if="viewMode === 'add_commodity'" class="card">
      <h2>افزودن کالای جدید</h2>
      <form @submit.prevent="onAddCommoditySubmit">
        <div class="form-group">
          <label for="comm_name">نام اصلی کالا</label>
          <input v-model="form.name" id="comm_name" type="text" placeholder="مثلاً: سکه امامی" required />
        </div>
        <div class="form-group">
          <label for="comm_aliases">نام‌های مستعار (جدا با `،` یا `-`)</label>
          <input v-model="form.aliasesText" id="comm_aliases" type="text" placeholder="مثال: سکه جدید ، امامی - سکه بانکی" />
        </div>
        <div class="form-actions">
          <button type="submit" :disabled="isLoading">
            {{ isLoading ? 'در حال افزودن...' : 'افزودن کالا' }}
          </button>
          <button type="button" class="secondary" @click="fetchCommodities" :disabled="isLoading">لغو</button>
        </div>
      </form>
    </div>

    <div v-if="viewMode === 'edit_commodity_name' && selectedCommodity" class="card">
      <h2>ویرایش نام کالا</h2>
      <form @submit.prevent="onEditCommodityNameSubmit">
        <div class="form-group">
          <label for="comm_edit_name">نام جدید برای «{{ selectedCommodity.name }}»</label>
          <input v-model="form.name" id="comm_edit_name" type="text" required />
        </div>
        <div class="form-actions">
          <button type="submit" :disabled="isLoading">
            {{ isLoading ? 'در حال ذخیره...' : 'ذخیره نام' }}
          </button>
          <button type="button" class="secondary" @click="onManageAliases(selectedCommodity)" :disabled="isLoading">لغو</button>
        </div>
      </form>
    </div>
    
    <div v-if="viewMode === 'add_alias' && selectedCommodity" class="card">
      <h2>افزودن نام مستعار به «{{ selectedCommodity.name }}»</h2>
      <form @submit.prevent="onAddAliasSubmit">
        <div class="form-group">
          <label for="alias_add_name">نام مستعار جدید</label>
          <input v-model="form.name" id="alias_add_name" type="text" required />
        </div>
        <div class="form-actions">
          <button type="submit" :disabled="isLoading">
            {{ isLoading ? 'در حال افزودن...' : 'افزودن' }}
          </button>
          <button type="button" class="secondary" @click="onManageAliases(selectedCommodity)" :disabled="isLoading">لغو</button>
        </div>
      </form>
    </div>
    
    <div v-if="viewMode === 'edit_alias' && selectedCommodity && selectedAlias" class="card">
      <h2>ویرایش نام مستعار</h2>
      <form @submit.prevent="onEditAliasSubmit">
        <div class="form-group">
          <label for="alias_edit_name">نام جدید برای «{{ selectedAlias.alias }}»</label>
          <input v-model="form.name" id="alias_edit_name" type="text" required />
        </div>
        <div class="form-actions">
          <button type="submit" :disabled="isLoading">
            {{ isLoading ? 'در حال ذخیره...' : 'ذخیره' }}
          </button>
          <button type="button" class="secondary" @click="onManageAliases(selectedCommodity)" :disabled="isLoading">لغو</button>
        </div>
      </form>
    </div>

    <div v-if="viewMode === 'delete_commodity' && selectedCommodity" class="card confirmation-dialog">
      <h2>حذف کالا</h2>
      <p>⚠️ آیا از حذف کامل کالا **«{{ selectedCommodity.name }}»** مطمئن هستید؟ (تمام نام‌های مستعار آن نیز حذف خواهند شد)</p>
      <div class="form-actions">
        <button @click="onDeleteCommodityConfirm" :disabled="isLoading" class="delete-confirm">
          {{ isLoading ? 'در حال حذف...' : ' بله، حذف کامل' }}
        </button>
        <button type="button" class="secondary" @click="onManageAliases(selectedCommodity)" :disabled="isLoading">لغو</button>
      </div>
    </div>
    
    <div v-if="viewMode === 'delete_alias' && selectedCommodity && selectedAlias" class="card confirmation-dialog">
      <h2>حذف نام مستعار</h2>
      <p>⚠️ آیا از حذف نام مستعار **«{{ selectedAlias.alias }}»** مطمئن هستید؟</p>
      <div class="form-actions">
        <button @click="onDeleteAliasConfirm" :disabled="isLoading" class="delete-confirm">
          {{ isLoading ? 'در حال حذف...' : ' بله، حذف شود' }}
        </button>
        <button type="button" class="secondary" @click="onManageAliases(selectedCommodity)" :disabled="isLoading">لغو</button>
      </div>
    </div>

  </div>
</template>

<style scoped>
/* Base card & form */
.card {
  background: rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(245, 158, 11, 0.1);
  border-radius: 1.25rem;
  padding: 1.25rem;
  box-shadow: 0 4px 16px rgba(0,0,0,0.04);
}
.form-group { margin-bottom: 1rem; }
label { display: block; margin-bottom: 0.5rem; font-weight: 700; font-size: 0.8rem; color: #6b7280; }
input {
  width: 100%; padding: 0.625rem 0.875rem; border-radius: 0.75rem;
  border: 1px solid rgba(245, 158, 11, 0.15); background: white;
  font-size: 0.9rem; font-family: inherit; outline: none; transition: all 0.2s;
}
input:focus { border-color: #f59e0b; box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.1); }
.form-actions { display: flex; gap: 0.75rem; margin-top: 1.5rem; }
button {
  flex-grow: 1; background: linear-gradient(135deg, #f59e0b, #d97706); color: white;
  border: none; cursor: pointer; font-weight: 700; padding: 0.75rem;
  border-radius: 0.75rem; font-size: 0.9rem; transition: all 0.2s;
  -webkit-tap-highlight-color: transparent;
}
button:active { transform: scale(0.98); }
button:disabled { background: #d1d5db; cursor: not-allowed; }
button.secondary {
  background: white; color: #6b7280;
  border: 1px solid rgba(245, 158, 11, 0.15); flex-grow: 0;
}
button.secondary:active { background: #f9fafb; }
.message { padding: 0.75rem; border-radius: 0.75rem; margin-bottom: 1rem; font-size: 0.8rem; }
.message.error { background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }
.message.success { background: #f0fdf4; color: #166534; border: 1px solid #bbf7d0; }
.no-data { text-align: center; color: #9ca3af; padding: 1.5rem 0; font-size: 0.85rem; }
.divider { border: none; border-top: 1px solid rgba(245, 158, 11, 0.1); margin: 1rem 0; }

/* Header */
.header-row {
  display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;
}
.page-title { font-size: 1rem; font-weight: 800; color: #1f2937; margin: 0; }
.back-button {
  flex-grow: 0; width: 36px; height: 36px;
  background: white; border: 1px solid rgba(245, 158, 11, 0.15);
  border-radius: 0.75rem; font-size: 0.9rem; cursor: pointer; color: #6b7280;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.2s; -webkit-tap-highlight-color: transparent; padding: 0;
}
.back-button:active { transform: scale(0.9); background: #f9fafb; }

/* Commodity list */
.button-list { display: flex; flex-direction: column; gap: 0.5rem; }
.list-button {
  width: 100%; background: white; color: #1f2937;
  border: 1px solid rgba(245, 158, 11, 0.1); padding: 0.875rem 1rem;
  font-size: 0.9rem; font-weight: 600; text-align: right;
  display: flex; justify-content: space-between; align-items: center;
  border-radius: 0.875rem; transition: all 0.2s;
  -webkit-tap-highlight-color: transparent;
}
.list-button:hover { border-color: rgba(245, 158, 11, 0.3); background: #fffbeb; }
.list-button:active { transform: scale(0.98); }
.list-button span:last-child { color: #d1d5db; }
.list-button.add-button {
  color: #d97706; justify-content: center;
  background: linear-gradient(135deg, #fffbeb, #fef3c7);
  border-color: rgba(245, 158, 11, 0.2);
}
.list-button.add-button:hover { background: #fef3c7; }
.list-button.edit-button {
  color: #92400e; justify-content: center;
  background: #fffbeb; border-color: rgba(245, 158, 11, 0.15);
}
.list-button.delete-button {
  color: #dc2626; justify-content: center;
  background: #fef2f2; border-color: #fecaca;
}

/* Aliases */
.alias-list { display: flex; flex-direction: column; gap: 0.5rem; margin-top: 1rem; }
.alias-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 0.75rem 0.875rem; background: white; border-radius: 0.75rem;
  border: 1px solid rgba(245, 158, 11, 0.08);
}
.alias-item span { font-weight: 600; font-size: 0.85rem; color: #1f2937; }
.alias-actions { display: flex; gap: 0.375rem; }
.action-btn {
  padding: 0.375rem 0.625rem; font-size: 0.8rem; border-radius: 0.5rem;
  flex-grow: 0; border: none; background: transparent; cursor: pointer;
  transition: all 0.2s; -webkit-tap-highlight-color: transparent;
}
.action-btn:active { transform: scale(0.9); }
.action-btn.edit { color: #d97706; background: #fffbeb; }
.action-btn.delete { color: #dc2626; background: #fef2f2; }
.button-list.stacked { margin-top: 1.25rem; }

/* Confirmation dialog */
.confirmation-dialog p { font-size: 0.85rem; line-height: 1.7; color: #4b5563; }
.confirmation-dialog p strong { color: #dc2626; }
button.delete-confirm {
  background: linear-gradient(135deg, #ef4444, #dc2626);
  box-shadow: 0 4px 12px rgba(239, 68, 68, 0.25);
}
h2 { margin-top: 0; margin-bottom: 1rem; font-size: 1rem; font-weight: 800; color: #1f2937; }
</style>