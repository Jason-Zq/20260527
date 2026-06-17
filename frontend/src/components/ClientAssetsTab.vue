<template>
  <div class="assets-tab">
    <div class="tab-toolbar">
      <span class="muted">共 {{ list.length }} 项资产</span>
      <el-button size="small" type="primary" @click="openCreate">
        <el-icon style="margin-right: 4px"><Plus /></el-icon>
        新增资产
      </el-button>
    </div>

    <div v-if="list.length === 0" class="empty-inline">暂无资产记录</div>

    <div v-else class="asset-list">
      <div v-for="a in list" :key="a.id" class="asset-card">
        <div class="asset-head">
          <el-tag :type="typeTagType(a.asset_type)" size="small">{{ a.asset_type }}</el-tag>
          <span class="a-name">{{ a.asset_name || displayName(a) }}</span>
          <span v-if="a.value_amount" class="a-amount">
            {{ formatAmount(a.value_amount) }} {{ a.currency || '' }}
          </span>
          <div class="actions">
            <el-button size="small" link type="primary" @click="openEdit(a)">编辑</el-button>
            <el-button size="small" link type="danger" @click="onDelete(a)">删除</el-button>
          </div>
        </div>
        <div class="asset-grid">
          <div v-if="a.owner_name"><span class="k">权利人</span>{{ a.owner_name }}</div>
          <div v-if="a.co_owners"><span class="k">共有人</span>{{ a.co_owners }}</div>
          <div v-if="a.certificate_no"><span class="k">证书号</span>{{ a.certificate_no }}</div>

          <!-- 房产专用 -->
          <div v-if="a.location_address" class="full"><span class="k">坐落</span>{{ a.location_address }}</div>
          <div v-if="a.area_sqm"><span class="k">面积</span>{{ a.area_sqm }} ㎡</div>
          <div v-if="a.usage_type"><span class="k">用途</span>{{ a.usage_type }}</div>
          <div v-if="a.acquired_date"><span class="k">取得</span>{{ a.acquired_date }}</div>

          <!-- 银行专用 -->
          <div v-if="a.bank_name"><span class="k">银行</span>{{ a.bank_name }}</div>
          <div v-if="a.account_no"><span class="k">账号</span>{{ a.account_no }}</div>
          <div v-if="a.period_start"><span class="k">起始</span>{{ a.period_start }}</div>
          <div v-if="a.period_end"><span class="k">结束</span>{{ a.period_end }}</div>
          <div v-if="a.frozen_until"><span class="k">冻结至</span>{{ a.frozen_until }}</div>
        </div>
      </div>
    </div>

    <!-- 编辑/新增弹窗 -->
    <el-dialog v-model="dialogOpen" :title="editingId ? '编辑资产' : '新增资产'" width="640px">
      <el-form :model="form" label-width="92px" label-position="right">
        <el-row :gutter="12">
          <el-col :span="8">
            <el-form-item label="类型" required>
              <el-select v-model="form.asset_type" style="width: 100%">
                <el-option v-for="t in TYPES" :key="t" :label="t" :value="t" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="16">
            <el-form-item label="名称">
              <el-input v-model="form.asset_name" placeholder="例：上海浦东 / 招行 6 个月定期" />
            </el-form-item>
          </el-col>

          <el-col :span="12">
            <el-form-item label="权利人/户名">
              <el-input v-model="form.owner_name" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="共有人">
              <el-input v-model="form.co_owners" />
            </el-form-item>
          </el-col>

          <el-col :span="12">
            <el-form-item label="金额">
              <el-input v-model="form.value_amount" type="number" />
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="币种">
              <el-input v-model="form.currency" placeholder="CNY" />
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="证书号">
              <el-input v-model="form.certificate_no" />
            </el-form-item>
          </el-col>

          <!-- 房产 -->
          <template v-if="form.asset_type === '房产'">
            <el-col :span="24">
              <el-form-item label="坐落">
                <el-input v-model="form.location_address" />
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="面积">
                <el-input v-model="form.area_sqm" placeholder="平米" />
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="用途">
                <el-input v-model="form.usage_type" placeholder="住宅/商业/工业" />
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="取得日期">
                <el-date-picker v-model="form.acquired_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
              </el-form-item>
            </el-col>
          </template>

          <!-- 银行 -->
          <template v-if="['存款', '银行流水'].includes(form.asset_type)">
            <el-col :span="12">
              <el-form-item label="银行">
                <el-input v-model="form.bank_name" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="账号">
                <el-input v-model="form.account_no" />
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="起始">
                <el-date-picker v-model="form.period_start" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label="结束">
                <el-date-picker v-model="form.period_end" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
              </el-form-item>
            </el-col>
            <el-col :span="8" v-if="form.asset_type === '存款'">
              <el-form-item label="冻结至">
                <el-date-picker v-model="form.frozen_until" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
              </el-form-item>
            </el-col>
          </template>

          <el-col :span="24">
            <el-form-item label="备注">
              <el-input v-model="form.notes" type="textarea" :rows="2" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listAssets, createAsset, updateAsset, deleteAsset } from '../api.js'

const props = defineProps({
  clientId: { type: Number, required: true },
})
const emit = defineEmits(['changed'])

const TYPES = ['房产', '存款', '银行流水', '股票', '车辆', '其他']

const list = ref([])
const dialogOpen = ref(false)
const editingId = ref(null)
const saving = ref(false)

const emptyForm = () => ({
  asset_type: '房产',
  asset_name: '',
  owner_name: '',
  co_owners: '',
  value_amount: '',
  currency: 'CNY',
  certificate_no: '',
  location_address: '',
  area_sqm: '',
  usage_type: '',
  acquired_date: '',
  bank_name: '',
  account_no: '',
  period_start: '',
  period_end: '',
  frozen_until: '',
  notes: '',
})

const form = ref(emptyForm())

async function load() {
  try {
    const data = await listAssets(props.clientId)
    list.value = data.items || []
  } catch (err) {
    ElMessage.error('加载资产失败：' + (err.response?.data?.detail || err.message))
  }
}

function openCreate() {
  editingId.value = null
  form.value = emptyForm()
  dialogOpen.value = true
}

function openEdit(a) {
  editingId.value = a.id
  form.value = { ...emptyForm(), ...a }
  dialogOpen.value = true
}

async function onSave() {
  if (!form.value.asset_type) {
    ElMessage.warning('请选择资产类型')
    return
  }
  saving.value = true
  try {
    const payload = {}
    for (const [k, v] of Object.entries(form.value)) {
      if (v != null && v !== '') payload[k] = v
    }
    if (editingId.value) {
      await updateAsset(editingId.value, payload)
      ElMessage.success('已更新')
    } else {
      await createAsset(props.clientId, payload)
      ElMessage.success('已新增')
    }
    dialogOpen.value = false
    await load()
    emit('changed')
  } catch (err) {
    ElMessage.error('保存失败：' + (err.response?.data?.detail || err.message))
  } finally {
    saving.value = false
  }
}

async function onDelete(a) {
  try {
    await ElMessageBox.confirm(`确定删除 ${a.asset_type}「${displayName(a)}」？`, '确认', { type: 'warning' })
  } catch { return }
  try {
    await deleteAsset(a.id)
    ElMessage.success('已删除')
    await load()
    emit('changed')
  } catch (err) {
    ElMessage.error('删除失败：' + (err.response?.data?.detail || err.message))
  }
}

function displayName(a) {
  if (a.asset_name) return a.asset_name
  if (a.location_address) return a.location_address
  if (a.bank_name) return `${a.bank_name} ${a.account_no || ''}`.trim()
  return a.certificate_no || '—'
}

function typeTagType(t) {
  if (t === '房产') return 'warning'
  if (t === '存款') return 'success'
  if (t === '银行流水') return 'info'
  if (t === '股票') return 'danger'
  return ''
}

function formatAmount(n) {
  if (n == null) return ''
  const num = Number(n)
  if (isNaN(num)) return n
  return num.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

watch(() => props.clientId, load)
onMounted(load)
defineExpose({ reload: load })
</script>

<style scoped>
.tab-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.muted { font-size: 12px; color: #94a3b8; }
.empty-inline { text-align: center; color: #94a3b8; padding: 32px 0; font-size: 13px; }
.asset-list { display: flex; flex-direction: column; gap: 10px; }
.asset-card {
  background: #f8fafc;
  border-radius: 10px;
  padding: 12px 16px;
  border: 1px solid #e2e8f0;
}
.asset-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.a-name {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
}
.a-amount {
  margin-left: auto;
  margin-right: 12px;
  font-size: 13px;
  color: #6366f1;
  font-weight: 600;
  font-family: 'JetBrains Mono', 'Consolas', monospace;
}
.actions { display: flex; gap: 4px; }
.asset-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px 16px;
  font-size: 12px;
  color: #475569;
}
.asset-grid .full { grid-column: 1 / -1; }
.asset-grid .k {
  display: inline-block;
  width: 56px;
  color: #94a3b8;
  font-weight: 500;
}
</style>
