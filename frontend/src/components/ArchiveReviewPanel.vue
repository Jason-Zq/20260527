<template>
  <div class="archive-review">
    <!-- 智能匹配 banner -->
    <div v-if="matchedClient" class="match-banner success">
      <el-icon><CircleCheck /></el-icon>
      <span>
        已自动匹配客户：<strong>{{ matchedClient.name }}</strong>
        <span class="muted" v-if="matchedClient.client_code">[{{ matchedClient.client_code }}]</span>
        <span class="muted">（{{ matchedReason }}）</span>
      </span>
      <el-button size="small" link @click="clearMatch">取消选中</el-button>
    </div>

    <div v-else-if="matchCandidates.length > 0" class="match-banner warn">
      <el-icon><Warning /></el-icon>
      <span>识别到 {{ matchCandidates.length }} 个候选客户，请选择：</span>
      <el-select v-model="pendingMatchId" placeholder="选一个" size="small" style="width: 240px">
        <el-option v-for="c in matchCandidates" :key="c.client_id"
          :label="`${c.name}${c.id_number ? ' · ' + c.id_number : ''}（${c.score}）`"
          :value="c.client_id" />
      </el-select>
      <el-button size="small" type="primary" @click="confirmMatch">确认</el-button>
      <el-button size="small" link @click="emit('request-create-client')">+新建客户</el-button>
    </div>

    <div v-else-if="!boundClientId && matchSearched" class="match-banner info">
      <el-icon><InfoFilled /></el-icon>
      <span>未匹配到现有客户</span>
      <el-button size="small" type="primary" @click="emit('request-create-client')">
        <el-icon style="margin-right: 4px"><Plus /></el-icon>新建客户并归档
      </el-button>
      <el-button size="small" @click="emit('request-pick-client')">选择已有客户</el-button>
    </div>

    <!-- 三段式表单 -->
    <div class="archive-form">
      <!-- ① 文件类型 -->
      <div class="row">
        <label class="lbl">① 文件类型</label>
        <el-select v-model="docType" filterable allow-create placeholder="选择或输入"
          size="default" style="width: 100%" @change="onDocTypeChange">
          <el-option v-for="t in docTypeOptions" :key="t" :label="t" :value="t" />
        </el-select>
      </div>

      <!-- ② 归属对象 -->
      <div class="row">
        <label class="lbl">② 归属对象</label>
        <div class="entity-picker">
          <el-radio-group v-model="entity" size="default" @change="onEntityChange">
            <el-radio-button label="clients">主申本人</el-radio-button>
            <el-radio-button label="family">家庭成员</el-radio-button>
            <el-radio-button label="assets">资产</el-radio-button>
          </el-radio-group>

          <!-- family：选 relation + target_id（已有/新建） -->
          <template v-if="entity === 'family'">
            <el-select v-model="subRelation" placeholder="关系" size="default" style="width: 130px">
              <el-option v-for="r in RELATIONS" :key="r" :label="r" :value="r" />
            </el-select>
            <el-select v-model="targetId" placeholder="选目标家属（不选=新建）" clearable
              size="default" style="flex: 1; min-width: 200px">
              <el-option label="（新建一行）" :value="null" />
              <el-option v-for="m in familyOptions" :key="m.id"
                :label="`${m.relation}：${m.name}${m.birth_date ? ' (' + m.birth_date + ')' : ''}`"
                :value="m.id" />
            </el-select>
          </template>

          <!-- assets：选 asset_type + target_id -->
          <template v-if="entity === 'assets'">
            <el-select v-model="subAssetType" placeholder="资产类型" size="default" style="width: 140px">
              <el-option v-for="t in ASSET_TYPES" :key="t" :label="t" :value="t" />
            </el-select>
            <el-select v-model="targetId" placeholder="选目标资产（不选=新建）" clearable
              size="default" style="flex: 1; min-width: 200px">
              <el-option label="（新建一行）" :value="null" />
              <el-option v-for="a in assetOptions" :key="a.id"
                :label="`${a.asset_type}：${a.asset_name || a.location_address || a.bank_name || ('#' + a.id)}`"
                :value="a.id" />
            </el-select>
          </template>
        </div>
      </div>

      <!-- ③ 字段表 -->
      <div class="field-table">
        <div class="ft-head">
          <span class="ft-col-key">字段</span>
          <span class="ft-col-val">值</span>
          <span class="ft-col-target">目标列</span>
        </div>
        <div v-if="flatFields.length === 0" class="empty-inline">未识别到字段</div>
        <div v-for="row in flatFields" :key="row.key" class="ft-row" :class="{ unmapped: !row.targetCol }">
          <div class="ft-col-key">{{ row.key }}</div>
          <div class="ft-col-val">
            <el-input v-model="row.value" size="small" />
          </div>
          <div class="ft-col-target">
            <el-tag v-if="row.targetCol" size="small" type="success" effect="plain">
              {{ entityLabel }}.{{ row.targetCol }}
            </el-tag>
            <el-tag v-else size="small" type="warning" effect="plain">→ KV 兜底</el-tag>
          </div>
        </div>
        <div class="ft-summary">
          命中 schema：<strong>{{ mappedCount }}</strong> 项；进 KV：<strong>{{ unmappedCount }}</strong> 项
        </div>
      </div>

      <!-- 操作 -->
      <div class="actions">
        <el-button :disabled="!canArchive" type="primary" :loading="archiving" @click="onArchive">
          <el-icon style="margin-right: 4px"><Check /></el-icon>
          归档此文件
        </el-button>
        <el-button @click="emit('skip')">跳过</el-button>
        <span v-if="!canArchive" class="hint-text">{{ archiveBlocker }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import {
  CircleCheck, Warning, InfoFilled, Plus, Check,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import {
  matchClients, getClientDetail, getDocTypes, listFamily, listAssets, saveReview,
} from '../api.js'

const props = defineProps({
  // 当前 task：{ task_id, items, filename, ... }
  task: { type: Object, required: true },
  // 顶部"绑定客户"已选的 client_id（可选）
  boundClientId: { type: Number, default: null },
})
const emit = defineEmits([
  'archived',                  // 归档成功 → 父组件标记队列为 done
  'skip',
  'request-create-client',     // 候选无客户，请求顶部弹窗创建
  'request-pick-client',       // 请求显示客户选择器
  'client-selected',           // 通过 banner 选中了某客户 → 同步到顶部下拉
])

const RELATIONS = ['配偶', '子', '女', '父', '母', '兄', '弟', '姐', '妹', '紧急联系人']
const ASSET_TYPES = ['房产', '存款', '银行流水', '股票', '车辆', '其他']

// ---------- doc_type ----------
const docTypeOptions = ref([])
const docType = ref('')

// ---------- entity / sub_meta ----------
const entity = ref('clients')
const subRelation = ref('配偶')
const subAssetType = ref('房产')
const targetId = ref(null)

const entityLabel = computed(() => {
  if (entity.value === 'clients') return 'clients'
  if (entity.value === 'family') return 'family'
  return 'assets'
})

// ---------- 智能匹配 ----------
const matchCandidates = ref([])
const matchedClient = ref(null)        // {client_id, name, ...} 已选中的
const matchedReason = ref('')
const pendingMatchId = ref(null)
const matchSearched = ref(false)        // OCR 匹配跑过没

// ---------- 字段表 ----------
const flatFields = ref([])              // [{ key, value, targetCol }]
const mappedCount = computed(() => flatFields.value.filter(f => f.targetCol).length)
const unmappedCount = computed(() => flatFields.value.length - mappedCount.value)

// ---------- 子表选项（family/assets 数据源）----------
const familyOptions = ref([])
const assetOptions = ref([])

// ---------- archive ----------
const archiving = ref(false)

const effectiveClientId = computed(() => matchedClient.value?.client_id || props.boundClientId)

const canArchive = computed(() => {
  if (!effectiveClientId.value) return false
  if (!docType.value) return false
  if (entity.value === 'family' && !subRelation.value) return false
  if (entity.value === 'assets' && !subAssetType.value) return false
  return true
})

const archiveBlocker = computed(() => {
  if (!effectiveClientId.value) return '请先匹配/选择客户'
  if (!docType.value) return '请选文件类型'
  if (entity.value === 'family' && !subRelation.value) return '请选关系'
  if (entity.value === 'assets' && !subAssetType.value) return '请选资产类型'
  return ''
})

// ============ 字段路由器（前端简版）============
// 同步自后端 db/field_router.py 的 FIELD_TO_COLUMN，仅展示用
// 这里不再二次定义完整映射；后端归档时会再跑一次精确路由
const FE_FIELD_MAP = {
  clients: {
    '姓名': 'name', '中文姓名': 'name', '申请人姓名': 'name', 'Name': 'name',
    '拼音': 'name_en', '英文姓名': 'name_en', '拼音姓名': 'name_en', 'Pinyin': 'name_en',
    '曾用名': 'former_name',
    '性别': 'gender', 'Gender': 'gender', 'Sex': 'gender',
    '出生日期': 'birth_date', 'Date of Birth': 'birth_date', 'DOB': 'birth_date', '生日': 'birth_date',
    '出生地': 'birth_place', '出生地点': 'birth_place',
    '民族': 'ethnicity',
    '国籍': 'nationality', 'Nationality': 'nationality',
    '身份证号': 'id_number', '身份证号码': 'id_number', '公民身份号码': 'id_number',
    '住址': 'hukou_address', '户籍地址': 'hukou_address',
    '婚姻状况': 'marital_status',
    '手机': 'phone', '手机号': 'phone', '电话': 'phone', 'Phone': 'phone', 'Tel': 'phone',
    '邮箱': 'email', 'Email': 'email',
    '现家庭住址': 'current_address', '现居地址': 'current_address', 'Current residence': 'current_address', 'Address': 'current_address',
    '护照号': 'passport_no', '护照号码': 'passport_no', 'Passport No': 'passport_no',
    '签发日期': 'passport_issue_date', 'Issue Date': 'passport_issue_date',
    '有效期': 'passport_expiry_date', '有效期至': 'passport_expiry_date', 'Expiry Date': 'passport_expiry_date',
    '签发机关': 'passport_issuing_authority', '发证机关': 'passport_issuing_authority',
    '学校': 'school_name', '学校名称': 'school_name', '毕业院校': 'school_name', '毕业学校': 'school_name',
    '专业': 'major', 'Major': 'major',
    '学位': 'degree', '学位等级': 'degree', 'Degree': 'degree',
    '毕业日期': 'graduation_date', '毕业时间': 'graduation_date',
    '毕业证编号': 'graduation_cert_no', '毕业证书编号': 'graduation_cert_no',
    '学位证编号': 'degree_cert_no', '学位证书编号': 'degree_cert_no',
    '公司': 'company_name', '公司名称': 'company_name', '工作单位': 'company_name', '雇主': 'company_name',
    '职位': 'position', 'Title': 'position', 'Position': 'position',
    '入职日期': 'employment_start_date', '入职时间': 'employment_start_date',
    '月薪': 'monthly_salary', '月收入': 'monthly_salary',
    '登记日期': 'marriage_date', '结婚日期': 'marriage_date',
    '登记机关': 'marriage_authority',
    '结婚证编号': 'marriage_cert_no', '结婚证字号': 'marriage_cert_no',
  },
  family: {
    '姓名': 'name', 'Name': 'name', '英文姓名': 'name_en', '拼音': 'name_en',
    '性别': 'gender', '出生日期': 'birth_date', '国籍': 'nationality',
    '身份证号': 'id_number', '身份证号码': 'id_number', '公民身份号码': 'id_number',
    '手机': 'phone', '电话': 'phone', 'Tel': 'phone',
    '护照号': 'passport_no', 'Passport No': 'passport_no',
    '邮箱': 'email', 'Email': 'email',
    '现家庭住址': 'current_address', '现居地址': 'current_address', 'Current residence': 'current_address',
    '公司': 'company_name', '公司名称': 'company_name', '工作单位': 'company_name',
    '职位': 'position', 'Title': 'position',
    '学校': 'school_name', '毕业院校': 'school_name',
    '专业': 'major', '学位': 'degree',
    '毕业日期': 'graduation_date',
    '毕业证编号': 'graduation_cert_no', '学位证编号': 'degree_cert_no',
    '出生医学证编号': 'birth_cert_no', '出生证编号': 'birth_cert_no',
    '出生医院': 'birth_hospital',
    '出生地': 'birth_place', '出生地点': 'birth_place',
  },
  assets: {
    '权利人': 'owner_name', '户名': 'owner_name', '持有人': 'owner_name',
    '共有人': 'co_owners',
    '金额': 'value_amount', '存款金额': 'value_amount',
    '币种': 'currency',
    '产权证号': 'certificate_no', '不动产权证号': 'certificate_no', '存单号': 'certificate_no', '证明编号': 'certificate_no',
    '坐落': 'location_address', '房产地址': 'location_address',
    '面积': 'area_sqm', '建筑面积': 'area_sqm', '套内面积': 'area_sqm',
    '用途': 'usage_type', '房屋用途': 'usage_type',
    '取得日期': 'acquired_date', '取得时间': 'acquired_date',
    '银行': 'bank_name', '银行名称': 'bank_name', '开户行': 'bank_name',
    '账号': 'account_no', '账户号': 'account_no',
    '起息日': 'period_start', '存入日期': 'period_start', '起始日期': 'period_start',
    '到期日': 'period_end', '到期日期': 'period_end', '结束日期': 'period_end',
    '冻结期': 'frozen_until', '冻结至': 'frozen_until',
  },
}

function feFieldToColumn(ent, key) {
  const map = FE_FIELD_MAP[ent] || {}
  if (key in map) return map[key]
  const stripped = String(key).trim()
  if (stripped !== key && stripped in map) return map[stripped]
  // case-insensitive ASCII
  if (/^[\x00-\x7F]+$/.test(stripped)) {
    for (const k of Object.keys(map)) {
      if (k.toLowerCase() === stripped.toLowerCase()) return map[k]
    }
  }
  return null
}

function rebuildFlat() {
  const flat = []
  const items = props.task?.items || []
  // 把所有 fields 拍平
  for (const it of items) {
    const fields = it?.fields || {}
    for (const [k, v] of Object.entries(fields)) {
      const value = (v && typeof v === 'object' && 'value' in v) ? v.value : v
      flat.push({
        key: k,
        value: value == null ? '' : String(value),
        targetCol: feFieldToColumn(entity.value, k),
      })
    }
  }
  flatFields.value = flat
}

watch(entity, rebuildFlat)
watch(() => props.task, () => {
  rebuildFlat()
  initDocTypeFromTask()
  triggerMatch()
}, { deep: true })

// ---------- 启动逻辑 ----------
async function loadDocTypes() {
  try {
    const data = await getDocTypes()
    docTypeOptions.value = data.doc_types || []
  } catch (err) {
    docTypeOptions.value = []
  }
}

function initDocTypeFromTask() {
  // 用 LLM 输出的 doc_type 作为默认
  const items = props.task?.items || []
  const dt = items[0]?.doc_type
  if (dt) {
    docType.value = dt
    onDocTypeChange()
  }
}

function onDocTypeChange() {
  // 根据 doc_type 设默认 entity / sub_meta
  const t = docType.value
  if (!t) return
  // family 类
  if (t.startsWith('配偶')) {
    entity.value = 'family'
    subRelation.value = '配偶'
    targetId.value = null
  } else if (t.includes('出生医学证') || t === '出生证') {
    entity.value = 'family'
    subRelation.value = '子'
    targetId.value = null
  } else if (['房产证', '不动产权证'].includes(t)) {
    entity.value = 'assets'
    subAssetType.value = '房产'
    targetId.value = null
  } else if (['存款证明'].includes(t)) {
    entity.value = 'assets'
    subAssetType.value = '存款'
    targetId.value = null
  } else if (['银行流水', '银行对账单'].includes(t)) {
    entity.value = 'assets'
    subAssetType.value = '银行流水'
    targetId.value = null
  } else if (['行驶证', '车辆登记证'].includes(t)) {
    entity.value = 'assets'
    subAssetType.value = '车辆'
    targetId.value = null
  } else if (t === '股票账户') {
    entity.value = 'assets'
    subAssetType.value = '股票'
    targetId.value = null
  } else {
    entity.value = 'clients'
    targetId.value = null
  }
  rebuildFlat()
}

function onEntityChange() {
  targetId.value = null
  if (entity.value === 'family') reloadFamilyOptions()
  if (entity.value === 'assets') reloadAssetOptions()
  rebuildFlat()
}

async function reloadFamilyOptions() {
  const cid = effectiveClientId.value
  if (!cid) { familyOptions.value = []; return }
  try {
    const data = await listFamily(cid)
    familyOptions.value = data.items || []
  } catch (err) {
    familyOptions.value = []
  }
}

async function reloadAssetOptions() {
  const cid = effectiveClientId.value
  if (!cid) { assetOptions.value = []; return }
  try {
    const data = await listAssets(cid)
    assetOptions.value = data.items || []
  } catch (err) {
    assetOptions.value = []
  }
}

watch(effectiveClientId, () => {
  if (entity.value === 'family') reloadFamilyOptions()
  if (entity.value === 'assets') reloadAssetOptions()
})

// ---------- 智能匹配 ----------
async function triggerMatch() {
  matchSearched.value = false
  matchCandidates.value = []
  matchedClient.value = null
  pendingMatchId.value = null

  // 已绑定客户：跳过匹配
  if (props.boundClientId) return

  // 抽取关键标识
  const items = props.task?.items || []
  const fields = items[0]?.fields || {}
  const get = (keys) => {
    for (const k of keys) {
      if (k in fields) {
        const v = fields[k]
        const val = (v && typeof v === 'object' && 'value' in v) ? v.value : v
        if (val) return String(val).trim()
      }
    }
    return null
  }
  const id_number = get(['身份证号', '身份证号码', '公民身份号码'])
  const passport_no = get(['护照号', '护照号码', 'Passport No'])
  const name = get(['姓名', 'Name', '中文姓名'])
  const birth_date = get(['出生日期', 'Date of Birth', '生日'])

  if (!id_number && !passport_no && !name) return

  try {
    const data = await matchClients({ id_number, passport_no, name, birth_date })
    matchSearched.value = true
    matchCandidates.value = data.candidates || []

    // 高分自动选中（best_match_client_id）
    if (data.best_match_client_id) {
      const best = matchCandidates.value.find(c => c.client_id === data.best_match_client_id)
      if (best) {
        matchedClient.value = best
        matchedReason.value = best.reason
        emit('client-selected', best.client_id)
        // matchCandidates 保留以便用户看到
      }
    }
  } catch (err) {
    matchSearched.value = true
    console.warn('match failed', err)
  }
}

function confirmMatch() {
  if (!pendingMatchId.value) return
  const c = matchCandidates.value.find(x => x.client_id === pendingMatchId.value)
  if (c) {
    matchedClient.value = c
    matchedReason.value = c.reason
    emit('client-selected', c.client_id)
  }
}

function clearMatch() {
  matchedClient.value = null
  matchedReason.value = ''
  pendingMatchId.value = null
  emit('client-selected', null)
}

// ---------- 归档 ----------
async function onArchive() {
  if (!canArchive.value) return
  archiving.value = true
  try {
    // 把 flatFields 编辑后的值回写到 items 的 fields
    const items = JSON.parse(JSON.stringify(props.task.items || []))
    // 简化：所有字段统一改写到 items[0].fields；多 item 场景较少
    if (items.length > 0) {
      items[0].doc_type = docType.value
      const newFields = {}
      for (const row of flatFields.value) {
        // 保留 OCR confidence 信息（如有）
        const orig = items[0].fields?.[row.key]
        if (orig && typeof orig === 'object' && 'value' in orig) {
          newFields[row.key] = { ...orig, value: row.value }
        } else {
          newFields[row.key] = row.value
        }
      }
      items[0].fields = newFields
    }

    const archive = {
      client_id: effectiveClientId.value,
      entity: entity.value,
      target_id: targetId.value,
      sub_meta: entity.value === 'family' ? { relation: subRelation.value }
              : entity.value === 'assets' ? { asset_type: subAssetType.value }
              : null,
    }

    const res = await saveReview(props.task.task_id, { items, archive })
    ElMessage.success(`已归档（命中 ${res.archive?.mapped_count || 0} 项，${res.archive?.unmapped_count || 0} 项进 KV）`)
    emit('archived', { task_id: props.task.task_id, archive_result: res.archive })
  } catch (err) {
    ElMessage.error('归档失败：' + (err.response?.data?.detail || err.message))
  } finally {
    archiving.value = false
  }
}

// ---------- mount ----------
onMounted(async () => {
  await loadDocTypes()
  initDocTypeFromTask()
  rebuildFlat()
  triggerMatch()
})

defineExpose({ archive: onArchive, canArchive, retriggerMatch: triggerMatch })
</script>

<style scoped>
.archive-review {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 14px 16px;
  height: 100%;
  overflow-y: auto;
}

/* match banner */
.match-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 13px;
  flex-wrap: wrap;
}
.match-banner.success { background: #ecfdf5; color: #065f46; border-left: 3px solid #10b981; }
.match-banner.warn    { background: #fffbeb; color: #78350f; border-left: 3px solid #f59e0b; }
.match-banner.info    { background: #eff6ff; color: #1e3a8a; border-left: 3px solid #3b82f6; }
.match-banner .muted  { color: #94a3b8; font-weight: normal; }

/* 三段式 */
.archive-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.row {
  display: flex;
  gap: 10px;
  align-items: center;
}
.lbl {
  flex-shrink: 0;
  width: 86px;
  font-size: 13px;
  font-weight: 600;
  color: #475569;
}
.entity-picker {
  flex: 1;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}

/* 字段表 */
.field-table {
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}
.ft-head, .ft-row {
  display: grid;
  grid-template-columns: 130px 1fr 160px;
  gap: 10px;
  padding: 6px 12px;
  align-items: center;
  font-size: 12px;
}
.ft-head {
  background: #f8fafc;
  font-weight: 600;
  color: #475569;
  border-bottom: 1px solid #e2e8f0;
  font-size: 11px;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}
.ft-row {
  border-bottom: 1px solid #f1f5f9;
}
.ft-row:last-of-type { border-bottom: none; }
.ft-row.unmapped { background: #fffbeb; }
.ft-col-key { color: #1e293b; font-weight: 500; word-break: break-all; }
.ft-col-target { text-align: right; }

.ft-summary {
  padding: 8px 12px;
  background: #f8fafc;
  font-size: 12px;
  color: #64748b;
  border-top: 1px solid #e2e8f0;
}
.empty-inline { padding: 24px; text-align: center; color: #94a3b8; font-size: 13px; }

/* 操作 */
.actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.hint-text { font-size: 12px; color: #94a3b8; }
</style>
