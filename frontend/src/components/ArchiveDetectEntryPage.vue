<template>
  <div class="archive-detect-page">
    <!-- 顶栏：标题 + tab 切换 + 历史记录按钮 -->
    <div class="entry-header">
      <div class="entry-title">
        <span class="title-indicator"></span>
        文件留底检测
      </div>
      <el-radio-group v-model="tabKind" :disabled="busy" size="default" class="tab-switch">
        <el-radio-button label="business">业务审核</el-radio-button>
        <el-radio-button label="quick">快速检测</el-radio-button>
      </el-radio-group>
      <el-button class="history-btn" size="default" @click="openHistory">
        <el-icon style="margin-right: 4px"><Clock /></el-icon>
        历史记录
      </el-button>
    </div>

    <!-- 主体 -->
    <div class="main-scroll">
      <!-- 隐私提醒条(仅快速检测模式) -->
      <div v-if="tabKind === 'quick'" class="privacy-banner">
        <el-icon><Lock /></el-icon>
        <span>文件不入库、处理完即删，不会在本服务保存</span>
      </div>

      <!-- 判定提示词(快速模式专属位置:顶部) -->
      <section v-if="tabKind === 'quick'" class="card">
        <div class="card-head">
          <el-icon><EditPen /></el-icon>
          <span>判定提示词（可编辑，将拼接到 AI 识别中）</span>
        </div>
        <el-input
          v-model="userPrompt"
          type="textarea"
          :rows="3"
          :autosize="{ minRows: 3, maxRows: 8 }"
          placeholder="例：帮我检测文件是否是 XXX 客户 XXX 项目的 XXX 进展（留底）文件"
          :disabled="busy"
          @input="onCriteriaInput"
        />
        <div class="hint">
          请把模板中的 XXX 替换为客户、项目、进展的具体名称。AI 会按你的描述判定每个文件是否符合留底要求。
        </div>
      </section>

      <!-- 2. 来源切换(仅快速检测模式) -->
      <section v-if="tabKind === 'quick'" class="card">
        <div class="card-head">
          <el-icon><Files /></el-icon>
          <span>文件来源</span>
        </div>
        <el-radio-group v-model="sourceKind" :disabled="busy" size="default">
          <el-radio-button label="upload">上传文件</el-radio-button>
          <el-radio-button label="url">输入文件地址</el-radio-button>
        </el-radio-group>

        <!-- upload 模式 -->
        <div v-if="sourceKind === 'upload'" class="source-pane">
          <el-upload
            v-model:file-list="uploadFiles"
            multiple
            :auto-upload="false"
            :limit="MAX_FILES"
            accept=".pdf,.png,.jpg,.jpeg,.bmp,.tiff,.webp,.gif,.doc,.docx,.xls,.xlsx,.pptx"
            :on-exceed="onExceedUpload"
            :on-change="onUploadChange"
            drag
          >
            <el-icon class="upload-icon"><UploadFilled /></el-icon>
            <div class="upload-text">点击或拖拽文件到此处</div>
            <div class="upload-tip">最多 {{ MAX_FILES }} 个文件 · 支持 PDF / 图片 / Word / Excel / PPT（GIF 仅识别第一帧）</div>
          </el-upload>
        </div>

        <!-- url 模式：动态行输入 -->
        <div v-else class="source-pane">
          <div class="url-rows">
            <div
              v-for="(row, i) in urlRows"
              :key="row.id"
              class="url-row"
              :class="{ 'has-error': row.invalid }"
            >
              <span class="url-row-label">文件地址 {{ i + 1 }}</span>
              <el-input
                v-model="row.value"
                placeholder="https://...（仅支持 http/https）"
                :disabled="busy"
                clearable
                @input="row.invalid = false"
                @paste="(e) => onUrlPaste(e, i)"
              />
              <el-button
                class="url-row-del"
                :disabled="busy || urlRows.length <= 1"
                circle
                size="small"
                @click="removeUrlRow(i)"
              >
                <el-icon><Close /></el-icon>
              </el-button>
            </div>
          </div>
          <div class="url-row-actions">
            <el-button
              size="small"
              :disabled="busy || urlRows.length >= MAX_FILES"
              @click="addUrlRow()"
            >
              <el-icon style="margin-right: 4px"><Plus /></el-icon>
              添加文件地址
            </el-button>
            <span class="hint url-counter">
              当前 <b>{{ filledUrlCount }}</b>/{{ MAX_FILES }}
              <span v-if="urlRows.length >= MAX_FILES" class="warn">（已达上限）</span>
            </span>
          </div>
        </div>

        <div class="submit-row">
          <el-button
            type="primary"
            size="large"
            :loading="busy"
            :disabled="!canSubmit"
            @click="handleSubmit"
          >
            <el-icon v-if="!busy" style="margin-right: 4px"><MagicStick /></el-icon>
            {{ submitButtonText }}
          </el-button>
        </div>
      </section>

      <!-- 业务审核模式专属区块:客户 + 进展 + 业务文件行 -->
      <template v-if="tabKind === 'business'">
        <!-- 阶段选择 -->
        <section class="card">
          <div class="card-head">
            <el-icon><Files /></el-icon>
            <span>检测阶段</span>
          </div>
          <el-radio-group v-model="bizStage" :disabled="busy" size="default">
            <el-radio-button label="post_submit">递交后</el-radio-button>
            <el-radio-button label="pre_submit">递交前</el-radio-button>
          </el-radio-group>
          <div class="hint" style="margin-top: 6px">
            当前阶段决定文件分类体系:递交前检测客户基础/个人/公司/其他备用/转款凭证 5 大类;递交后检测文案制作/获批/失败/其他/停滞 4 大类
          </div>
        </section>

        <!-- 客户区块 -->
        <section class="card">
          <div class="card-head">
            <el-icon><Files /></el-icon>
            <span>客户信息</span>
            <span class="card-sub">业务方传入,系统按 client_code 自动归档</span>
          </div>
          <div class="form-grid form-grid-2">
            <div class="form-item">
              <label class="form-label">客户编码 <span class="required">*</span></label>
              <el-input v-model="bizClient.client_code" :disabled="busy" placeholder="如 C001" />
            </div>
            <div class="form-item">
              <label class="form-label">客户姓名 <span class="required">*</span></label>
              <el-input v-model="bizClient.name" :disabled="busy" placeholder="如 张三" />
            </div>
          </div>
        </section>

        <!-- 进展区块 -->
        <section class="card">
          <div class="card-head">
            <el-icon><Files /></el-icon>
            <span>进展信息</span>
            <span class="card-sub">同 progress_oid 重复提交会复用历史检测结果</span>
          </div>
          <div class="form-grid form-grid-3">
            <div class="form-item">
              <label class="form-label">办理人</label>
              <el-input v-model="bizProgress.handler" :disabled="busy" placeholder="如 李顾问" />
            </div>
            <div class="form-item">
              <label class="form-label">进展名称</label>
              <el-input v-model="bizProgress.progress_name" :disabled="busy" placeholder="如 递交后" />
            </div>
            <div class="form-item">
              <label class="form-label">进展 OID</label>
              <el-input v-model="bizProgress.progress_oid" :disabled="busy" placeholder="POID_xxx" />
            </div>
            <div class="form-item">
              <label class="form-label">项目编码</label>
              <el-input v-model="bizProgress.project_code" :disabled="busy" placeholder="如 P001" />
            </div>
            <div class="form-item">
              <label class="form-label">项目名称</label>
              <el-input v-model="bizProgress.project_name" :disabled="busy" placeholder="如 新加坡家办" />
            </div>
            <div class="form-item"></div>
            <div class="form-item">
              <label class="form-label">项目详情编码</label>
              <el-input v-model="bizProgress.project_detail_code" :disabled="busy" placeholder="如 PD001" />
            </div>
            <div class="form-item">
              <label class="form-label">项目详情名称</label>
              <el-input v-model="bizProgress.project_detail_name" :disabled="busy" placeholder="如 架构设计" />
            </div>
          </div>
        </section>

        <!-- 判定提示词(业务模式专属位置:进展之后) -->
        <section class="card">
          <div class="card-head">
            <el-icon><EditPen /></el-icon>
            <span>判定提示词（可编辑，将拼接到 AI 识别中）</span>
          </div>
          <el-input
            v-model="userPrompt"
            type="textarea"
            :rows="3"
            :autosize="{ minRows: 3, maxRows: 8 }"
            placeholder="进入业务审核 tab 时将自动预填..."
            :disabled="busy"
            @input="onCriteriaInput"
          />
          <div v-if="!criteriaDirty" class="hint">
            已根据客户/项目/阶段自动生成。手动修改后将不再自动覆盖。
          </div>
          <div v-if="criteriaDirty" class="hint">
            <el-link type="primary" :underline="false" @click="resetCriteria">重置为自动生成</el-link>
          </div>
        </section>

        <!-- 文件区块:URL -->
        <section class="card">
          <div class="card-head">
            <el-icon><Files /></el-icon>
            <span>文件列表</span>
            <span class="card-sub">每个文件必填 file_id(增量复用 key),文件需先上传 OSS 后填写 URL</span>
          </div>

          <!-- URL 模式 -->
          <div class="biz-file-rows">
            <div v-for="(row, i) in bizUrlRows" :key="row.id" class="biz-file-row">
              <span class="biz-row-idx">{{ i + 1 }}</span>
              <el-input v-model="row.file_id" placeholder="file_id *" :disabled="busy" class="biz-input-id" />
              <el-input v-model="row.filename" placeholder="文件名(可选)" :disabled="busy" class="biz-input-name" />
              <el-input v-model="row.url" placeholder="https://oss.../signed?..." :disabled="busy" class="biz-input-url" />
              <el-button :disabled="busy || bizUrlRows.length <= 1" circle size="small" @click="bizRemoveUrlRow(i)">
                <el-icon><Close /></el-icon>
              </el-button>
            </div>
            <div class="biz-actions">
              <el-button size="small" :disabled="busy || bizUrlRows.length >= MAX_FILES" @click="bizAddUrlRow">
                <el-icon style="margin-right: 4px"><Plus /></el-icon>
                添加文件行
              </el-button>
              <span class="hint">当前 <b>{{ bizUrlRows.length }}</b>/{{ MAX_FILES }}</span>
            </div>
          </div>

          <div class="submit-row">
            <el-button
              type="primary"
              size="large"
              :loading="busy"
              :disabled="!canSubmitBiz"
              @click="handleSubmitBiz"
            >
              <el-icon v-if="!busy" style="margin-right: 4px"><MagicStick /></el-icon>
              {{ bizSubmitButtonText }}
            </el-button>
          </div>
        </section>
      </template>

      <!-- 3. 结果列表（一文件一卡） -->
      <section v-if="batch" class="card">
        <div class="card-head">
          <el-icon><Reading /></el-icon>
          <span>检测结果</span>
          <el-tag
            :type="batch.status === 'done' ? 'success' : 'warning'"
            size="small"
            style="margin-left: 8px"
          >
            {{ batch.status === 'done' ? '全部完成' : `进行中 ${batch.done_files}/${batch.total_files}` }}
          </el-tag>
          <el-button
            v-if="batch.status === 'done'"
            size="small"
            type="warning"
            plain
            style="margin-left: auto"
            :disabled="busy || !userPrompt.trim()"
            @click="handleRecheckBatch"
          >
            重新审核
          </el-button>
        </div>

        <!-- 业务字段回显条(仅业务审核模式) -->
        <div v-if="batch.client || batch.progress" class="biz-context-bar">
          <span v-if="batch.client" class="biz-ctx-item">
            <span class="biz-ctx-label">客户</span>
            {{ batch.client.name }}
            <span class="dim">({{ batch.client.client_code }})</span>
          </span>
          <span v-if="batch.progress" class="biz-ctx-item">
            <span class="biz-ctx-label">进展</span>
            {{ batch.progress.progress_name || batch.progress.progress_oid }}
            <span class="dim">({{ batch.progress.progress_oid }})</span>
          </span>
          <span v-if="batch.progress && batch.progress.handler" class="biz-ctx-item">
            <span class="biz-ctx-label">办理人</span>
            {{ batch.progress.handler }}
          </span>
          <span v-if="batch.progress && batch.progress.project_name" class="biz-ctx-item">
            <span class="biz-ctx-label">项目</span>
            {{ batch.progress.project_name }}
          </span>
        </div>

        <!-- 总体判断条:始终显示骨架,done 时填入实际 verdict/score/reason -->
        <div
          class="overall-banner"
          :class="batch.overall_verdict ? `overall-${batch.overall_verdict}` : 'overall-pending'"
        >
          <div class="overall-head">
            <!-- 完成态:三态图标 -->
            <el-icon v-if="batch.overall_verdict" :size="22">
              <CircleCheck v-if="batch.overall_verdict === 'match'" />
              <Warning v-else-if="batch.overall_verdict === 'partial'" />
              <CircleClose v-else />
            </el-icon>
            <!-- 进行态:loading 图标 -->
            <el-icon v-else :size="22" class="spin"><Loading /></el-icon>

            <span class="overall-title">
              {{ batch.overall_verdict ? overallVerdictLabel(batch.overall_verdict) : 'AI 正在汇总总体判断...' }}
            </span>
            <span v-if="batch.overall_score != null" class="overall-score">
              综合匹配度 {{ batch.overall_score }}/100
            </span>
          </div>
          <div class="overall-bar">
            <div
              class="overall-fill"
              :class="{ 'overall-fill-pending': batch.overall_verdict == null }"
              :style="{ width: batch.overall_verdict ? (batch.overall_score || 0) + '%' : '100%' }"
            ></div>
          </div>
          <p v-if="batch.overall_reason" class="overall-reason">{{ batch.overall_reason }}</p>
          <p v-else class="overall-reason overall-reason-pending">
            待所有文件检测完成后,AI 将生成 80-200 字的整体留底判断说明。
          </p>
          <!-- 复用/新检测计数(仅业务模式) -->
          <p v-if="batch.reused_count != null || batch.new_count != null" class="overall-counts">
            本次共 {{ batch.total_files }} 个文件
            <span v-if="batch.reused_count">（复用 {{ batch.reused_count }} 个）</span>
            <span v-if="batch.new_count">（新检测 {{ batch.new_count }} 个）</span>
          </p>
        </div>

        <div class="result-grid">
          <div
            v-for="f in batch.files"
            :key="f.idx"
            class="file-card"
            :class="`status-${f.status}`"
          >
            <div class="fc-head">
              <span class="fc-name" :title="f.filename || f.source_url">
                {{ f.filename || f.source_url || '—' }}
              </span>
              <el-tag v-if="f.is_reused" size="small" class="reused-tag" effect="dark">已复用</el-tag>
              <el-tag size="small" :type="statusTagType(f.status)">{{ statusLabel(f.status) }}</el-tag>
            </div>

            <!-- 进行中：显示 spinner + 阶段说明 -->
            <div v-if="['pending','fetching','ocr','llm'].includes(f.status)" class="fc-progress">
              <el-icon class="spin"><Loading /></el-icon>
              <span>{{ stageLabel(f.status) }}</span>
            </div>

            <!-- 完成：展示判定结论(三态) -->
            <div v-else-if="f.status === 'done'" class="fc-body">
              <div class="verdict" :class="`verdict-${fileVerdict(f)}`">
                <el-icon>
                  <CircleCheck v-if="fileVerdict(f) === 'match'" />
                  <Warning v-else-if="fileVerdict(f) === 'partial'" />
                  <CircleClose v-else />
                </el-icon>
                <span class="verdict-text">{{ verdictLabel(fileVerdict(f)) }}</span>
                <span class="verdict-score">匹配度 {{ fileScore(f) }}/100</span>
              </div>

              <div class="confidence-track">
                <div class="confidence-fill" :style="{ width: (fileScore(f) || 0) + '%' }"></div>
              </div>

              <div class="meta-row">
                <el-tag v-if="f.doc_category" size="small" type="warning" effect="plain">{{ f.doc_category }}</el-tag>
                <el-tag v-if="f.page_count" size="small" effect="plain">{{ f.page_count }} 页</el-tag>
                <el-tag v-if="f.elapsed_sec != null" size="small" effect="plain">{{ f.elapsed_sec }}s</el-tag>
              </div>

              <div v-if="f.reason" class="reason">
                <div class="section-title">判断依据</div>
                <p class="reason-text">{{ f.reason }}</p>
              </div>

              <div v-if="f.key_points && f.key_points.length" class="key-points">
                <div class="section-title">关键要点</div>
                <ul>
                  <li v-for="(p, i) in f.key_points" :key="i">{{ p }}</li>
                </ul>
              </div>
            </div>

            <!-- 错误 -->
            <div v-else-if="f.status === 'error'" class="fc-error">
              <el-icon><Warning /></el-icon>
              <span>{{ f.error_msg || '处理失败' }}</span>
            </div>
          </div>
        </div>
      </section>

      <!-- 空状态 -->
      <section v-else-if="!busy" class="card empty">
        <el-icon :size="48"><Reading /></el-icon>
        <p class="empty-title">填写判定提示词，选择文件或输入文件地址，开始检测</p>
        <p class="empty-sub">检测结果中的金额、电话、身份证、银行卡等敏感信息会自动脱敏</p>
      </section>
    </div>

    <!-- 历史记录抽屉 -->
    <el-drawer
      v-model="historyVisible"
      title="留底检测记录"
      direction="rtl"
      size="60%"
      :destroy-on-close="false"
    >
      <div class="history-toolbar">
        <span class="history-summary">共 {{ historyList.length }} 条记录</span>
        <el-button size="small" @click="loadHistory" :loading="historyLoading">
          <el-icon style="margin-right: 4px"><Refresh /></el-icon>
          刷新
        </el-button>
      </div>

      <el-table
        :data="historyList"
        v-loading="historyLoading"
        stripe
        empty-text="暂无历史记录"
      >
        <el-table-column label="批次" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="mono dim">{{ row.batch_id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="historyStatusTag(row.status)" size="small" effect="light">
              {{ historyStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="完成" width="90" align="center">
          <template #default="{ row }">
            {{ row.done_files }}/{{ row.total_files }}
          </template>
        </el-table-column>
        <el-table-column label="来源" width="80" align="center">
          <template #default="{ row }">
            <span class="dim">{{ row.source_kind }}</span>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="160">
          <template #default="{ row }">
            <span class="dim mono">{{ row.created_at }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" align="center" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              link
              :disabled="row.status !== 'done'"
              @click="loadHistoryItem(row)"
            >
              查看
            </el-button>
            <el-popconfirm
              title="确认删除该批次记录?"
              @confirm="removeHistoryItem(row)"
            >
              <template #reference>
                <el-button size="small" type="danger" link>删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted, watch } from 'vue'
import {
  EditPen, UploadFilled, Files, Reading, MagicStick,
  CircleCheck, CircleClose, Warning, Loading, Plus, Close, Lock,
  Clock, Refresh,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  submitArchiveDetectUpload,
  submitArchiveDetectUrls,
  pollArchiveDetect,
  listArchiveDetectHistory,
  deleteArchiveDetectBatch,
  submitBusinessBatch,
  pollBusinessBatch,
  recheckArchiveDetectBatch,
} from '../api.js'

const MAX_FILES = 50

const userPrompt = ref('')
const sourceKind = ref('upload')         // 'upload' | 'url'
const uploadFiles = ref([])              // el-upload v-model 接管

// 动态行：每个对象 {id, value, invalid}；初始 1 行空
let _rowSeq = 0
function makeRow(value = '') {
  return { id: ++_rowSeq, value, invalid: false }
}
const urlRows = ref([makeRow()])

const submitting = ref(false)            // HTTP 提交瞬态
const batch = ref(null)
let pollTimer = null

// 历史记录抽屉
const historyVisible = ref(false)
const historyList = ref([])
const historyLoading = ref(false)

// ==================== 业务审核 tab(阶段三) ====================
const tabKind = ref('business')               // 'quick' | 'business'，默认进入业务审核
const tabMode = ref('business')               // 当前 batch 是哪个 tab 提交的(决定轮询走哪个 api)

const bizClient = ref({
  client_code: '',
  name: '',
})
const bizProgress = ref({
  progress_oid: '',
  handler: '',
  project_name: '',
  project_code: '',
  project_detail_name: '',
  project_detail_code: '',
  progress_name: '',
})

const bizStage = ref('post_submit')           // 业务阶段:pre_submit | post_submit
const criteriaDirty = ref(false)              // 用户是否手改过 criteria

let _bizRowSeq = 0
function makeBizUrlRow() {
  return { id: ++_bizRowSeq, file_id: '', filename: '', url: '' }
}
const bizUrlRows = ref([makeBizUrlRow()])

// 用业务字段自动拼装 criteria
// 注意：不包含客户姓名。同一客户的进度包下，文件可能属于配偶/子女/父母，名字都不一样。
// 如需按姓名严格匹配，业务方手动在界面上改 criteria 加"必须是 XXX 的"即可。
function buildBizCriteria() {
  const c = bizClient.value
  const p = bizProgress.value
  const stage = bizStage.value === 'pre_submit' ? '递交前' : '递交后'

  const parts = []
  // 客户标签用 client_code 而非姓名 —— 不作为姓名硬匹配条件，仅为上下文
  const label = c.client_code ? `客户代号${c.client_code}` : (c.name ? `客户「${c.name}」` : '')
  if (label) parts.push(label)
  if (p.project_name) parts.push(`「${p.project_name}」项目`)
  if (p.project_detail_name) parts.push(`「${p.project_detail_name}」`)
  if (p.progress_name) parts.push(`「${p.progress_name}」进展`)

  const subject = parts.length ? parts.join(' / ') : '本客户'
  return `请按公司文件留底标准，审核此文件是否为 ${subject} 在「${stage}」阶段应上传的留底文件。重点判断文件类型、内容完整性和格式规范，而不是严格匹配文件上的姓名（该客户的文件可能属于其配偶/子女/父母）。`
}

function onCriteriaInput() {
  // 用户手改 → 标 dirty,后续业务字段变更不再覆盖
  criteriaDirty.value = true
}

function resetCriteria() {
  criteriaDirty.value = false
  userPrompt.value = buildBizCriteria()
}

// 监听业务字段变化 → 实时刷新 criteria(除非用户已手改)
watch(
  () => [
    bizClient.value.name,
    bizProgress.value.project_name,
    bizProgress.value.project_detail_name,
    bizProgress.value.progress_name,
    bizStage.value,
  ],
  () => {
    if (tabKind.value !== 'business') return
    if (criteriaDirty.value) return
    userPrompt.value = buildBizCriteria()
  },
  { immediate: true },
)

// 切换到业务 tab → 自动预填(若用户未手改)
watch(tabKind, (newTab) => {
  if (newTab === 'business' && !criteriaDirty.value) {
    userPrompt.value = buildBizCriteria()
  }
})

const filledUrlCount = computed(
  () => urlRows.value.filter((r) => r.value.trim()).length,
)

// 是否还在跑（提交瞬态 OR 轮询期）。整个生命周期都视为忙。
const busy = computed(
  () => submitting.value || (batch.value && batch.value.status !== 'done'),
)

// 已有完整结果 → 再次提交前要确认（避免误覆盖）
const hasResult = computed(
  () => batch.value && batch.value.status === 'done',
)

const submitButtonText = computed(() => {
  if (submitting.value) return '提交中...'
  if (batch.value && batch.value.status !== 'done') {
    return `检测中 ${batch.value.done_files}/${batch.value.total_files}...`
  }
  if (hasResult.value) return '重新检测'
  return '开始检测'
})

const canSubmit = computed(() => {
  if (busy.value) return false
  if (!userPrompt.value.trim()) return false
  if (sourceKind.value === 'upload') {
    return uploadFiles.value.length > 0 && uploadFiles.value.length <= MAX_FILES
  }
  return filledUrlCount.value > 0 && urlRows.value.length <= MAX_FILES
})

// ==================== 业务审核 computed + 函数 ====================

const canSubmitBiz = computed(() => {
  if (busy.value) return false
  if (!userPrompt.value.trim()) return false
  if (!bizClient.value.client_code.trim() || !bizClient.value.name.trim()) return false
  if (!bizProgress.value.progress_oid.trim()) return false
  return bizUrlRows.value.some(r => r.file_id.trim() && r.url.trim() &&
    /^https?:\/\//i.test(r.url.trim()))
})

const bizSubmitButtonText = computed(() => {
  if (submitting.value) return '提交中...'
  if (batch.value && batch.value.status !== 'done') {
    return `审核中 ${batch.value.done_files}/${batch.value.total_files}...`
  }
  if (batch.value && batch.value.status === 'done') return '重新审核'
  return '开始审核'
})

function bizAddUrlRow() {
  if (bizUrlRows.value.length >= MAX_FILES) {
    ElMessage.warning(`最多 ${MAX_FILES} 个文件`)
    return
  }
  bizUrlRows.value.push(makeBizUrlRow())
}
function bizRemoveUrlRow(i) {
  if (bizUrlRows.value.length <= 1) return
  bizUrlRows.value.splice(i, 1)
}
async function handleSubmitBiz() {
  if (busy.value) return

  if (batch.value && batch.value.status === 'done') {
    try {
      await ElMessageBox.confirm(
        '当前已有审核结果,继续提交会清空页面上的当前结果并创建新批次,是否继续?',
        '确认提交新批次',
        { confirmButtonText: '继续', cancelButtonText: '取消', type: 'warning' },
      )
    } catch { return }
  }

  const criteria = userPrompt.value.trim()
  if (!criteria) { ElMessage.warning('请填写判定提示词'); return }

  const clientObj = {
    client_code: bizClient.value.client_code.trim(),
    name: bizClient.value.name.trim(),
  }
  const progressObj = {
    progress_oid: bizProgress.value.progress_oid.trim(),
    handler: bizProgress.value.handler.trim() || null,
    project_name: bizProgress.value.project_name.trim() || null,
    project_code: bizProgress.value.project_code.trim() || null,
    project_detail_name: bizProgress.value.project_detail_name.trim() || null,
    project_detail_code: bizProgress.value.project_detail_code.trim() || null,
    progress_name: bizProgress.value.progress_name.trim() || null,
  }

  submitting.value = true
  batch.value = null
  stopPoll()
  tabMode.value = 'business'

  try {
    // URL 模式:校验每行
    const items = []
    const seen = new Set()
    for (let i = 0; i < bizUrlRows.value.length; i++) {
      const row = bizUrlRows.value[i]
      const fid = row.file_id.trim()
      const url = row.url.trim()
      if (!fid && !url) continue   // 空行允许
      if (!fid) throw new Error(`第 ${i+1} 行缺 file_id`)
      if (seen.has(fid)) throw new Error(`重复 file_id: ${fid}`)
      if (!/^https?:\/\//i.test(url)) throw new Error(`第 ${i+1} 行 url 不合法`)
      seen.add(fid)
      items.push({ file_id: fid, filename: row.filename.trim() || null, url })
    }
    if (!items.length) throw new Error('请至少添加一个文件 URL')

    const resp = await submitBusinessBatch({
      criteria,
      stage: bizStage.value,
      client: clientObj,
      progress: progressObj,
      items,
    })

    ElMessage.success(
      `已提交 ${resp.total_files} 个文件:${resp.reused_count} 个复用历史 + ${resp.new_count} 个新检测`
    )
    startPoll(resp.batch_id)
  } catch (err) {
    const msg = err.response?.data?.detail || err.message || '提交失败'
    ElMessage.error('提交失败:' + msg)
  } finally {
    submitting.value = false
  }
}

async function handleRecheckBatch() {
  if (!batch.value || batch.value.status !== 'done') return
  const criteria = userPrompt.value.trim()
  if (!criteria) {
    ElMessage.warning('请填写判定提示词')
    return
  }

  try {
    await ElMessageBox.confirm(
      '将使用当前判定提示词重新跑 AI 审核。已有 OCR 文本的文件不会重新 OCR；无 OCR 文本但有 URL 的文件会重新 OCR。是否继续？',
      '确认重新审核',
      { confirmButtonText: '继续', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }

  submitting.value = true
  stopPoll()
  try {
    const resp = await recheckArchiveDetectBatch(
      batch.value.batch_id,
      criteria,
      tabKind.value === 'business' ? bizStage.value : null,
    )
    ElMessage.success(
      `已创建重新审核批次:${resp.ai_only_count} 个文件复用 OCR,${resp.ocr_count} 个文件需重新 OCR`,
    )
    tabMode.value = resp.mode === 'business' ? 'business' : 'quick'
    batch.value = null
    startPoll(resp.batch_id)
  } catch (err) {
    const msg = err.response?.data?.detail || err.message || '重新审核失败'
    ElMessage.error('重新审核失败:' + msg)
  } finally {
    submitting.value = false
  }
}

const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024   // 与后端 file_fetcher.MAX_DOWNLOAD_BYTES 对齐

function onExceedUpload() {
  ElMessage.warning(`最多 ${MAX_FILES} 个文件`)
}

/**
 * el-upload 每次添加/移除文件触发。仅做大小提醒，不阻止用户加入。
 * 后端 file_fetcher 上限 50MB，超限会被服务端 413 拒绝（写到该文件结果卡的 error_msg）。
 */
function onUploadChange(file) {
  if (file?.status !== 'ready') return
  if (file.size && file.size > MAX_FILE_SIZE_BYTES) {
    const mb = (file.size / 1024 / 1024).toFixed(1)
    ElMessage.warning(`「${file.name}」体积 ${mb}MB 超过 50MB 上限，提交后该文件会失败`)
  }
}

function addUrlRow() {
  if (urlRows.value.length >= MAX_FILES) {
    ElMessage.warning(`最多 ${MAX_FILES} 个文件地址`)
    return
  }
  urlRows.value.push(makeRow())
}

function removeUrlRow(i) {
  if (urlRows.value.length <= 1) return
  urlRows.value.splice(i, 1)
}

/**
 * 粘贴增强：当用户在某行粘贴包含换行的文本时，按行拆分：
 *  - 第 1 行填到当前行
 *  - 后续行依次填到下一行（不存在则新建，受 MAX_FILES 限制）
 * 不含换行 → 走默认粘贴行为。
 */
function onUrlPaste(event, rowIdx) {
  const txt = (event.clipboardData || window.clipboardData)?.getData('text') ?? ''
  if (!/[\r\n]/.test(txt)) return
  event.preventDefault()
  const lines = txt.split(/\r?\n/).map((s) => s.trim()).filter(Boolean)
  if (lines.length === 0) return

  let cursor = rowIdx
  let truncated = false
  for (const line of lines) {
    if (cursor >= MAX_FILES) {
      truncated = true
      break
    }
    if (cursor >= urlRows.value.length) {
      urlRows.value.push(makeRow(line))
    } else {
      urlRows.value[cursor].value = line
      urlRows.value[cursor].invalid = false
    }
    cursor++
  }
  if (truncated) {
    ElMessage.warning(`仅保留前 ${MAX_FILES} 个文件地址，其余已忽略`)
  } else {
    ElMessage.success(`已拆分 ${lines.length} 个地址到独立输入框`)
  }
}

async function handleSubmit() {
  // 防呆 1: 进行中禁止再次提交（按钮已禁用，但保险起见再挡一层）
  if (busy.value) return

  tabMode.value = 'quick'      // 快速检测模式,轮询走 pollArchiveDetect

  // 防呆 2: 已有完成结果时再次提交 → 弹确认
  if (hasResult.value) {
    try {
      await ElMessageBox.confirm(
        '当前已有检测结果，重新检测会清空现有结果，是否继续？',
        '确认重新检测',
        { confirmButtonText: '继续', cancelButtonText: '取消', type: 'warning' },
      )
    } catch {
      return                                 // 用户取消
    }
  }

  const prompt = userPrompt.value.trim()
  if (!prompt) {
    ElMessage.warning('请填写判定提示词')
    return
  }

  if (sourceKind.value === 'upload') {
    if (uploadFiles.value.length === 0) {
      ElMessage.warning('请至少选择一个文件')
      return
    }
    if (uploadFiles.value.length > MAX_FILES) {
      ElMessage.warning(`最多 ${MAX_FILES} 个文件`)
      return
    }
  } else {
    // 校验每行
    let firstBad = -1
    const urls = []
    urlRows.value.forEach((row, i) => {
      const v = row.value.trim()
      if (!v) return                // 空行允许，提交时丢弃
      if (!/^https?:\/\//i.test(v)) {
        row.invalid = true
        if (firstBad === -1) firstBad = i
      } else {
        row.invalid = false
        urls.push(v)
      }
    })
    if (firstBad >= 0) {
      ElMessage.warning(`第 ${firstBad + 1} 行不是合法地址（需以 http:// 或 https:// 开头）`)
      return
    }
    if (urls.length === 0) {
      ElMessage.warning('请至少输入一个文件地址')
      return
    }
    if (urls.length > MAX_FILES) {
      ElMessage.warning(`最多 ${MAX_FILES} 个文件地址`)
      return
    }

    submitting.value = true
    batch.value = null
    stopPoll()
    try {
      const resp = await submitArchiveDetectUrls(urls, prompt)
      ElMessage.success(`已提交 ${resp.total_files} 个文件，AI 正在检测...`)
      startPoll(resp.batch_id)
    } catch (err) {
      const msg = err.response?.data?.detail || err.message || '提交失败'
      ElMessage.error('提交失败：' + msg)
    } finally {
      submitting.value = false
    }
    return
  }

  submitting.value = true
  batch.value = null
  stopPoll()
  try {
    const realFiles = uploadFiles.value.map((it) => it.raw).filter(Boolean)
    const resp = await submitArchiveDetectUpload(realFiles, prompt)
    ElMessage.success(`已提交 ${resp.total_files} 个文件，AI 正在检测...`)
    startPoll(resp.batch_id)
  } catch (err) {
    const msg = err.response?.data?.detail || err.message || '提交失败'
    ElMessage.error('提交失败：' + msg)
  } finally {
    submitting.value = false
  }
}

function startPoll(batchId) {
  pollOnce(batchId)
  pollTimer = setInterval(() => pollOnce(batchId), 1500)
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function pollOnce(batchId) {
  try {
    // 业务模式走 pollBusinessBatch(含 client/progress 透传);快速模式走 pollArchiveDetect
    const data = (tabMode.value === 'business')
      ? await pollBusinessBatch(batchId)
      : await pollArchiveDetect(batchId)
    batch.value = data
    if (data.status === 'done') {
      stopPoll()
      const okCnt = (data.files || []).filter((f) => f.status === 'done').length
      ElMessage.success(`检测完成：${okCnt}/${data.total_files}`)
    }
  } catch (err) {
    stopPoll()
    ElMessage.error('查询状态失败：' + (err.response?.data?.detail || err.message))
  }
}

// ==================== utils ====================

// ==================== verdict 三态相关 ====================

/** 优先用后端新字段 verdict;老 batch 无 verdict 时回落到 is_archival 二态。 */
function fileVerdict(f) {
  if (f.verdict && ['match', 'partial', 'mismatch'].includes(f.verdict)) return f.verdict
  // 老 batch 兼容:is_archival=true → match,否则 → mismatch
  return f.is_archival ? 'match' : 'mismatch'
}

/** 优先用 match_score(新);老 batch 回落到 confidence(旧)。 */
function fileScore(f) {
  if (f.match_score != null) return f.match_score
  return f.confidence ?? 0
}

function verdictLabel(v) {
  return {
    match: '符合留底要求',
    partial: '部分符合',
    mismatch: '不符合留底要求',
  }[v] || '判定异常'
}

function overallVerdictLabel(v) {
  return {
    match: '整体符合留底要求',
    partial: '部分符合',
    mismatch: '整体不符合留底要求',
  }[v] || '判定异常'
}

function statusLabel(s) {
  return {
    pending: '排队中',
    fetching: '下载中',
    ocr: 'OCR 中',
    llm: 'AI 分析中',
    done: '完成',
    error: '失败',
  }[s] || s
}

function stageLabel(s) {
  return {
    pending: '等待开始...',
    fetching: '下载文件中...',
    ocr: 'OCR / 文本抽取中...',
    llm: 'AI 判定中...',
  }[s] || '处理中...'
}

function statusTagType(s) {
  if (s === 'done') return 'success'
  if (s === 'error') return 'danger'
  if (s === 'llm') return 'warning'
  return 'info'
}

// ==================== 历史记录 ====================

function historyStatusLabel(s) {
  return { running: '进行中', done: '完成', error: '失败' }[s] || s
}

function historyStatusTag(s) {
  if (s === 'done') return 'success'
  if (s === 'error') return 'danger'
  return 'warning'
}

async function openHistory() {
  historyVisible.value = true
  await loadHistory()
}

async function loadHistory() {
  historyLoading.value = true
  try {
    const resp = await listArchiveDetectHistory(200)
    historyList.value = resp.items || []
  } catch (err) {
    ElMessage.error('加载历史失败：' + (err.response?.data?.detail || err.message))
  } finally {
    historyLoading.value = false
  }
}

async function loadHistoryItem(row) {
  // 加载该 batch 结果并停止当前轮询
  stopPoll()
  historyVisible.value = false
  submitting.value = true
  try {
    const data = await pollArchiveDetect(row.batch_id)
    batch.value = data
    if (data.status !== 'done') {
      // 历史 batch 一般是 done；若仍 running 则继续轮询
      startPoll(row.batch_id)
    }
    ElMessage.success(`已加载批次 ${row.batch_id}`)
  } catch (err) {
    ElMessage.error('加载批次失败：' + (err.response?.data?.detail || err.message))
  } finally {
    submitting.value = false
  }
}

async function removeHistoryItem(row) {
  try {
    await deleteArchiveDetectBatch(row.batch_id)
    ElMessage.success('已删除')
    // 若删的是当前正在看的 batch，清空
    if (batch.value && batch.value.batch_id === row.batch_id) {
      stopPoll()
      batch.value = null
    }
    await loadHistory()
  } catch (err) {
    ElMessage.error('删除失败：' + (err.response?.data?.detail || err.message))
  }
}

onUnmounted(stopPoll)
</script>

<style scoped>
.archive-detect-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #f0f2f8;
  overflow: hidden;
}

/* 顶栏 */
.entry-header {
  padding: 0 24px;
  height: 56px;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-shrink: 0;
  border-bottom: 1px solid #e8ebf5;
}
.entry-title { font-size: 16px; font-weight: 700; color: #1e293b; display: flex; align-items: center; gap: 10px; }
.title-indicator {
  width: 3px; height: 16px;
  background: linear-gradient(180deg, #fb923c, #f59e0b);
  border-radius: 2px;
}
.history-btn {
  color: #64748b !important;
  border-color: #e2e8f0 !important;
}
.history-btn:hover {
  color: #fb923c !important;
  border-color: #fb923c !important;
}

/* 历史抽屉 */
.history-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.history-summary {
  font-size: 13px;
  color: #64748b;
}
.dim { color: #94a3b8; }
.mono { font-family: 'JetBrains Mono', 'Consolas', monospace; font-size: 12px; }

/* ==================== 业务审核 tab(阶段三) ==================== */

.tab-switch {
  margin-right: auto;
  margin-left: 20px;
}

.card-sub {
  margin-left: auto;
  font-size: 12px;
  color: #94a3b8;
  font-weight: normal;
}

.form-grid {
  display: grid;
  gap: 14px 18px;
}
.form-grid-2 { grid-template-columns: repeat(2, 1fr); }
.form-grid-3 { grid-template-columns: repeat(3, 1fr); }

.form-item { display: flex; flex-direction: column; gap: 6px; }
.form-label {
  font-size: 12px;
  color: #475569;
  font-weight: 500;
}
.required { color: #ef4444; margin-left: 2px; }

.biz-file-rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.biz-file-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.biz-row-idx {
  flex-shrink: 0;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f1f5f9;
  border-radius: 50%;
  font-size: 12px;
  color: #64748b;
  font-weight: 600;
}
.biz-input-id { flex: 0 0 140px; }
.biz-input-name { flex: 0 0 180px; }
.biz-input-url { flex: 1; }
.biz-upload-cell {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  background: #fff;
  border: 1px dashed #cbd5e1;
  border-radius: 6px;
  padding: 0 10px;
  height: 32px;
}
.biz-upload-btn {
  cursor: pointer;
  color: #fb923c;
  font-size: 13px;
  font-weight: 500;
}
.biz-upload-btn:hover { color: #f59e0b; }
.biz-file-size { color: #94a3b8; font-size: 12px; }

.biz-actions {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 12px;
}

/* 业务字段回显条 */
.biz-context-bar {
  margin-bottom: 12px;
  padding: 8px 12px;
  background: #fff7ed;
  border: 1px solid #fed7aa;
  border-radius: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  font-size: 12px;
  color: #92400e;
}
.biz-ctx-item { display: inline-flex; align-items: center; gap: 4px; }
.biz-ctx-label {
  display: inline-block;
  padding: 1px 6px;
  background: #fb923c;
  color: #fff;
  border-radius: 3px;
  font-size: 11px;
  font-weight: 600;
}

/* 复用/新检测计数 */
.overall-counts {
  margin: 4px 0 0;
  font-size: 12px;
  color: rgba(0, 0, 0, 0.55);
  font-style: italic;
}

/* 已复用徽章 */
.reused-tag {
  background: #8b5cf6 !important;
  border-color: #8b5cf6 !important;
  color: #fff !important;
  font-size: 11px !important;
  padding: 0 6px !important;
  height: 18px !important;
  line-height: 18px !important;
  margin-right: 4px;
}

/* 主体 */
.main-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 18px 24px 32px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* 隐私提醒条 */
.privacy-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  background: #ecfdf5;
  border: 1px solid #a7f3d0;
  border-radius: 10px;
  font-size: 13px;
  color: #065f46;
  font-weight: 500;
}
.privacy-banner :deep(.el-icon) {
  color: #10b981;
  font-size: 16px;
  flex-shrink: 0;
}

/* 总体判断条(在文件网格之前) */
.overall-banner {
  margin-bottom: 14px;
  padding: 14px 16px;
  border-radius: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  border: 1px solid transparent;
}
.overall-banner.overall-match {
  background: #ecfdf5;
  border-color: #a7f3d0;
  color: #065f46;
}
.overall-banner.overall-partial {
  background: #fffbeb;
  border-color: #fde68a;
  color: #92400e;
}
.overall-banner.overall-mismatch {
  background: #f1f5f9;
  border-color: #cbd5e1;
  color: #475569;
}
.overall-banner.overall-pending {
  background: #f8fafc;
  border-color: #cbd5e1;
  color: #64748b;
}
.overall-head {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 600;
}
.overall-banner.overall-match .overall-head :deep(.el-icon) { color: #10b981; }
.overall-banner.overall-partial .overall-head :deep(.el-icon) { color: #f59e0b; }
.overall-banner.overall-mismatch .overall-head :deep(.el-icon) { color: #94a3b8; }
.overall-title { font-size: 15px; }
.overall-score {
  margin-left: auto;
  font-size: 12px;
  font-family: 'JetBrains Mono', 'Consolas', monospace;
  font-weight: 600;
  opacity: 0.85;
}
.overall-bar {
  height: 6px;
  background: rgba(0, 0, 0, 0.06);
  border-radius: 3px;
  overflow: hidden;
}
.overall-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.6s ease;
}
.overall-banner.overall-match .overall-fill { background: linear-gradient(90deg, #34d399, #10b981); }
.overall-banner.overall-partial .overall-fill { background: linear-gradient(90deg, #fbbf24, #f59e0b); }
.overall-banner.overall-mismatch .overall-fill { background: linear-gradient(90deg, #cbd5e1, #94a3b8); }
.overall-banner.overall-pending .overall-fill,
.overall-fill-pending {
  background: linear-gradient(
    90deg,
    rgba(148, 163, 184, 0.2) 0%,
    rgba(148, 163, 184, 0.5) 50%,
    rgba(148, 163, 184, 0.2) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.6s linear infinite;
}
@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
.overall-reason-pending {
  font-style: italic;
  opacity: 0.7;
}
.overall-reason {
  margin: 4px 0 0;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.card {
  background: #fff;
  border: 1px solid #e8ebf5;
  border-radius: 12px;
  padding: 18px 20px;
}

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
  margin-bottom: 12px;
}

.hint {
  margin-top: 8px;
  font-size: 12px;
  color: #94a3b8;
}
.hint .warn { color: #ef4444; margin-left: 4px; }

.source-pane {
  margin-top: 14px;
}

.upload-icon {
  font-size: 40px;
  color: #cbd5e1;
}
.upload-text { font-size: 14px; color: #475569; margin-top: 4px; }
.upload-tip { font-size: 12px; color: #94a3b8; margin-top: 4px; }

/* URL 动态行 */
.url-rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.url-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.url-row-label {
  flex-shrink: 0;
  width: 92px;
  font-size: 12px;
  color: #64748b;
  font-weight: 500;
}
.url-row :deep(.el-input) { flex: 1; }
.url-row :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px #cbd5e1 inset;
  transition: box-shadow 0.2s;
}
.url-row :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 2px #fb923c inset !important;
}
.url-row.has-error :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px #ef4444 inset !important;
  background: #fef2f2;
}
.url-row-del {
  flex-shrink: 0;
  color: #94a3b8 !important;
}
.url-row-del:hover:not(.is-disabled) {
  color: #ef4444 !important;
  border-color: #fecaca !important;
}

.url-row-actions {
  margin-top: 12px;
  display: flex;
  align-items: center;
  gap: 12px;
}
.url-counter {
  margin-top: 0 !important;
}

.submit-row {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
.submit-row :deep(.el-button) {
  background: linear-gradient(135deg, #fb923c, #f59e0b) !important;
  border: none !important;
  font-weight: 600 !important;
  padding: 0 28px !important;
  height: 42px;
}
.submit-row :deep(.el-button.is-disabled) {
  opacity: 0.55;
}

/* 结果卡片网格 */
.result-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 14px;
}

.file-card {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.file-card.status-done.status-done { /* 占位优先级 */ }
.file-card.status-error {
  background: #fef2f2;
  border-color: #fecaca;
}

.fc-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.fc-name {
  flex: 1;
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fc-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #f59e0b;
  padding: 8px 0;
}
.spin { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }

.fc-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.verdict {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  font-weight: 600;
  font-size: 14px;
}
.verdict.pass, .verdict.verdict-match { background: #ecfdf5; color: #065f46; }
.verdict.pass :deep(.el-icon), .verdict.verdict-match :deep(.el-icon) { color: #10b981; font-size: 20px; }
.verdict.verdict-partial { background: #fffbeb; color: #92400e; }
.verdict.verdict-partial :deep(.el-icon) { color: #f59e0b; font-size: 20px; }
.verdict.fail, .verdict.verdict-mismatch { background: #f1f5f9; color: #475569; }
.verdict.fail :deep(.el-icon), .verdict.verdict-mismatch :deep(.el-icon) { color: #94a3b8; font-size: 20px; }
.verdict-score {
  margin-left: auto;
  font-size: 12px;
  font-family: 'JetBrains Mono', 'Consolas', monospace;
  font-weight: 600;
  opacity: 0.85;
}

.confidence-track {
  height: 5px;
  background: #e2e8f0;
  border-radius: 3px;
  overflow: hidden;
}
.confidence-fill {
  height: 100%;
  background: linear-gradient(90deg, #fb923c, #f59e0b);
  border-radius: 3px;
  transition: width 0.5s ease;
}

.meta-row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.section-title {
  font-size: 11px;
  font-weight: 700;
  color: #f59e0b;
  letter-spacing: 1px;
  margin-bottom: 6px;
  text-transform: uppercase;
}

.reason-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: #1e293b;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px 12px;
}

.key-points ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.key-points li {
  position: relative;
  padding: 5px 10px 5px 20px;
  font-size: 12px;
  color: #475569;
  background: #fff;
  border-radius: 6px;
  line-height: 1.6;
}
.key-points li::before {
  content: '▸';
  position: absolute;
  left: 8px;
  color: #f59e0b;
  font-weight: 700;
}

.fc-error {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #b91c1c;
  padding: 8px 0;
}

/* 空状态 */
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px;
  text-align: center;
  color: #94a3b8;
  gap: 10px;
}
.empty :deep(.el-icon) { color: #cbd5e1; }
.empty-title { font-size: 14px; color: #475569; margin: 0; font-weight: 500; }
.empty-sub { font-size: 12px; color: #94a3b8; margin: 0; }
</style>
