<script setup lang="ts">
import { ref, onMounted, reactive } from 'vue';

const props = defineProps<{
  apiBaseUrl: string;
  jwtToken: string | null;
}>();

// --- اینترفیس‌های داده ---
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

// === شروع اصلاح ===
// اینترفیس جدید برای فرم ویرایش که شامل فیلد کمکی ماست
interface CommodityForEdit extends Commodity {
  aliasesInput?: string; // فیلد کمکی برای input
}

const commodities = ref<Commodity[]>([]);
const isLoading = ref(false);
const errorMessage = ref('');
const successMessage = ref('');

// ref اکنون از نوع اینترفیس جدید است
const editingCommodity = ref<CommodityForEdit | null>(null);
// === پایان اصلاح ===

const newCommodity = reactive({ name: '', aliases: '' }); // برای فرم افزودن

// --- Fetch Commodities ---
async function fetchCommodities() {
  if (!props.jwtToken) return;
  isLoading.value = true;
  errorMessage.value = '';
  successMessage.value = '';
  try {
    const response = await fetch(`${props.apiBaseUrl}/api/commodities/`, {
      headers: { Authorization: `Bearer ${props.jwtToken}` },
    });
    if (!response.ok) throw new Error('Failed to fetch commodities');
    commodities.value = await response.json();
  } catch (e: any) {
    errorMessage.value = e.message;
  } finally {
    isLoading.value = false;
  }
}

// --- Add Commodity ---
async function addCommodity() {
  if (!props.jwtToken || !newCommodity.name) return;
  isLoading.value = true;
  errorMessage.value = '';
  successMessage.value = '';
  try {
    const aliasList = newCommodity.aliases.split(',')
                           .map(a => a.trim())
                           .filter(a => a.length > 0);

    const response = await fetch(`${props.apiBaseUrl}/api/commodities/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${props.jwtToken}`,
      },
      body: JSON.stringify({ name: newCommodity.name, aliases: aliasList }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Failed to add commodity');
    successMessage.value = `کالای '${data.name}' با موفقیت اضافه شد.`;
    newCommodity.name = ''; // Reset form
    newCommodity.aliases = '';
    await fetchCommodities(); // Refresh list
  } catch (e: any) {
    errorMessage.value = e.message;
  } finally {
    isLoading.value = false;
  }
}

// --- Start Editing ---
function startEdit(commodity: Commodity) {
  // === شروع اصلاح ===
  // کپی عمیق و تخصیص نوع جدید
  const commodityToEdit: CommodityForEdit = JSON.parse(JSON.stringify(commodity));
  // تبدیل آرایه alias ها به رشته برای نمایش در input
  commodityToEdit.aliasesInput = commodityToEdit.aliases.map(a => a.alias).join(', ');
  editingCommodity.value = commodityToEdit;
  // === پایان اصلاح ===
}

// --- Save Edit ---
async function saveEdit() {
  if (!props.jwtToken || !editingCommodity.value) return;
  isLoading.value = true;
  errorMessage.value = '';
  successMessage.value = '';
  try {
    const commodityToUpdate = editingCommodity.value;
    // === اصلاح: دسترسی به aliasesInput که اکنون در نوع تعریف شده ===
    const aliasList = (commodityToUpdate.aliasesInput || '').split(',')
                           .map(a => a.trim())
                           .filter(a => a.length > 0);

    const response = await fetch(`${props.apiBaseUrl}/api/commodities/${commodityToUpdate.id}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${props.jwtToken}`,
      },
      body: JSON.stringify({ name: commodityToUpdate.name, aliases: aliasList }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Failed to update commodity');
    successMessage.value = `کالای '${data.name}' با موفقیت ویرایش شد.`;
    editingCommodity.value = null; // Exit edit mode
    await fetchCommodities(); // Refresh list
  } catch (e: any) {
    errorMessage.value = `خطا در ویرایش: ${e.message}`;
  } finally {
    isLoading.value = false;
  }
}

// --- Delete Commodity ---
async function deleteCommodity(commodityId: number, commodityName: string) {
  if (!props.jwtToken || !confirm(`آیا از حذف کالای '${commodityName}' مطمئن هستید؟`)) return;
  isLoading.value = true;
  errorMessage.value = '';
  successMessage.value = '';
  try {
    const response = await fetch(`${props.apiBaseUrl}/api/commodities/${commodityId}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${props.jwtToken}` },
    });
    if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(data?.detail || `Failed to delete commodity (Status: ${response.status})`);
    }
    successMessage.value = `کالای '${commodityName}' با موفقیت حذف شد.`;
    await fetchCommodities(); // Refresh list
  } catch (e: any) {
    errorMessage.value = e.message;
  } finally {
    isLoading.value = false;
  }
}

onMounted(fetchCommodities);

// Helper to display aliases
function formatAliases(aliases: CommodityAlias[]): string {
  return aliases.map(a => a.alias).join(', ') || '-';
}

</script>

<template>
  <div class="card commodity-manager">
    <h2>مدیریت کالاها</h2>

    <div v-if="isLoading" class="loading-inline">در حال بارگذاری...</div>
    <div v-if="errorMessage" class="message error">{{ errorMessage }}</div>
    <div v-if="successMessage" class="message success">{{ successMessage }}</div>

    <form @submit.prevent="addCommodity" class="add-form" v-if="!editingCommodity">
      <h3>افزودن کالای جدید</h3>
      <div class="form-row">
        <input v-model="newCommodity.name" placeholder="نام کالا (مثلا: سکه امامی)" required />
        <input v-model="newCommodity.aliases" placeholder="نام‌های مستعار (جدا با ویرگول)" />
        <button type="submit" :disabled="isLoading">افزودن</button>
      </div>
    </form>

    <form @submit.prevent="saveEdit" class="edit-form" v-if="editingCommodity">
       <h3>ویرایش: {{ editingCommodity.name }}</h3>
       <div class="form-group">
         <label>نام کالا:</label>
         <input v-model="editingCommodity.name" required />
       </div>
       <div class="form-group">
         <label>نام‌های مستعار (جدا با ویرگول):</label>
         <input v-model="editingCommodity.aliasesInput" />
       </div>
       <div class="form-actions">
         <button type="submit" :disabled="isLoading">ذخیره تغییرات</button>
         <button type="button" class="secondary" @click="editingCommodity = null" :disabled="isLoading">لغو</button>
       </div>
    </form>

    <div class="commodity-list" v-if="!editingCommodity && commodities.length > 0">
      <h3>لیست کالاها</h3>
      <table>
        <thead>
          <tr>
            <th>نام کالا</th>
            <th>نام‌های مستعار</th>
            <th>عملیات</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="comm in commodities" :key="comm.id">
            <td>{{ comm.name }}</td>
            <td>{{ formatAliases(comm.aliases) }}</td>
            <td>
              <button class="action-btn edit" @click="startEdit(comm)" :disabled="isLoading">ویرایش</button>
              <button class="action-btn delete" @click="deleteCommodity(comm.id, comm.name)" :disabled="isLoading">حذف</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
     <div v-if="!isLoading && commodities.length === 0 && !editingCommodity" class="no-data">
        هنوز کالایی ثبت نشده است.
    </div>

  </div>
</template>

<style scoped>
/* ... (تمام استایل‌های شما بدون تغییر باقی می‌ماند) ... */
.commodity-manager { /* Inherits .card styles */ }
h2 { margin-top: 0; margin-bottom: 20px; }
h3 { margin-top: 25px; margin-bottom: 10px; font-size: 16px; border-bottom: 1px solid var(--border-color); padding-bottom: 5px; }

.loading-inline { color: var(--text-secondary); margin-bottom: 15px; }
.message { padding: 10px; border-radius: 6px; margin-bottom: 15px; font-size: 14px; }
.message.error { background-color: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }
.message.success { background-color: #f0fdf4; color: #166534; border: 1px solid #bbf7d0; }

/* Add/Edit Forms */
.add-form .form-row { display: grid; grid-template-columns: 1fr 1fr auto; gap: 10px; align-items: end; margin-bottom: 20px;}
.edit-form .form-group { margin-bottom: 15px; }
.edit-form label { display: block; margin-bottom: 5px; font-size: 13px; color: var(--text-secondary); }
.edit-form .form-actions { display: flex; gap: 10px; margin-top: 15px; }

input, select, button { /* Inherits base styles */ }
button[type="submit"] { background-color: var(--primary-color); color: white; }
button.secondary { background-color: var(--card-bg); color: var(--text-secondary); border: 1px solid var(--border-color); }
button:disabled { background-color: #e5e7eb; cursor: not-allowed; color: #9ca3af; border-color: #e5e7eb; }

/* Commodity List Table */
.commodity-list { margin-top: 20px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: right; padding: 10px 8px; border-bottom: 1px solid var(--border-color); }
th { color: var(--text-secondary); font-weight: 500; font-size: 13px; }
td:last-child { text-align: left; white-space: nowrap; } /* Align actions to left */

.action-btn {
  padding: 4px 8px;
  font-size: 12px;
  border-radius: 6px;
  margin-right: 5px;
  cursor: pointer;
  border: none;
}
.action-btn.edit { background-color: #e0f2fe; color: #075985; }
.action-btn.delete { background-color: #fee2e2; color: #991b1b; }
.action-btn:disabled { background-color: #f3f4f6; color: #9ca3af; }

.no-data {
    text-align: center;
    color: var(--text-secondary);
    margin-top: 20px;
    padding: 15px;
    background-color: #f9fafb;
    border-radius: 8px;
}
</style>