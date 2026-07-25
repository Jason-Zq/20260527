<template>
  <!-- 查看文件抽屉:左人员文件列表 | 右原件。父组件用 ref 调 open(personId) 打开。 -->
  <el-drawer v-model="visible" title="查看文件" :size="drawerWidth + 'px'" :modal="false" modal-class="side-drawer-overlay" append-to-body>
    <div class="drawer-resize-handle" @mousedown="startResize"></div>
    <div class="review-layout">
      <aside class="review-queue">
        <div class="queue-title">人员文件({{ fileQueue.length }})</div>
        <div
          v-for="item in fileQueue" :key="item.id"
          class="queue-item" :class="{ active: currentFile?.id === item.id }"
          @click="openFile(item)"
        >
          <div class="queue-name">{{ item.filename || item.file_code }}</div>
          <div class="queue-meta">
            <el-tag size="small">{{ docTypeLabel(item.doc_type) }}</el-tag>
          </div>
        </div>
        <el-empty v-if="!fileQueue.length" description="该人员无关联文件" :image-size="60" />
      </aside>
      <main class="review-main" v-loading="loading">
        <template v-if="currentFile">
          <div class="review-file-head">
            <b>{{ currentFile.filename || currentFile.file_code }}</b>
            <el-tag size="small">{{ docTypeLabel(currentFile.doc_type) }}</el-tag>
          </div>
          <div class="pane">
            <div class="pane-title">原件</div>
            <div class="pane-body raw-view">
              <img v-if="rawState.url && rawState.isImage" :src="rawState.url" class="raw-img" alt="原件" />
              <iframe v-else-if="rawState.url" :src="rawState.url" class="raw-iframe" title="原件"></iframe>
              <span v-else class="dim">{{ rawState.hint }}</span>
            </div>
          </div>
        </template>
        <el-empty v-else description="从左侧选择文件查看" />
      </main>
    </div>
  </el-drawer>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { fetchCustomerFileRawUrl, listPersonFiles } from '../api'
import { docTypeLabel } from '../utils/labels'

const props = defineProps({
  visible: { type: Boolean, default: false },
  drawerWidth: { type: Number, default: () => Math.round(window.innerWidth / 3) },
  personId: { type: Number, default: null },
})
const emit = defineEmits(['update:visible', 'update:drawerWidth'])

const visible = computed({ get: () => props.visible, set: (v) => emit('update:visible', v) })
const drawerWidth = computed({ get: () => props.drawerWidth, set: (v) => emit('update:drawerWidth', v) })
const fileQueue = ref([])
const loading = ref(false)
const currentFile = ref(null)
const rawState = ref({ url: '', isImage: false, hint: '原件加载中…', _revoke: null })

function startResize(e) {
  e.preventDefault()
  const onMove = (ev) => {
    const w = window.innerWidth - ev.clientX
    drawerWidth.value = Math.min(Math.max(w, 300), window.innerWidth - 120)
  }
  const onUp = () => {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

watch(() => props.visible, (v) => {
  if (v && props.personId) loadFiles(props.personId)
})

async function loadFiles(pid) {
  loading.value = true
  currentFile.value = null
  fileQueue.value = []
  try {
    fileQueue.value = await listPersonFiles(pid)
    if (fileQueue.value.length) await openFile(fileQueue.value[0])
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    loading.value = false
  }
}

async function openFile(item) {
  currentFile.value = item
  loadRaw(item.id)
}

async function loadRaw(fileId) {
  if (rawState.value._revoke) rawState.value._revoke()
  rawState.value = { url: '', isImage: false, hint: '原件加载中…', _revoke: null }
  try {
    const raw = await fetchCustomerFileRawUrl(fileId)
    rawState.value = {
      url: raw.blobUrl,
      isImage: (raw.mime || '').startsWith('image/'),
      hint: '',
      _revoke: raw.revoke,
    }
  } catch (err) {
    rawState.value = { url: '', isImage: false, hint: '原件不可用(可能已清理且无法重下)', _revoke: null }
  }
}
</script>

<style scoped>
.drawer-resize-handle {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 4px;
  cursor: col-resize;
  background: #dcdfe6;
  z-index: 10;
}
.drawer-resize-handle:hover,
.drawer-resize-handle:active {
  background: #409eff;
}
.review-layout {
  display: flex;
  gap: 12px;
  height: calc(100vh - 130px);
}
.review-queue {
  width: 180px;
  flex-shrink: 0;
  overflow-y: auto;
  border-right: 1px solid #e4e7ed;
  padding-right: 10px;
}
.queue-title {
  font-weight: 600;
  margin-bottom: 10px;
  font-size: 13px;
}
.queue-item {
  padding: 6px 8px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 6px;
  border: 1px solid #ebeef5;
}
.queue-item:hover {
  background: #f5f7fa;
}
.queue-item.active {
  border-color: #409eff;
  background: #ecf5ff;
}
.queue-name {
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.queue-meta {
  margin-top: 4px;
}
.review-main {
  flex: 1;
  overflow-y: auto;
  min-width: 0;
}
.review-file-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  flex-wrap: wrap;
  font-size: 13px;
}
.pane-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
  color: #606266;
}
.pane-body {
  background: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 10px;
  height: calc(100vh - 230px);
  overflow: auto;
}
.raw-view {
  display: flex;
  align-items: flex-start;
  justify-content: center;
}
.raw-img {
  max-width: 100%;
  max-height: 100%;
}
.raw-iframe {
  width: 100%;
  height: 100%;
  border: none;
}
.dim {
  color: #909399;
  font-size: 12px;
}
</style>
