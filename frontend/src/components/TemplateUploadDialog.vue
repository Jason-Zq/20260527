<template>
  <el-dialog
    :model-value="visible"
    @update:model-value="(v) => emit('update:visible', v)"
    title="上传 Word 模板（v2 anchor 编辑）"
    width="960px"
    top="5vh"
    :close-on-click-modal="false"
    @close="reset"
  >
    <!-- Step 1：选文件 -->
    <div v-if="step === 1" class="step-content">
      <el-upload
        ref="uploadRef"
        :auto-upload="false"
        :show-file-list="false"
        accept=".docx"
        :on-change="handleFileSelect"
        drag
      >
        <el-icon class="el-icon--upload" :size="42"><UploadFilled /></el-icon>
        <div class="el-upload__text">
          点击或拖拽 <em>.docx</em> 文件到此处
        </div>
        <template #tip>
          <div class="upload-tip">
            上传后系统会用 AI 自动识别可填位置（空单元格 + 下划线 + LLM 描述）。
            你可在右侧编辑每个位置的描述和字段类型，也可以直接快速保存。
          </div>
        </template>
      </el-upload>
      <div v-if="parsing" class="parsing-tip">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span>正在解析模板（结构扫描 + AI 描述）...</span>
      </div>
    </div>

    <!-- Step 2：编辑 anchor 列表 -->
    <div v-else-if="step === 2" class="editor">
      <div class="editor-left">
        <div class="toolbar">
          <el-radio-group v-model="previewMode" size="small">
            <el-radio-button label="word">Word 原貌</el-radio-button>
            <el-radio-button label="html">可编辑结构</el-radio-button>
          </el-radio-group>
        </div>
        <div class="doc-preview">
          <template v-if="previewMode === 'word'">
            <div v-if="pages.length" class="pages-stack">
              <div v-for="(src, i) in pages" :key="i" class="page-frame">
                <img :src="src" :alt="`第 ${i + 1} 页`" class="page-img" />
              </div>
            </div>
            <div v-else class="page-fallback">
              <el-icon :size="36"><Picture /></el-icon>
              <p class="fb-title">Word 原貌渲染不可用</p>
              <p class="fb-sub">已自动切换到"可编辑结构"视图</p>
            </div>
          </template>
          <div v-else class="doc-html" v-html="renderedHtml"></div>
        </div>
      </div>

      <div class="editor-right">
        <div class="right-header">
          <span>待填位置（{{ anchors.length }}）</span>
          <el-tag size="small" type="success" v-if="autoTagged > 0">
            AI 自动识别 {{ autoTagged }} 个
          </el-tag>
        </div>
        <div v-if="anchors.length === 0" class="empty-anchor">
          <p>未检测到任何可填位置</p>
          <p class="sub">可能是模板本身没有空单元格 / 下划线占位。</p>
        </div>
        <div v-for="(a, idx) in anchors" :key="a.id" class="anchor-item">
          <div class="anchor-id">{{ a.id }}</div>
          <div class="anchor-info">
            <div class="anchor-label" :title="a.label_context">
              位置：{{ truncate(a.label_context, 24) || '（未识别）' }}
              <el-tag size="small" effect="plain" :type="kindTagType(a)">{{ kindLabel(a) }}</el-tag>
            </div>
            <el-input
              v-model="a.description"
              size="small"
              placeholder="描述（让 AI 知道这里要填什么）"
              class="anchor-desc"
            />
            <el-select
              v-model="a.field_hint"
              size="small"
              placeholder="（不指定）"
              class="anchor-hint"
              clearable
            >
              <el-option
                v-for="opt in fieldOptions"
                :key="opt.value"
                :label="opt.label"
                :value="opt.value"
              />
            </el-select>
          </div>
          <el-button size="small" text type="danger" @click="removeAnchor(idx)">
            <el-icon><Delete /></el-icon>
          </el-button>
        </div>
      </div>
    </div>

    <template #footer>
      <el-button @click="emit('update:visible', false)">取消</el-button>
      <el-button v-if="step === 2" @click="step = 1">重新选择文件</el-button>
      <el-button v-if="step === 2" type="info" plain :loading="saving" :disabled="anchors.length === 0" @click="handleQuickSave">
        <el-icon><MagicStick /></el-icon>
        快速保存（{{ anchors.length }}）
      </el-button>
      <el-button v-if="step === 2" type="primary" :loading="saving" :disabled="anchors.length === 0" @click="handleSave">
        保存模板
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, nextTick, watch } from 'vue'
import { UploadFilled, Loading, Delete, Picture, MagicStick } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  parseTemplate, saveTemplate, quickSaveTemplate, FIELD_DICTIONARY_OPTIONS
} from '../api.js'

const props = defineProps({
  visible: { type: Boolean, default: false }
})
const emit = defineEmits(['update:visible', 'saved'])

const step = ref(1)
const parsing = ref(false)
const saving = ref(false)
const rawHtml = ref('')
const pages = ref([])
const previewMode = ref('word')
const anchors = ref([])         // v2: [{id, anchor, label_context, description, field_hint, default_fmt}]
const templateName = ref('')
const filename = ref('')
const tempToken = ref('')

const fieldOptions = FIELD_DICTIONARY_OPTIONS

// 统计 AI 自动识别的（field_hint 有值的）
const autoTagged = computed(() => anchors.value.filter(a => a.field_hint).length)

watch(previewMode, (m) => {
  if (m !== 'word') {
    const sel = window.getSelection()
    if (sel) sel.removeAllRanges()
  }
})

const renderedHtml = computed(() => rawHtml.value || '')

function truncate(s, n) {
  s = String(s || '')
  return s.length > n ? s.slice(0, n) + '…' : s
}

function kindLabel(a) {
  const k = a?.anchor?.kind
  if (k === 'cell') return '空单元格'
  if (k === 'run') return '下划线'
  if (k === 'paragraph') return '段落'
  return '?'
}

function kindTagType(a) {
  const k = a?.anchor?.kind
  if (k === 'cell') return 'warning'
  if (k === 'run') return 'info'
  return ''
}

async function handleFileSelect(file) {
  if (!file?.raw) return
  if (!file.name.toLowerCase().endsWith('.docx')) {
    ElMessage.warning('仅支持 .docx 文件')
    return
  }
  parsing.value = true
  try {
    const data = await parseTemplate(file.raw)
    rawHtml.value = data.html || ''
    pages.value = data.pages || []
    if (pages.value.length === 0) previewMode.value = 'html'

    anchors.value = (data.anchors || []).map(a => ({
      id: a.id,
      anchor: a.anchor,
      label_context: a.label_context || '',
      description: a.description || a.label_context || '',
      field_hint: a.field_hint || '',
      default_fmt: a.default_fmt || null,
    }))
    filename.value = data.filename || file.name
    templateName.value = (filename.value || '').replace(/\.docx$/i, '')
    tempToken.value = data.temp_token

    if (anchors.value.length === 0) {
      ElMessage.warning('未检测到任何可填位置，请检查模板内容')
    }

    step.value = 2
    await nextTick()
  } catch (err) {
    ElMessage.error('解析失败：' + (err.response?.data?.detail || err.message))
  } finally {
    parsing.value = false
  }
}

function removeAnchor(idx) {
  anchors.value.splice(idx, 1)
}

async function handleSave() {
  let name = templateName.value
  if (!name || !name.trim()) {
    try {
      const res = await ElMessageBox.prompt('请输入模板名称', '保存模板', {
        inputPlaceholder: '模板名称',
        inputValidator: (v) => !!(v && v.trim()) || '模板名称不能为空',
      })
      name = (res.value || '').trim()
    } catch {
      return
    }
  }
  saving.value = true
  try {
    const payload = {
      name,
      filename: filename.value,
      anchors: anchors.value.map(a => ({
        id: a.id,
        anchor: a.anchor,
        description: a.description || '',
        field_hint: a.field_hint || null,
        default_fmt: a.default_fmt || null,
      })),
      temp_token: tempToken.value,
    }
    const result = await saveTemplate(payload)
    ElMessage.success(`模板已保存（${anchors.value.length} 个 anchor）`)
    emit('saved', result?.id)
    reset()
  } catch (err) {
    ElMessage.error('保存失败：' + (err.response?.data?.detail || err.message))
  } finally {
    saving.value = false
  }
}

async function handleQuickSave() {
  // 快速保存跳过 dialog 中用户编辑，直接以当前 anchor 列表入库
  // 但我们已经有了用户编辑后的列表，所以直接调用 save_template 而不是 quick_save_template
  // 因为 quick_save_template 用的是 parse 阶段缓存，用户在 dialog 里编辑过的要生效
  await handleSave()
}

function reset() {
  step.value = 1
  rawHtml.value = ''
  pages.value = []
  previewMode.value = 'word'
  anchors.value = []
  templateName.value = ''
  filename.value = ''
  tempToken.value = ''
}
</script>

<style scoped>
.step-content { min-height: 200px; }

.upload-tip {
  font-size: 12px;
  color: #64748b;
  line-height: 1.7;
  text-align: left;
  padding: 8px 4px 0;
}
.upload-tip code {
  background: #f1f5f9;
  padding: 1px 6px;
  border-radius: 4px;
  font-family: 'JetBrains Mono', 'Consolas', monospace;
  color: #6366f1;
}

.parsing-tip {
  margin-top: 18px;
  color: #6366f1;
  display: flex;
  align-items: center;
  gap: 6px;
  justify-content: center;
  font-size: 13px;
}

/* Step 2: 编辑器 */
.editor {
  display: grid;
  grid-template-columns: 1fr 380px;
  gap: 14px;
  height: 62vh;
  min-height: 480px;
}

.editor-left {
  display: flex;
  flex-direction: column;
  min-width: 0;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #fff;
  overflow: hidden;
}
.toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid #e2e8f0;
  background: #f8fafc;
}
.doc-preview {
  flex: 1;
  overflow-y: auto;
  padding: 24px 32px;
  color: #1e293b;
  line-height: 1.7;
  font-size: 14px;
  background: #fafbff;
}
.pages-stack { display: flex; flex-direction: column; align-items: center; gap: 18px; }
.page-frame {
  background: #fff;
  box-shadow: 0 2px 14px rgba(15, 23, 42, 0.12);
  border-radius: 2px;
  max-width: 720px;
  width: 100%;
  overflow: hidden;
}
.page-img { display: block; width: 100%; height: auto; }
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
.fb-title { margin: 6px 0 0; font-size: 14px; color: #475569; font-weight: 600; }
.fb-sub { margin: 0; font-size: 12px; color: #94a3b8; }
.doc-html { min-height: 100%; }

/* mammoth 样式简版 */
.doc-html :deep(p) { margin: 0 0 10px; line-height: 1.75; }
.doc-html :deep(table) {
  border-collapse: collapse; width: 100%;
  margin: 10px 0; background: #fff;
  font-size: 13px;
}
.doc-html :deep(td), .doc-html :deep(th) {
  border: 1px solid #94a3b8; padding: 6px 10px;
  vertical-align: middle; text-align: left;
  line-height: 1.55; word-break: break-word; background: #fff;
}

/* 右侧 anchor 列表 */
.editor-right {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  background: #fff;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.right-header {
  padding: 10px 14px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
  font-weight: 600;
  color: #1e293b;
  font-size: 13px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.empty-anchor {
  padding: 28px 14px;
  color: #94a3b8;
  text-align: center;
  font-size: 13px;
}
.empty-anchor .sub { font-size: 12px; color: #b8c0cc; margin-top: 8px; }
.anchor-item {
  display: grid;
  grid-template-columns: 50px 1fr auto;
  gap: 6px;
  align-items: center;
  padding: 10px 12px;
  border-bottom: 1px solid #f1f5f9;
  background: #fafbff;
}
.anchor-id {
  font-family: 'JetBrains Mono', monospace;
  color: #6366f1;
  font-size: 12px;
  font-weight: 600;
}
.anchor-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.anchor-label {
  font-size: 11px;
  color: #64748b;
  display: flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.anchor-desc :deep(.el-input__inner) { font-size: 12px; }
.anchor-hint { width: 100%; }
.anchor-hint :deep(.el-input__inner) { font-size: 12px; }
</style>