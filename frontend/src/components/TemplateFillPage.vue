<template>
  <div class="template-fill-page">
    <div class="page-header">
      <el-button class="back-btn" @click="emit('back')" size="default">
        <el-icon style="margin-right: 4px"><ArrowLeft /></el-icon>
        返回
      </el-button>
      <div class="page-title">
        <span class="title-indicator"></span>
        填写：{{ template?.name || '加载中...' }}
      </div>
      <div class="header-right">
        <el-button
          type="primary"
          :loading="generating"
          :disabled="loading"
          class="generate-btn"
          @click="handleGenerate"
        >
          <el-icon style="margin-right: 4px"><Download /></el-icon>
          生成 PDF 下载
        </el-button>
      </div>
    </div>

    <div class="page-content">
      <div v-if="loading" class="loading-state">
        <el-icon class="is-loading" :size="28"><Loading /></el-icon>
        <p>加载模板中...</p>
      </div>

      <div v-else class="fill-layout">
        <!-- 左：客户选择 + 模板信息 -->
        <div class="left-side">
          <div class="card">
            <div class="card-header">
              <el-icon><User /></el-icon>
              <span>选择已有客户（可选）</span>
            </div>
            <div class="card-body">
              <el-select
                v-model="selectedClientId"
                filterable
                clearable
                placeholder="输入姓名或证件号搜索"
                @change="onClientChange"
                :loading="matching"
                style="width: 100%;"
              >
                <el-option
                  v-for="c in clients"
                  :key="c.id"
                  :label="`${c.name}${c.id_number ? ' · ' + c.id_number : ''}`"
                  :value="c.id"
                />
              </el-select>
              <div v-if="fromCache" class="cache-tip">
                <el-icon><InfoFilled /></el-icon>
                <span>命中历史缓存，已跳过 AI 匹配</span>
              </div>
            </div>
          </div>

          <div class="card template-info">
            <div class="card-header">
              <el-icon><Document /></el-icon>
              <span>模板信息</span>
            </div>
            <div class="card-body">
              <div class="info-row"><span class="k">名称</span><span class="v">{{ template.name }}</span></div>
              <div v-if="template.filename" class="info-row"><span class="k">原文件</span><span class="v" :title="template.filename">{{ truncate(template.filename, 18) }}</span></div>
              <div class="info-row"><span class="k">占位符</span><span class="v">{{ placeholders.length }} 个</span></div>
              <div class="info-row">
                <span class="k">已填</span>
                <span class="v">{{ filledCount }} / {{ placeholders.length }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- 中：文档预览 -->
        <div class="center-preview">
          <div class="preview-title">
            <span>文档预览</span>
            <el-radio-group v-model="previewMode" size="small" class="preview-toggle">
              <el-radio-button label="word">Word 原貌</el-radio-button>
              <el-radio-button label="html">可编辑结构</el-radio-button>
            </el-radio-group>
          </div>
          <div class="preview-scroll">
            <template v-if="previewMode === 'word'">
              <div v-if="pages.length" class="pages-stack">
                <div v-for="(src, i) in pages" :key="i" class="page-frame">
                  <img :src="src" :alt="`第 ${i + 1} 页`" class="page-img" />
                </div>
              </div>
              <div v-else class="page-fallback">
                <el-icon :size="36"><Picture /></el-icon>
                <p class="fb-title">Word 原貌渲染不可用</p>
                <p class="fb-sub">请确认 LibreOffice 已安装并添加到 PATH</p>
                <p class="fb-sub">已自动切换到"可编辑结构"视图</p>
              </div>
            </template>
            <div v-else class="preview-body" v-html="previewHtml"></div>
          </div>
        </div>

        <!-- 右：占位符表单 -->
        <div class="right-form">
          <div class="card-header">
            <el-icon><EditPen /></el-icon>
            <span>占位符填写</span>
          </div>
          <div class="form-scroll">
            <div
              v-for="(ph, idx) in placeholders"
              :key="ph.id"
              class="form-row"
              :class="{
                'unmatched': unmatchedIds.has(ph.id),
                'filled': !!values[ph.id],
                'has-hint': !!ph.field_hint,
                'hit-hint': matchedHints.has(ph.field_hint),
                'locked': isLocked(ph)
              }"
            >
              <div class="form-id">str{{ idx + 1 }}</div>
              <div class="form-desc">{{ ph.description || '（未描述）' }}</div>
              <div class="form-meta">
                <el-tag size="small" effect="plain" :type="kindTagType(ph)">{{ kindLabel(ph) }}</el-tag>
                <el-tag v-if="ph.field_hint" size="small" effect="plain" type="success" :title="'字段：' + ph.field_hint">
                  {{ fieldHintLabel(ph.field_hint) }}
                </el-tag>
                <el-tag v-if="isLocked(ph)" size="small" effect="dark" type="info" title="该字段为法定核心信息，请到客户档案修改">
                  <el-icon style="vertical-align: -2px; margin-right: 2px"><Lock /></el-icon>
                  锁定
                </el-tag>
              </div>
              <el-input
                v-model="values[ph.id]"
                size="small"
                :placeholder="ph.description || '填写替换值'"
                :readonly="isLocked(ph)"
                :class="{ 'locked-input': isLocked(ph) }"
                @input="onValueInput"
              />
            </div>
            <div v-if="placeholders.length === 0" class="empty-form">
              此模板无占位符
            </div>
          </div>
          <!-- B1：反向同步主数据 -->
          <div v-if="selectedClientId" class="form-footer">
            <el-checkbox v-model="syncToClient" size="small">
              生成后把可变字段同步到客户档案
            </el-checkbox>
            <div class="footer-tip">
              <el-icon><InfoFilled /></el-icon>
              锁定字段（{{ lockedFieldsCount }} 项）不会同步，仅未锁定字段会写回。
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, reactive } from 'vue'
import {
  ArrowLeft, Download, Loading, User, Document, EditPen, InfoFilled, Picture, Lock,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getTemplatePreviewHtml, getTemplatePreviewPages, listClients, mapClientToTemplate,
  generateTemplatePdf, FIELD_DICTIONARY_OPTIONS, LOCKED_FIELD_HINTS, upsertClientInfo,
} from '../api.js'

const props = defineProps({
  templateId: { type: [Number, String], required: true }
})
const emit = defineEmits(['back'])

const loading = ref(true)
const generating = ref(false)
const matching = ref(false)
const fromCache = ref(false)

const template = ref(null)
const rawHtml = ref('')
const pages = ref([])                    // Word 原貌 PNG URL 列表
const previewMode = ref('word')          // 'word' | 'html'
const placeholders = ref([])
const values = reactive({})              // {strN: value}
const unmatchedIds = ref(new Set())

const clients = ref([])
const selectedClientId = ref(null)
const matchedHints = ref(new Set())  // 客户匹配命中的 field_hint 集合（用于绿标）

// B1：是否把可变字段反向同步到客户档案（生成时一并提交）
const syncToClient = ref(true)

const fieldHintMap = Object.fromEntries(
  FIELD_DICTIONARY_OPTIONS.map(o => [o.value, o.label])
)

function fieldHintLabel(h) {
  return fieldHintMap[h] || h
}

/** B1：判断 anchor 是否锁定（field_hint 在 LOCKED_FIELD_HINTS 中）。 */
function isLocked(ph) {
  return !!ph?.field_hint && LOCKED_FIELD_HINTS.has(ph.field_hint)
}

function kindLabel(ph) {
  const k = ph?.anchor?.kind
  if (k === 'cell') return '空单元格'
  if (k === 'run') return '下划线'
  if (k === 'paragraph') return '段落'
  return '?'
}

function kindTagType(ph) {
  const k = ph?.anchor?.kind
  if (k === 'cell') return 'warning'
  if (k === 'run') return 'info'
  return ''
}

const filledCount = computed(() =>
  placeholders.value.filter(p => values[p.id]).length
)

/** B1：模板里有 field_hint 命中锁定字段的数量。 */
const lockedFieldsCount = computed(() =>
  placeholders.value.filter(isLocked).length
)

// 预览 HTML：把每个占位符的 original_text 替换为值或高亮未填
const previewHtml = computed(() => {
  let html = rawHtml.value || ''
  // 按原文长度降序，避免短串被长串内的子串先替换
  const sorted = [...placeholders.value].sort(
    (a, b) => (b.original_text || '').length - (a.original_text || '').length
  )
  for (const ph of sorted) {
    if (!ph.original_text) continue
    const v = values[ph.id]
    let replacement
    if (v) {
      replacement = `<span class="pv-filled" title="str${placeholders.value.indexOf(ph)+1}">${escapeHtml(v)}</span>`
    } else {
      replacement = `<span class="pv-empty" title="${escapeHtml(ph.description || '')}">str${placeholders.value.indexOf(ph)+1}</span>`
    }
    // mammoth 把 < > 转义为 &lt; &gt;，原文中含 < > 的占位符要按转义后形式匹配
    const needle = escapeHtml(ph.original_text)
    html = html.split(needle).join(replacement)
  }
  return html
})

function escapeHtml(s) {
  return String(s || '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]))
}

function truncate(s, n) {
  s = String(s || '')
  return s.length > n ? s.slice(0, n) + '…' : s
}

function onValueInput() {
  /* 预览通过 computed 自动更新 */
}

async function loadTemplate() {
  loading.value = true
  try {
    const data = await getTemplatePreviewHtml(props.templateId)
    template.value = { name: data.name, filename: data.filename }
    rawHtml.value = data.html || ''
    placeholders.value = data.placeholders || []
    // 初始化值
    for (const p of placeholders.value) {
      if (!(p.id in values)) values[p.id] = ''
    }
  } catch (err) {
    ElMessage.error('加载模板失败：' + (err.response?.data?.detail || err.message))
  } finally {
    loading.value = false
  }
}

async function loadPages() {
  try {
    const data = await getTemplatePreviewPages(props.templateId)
    pages.value = data.pages || []
    if (pages.value.length === 0) {
      previewMode.value = 'html'
    }
  } catch (err) {
    // 后端降级 503 时不弹错（HTML 仍可看），仅打 console
    console.warn('Word 原貌渲染失败，降级为 HTML 视图', err)
    pages.value = []
    previewMode.value = 'html'
  }
}

async function loadClients() {
  try {
    const data = await listClients()
    clients.value = data.clients || []
  } catch (err) {
    console.warn('客户列表加载失败', err)
  }
}

async function onClientChange(clientId) {
  unmatchedIds.value = new Set()
  matchedHints.value = new Set()
  fromCache.value = false
  if (!clientId) {
    // 清空所有值
    for (const p of placeholders.value) values[p.id] = ''
    return
  }
  matching.value = true
  try {
    const result = await mapClientToTemplate(props.templateId, clientId)
    const matched = result.matched || {}
    const unmatched = result.unmatched || []
    for (const p of placeholders.value) {
      if (matched[p.id]) values[p.id] = matched[p.id]
    }
    unmatchedIds.value = new Set(unmatched)
    fromCache.value = !!result.from_cache
    // 记录匹配的 field_hint，用于绿标
    const hitHints = new Set()
    for (const p of placeholders.value) {
      if (matched[p.id] && p.field_hint) hitHints.add(p.field_hint)
    }
    matchedHints.value = hitHints
    if (Object.keys(matched).length) {
      ElMessage.success(`已自动填充 ${Object.keys(matched).length} 个占位符`)
    } else {
      ElMessage.info('未匹配到任何客户信息')
    }
  } catch (err) {
    ElMessage.error('客户匹配失败：' + (err.response?.data?.detail || err.message))
  } finally {
    matching.value = false
  }
}

async function handleGenerate() {
  if (!template.value) return
  // 未填二次确认：避免用户漏填后默默生成大量空白
  if (filledCount.value < placeholders.value.length) {
    const remaining = placeholders.value.length - filledCount.value
    try {
      await ElMessageBox.confirm(
        `还有 ${remaining} 个占位符未填写，仍要继续生成吗？未填项会变为空白。`,
        '确认生成',
        {
          type: 'warning',
          confirmButtonText: '继续生成',
          cancelButtonText: '回去填写',
        }
      )
    } catch {
      return
    }
  }
  const anchor_values = {}
  for (const p of placeholders.value) {
    anchor_values[p.id] = values[p.id] || ''
  }
  generating.value = true
  try {
    const { filename, isFallback } = await generateTemplatePdf(props.templateId, {
      client_id: selectedClientId.value,
      anchor_values,
    })
    if (isFallback) {
      ElMessage.warning('PDF 转换失败，已降级下载 docx 文件')
    } else {
      ElMessage.success(`已下载：${filename}`)
    }

    // B1：把未锁定字段反向同步到客户档案
    if (selectedClientId.value && syncToClient.value) {
      const keyValues = {}
      for (const p of placeholders.value) {
        if (isLocked(p)) continue                     // 锁定字段不同步
        if (!p.field_hint) continue                   // 没有明确字段映射的不同步（避免乱写）
        const v = values[p.id]
        if (!v || !String(v).trim()) continue
        // 用字段字典中的中文 label 作为 info_key，与人工复核归档保持一致
        const label = fieldHintMap[p.field_hint] || p.field_hint
        keyValues[label] = String(v).trim()
      }
      if (Object.keys(keyValues).length) {
        try {
          const res = await upsertClientInfo(selectedClientId.value, keyValues)
          if (res.updated > 0) {
            ElMessage.success(`已同步 ${res.updated} 项到客户档案`)
          }
        } catch (err) {
          ElMessage.warning('档案同步失败：' + (err.response?.data?.detail || err.message))
        }
      }
    }
  } catch (err) {
    ElMessage.error('生成失败：' + (err.response?.data?.detail || err.message))
  } finally {
    generating.value = false
  }
}

onMounted(() => {
  loadTemplate()
  loadPages()
  loadClients()
})
</script>

<style scoped>
.template-fill-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #f0f2f8;
  overflow: hidden;
}
.page-header {
  padding: 16px 28px;
  background: #ffffff;
  display: flex;
  align-items: center;
  gap: 16px;
  flex-shrink: 0;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
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
.header-right { margin-left: auto; }
.generate-btn {
  background: linear-gradient(135deg, #6366f1, #8b5cf6) !important;
  border: none !important;
}

.page-content {
  flex: 1;
  overflow: hidden;
  padding: 18px;
}
.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 20px;
  color: #94a3b8;
}
.fill-layout {
  display: grid;
  grid-template-columns: 260px 1fr 340px;
  gap: 14px;
  height: 100%;
}

/* 左 */
.left-side {
  display: flex;
  flex-direction: column;
  gap: 12px;
  overflow-y: auto;
}
.card {
  background: #fff;
  border-radius: 10px;
  border: 1px solid #e2e8f0;
  overflow: hidden;
}
.card-header {
  padding: 10px 14px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
  display: flex;
  align-items: center;
  gap: 6px;
}
.card-body { padding: 12px 14px; }
.cache-tip {
  margin-top: 8px;
  font-size: 11px;
  color: #10b981;
  display: flex;
  align-items: center;
  gap: 4px;
}
.template-info .info-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  padding: 4px 0;
  color: #475569;
}
.template-info .info-row .k { color: #94a3b8; }
.template-info .info-row .v {
  color: #1e293b;
  font-weight: 500;
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 60%;
}

/* 中 */
.center-preview {
  background: #fff;
  border-radius: 10px;
  border: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
}
.preview-title {
  padding: 8px 14px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
  font-size: 13px;
  color: #475569;
  font-weight: 500;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.preview-toggle { flex-shrink: 0; }
.preview-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 24px 32px;
  background: #fafbff;
}
.preview-body {
  color: #1e293b;
  font-size: 14px;
  line-height: 1.7;
  font-family: 'Times New Roman', 'Source Han Serif SC', 'SimSun', serif;
}

/* mammoth 深度优化样式（与 TemplateUploadDialog 保持一致） */
.preview-body :deep(p) { margin: 0 0 10px; line-height: 1.75; }
.preview-body :deep(p.normal),
.preview-body :deep(p.body-text) { margin: 0 0 8px; }
.preview-body :deep(.doc-title) {
  font-size: 22px; font-weight: 700; text-align: center;
  margin: 8px 0 14px; color: #0f172a;
}
.preview-body :deep(.doc-subtitle) {
  font-size: 15px; text-align: center; color: #475569; margin: 0 0 18px;
}
.preview-body :deep(h1) { font-size: 20px; margin: 18px 0 10px; font-weight: 700; color: #0f172a; border-bottom: 1px solid #e2e8f0; padding-bottom: 4px; }
.preview-body :deep(h2) { font-size: 17px; margin: 16px 0 8px; font-weight: 700; color: #1e293b; }
.preview-body :deep(h3) { font-size: 15px; margin: 14px 0 6px; font-weight: 700; color: #334155; }
.preview-body :deep(h4), .preview-body :deep(h5), .preview-body :deep(h6) { font-size: 14px; margin: 12px 0 6px; font-weight: 700; color: #475569; }
.preview-body :deep(strong) { font-weight: 700; color: #0f172a; }
.preview-body :deep(em) { font-style: italic; }
.preview-body :deep(ul), .preview-body :deep(ol) { padding-left: 28px; margin: 6px 0 10px; }
.preview-body :deep(li) { margin: 2px 0; line-height: 1.7; }
.preview-body :deep(blockquote) {
  border-left: 3px solid #cbd5e1; padding: 4px 14px;
  margin: 8px 0; color: #475569; background: #f8fafc;
}
.preview-body :deep(table) {
  border-collapse: collapse; width: 100%;
  margin: 10px 0; background: #fff;
  table-layout: auto; font-size: 13px;
}
.preview-body :deep(table caption) { font-size: 12px; color: #64748b; margin-bottom: 6px; caption-side: top; text-align: center; }
.preview-body :deep(td), .preview-body :deep(th) {
  border: 1px solid #94a3b8; padding: 6px 10px;
  vertical-align: middle; min-width: 56px;
  text-align: left; line-height: 1.55;
  word-break: break-word; background: #fff;
}
.preview-body :deep(th) { background: #f1f5f9; font-weight: 600; color: #1e293b; }
.preview-body :deep(td[colspan]), .preview-body :deep(th[colspan]) { text-align: center; }
.preview-body :deep(td[rowspan]), .preview-body :deep(th[rowspan]) { vertical-align: middle; }
.preview-body :deep(table.table-grid),
.preview-body :deep(table.table-with-borders) { border: 1px solid #64748b; }
.preview-body :deep(img) { max-width: 100%; height: auto; display: inline-block; margin: 4px 0; }

.preview-body :deep(.pv-filled) {
  background: #dbeafe;
  color: #1e40af;
  padding: 1px 6px;
  border-radius: 4px;
  border-bottom: 1px solid #3b82f6;
}
.preview-body :deep(.pv-empty) {
  background: #fef3c7;
  color: #92400e;
  padding: 1px 6px;
  border-radius: 4px;
  border-bottom: 1px dashed #f59e0b;
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
}

/* Word 原貌：图片栈 */
.pages-stack {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 18px;
  padding: 4px 0;
}
.page-frame {
  background: #fff;
  box-shadow: 0 2px 14px rgba(15, 23, 42, 0.12);
  border-radius: 2px;
  max-width: 720px;
  width: 100%;
  overflow: hidden;
}
.page-img {
  display: block;
  width: 100%;
  height: auto;
  user-select: none;
}
.page-fallback {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #94a3b8;
  text-align: center;
  padding: 40px 20px;
}
.page-fallback .fb-title {
  margin: 6px 0 0;
  font-size: 14px;
  color: #475569;
  font-weight: 600;
}
.page-fallback .fb-sub {
  margin: 0;
  font-size: 12px;
  color: #94a3b8;
}

/* 右 */
.right-form {
  background: #fff;
  border-radius: 10px;
  border: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.form-scroll {
  flex: 1;
  overflow-y: auto;
}
.form-row {
  padding: 10px 14px;
  border-bottom: 1px solid #f1f5f9;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.form-row.unmatched { background: #fffbeb; }
.form-row.filled .form-id { color: #10b981; }
.form-row.locked { background: #f8fafc; }
.form-row.locked .form-desc::after {
  content: '（核心字段，不可在此修改）';
  font-size: 11px;
  color: #94a3b8;
  margin-left: 4px;
  font-weight: 400;
}
.locked-input :deep(.el-input__wrapper) {
  background: #f1f5f9 !important;
  cursor: not-allowed;
  box-shadow: 0 0 0 1px #cbd5e1 inset !important;
}
.locked-input :deep(.el-input__inner) {
  color: #64748b !important;
  cursor: not-allowed;
}
.form-meta {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  margin-top: 2px;
}
.form-footer {
  flex-shrink: 0;
  border-top: 1px solid #e2e8f0;
  padding: 12px 14px;
  background: #f8fafc;
}
.footer-tip {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 4px;
  display: flex;
  align-items: center;
  gap: 4px;
}
.form-id {
  font-family: 'JetBrains Mono', monospace;
  color: #6366f1;
  font-size: 12px;
  font-weight: 600;
}
.form-desc {
  font-size: 13px;
  color: #1e293b;
  font-weight: 500;
}
.form-ot {
  font-size: 11px;
  color: #94a3b8;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.empty-form {
  padding: 28px 14px;
  color: #94a3b8;
  text-align: center;
}
</style>
