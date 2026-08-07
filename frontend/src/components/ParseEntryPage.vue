<template>
  <div class="parse-entry-page">
    <!-- 顶栏:标题 + 唯一上传按钮 -->
    <div class="entry-header">
      <div class="entry-title">
        <span class="title-indicator"></span>
        材料解析
      </div>
      <div class="entry-actions">
        <el-upload
          :auto-upload="false"
          :show-file-list="false"
          :multiple="false"
          :accept="ACCEPT"
          :on-change="handleFileSelect"
        >
          <el-button size="small" class="upload-btn" :loading="loading">
            <el-icon style="margin-right: 4px"><Upload /></el-icon>
            上传文件
          </el-button>
        </el-upload>
      </div>
    </div>

    <!-- 主区 -->
    <div class="main-area">
      <!-- 空态 -->
      <div v-if="!loading && !result" class="empty-hint">
        <el-icon :size="48" style="margin-bottom: 12px"><Document /></el-icon>
        <span>点击右上角「上传文件」识别（支持 PDF / Office / 图片）</span>
      </div>

      <!-- 识别中 -->
      <div v-else-if="loading" class="empty-hint">
        <el-icon class="is-loading" :size="36" style="margin-bottom: 12px"><Loading /></el-icon>
        <span>正在识别 {{ currentName }}，请稍候…</span>
      </div>

      <!-- 结果:左图右文 -->
      <div v-else class="result-container">
        <div class="left-panel">
          <div class="panel-header">
            <span class="header-indicator"></span>
            文件预览
            <span class="file-name" :title="result.filename">{{ result.filename }}</span>
          </div>
          <div class="images-content">
            <template v-if="result.pages && result.pages.length">
              <div v-for="p in result.pages" :key="p.page" class="page-block">
                <div class="page-title">第 {{ p.page }} 页</div>
                <el-image
                  :src="imageUrl(p.image)"
                  :preview-src-list="previewList"
                  :initial-index="p.page - 1"
                  fit="contain"
                  class="page-image"
                  preview-teleported
                />
              </div>
            </template>
            <div v-else class="empty-hint"><span>该文件无可预览图片</span></div>
          </div>
        </div>

        <div class="right-panel">
          <div class="panel-header">
            <span class="header-indicator"></span>
            OCR 识别文字
          </div>
          <div class="ocr-text-content">
            <pre class="ocr-text">{{ result.text || '(未识别到文字)' }}</pre>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
defineOptions({ name: 'ParseEntryPage' })
import { ref, computed } from 'vue'
import { Upload, Document, Loading } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { ocrParse } from '../api.js'

// 与文件留底检测同口径的扩展名
const ACCEPT = '.pdf,.doc,.docx,.xls,.xlsx,.pptx,.png,.jpg,.jpeg,.bmp,.tiff,.webp,.gif'

const loading = ref(false)
const result = ref(null)
const currentName = ref('')

const previewList = computed(() =>
  (result.value?.pages || []).map(p => imageUrl(p.image))
)

function imageUrl(rel) {
  return `/uploads/${rel}`
}

async function handleFileSelect(file) {
  const raw = file?.raw
  if (!raw) return
  loading.value = true
  result.value = null
  currentName.value = raw.name
  try {
    result.value = await ocrParse(raw)
  } catch (err) {
    ElMessage.error('识别失败：' + (err.response?.data?.detail || err.message))
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.parse-entry-page { height: 100%; display: flex; flex-direction: column; background: #f0f2f8; color: #1e293b; }

.entry-header {
  padding: 0 24px;
  height: 56px;
  background: #fff;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
  border-bottom: 1px solid #e8ebf5;
}
.entry-title { font-size: 16px; font-weight: 700; color: #1e293b; display: flex; align-items: center; gap: 10px; }
.title-indicator { width: 3px; height: 16px; background: linear-gradient(180deg, #6366f1, #8b5cf6); border-radius: 2px; }
.entry-actions { margin-left: auto; display: flex; align-items: center; gap: 8px; }
.upload-btn {
  background: linear-gradient(135deg, #6366f1, #8b5cf6) !important;
  border: none !important;
  color: #fff !important;
  font-weight: 500 !important;
  border-radius: 8px !important;
  padding: 8px 18px !important;
}
.upload-btn:hover { box-shadow: 0 4px 14px rgba(99,102,241,0.4) !important; transform: translateY(-1px); }

.main-area { flex: 1; display: flex; flex-direction: column; overflow: hidden; }

.empty-hint {
  flex: 1;
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  color: #94a3b8; font-size: 13px;
}

.result-container { flex: 1; display: flex; gap: 12px; padding: 12px; overflow: hidden; }
.left-panel, .right-panel {
  flex: 1; min-width: 0; display: flex; flex-direction: column; overflow: hidden;
  background: #fff; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
.panel-header { padding: 12px 16px; font-size: 13px; font-weight: 600; color: #1e293b; display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.header-indicator { width: 3px; height: 14px; background: linear-gradient(180deg, #6366f1, #8b5cf6); border-radius: 2px; }
.file-name { font-weight: 400; color: #94a3b8; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* scrollbar-gutter: stable 常驻滚动条槽位,滚动条出现/消失不再挤压图片宽度(消除悬停/加载时的左右抖动) */
.images-content { flex: 1; overflow-y: auto; padding: 12px; scrollbar-gutter: stable; }
.page-block { margin-bottom: 14px; }
.page-title { font-size: 11px; color: #6366f1; font-weight: 600; margin-bottom: 6px; padding-bottom: 4px; border-bottom: 1px dashed #e2e8f0; }
/* el-image 默认 inline-block,基线间隙会多出几px 高度;改 block 去掉 */
.page-image { display: block; width: 100%; border-radius: 6px; background: #f8fafc; }
.page-image :deep(img) { display: block; }

.ocr-text-content { flex: 1; overflow-y: auto; padding: 12px; }
.ocr-text { font-size: 12px; line-height: 1.7; color: #475569; white-space: pre-wrap; word-break: break-all; font-family: 'JetBrains Mono','Consolas',monospace; margin: 0; background: #f8fafc; padding: 8px 10px; border-radius: 6px; }
</style>
