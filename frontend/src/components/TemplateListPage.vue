<template>
  <div class="template-list-page">
    <div class="page-header">
      <el-button class="back-btn" @click="emit('back')" size="default">
        <el-icon style="margin-right: 4px"><ArrowLeft /></el-icon>
        返回
      </el-button>
      <div class="page-title">
        <span class="title-indicator"></span>
        模板管理
      </div>
      <el-button type="primary" class="upload-btn" @click="uploadDialogVisible = true">
        <el-icon style="margin-right: 4px"><Upload /></el-icon>
        上传新模板
      </el-button>
    </div>

    <div class="page-content">
      <div v-if="loading" class="loading-state">
        <el-icon class="is-loading" :size="28"><Loading /></el-icon>
        <p>加载中...</p>
      </div>

      <div v-else-if="templates.length === 0" class="empty-state">
        <p class="empty-text">暂无模板</p>
        <p class="empty-hint">点击右上角"上传新模板"开始</p>
      </div>

      <div v-else class="templates-grid">
        <div class="results-summary">共 <strong>{{ templates.length }}</strong> 个模板</div>
        <div v-for="t in templates" :key="t.id" class="template-card" :class="{ legacy: t.legacy }">
          <div class="template-icon">
            <el-icon :size="22"><Document /></el-icon>
          </div>
          <div class="template-info">
            <div class="template-name">
              {{ t.name }}
              <el-tag v-if="t.legacy" size="small" type="info" effect="plain" class="legacy-tag">
                旧版不可用
              </el-tag>
            </div>
            <div class="template-meta">
              <span v-if="t.filename" class="meta-file">{{ t.filename }}</span>
              <span>{{ t.placeholder_count }} 个占位符</span>
            </div>
            <div class="template-bottom">
              <span class="update-time">创建于 {{ t.created_at }}</span>
            </div>
          </div>
          <div class="template-actions">
            <el-tooltip v-if="t.legacy" content="旧版模板不可填写，请重新上传 docx" placement="top">
              <el-button size="small" type="primary" disabled>
                <el-icon style="margin-right: 4px"><Edit /></el-icon>填写并生成
              </el-button>
            </el-tooltip>
            <el-button v-else size="small" type="primary" @click="emit('fill', t.id)">
              <el-icon style="margin-right: 4px"><Edit /></el-icon>填写并生成
            </el-button>
            <el-button size="small" type="danger" plain @click="handleDelete(t)">
              <el-icon><Delete /></el-icon>
            </el-button>
          </div>
        </div>
      </div>
    </div>

    <TemplateUploadDialog
      v-model:visible="uploadDialogVisible"
      @saved="onSaved"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ArrowLeft, Upload, Document, Edit, Delete, Loading } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listTemplates, deleteTemplate } from '../api.js'
import TemplateUploadDialog from './TemplateUploadDialog.vue'

const emit = defineEmits(['back', 'fill'])

const templates = ref([])
const loading = ref(false)
const uploadDialogVisible = ref(false)

async function loadTemplates() {
  loading.value = true
  try {
    const data = await listTemplates()
    templates.value = data.templates || []
  } catch (err) {
    ElMessage.error('加载失败：' + (err.response?.data?.detail || err.message))
  } finally {
    loading.value = false
  }
}

async function handleDelete(t) {
  try {
    await ElMessageBox.confirm(`确定删除模板「${t.name}」？删除后无法恢复。`, '确认删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消'
    })
    await deleteTemplate(t.id)
    templates.value = templates.value.filter(x => x.id !== t.id)
    ElMessage.success('已删除')
  } catch (err) {
    if (err !== 'cancel') {
      ElMessage.error('删除失败：' + (err.response?.data?.detail || err.message))
    }
  }
}

async function onSaved(newId) {
  uploadDialogVisible.value = false
  await loadTemplates()
  // 保存后引导用户立即去填写这份新模板（拿到 id 才能跳）
  if (!newId) return
  try {
    await ElMessageBox.confirm('模板已保存，是否立即填写这份模板？', '保存成功', {
      type: 'success',
      confirmButtonText: '立即填写',
      cancelButtonText: '留在列表',
    })
    emit('fill', newId)
  } catch {
    // 用户选择留在列表
  }
}

onMounted(loadTemplates)
</script>

<style scoped>
.template-list-page {
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

.upload-btn {
  margin-left: auto;
  background: linear-gradient(135deg, #6366f1, #8b5cf6) !important;
  border: none !important;
}

.page-content {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
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

.templates-grid {
  max-width: 1100px;
  margin: 0 auto;
}

.results-summary {
  font-size: 13px;
  color: #64748b;
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #e2e8f0;
}

.template-card {
  display: flex;
  gap: 14px;
  align-items: center;
  background: #ffffff;
  border-radius: 12px;
  padding: 16px 20px;
  margin-bottom: 12px;
  transition: all 0.2s;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
  border: 1px solid transparent;
}

.template-card:hover {
  border-color: rgba(99, 102, 241, 0.2);
  box-shadow: 0 4px 16px rgba(99, 102, 241, 0.12);
}

.template-card.legacy {
  opacity: 0.6;
  background: #f5f5f5;
}
.template-card.legacy .template-icon {
  background: linear-gradient(135deg, #94a3b8, #64748b);
}
.legacy-tag {
  margin-left: 6px;
  vertical-align: middle;
}

.template-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.template-info { flex: 1; min-width: 0; }
.template-name {
  font-size: 15px;
  font-weight: 600;
  color: #1e293b;
  margin-bottom: 4px;
}
.template-meta {
  font-size: 12px;
  color: #64748b;
  display: flex;
  gap: 12px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}
.meta-file { color: #475569; }
.template-bottom { font-size: 12px; color: #94a3b8; }
.template-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}
</style>
