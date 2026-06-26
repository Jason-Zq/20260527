<template>
  <div class="client-detail-page">
    <div class="page-header">
      <el-button class="back-btn" @click="handleBack" size="default">
        <el-icon style="margin-right: 4px"><ArrowLeft /></el-icon>
        返回
      </el-button>
      <div class="page-title">
        <span class="title-indicator"></span>
        客户档案详情
      </div>
      <div class="header-right" v-if="detail">
        <el-button size="small" type="warning" :loading="profileGenerating" @click="openProfileGenerateDialog">
          <el-icon style="margin-right: 4px"><Star /></el-icon>
          AI 生成客户档案
        </el-button>
        <el-button size="small" @click="editOpen = true">
          <el-icon style="margin-right: 4px"><Edit /></el-icon>
          编辑主表
        </el-button>
      </div>
    </div>

    <div class="page-content">
      <div v-if="loading" class="loading-state">
        <el-icon class="is-loading" :size="28"><Loading /></el-icon>
        <p>加载中...</p>
      </div>

      <div v-else-if="!detail" class="empty-state">
        <p class="empty-text">客户不存在</p>
      </div>

      <div v-else class="detail-wrap">
        <!-- 基本信息：分组卡片 -->
        <section class="card profile-card">
          <div class="profile-avatar">{{ (detail.name || '?').charAt(0) }}</div>
          <div class="profile-main">
            <div class="profile-name">
              <span v-if="detail.client_code" class="code">[{{ detail.client_code }}]</span>
              {{ detail.name || '未知' }}
              <span v-if="detail.name_en" class="name-en">{{ detail.name_en }}</span>
              <el-tag v-if="detail.gender" size="small" type="info">{{ detail.gender }}</el-tag>
              <el-tag v-if="detail.visa_type" size="small" type="primary">{{ detail.visa_type }}</el-tag>
            </div>
            <div class="profile-grid">
              <div class="cell"><span class="k">出生</span>{{ detail.birth_date || '-' }}</div>
              <div class="cell"><span class="k">出生地</span>{{ detail.birth_place || '-' }}</div>
              <div class="cell"><span class="k">国籍</span>{{ detail.nationality || '-' }}</div>
              <div class="cell"><span class="k">民族</span>{{ detail.ethnicity || '-' }}</div>
              <div class="cell"><span class="k">婚姻</span>{{ detail.marital_status || '-' }}</div>
              <div class="cell"><span class="k">曾用名</span>{{ detail.former_name || '-' }}</div>
              <div class="cell mono"><span class="k">身份证</span>{{ detail.id_number || '-' }}</div>
              <div class="cell"><span class="k">顾问</span>{{ detail.consultant || '-' }}</div>
              <div class="cell full"><span class="k">户籍地址</span>{{ detail.hukou_address || '-' }}</div>
            </div>
          </div>
        </section>

        <!-- 即将到期提醒 -->
        <section v-if="upcomingExpiries.length || passportExpirySoon" class="card alert-card">
          <div class="card-title">
            <span class="indicator-warn"></span>
            即将到期提醒（90 天内）
          </div>
          <div class="alert-list">
            <div v-if="passportExpirySoon" class="alert-item">
              <span class="alert-key">护照</span>
              <span class="alert-value">{{ detail.passport_no || '—' }}</span>
              <span class="alert-date">{{ detail.passport_expiry_date }} ({{ passportDaysLeft }} 天)</span>
            </div>
            <div v-for="item in upcomingExpiries" :key="item.id" class="alert-item">
              <span class="alert-key">{{ item.info_key }}</span>
              <span class="alert-value">{{ item.info_value }}</span>
              <span class="alert-date">{{ item.valid_until }} ({{ item.daysLeft }} 天)</span>
            </div>
          </div>
        </section>

        <!-- AI 档案生成记录 -->
        <section class="card" v-if="generationTasks.length">
          <div class="card-title">
            <span class="indicator"></span>
            AI 档案生成记录
          </div>
          <el-table :data="generationTasks" stripe size="small" empty-text="暂无生成记录">
            <el-table-column label="时间" width="160" prop="created_at" />
            <el-table-column label="状态" width="90">
              <template #default="{ row }"><el-tag size="small" :type="row.status === 'done' ? 'success' : row.status === 'error' ? 'danger' : 'warning'">{{ row.status }}</el-tag></template>
            </el-table-column>
            <el-table-column label="使用文件数" width="110" prop="source_file_count" />
            <el-table-column label="写入结果" min-width="220">
              <template #default="{ row }">
                <span class="mono">{{ JSON.stringify(row.created_count || {}) }}</span>
              </template>
            </el-table-column>
          </el-table>
        </section>

        <!-- 分组卡：联系/护照/教育/工作/婚姻 -->
        <section class="card group-cards">
          <div class="group-grid">
            <div class="group-block">
              <div class="block-title"><el-icon><Phone /></el-icon> 联系方式</div>
              <div class="block-row"><span class="k">手机</span>{{ detail.phone || '-' }}</div>
              <div class="block-row"><span class="k">邮箱</span>{{ detail.email || '-' }}</div>
              <div class="block-row"><span class="k">现居</span>{{ detail.current_address || '-' }}</div>
            </div>

            <div class="group-block">
              <div class="block-title"><el-icon><Postcard /></el-icon> 护照</div>
              <div class="block-row"><span class="k">护照号</span>{{ detail.passport_no || '-' }}</div>
              <div class="block-row"><span class="k">签发</span>{{ detail.passport_issue_date || '-' }}</div>
              <div class="block-row"><span class="k">到期</span>{{ detail.passport_expiry_date || '-' }}</div>
              <div class="block-row"><span class="k">机关</span>{{ detail.passport_issuing_authority || '-' }}</div>
            </div>

            <div class="group-block">
              <div class="block-title"><el-icon><School /></el-icon> 教育（最高学历）</div>
              <div class="block-row"><span class="k">学校</span>{{ detail.school_name || '-' }}</div>
              <div class="block-row"><span class="k">学位</span>{{ detail.degree || '-' }} / {{ detail.major || '-' }}</div>
              <div class="block-row"><span class="k">毕业</span>{{ detail.graduation_date || '-' }}</div>
              <div class="block-row" v-if="detail.degree_cert_no || detail.graduation_cert_no">
                <span class="k">证号</span>毕业 {{ detail.graduation_cert_no || '-' }} / 学位 {{ detail.degree_cert_no || '-' }}
              </div>
            </div>

            <div class="group-block">
              <div class="block-title"><el-icon><OfficeBuilding /></el-icon> 工作（当前）</div>
              <div class="block-row"><span class="k">公司</span>{{ detail.company_name || '-' }}</div>
              <div class="block-row"><span class="k">职位</span>{{ detail.position || '-' }}</div>
              <div class="block-row"><span class="k">入职</span>{{ detail.employment_start_date || '-' }}</div>
              <div class="block-row"><span class="k">月薪</span>{{ detail.monthly_salary != null ? formatAmount(detail.monthly_salary) : '-' }}</div>
            </div>

            <div class="group-block" v-if="detail.marriage_date || detail.marriage_authority || detail.marriage_cert_no">
              <div class="block-title"><el-icon><Star /></el-icon> 婚姻</div>
              <div class="block-row"><span class="k">登记</span>{{ detail.marriage_date || '-' }}</div>
              <div class="block-row"><span class="k">机关</span>{{ detail.marriage_authority || '-' }}</div>
              <div class="block-row"><span class="k">证号</span>{{ detail.marriage_cert_no || '-' }}</div>
            </div>

            <div class="group-block" v-if="detail.notes">
              <div class="block-title"><el-icon><EditPen /></el-icon> 备注</div>
              <div class="block-row" style="white-space: pre-wrap">{{ detail.notes }}</div>
            </div>
          </div>
        </section>

        <!-- Tab 切换 -->
        <section class="card">
          <div class="card-tabs">
            <button class="tab-btn" :class="{ active: activeTab === 'family' }" @click="activeTab = 'family'">
              家庭成员
              <el-tag size="small" type="info" effect="plain">{{ detail.family_members?.length || 0 }}</el-tag>
            </button>
            <button class="tab-btn" :class="{ active: activeTab === 'assets' }" @click="activeTab = 'assets'">
              资产
              <el-tag size="small" type="info" effect="plain">{{ detail.assets?.length || 0 }}</el-tag>
            </button>
            <button class="tab-btn" :class="{ active: activeTab === 'kv' }" @click="activeTab = 'kv'">
              其他信息
              <el-tag size="small" type="info" effect="plain">{{ detail.infos?.length || 0 }}</el-tag>
            </button>
            <button class="tab-btn" :class="{ active: activeTab === 'docs' }" @click="activeTab = 'docs'">
              名下文档
              <el-tag size="small" type="info" effect="plain">{{ detail.documents?.length || 0 }}</el-tag>
            </button>
            <button class="tab-btn" :class="{ active: activeTab === 'fills' }" @click="activeTab = 'fills'">
              已生成文件
              <el-tag size="small" type="info" effect="plain">{{ fills.length }}</el-tag>
            </button>
          </div>

          <div class="tab-pane">
            <ClientFamilyTab v-if="activeTab === 'family'" :client-id="detail.id" @changed="reload" />
            <ClientAssetsTab v-else-if="activeTab === 'assets'" :client-id="detail.id" @changed="reload" />
            <ClientInfoKvTab v-else-if="activeTab === 'kv'" :list="detail.infos || []" />

            <!-- 名下文档 -->
            <template v-else-if="activeTab === 'docs'">
              <div v-if="!detail.documents?.length" class="empty-inline">暂无文档</div>
              <div v-else class="doc-list">
                <div v-for="doc in detail.documents" :key="doc.task_id" class="doc-row" @click="emit('select-doc', doc.task_id)">
                  <div class="doc-name">
                    {{ doc.filename || doc.task_id }}
                    <el-tag v-if="doc.reviewed" size="small" type="success">已复核</el-tag>
                  </div>
                  <div class="doc-meta">
                    <span v-if="doc.doc_types?.length" class="meta-tag">{{ doc.doc_types.join('、') }}</span>
                    <span>字段 {{ doc.field_count }} 项</span>
                    <span v-if="doc.confidence_avg !== null">置信度 {{ Math.round(doc.confidence_avg * 100) }}%</span>
                    <span class="doc-time">{{ doc.created_at }}</span>
                  </div>
                </div>
              </div>
            </template>

            <!-- 已生成文件 -->
            <template v-else-if="activeTab === 'fills'">
              <div v-if="fillsLoading" class="empty-inline">加载中...</div>
              <div v-else-if="fills.length === 0" class="empty-inline">暂无生成记录</div>
              <div v-else class="doc-list">
                <div v-for="fill in fills" :key="fill.id" class="doc-row fill-row">
                  <div class="doc-name">
                    {{ fill.template_name }}
                    <el-tag size="small" :type="fill.output_kind === 'pdf' ? 'danger' : 'primary'" effect="light">
                      {{ fill.output_kind ? fill.output_kind.toUpperCase() : '?' }}
                    </el-tag>
                  </div>
                  <div class="doc-meta">
                    <span class="meta-tag">基于：{{ fill.template_filename || '—' }}</span>
                    <span>填充 {{ fill.placeholder_count }} 项</span>
                    <span class="doc-time">{{ fill.created_at }}</span>
                    <a v-if="fill.output_url" :href="fill.output_url" target="_blank" class="dl-link" @click.stop>下载</a>
                    <span v-else class="dl-disabled" title="文件已被清理或丢失">不可用</span>
                  </div>
                </div>
              </div>
            </template>
          </div>
        </section>
      </div>
    </div>

    <ClientEditDialog v-model="editOpen" :client="detail" @saved="reload" />

    <el-dialog v-model="profileDialogOpen" title="选择用于生成客户档案的文件" width="760px">
      <div v-loading="profileFilesLoading">
        <p class="dialog-hint">默认勾选可用 OCR 文件；可取消不相关文件后再生成。</p>
        <el-table :data="profileSourceFiles" size="small" max-height="420" @selection-change="profileSelection = $event">
          <el-table-column type="selection" width="48" :selectable="row => row.selectable" />
          <el-table-column label="文件名" min-width="190" show-overflow-tooltip prop="filename" />
          <el-table-column label="分类" width="130" show-overflow-tooltip prop="doc_category" />
          <el-table-column label="进展" width="120" show-overflow-tooltip prop="progress_name" />
          <el-table-column label="OCR" width="90" align="center">
            <template #default="{ row }"><el-tag size="small" :type="row.has_ocr_text ? 'success' : 'info'">{{ row.has_ocr_text ? '有' : '无' }}</el-tag></template>
          </el-table-column>
          <el-table-column label="字数" width="80" align="center" prop="char_count" />
        </el-table>
      </div>
      <template #footer>
        <el-button @click="profileDialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="profileGenerating" :disabled="!profileSelection.length" @click="startProfileGenerate">确认生成</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="profileTaskOpen" title="客户档案生成进度" width="560px" :close-on-click-modal="false">
      <div v-if="profileTask" class="task-box">
        <p>任务状态：<el-tag :type="profileTask.status === 'done' ? 'success' : profileTask.status === 'error' ? 'danger' : 'warning'">{{ profileTask.status }}</el-tag></p>
        <p>使用文件数：{{ profileTask.source_file_count }}</p>
        <p v-if="profileTask.created_count">写入结果：<span class="mono">{{ JSON.stringify(profileTask.created_count) }}</span></p>
        <p v-if="profileTask.error" class="error-text">{{ profileTask.error }}</p>
      </div>
      <template #footer>
        <el-button :disabled="profileTask?.status === 'running'" @click="profileTaskOpen = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  ArrowLeft, Loading, Edit, Phone, Postcard, School, OfficeBuilding, Star, EditPen,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getClientDetail, getClientFills, listClientProfileSourceFiles, generateClientProfile, getClientProfileGenerationTask, listClientProfileGenerationTasks } from '../api.js'
import ClientFamilyTab from './ClientFamilyTab.vue'
import ClientAssetsTab from './ClientAssetsTab.vue'
import ClientInfoKvTab from './ClientInfoKvTab.vue'
import ClientEditDialog from './ClientEditDialog.vue'

const props = defineProps({
  clientId: { type: Number, required: true },
})
const emit = defineEmits(['back', 'select-doc'])
const router = useRouter()

function handleBack() {
  // 路由直达时回客户列表；嵌入式使用时交给父组件处理
  if (router.currentRoute.value.path.startsWith('/clients')) {
    router.push('/clients')
  } else {
    emit('back')
  }
}

const detail = ref(null)
const loading = ref(false)
const editOpen = ref(false)
const profileDialogOpen = ref(false)
const profileFilesLoading = ref(false)
const profileSourceFiles = ref([])
const profileSelection = ref([])
const profileGenerating = ref(false)
const profileTaskOpen = ref(false)
const profileTask = ref(null)
const generationTasks = ref([])
let profileTaskTimer = null

const activeTab = ref('family')
const fills = ref([])
const fillsLoading = ref(false)

const upcomingExpiries = computed(() => {
  if (!detail.value?.infos) return []
  const now = new Date()
  const list = []
  for (const info of detail.value.infos) {
    if (!info.valid_until) continue
    const expDate = new Date(info.valid_until)
    const daysLeft = Math.ceil((expDate - now) / (1000 * 60 * 60 * 24))
    if (daysLeft >= 0 && daysLeft <= 90) list.push({ ...info, daysLeft })
  }
  return list.sort((a, b) => a.daysLeft - b.daysLeft)
})

const passportDaysLeft = computed(() => {
  if (!detail.value?.passport_expiry_date) return null
  const exp = new Date(detail.value.passport_expiry_date)
  return Math.ceil((exp - new Date()) / (1000 * 60 * 60 * 24))
})

const passportExpirySoon = computed(() => {
  const d = passportDaysLeft.value
  return d != null && d >= 0 && d <= 90
})

async function openProfileGenerateDialog() {
  profileDialogOpen.value = true
  profileFilesLoading.value = true
  profileSelection.value = []
  try {
    const resp = await listClientProfileSourceFiles(props.clientId)
    profileSourceFiles.value = resp.items || []
  } catch (err) {
    ElMessage.error('加载候选文件失败：' + (err.response?.data?.detail || err.message))
  } finally {
    profileFilesLoading.value = false
  }
}

async function startProfileGenerate() {
  const ids = profileSelection.value.map(x => x.id).filter(Boolean)
  if (!ids.length) {
    ElMessage.warning('请选择至少一个文件')
    return
  }
  profileGenerating.value = true
  try {
    const resp = await generateClientProfile(props.clientId, ids)
    profileDialogOpen.value = false
    profileTaskOpen.value = true
    profileTask.value = resp
    pollProfileTask(resp.task_id)
  } catch (err) {
    ElMessage.error('创建生成任务失败：' + (err.response?.data?.detail || err.message))
  } finally {
    profileGenerating.value = false
  }
}

function pollProfileTask(taskId) {
  if (profileTaskTimer) clearInterval(profileTaskTimer)
  profileTaskTimer = setInterval(async () => {
    try {
      const data = await getClientProfileGenerationTask(taskId)
      profileTask.value = data
      if (data.status !== 'running') {
        clearInterval(profileTaskTimer)
        profileTaskTimer = null
        if (data.status === 'done') {
          ElMessage.success('客户档案生成完成')
          await load()
        }
      }
    } catch (err) {
      clearInterval(profileTaskTimer)
      profileTaskTimer = null
      ElMessage.error('查询生成任务失败：' + (err.response?.data?.detail || err.message))
    }
  }, 2000)
}

async function loadGenerationTasks() {
  try {
    const resp = await listClientProfileGenerationTasks(props.clientId, 10)
    generationTasks.value = resp.items || []
  } catch {
    generationTasks.value = []
  }
}
function formatAmount(n) {
  return Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

async function load() {
  loading.value = true
  try {
    detail.value = await getClientDetail(props.clientId)
    loadFills()
    loadGenerationTasks()
  } catch (err) {
    ElMessage.error('加载失败：' + (err.response?.data?.detail || err.message))
    detail.value = null
  } finally {
    loading.value = false
  }
}

async function loadFills() {
  fillsLoading.value = true
  try {
    const data = await getClientFills(props.clientId)
    fills.value = data.fills || []
  } catch (err) {
    fills.value = []
  } finally {
    fillsLoading.value = false
  }
}

function reload() {
  load()
}

watch(() => props.clientId, load)
onMounted(load)
onUnmounted(() => {
  if (profileTaskTimer) clearInterval(profileTaskTimer)
})
</script>

<style scoped>
.client-detail-page { height: 100%; display: flex; flex-direction: column; background: #f0f2f8; overflow: hidden; }
.page-header { padding: 16px 28px; background: #fff; display: flex; align-items: center; gap: 16px; flex-shrink: 0; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
.page-title { font-size: 16px; font-weight: 700; color: #1e293b; display: flex; align-items: center; gap: 8px; }
.title-indicator { width: 3px; height: 16px; background: linear-gradient(180deg, #6366f1, #8b5cf6); border-radius: 2px; }
.indicator-warn { width: 3px; height: 16px; background: linear-gradient(180deg, #f59e0b, #ef4444); border-radius: 2px; }
.header-right { margin-left: auto; display: flex; gap: 8px; }
.dialog-hint { margin: 0 0 10px; color: #64748b; font-size: 13px; }
.task-box { display: flex; flex-direction: column; gap: 10px; color: #334155; }
.error-text { color: #dc2626; white-space: pre-wrap; }
.mono { font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 12px; }

.page-content { flex: 1; overflow-y: auto; padding: 20px; }
.detail-wrap { max-width: 1100px; margin: 0 auto; display: flex; flex-direction: column; gap: 14px; }

.card { background: #fff; border-radius: 12px; padding: 18px 22px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
.card-title { font-size: 14px; font-weight: 700; color: #1e293b; display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }

/* 基本卡 */
.profile-card { display: flex; gap: 18px; align-items: flex-start; }
.profile-avatar { width: 60px; height: 60px; border-radius: 16px; background: linear-gradient(135deg, #6366f1, #8b5cf6); color: #fff; display: flex; align-items: center; justify-content: center; font-size: 26px; font-weight: 700; flex-shrink: 0; }
.profile-main { flex: 1; }
.profile-name { font-size: 18px; font-weight: 700; color: #1e293b; display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.profile-name .code { font-family: 'JetBrains Mono','Consolas',monospace; color: #6366f1; font-size: 14px; font-weight: 500; }
.profile-name .name-en { font-size: 13px; color: #94a3b8; font-weight: 400; }
.profile-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px 16px; font-size: 13px; }
.profile-grid .cell { color: #475569; }
.profile-grid .cell.full { grid-column: 1/-1; }
.profile-grid .cell.mono { font-family: 'JetBrains Mono','Consolas',monospace; }
.profile-grid .k { display: inline-block; width: 60px; color: #94a3b8; font-size: 11px; }

/* alert */
.alert-card { border-left: 3px solid #f59e0b; background: linear-gradient(90deg, #fffbeb 0%, #fff 30%); }
.alert-list { display: flex; flex-direction: column; gap: 6px; }
.alert-item { display: flex; align-items: center; gap: 12px; padding: 8px 12px; background: #fef3c7; border-radius: 6px; font-size: 13px; }
.alert-key { font-weight: 600; color: #78350f; min-width: 60px; }
.alert-value { flex: 1; color: #92400e; }
.alert-date { color: #b45309; font-weight: 500; }

/* group */
.group-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px 22px; }
.group-block { background: #f8fafc; padding: 12px 14px; border-radius: 8px; border-left: 2px solid #6366f1; }
.block-title { font-size: 13px; font-weight: 600; color: #1e293b; display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.block-row { font-size: 12px; color: #475569; padding: 2px 0; line-height: 1.6; }
.block-row .k { display: inline-block; width: 50px; color: #94a3b8; font-size: 11px; }

/* tabs */
.card-tabs { display: flex; gap: 4px; margin-bottom: 14px; border-bottom: 1px solid #e2e8f0; }
.tab-btn { background: transparent; border: none; padding: 8px 14px 10px; cursor: pointer; display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 600; color: #94a3b8; border-bottom: 2px solid transparent; margin-bottom: -1px; transition: all 0.15s; }
.tab-btn:hover { color: #475569; }
.tab-btn.active { color: #1e293b; border-bottom-color: #6366f1; }

.tab-pane { min-height: 200px; }

.empty-inline { font-size: 13px; color: #94a3b8; text-align: center; padding: 28px 0; }

/* 旧 doc list 复用 */
.doc-list { display: flex; flex-direction: column; gap: 8px; }
.doc-row { padding: 12px 16px; background: #f8fafc; border-radius: 8px; cursor: pointer; transition: all 0.15s; border: 1px solid transparent; }
.doc-row:hover { background: #fafafe; border-color: rgba(99,102,241,0.2); }
.doc-row.fill-row { cursor: default; }
.doc-row.fill-row:hover { background: #f8fafc; border-color: transparent; }
.doc-name { font-size: 14px; font-weight: 600; color: #1e293b; display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.doc-meta { font-size: 12px; color: #64748b; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.meta-tag { background: linear-gradient(135deg,#ede9fe,#e0e7ff); color: #6366f1; padding: 2px 8px; border-radius: 4px; font-weight: 500; }
.doc-time { margin-left: auto; color: #94a3b8; }
.dl-link { margin-left: auto; color: #6366f1; font-size: 12px; font-weight: 600; text-decoration: none; padding: 2px 8px; border-radius: 4px; background: #ede9fe; transition: all 0.15s; }
.dl-link:hover { background: #c7d2fe; }
.dl-disabled { margin-left: auto; color: #cbd5e1; font-size: 12px; }

.loading-state, .empty-state { display: flex; flex-direction: column; align-items: center; padding: 80px 20px; color: #94a3b8; }
.empty-text { font-size: 16px; color: #475569; }
</style>
