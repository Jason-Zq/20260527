<template>
  <div class="fill-entry-page">
    <!-- 子页顶栏 -->
    <div v-if="subView !== 'list'" class="entry-header">
      <el-button class="back-btn" @click="emit('back')" size="default">
        <el-icon style="margin-right: 4px"><ArrowLeft /></el-icon>
        返回首页
      </el-button>
      <div class="entry-title">
        <span class="title-indicator"></span>
        AI 填写文件
      </div>
    </div>

    <!-- 模板列表（把 TemplateListPage 自带的 back 事件改成返回首页） -->
    <TemplateListPage
      v-if="subView === 'list'"
      @back="emit('back')"
      @fill="onTemplateFill"
      class="full-view"
    />

    <!-- 模板填写页 -->
    <TemplateFillPage
      v-else-if="subView === 'fill' && currentTemplateId"
      :template-id="currentTemplateId"
      @back="subView = 'list'"
      class="full-view"
    />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ArrowLeft } from '@element-plus/icons-vue'
import TemplateListPage from './TemplateListPage.vue'
import TemplateFillPage from './TemplateFillPage.vue'

const emit = defineEmits(['back'])

const subView = ref('list')           // list | fill
const currentTemplateId = ref(null)

function onTemplateFill(templateId) {
  currentTemplateId.value = templateId
  subView.value = 'fill'
}
</script>

<style scoped>
.fill-entry-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #f0f2f8;
  color: #1e293b;
}

.entry-header {
  padding: 0 24px;
  height: 56px;
  background: #ffffff;
  display: flex;
  align-items: center;
  gap: 16px;
  flex-shrink: 0;
  border-bottom: 1px solid #e8ebf5;
}

.back-btn {
  background: #f1f5f9 !important;
  border: 1px solid #e2e8f0 !important;
  color: #475569 !important;
  font-weight: 500 !important;
  border-radius: 8px !important;
}

.back-btn:hover {
  background: #e2e8f0 !important;
  color: #6366f1 !important;
}

.entry-title {
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
  display: flex;
  align-items: center;
  gap: 10px;
}

.title-indicator {
  width: 3px;
  height: 16px;
  background: linear-gradient(180deg, #6366f1, #8b5cf6);
  border-radius: 2px;
}

.full-view {
  flex: 1;
  overflow: hidden;
}
</style>
