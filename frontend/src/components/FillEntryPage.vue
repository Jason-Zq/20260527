<template>
  <div class="fill-entry-page">
    <!-- 模板列表(自带标题栏) -->
    <TemplateListPage
      v-if="subView === 'list'"
      @fill="onTemplateFill"
      class="full-view"
    />

    <!-- 模板填写页(自带标题栏,页内「返回」回列表) -->
    <TemplateFillPage
      v-else-if="subView === 'fill' && currentTemplateId"
      :template-id="currentTemplateId"
      @back="subView = 'list'"
      class="full-view"
    />
  </div>
</template>

<script setup>
defineOptions({ name: 'FillEntryPage' })
import { ref } from 'vue'
import TemplateListPage from './TemplateListPage.vue'
import TemplateFillPage from './TemplateFillPage.vue'

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

.full-view {
  flex: 1;
  overflow: hidden;
}
</style>
