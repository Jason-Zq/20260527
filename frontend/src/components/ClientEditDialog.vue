<template>
  <el-dialog
    v-model="visible"
    :title="'编辑客户：' + (client?.name || '')"
    width="780px"
    @close="onClose"
  >
    <el-form :model="form" label-width="100px" label-position="right" size="small">
      <el-tabs v-model="activeSection">
        <!-- 身份 -->
        <el-tab-pane label="身份信息" name="identity">
          <el-row :gutter="12">
            <el-col :span="8"><el-form-item label="客户编号"><el-input v-model="form.client_code" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="姓名"><el-input v-model="form.name" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="拼音/英文"><el-input v-model="form.name_en" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="曾用名"><el-input v-model="form.former_name" /></el-form-item></el-col>
            <el-col :span="8">
              <el-form-item label="性别">
                <el-select v-model="form.gender" clearable style="width: 100%">
                  <el-option label="男" value="男" />
                  <el-option label="女" value="女" />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="8"><el-form-item label="出生日期"><el-date-picker v-model="form.birth_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="出生地"><el-input v-model="form.birth_place" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="国籍"><el-input v-model="form.nationality" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="民族"><el-input v-model="form.ethnicity" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="婚姻状况"><el-input v-model="form.marital_status" /></el-form-item></el-col>
            <el-col :span="16"><el-form-item label="身份证号"><el-input v-model="form.id_number" /></el-form-item></el-col>
            <el-col :span="24"><el-form-item label="户籍地址"><el-input v-model="form.hukou_address" /></el-form-item></el-col>
          </el-row>
        </el-tab-pane>

        <!-- 联系 -->
        <el-tab-pane label="联系方式" name="contact">
          <el-row :gutter="12">
            <el-col :span="12"><el-form-item label="手机"><el-input v-model="form.phone" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="邮箱"><el-input v-model="form.email" /></el-form-item></el-col>
            <el-col :span="24"><el-form-item label="现家庭住址"><el-input v-model="form.current_address" /></el-form-item></el-col>
          </el-row>
        </el-tab-pane>

        <!-- 护照 -->
        <el-tab-pane label="护照" name="passport">
          <el-row :gutter="12">
            <el-col :span="12"><el-form-item label="护照号"><el-input v-model="form.passport_no" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="签发机关"><el-input v-model="form.passport_issuing_authority" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="签发日期"><el-date-picker v-model="form.passport_issue_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="到期日期"><el-date-picker v-model="form.passport_expiry_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
          </el-row>
        </el-tab-pane>

        <!-- 教育 -->
        <el-tab-pane label="教育" name="education">
          <el-row :gutter="12">
            <el-col :span="12"><el-form-item label="学校"><el-input v-model="form.school_name" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="英文校名"><el-input v-model="form.school_name_en" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="专业"><el-input v-model="form.major" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="学位"><el-input v-model="form.degree" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="毕业日期"><el-date-picker v-model="form.graduation_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="毕业证编号"><el-input v-model="form.graduation_cert_no" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="学位证编号"><el-input v-model="form.degree_cert_no" /></el-form-item></el-col>
          </el-row>
        </el-tab-pane>

        <!-- 工作 -->
        <el-tab-pane label="工作" name="work">
          <el-row :gutter="12">
            <el-col :span="16"><el-form-item label="公司"><el-input v-model="form.company_name" /></el-form-item></el-col>
            <el-col :span="8"><el-form-item label="职位"><el-input v-model="form.position" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="入职日期"><el-date-picker v-model="form.employment_start_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="月薪"><el-input v-model="form.monthly_salary" type="number" /></el-form-item></el-col>
          </el-row>
        </el-tab-pane>

        <!-- 婚姻 -->
        <el-tab-pane label="婚姻" name="marriage">
          <el-row :gutter="12">
            <el-col :span="12"><el-form-item label="登记日期"><el-date-picker v-model="form.marriage_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="登记机关"><el-input v-model="form.marriage_authority" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="结婚证号"><el-input v-model="form.marriage_cert_no" /></el-form-item></el-col>
          </el-row>
        </el-tab-pane>

        <!-- 业务 -->
        <el-tab-pane label="业务+备注" name="biz">
          <el-row :gutter="12">
            <el-col :span="12"><el-form-item label="业务类型"><el-input v-model="form.visa_type" placeholder="如：加拿大技术移民 EE" /></el-form-item></el-col>
            <el-col :span="12"><el-form-item label="顾问"><el-input v-model="form.consultant" /></el-form-item></el-col>
            <el-col :span="24"><el-form-item label="备注"><el-input v-model="form.notes" type="textarea" :rows="3" /></el-form-item></el-col>
          </el-row>
        </el-tab-pane>
      </el-tabs>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { updateClient } from '../api.js'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  client: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'saved'])

const visible = computed({
  get: () => props.modelValue,
  set: v => emit('update:modelValue', v),
})

const activeSection = ref('identity')
const saving = ref(false)
const form = ref({})

const FIELDS = [
  'client_code', 'name', 'name_en', 'former_name', 'gender', 'birth_date',
  'birth_place', 'nationality', 'ethnicity', 'marital_status', 'id_number', 'hukou_address',
  'phone', 'email', 'current_address',
  'passport_no', 'passport_issuing_authority', 'passport_issue_date', 'passport_expiry_date',
  'school_name', 'school_name_en', 'major', 'degree', 'graduation_date', 'graduation_cert_no', 'degree_cert_no',
  'company_name', 'position', 'employment_start_date', 'monthly_salary',
  'marriage_date', 'marriage_authority', 'marriage_cert_no',
  'visa_type', 'consultant', 'notes',
]

watch(() => props.modelValue, v => {
  if (v && props.client) {
    const f = {}
    for (const k of FIELDS) f[k] = props.client[k] ?? ''
    form.value = f
  }
})

function onClose() {
  activeSection.value = 'identity'
}

async function onSave() {
  if (!form.value.name?.trim()) {
    ElMessage.warning('姓名不能为空')
    return
  }
  saving.value = true
  try {
    // 不下发空字符串和未变化的字段（简化：全量发，后端 _filter_client_payload 会忽略空值并强转）
    const payload = {}
    for (const k of FIELDS) {
      const v = form.value[k]
      // 空字符串当成清空，但发送给后端 _coerce 会过滤；此处以 null 作为"清空"信号会更稳，但部分更新语义就保持发空
      if (v != null) payload[k] = v
    }
    await updateClient(props.client.id, payload)
    ElMessage.success('已保存')
    emit('saved')
    visible.value = false
  } catch (err) {
    ElMessage.error('保存失败：' + (err.response?.data?.detail || err.message))
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
:deep(.el-tabs__content) {
  padding-top: 8px;
}
</style>
