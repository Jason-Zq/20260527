<template>
  <div class="client-list-page">
    <div class="page-header">
      <el-button class="back-btn" @click="emit('back')" size="default">
        <el-icon style="margin-right: 4px"><ArrowLeft /></el-icon>
        返回
      </el-button>
      <div class="page-title">
        <span class="title-indicator"></span>
        客户档案
      </div>

      <div class="header-controls">
        <el-input
          v-model="keyword"
          placeholder="姓名 / 证件号 / 护照号 / 客户编号"
          size="default"
          clearable
          class="search-input"
          @keyup.enter="loadClients"
          @clear="loadClients"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>

        <el-select
          v-model="visaTypeFilter"
          placeholder="业务类型"
          size="default"
          clearable
          class="filter-select"
          @change="loadClients"
        >
          <el-option v-for="vt in visaTypeOptions" :key="vt" :label="vt" :value="vt" />
        </el-select>

        <el-select
          v-model="sortBy"
          size="default"
          class="filter-select small"
          @change="loadClients"
        >
          <el-option label="最近更新" value="updated_at" />
          <el-option label="护照到期" value="passport_expiry" />
        </el-select>

        <el-button
          size="default"
          :type="expiringOnly ? 'warning' : 'default'"
          @click="toggleExpiring"
        >
          <el-icon style="margin-right: 4px"><Warning /></el-icon>
          90 天内到期
        </el-button>

        <el-button type="primary" size="default" @click="showCreate = true">
          <el-icon style="margin-right: 4px"><Plus /></el-icon>
          新建客户
        </el-button>
      </div>
    </div>

    <div class="page-content">
      <div v-if="loading" class="loading-state">
        <el-icon class="is-loading" :size="28"><Loading /></el-icon>
        <p>加载中...</p>
      </div>

      <div v-else-if="clients.length === 0" class="empty-state">
        <p class="empty-text">暂无客户档案</p>
        <p class="empty-hint">点击"新建客户"或上传证件后系统会自动归档</p>
      </div>

      <div v-else class="clients-grid">
        <div class="results-summary">
          共 <strong>{{ clients.length }}</strong> 位客户
          <span v-if="visaTypeFilter">（业务类型：{{ visaTypeFilter }}）</span>
          <span v-if="expiringOnly">（仅显示 90 天内护照到期）</span>
        </div>

        <div
          v-for="c in clients"
          :key="c.id"
          class="client-card"
          @click="emit('select', c.id)"
        >
          <div class="client-avatar">{{ (c.name || '?').charAt(0) }}</div>
          <div class="client-info">
            <div class="client-name">
              <span class="code" v-if="c.client_code">[{{ c.client_code }}]</span>
              {{ c.name || '未知' }}
              <span class="name-en" v-if="c.name_en">{{ c.name_en }}</span>
              <el-tag v-if="c.gender" size="small" type="info" effect="plain">{{ c.gender }}</el-tag>
              <el-tag v-if="c.visa_type" size="small" type="primary" effect="plain">{{ c.visa_type }}</el-tag>
            </div>

            <div class="client-meta">
              <span v-if="c.id_number" class="meta-id">身份证 {{ maskedId(c.id_number) }}</span>
              <span v-if="c.passport_no" class="meta-id">护照 {{ c.passport_no }}</span>
              <span v-if="c.nationality">{{ c.nationality }}</span>
              <span v-if="c.birth_date">出生 {{ c.birth_date }}</span>
            </div>

            <div class="client-bottom">
              <div class="bottom-left">
                <span class="stat-pill">
                  <el-icon size="12"><Document /></el-icon> {{ c.doc_count }}
                </span>
                <span class="stat-pill">
                  <el-icon size="12"><User /></el-icon> {{ c.family_count }}
                </span>
                <span class="stat-pill">
                  <el-icon size="12"><Wallet /></el-icon> {{ c.asset_count }}
                </span>
                <span
                  v-if="c.passport_expiry_date"
                  class="expiry-pill"
                  :class="{ 'expiry-soon': isExpiringSoon(c.passport_expiry_date) }"
                  :title="'护照到期 ' + c.passport_expiry_date"
                >
                  护照至 {{ c.passport_expiry_date }}
                </span>
              </div>
              <span class="update-time">{{ c.updated_at }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 新建客户弹窗 -->
    <el-dialog v-model="showCreate" title="新建客户" width="520px" @close="resetCreateForm">
      <el-form :model="createForm" label-width="92px" label-position="right">
        <el-form-item label="客户姓名" required>
          <el-input v-model="createForm.name" placeholder="必填" />
        </el-form-item>
        <el-form-item label="客户编号">
          <el-input v-model="createForm.client_code" placeholder="可选，自定义编号" />
        </el-form-item>
        <el-form-item label="拼音/英文">
          <el-input v-model="createForm.name_en" placeholder="护照拼写" />
        </el-form-item>
        <el-form-item label="性别">
          <el-select v-model="createForm.gender" clearable style="width: 100%">
            <el-option label="男" value="男" />
            <el-option label="女" value="女" />
          </el-select>
        </el-form-item>
        <el-form-item label="出生日期">
          <el-date-picker v-model="createForm.birth_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
        </el-form-item>
        <el-form-item label="证件号">
          <el-input v-model="createForm.id_number" placeholder="身份证号" />
        </el-form-item>
        <el-form-item label="护照号">
          <el-input v-model="createForm.passport_no" />
        </el-form-item>
        <el-form-item label="业务类型">
          <el-input v-model="createForm.visa_type" placeholder="如：加拿大技术移民 EE" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreate = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="handleCreate">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ArrowLeft, Search, Loading, Plus, Warning, Document, User, Wallet } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { listClients, createClient } from '../api.js'

const emit = defineEmits(['back', 'select'])

const keyword = ref('')
const visaTypeFilter = ref('')
const expiringOnly = ref(false)
const sortBy = ref('updated_at')
const clients = ref([])
const loading = ref(false)

const showCreate = ref(false)
const creating = ref(false)
const createForm = ref({
  name: '', client_code: '', name_en: '', gender: '', birth_date: '',
  id_number: '', passport_no: '', visa_type: '',
})

// visa_type 候选：从已有客户聚合（前端去重）
const visaTypeOptions = computed(() => {
  const set = new Set()
  for (const c of clients.value) {
    if (c.visa_type) set.add(c.visa_type)
  }
  return [...set].sort()
})

function isExpiringSoon(dateStr) {
  if (!dateStr) return false
  const exp = new Date(dateStr)
  const now = new Date()
  const days = (exp - now) / (1000 * 60 * 60 * 24)
  return days >= 0 && days <= 90
}

function maskedId(id) {
  if (!id || id.length < 8) return id
  return id.slice(0, 4) + '****' + id.slice(-4)
}

function toggleExpiring() {
  expiringOnly.value = !expiringOnly.value
  loadClients()
}

async function loadClients() {
  loading.value = true
  try {
    const data = await listClients(keyword.value.trim() || null, {
      visa_type: visaTypeFilter.value || null,
      expiring_soon_days: expiringOnly.value ? 90 : null,
      sort_by: sortBy.value,
    })
    clients.value = data.clients || []
  } catch (err) {
    ElMessage.error('加载失败：' + (err.response?.data?.detail || err.message))
  } finally {
    loading.value = false
  }
}

function resetCreateForm() {
  createForm.value = {
    name: '', client_code: '', name_en: '', gender: '', birth_date: '',
    id_number: '', passport_no: '', visa_type: '',
  }
}

async function handleCreate() {
  if (!createForm.value.name?.trim()) {
    ElMessage.warning('客户姓名不能为空')
    return
  }
  creating.value = true
  try {
    const payload = {}
    for (const [k, v] of Object.entries(createForm.value)) {
      if (v != null && v !== '') payload[k] = v
    }
    const c = await createClient(payload)
    ElMessage.success(`已创建客户：${c.name}`)
    showCreate.value = false
    resetCreateForm()
    await loadClients()
  } catch (err) {
    ElMessage.error('创建失败：' + (err.response?.data?.detail || err.message))
  } finally {
    creating.value = false
  }
}

onMounted(loadClients)
</script>

<style scoped>
.client-list-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #f0f2f8;
  overflow: hidden;
}

.page-header {
  padding: 16px 24px;
  background: #ffffff;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
  flex-wrap: wrap;
}

.back-btn { flex-shrink: 0; }

.page-title {
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.title-indicator {
  width: 3px;
  height: 16px;
  background: linear-gradient(180deg, #6366f1, #8b5cf6);
  border-radius: 2px;
}

.header-controls {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.search-input { width: 280px; }
.filter-select { width: 160px; }
.filter-select.small { width: 120px; }

.page-content {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
}

.loading-state,
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 20px;
  color: #94a3b8;
}

.empty-text { font-size: 16px; color: #475569; margin: 4px 0; font-weight: 500; }
.empty-hint { font-size: 13px; color: #94a3b8; margin: 4px 0; }

.clients-grid {
  max-width: 1200px;
  margin: 0 auto;
}

.results-summary {
  font-size: 13px;
  color: #64748b;
  margin-bottom: 14px;
  padding-bottom: 10px;
  border-bottom: 1px solid #e2e8f0;
}

.client-card {
  display: flex;
  gap: 14px;
  background: #ffffff;
  border-radius: 12px;
  padding: 14px 18px;
  margin-bottom: 10px;
  cursor: pointer;
  transition: all 0.2s;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
  border: 1px solid transparent;
}

.client-card:hover {
  border-color: rgba(99, 102, 241, 0.2);
  box-shadow: 0 4px 16px rgba(99, 102, 241, 0.12);
  transform: translateY(-1px);
}

.client-avatar {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;
  font-weight: 700;
  flex-shrink: 0;
}

.client-info {
  flex: 1;
  min-width: 0;
}

.client-name {
  font-size: 15px;
  font-weight: 600;
  color: #1e293b;
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
  flex-wrap: wrap;
}

.client-name .code {
  font-family: 'JetBrains Mono', 'Consolas', monospace;
  color: #6366f1;
  font-weight: 500;
  font-size: 12px;
}

.client-name .name-en {
  font-size: 12px;
  color: #94a3b8;
  font-weight: 400;
}

.client-meta {
  font-size: 12px;
  color: #64748b;
  display: flex;
  gap: 14px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}

.meta-id { font-family: 'JetBrains Mono', 'Consolas', monospace; }

.client-bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
  color: #94a3b8;
  flex-wrap: wrap;
  gap: 8px;
}

.bottom-left {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
}

.stat-pill {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  background: #f1f5f9;
  color: #475569;
  padding: 2px 8px;
  border-radius: 12px;
  font-weight: 500;
}

.expiry-pill {
  background: #ecfeff;
  color: #0e7490;
  padding: 2px 8px;
  border-radius: 12px;
}

.expiry-pill.expiry-soon {
  background: #fef2f2;
  color: #b91c1c;
  font-weight: 600;
}

.update-time { color: #94a3b8; }
</style>
