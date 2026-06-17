<template>
  <div class="family-tab">
    <div class="tab-toolbar">
      <span class="muted">共 {{ list.length }} 位</span>
      <el-button size="small" type="primary" @click="openCreate">
        <el-icon style="margin-right: 4px"><Plus /></el-icon>
        新增成员
      </el-button>
    </div>

    <div v-if="list.length === 0" class="empty-inline">暂无家庭成员</div>

    <div v-else class="member-list">
      <div v-for="m in list" :key="m.id" class="member-card">
        <div class="member-head">
          <el-tag :type="relTagType(m.relation)" size="small">{{ m.relation }}</el-tag>
          <span class="m-name">{{ m.name }}</span>
          <span v-if="m.name_en" class="m-name-en">{{ m.name_en }}</span>
          <el-tag v-if="m.gender" size="small" effect="plain">{{ m.gender }}</el-tag>
          <el-tag v-if="m.will_accompany" size="small" type="success" effect="light">随行</el-tag>
          <div class="actions">
            <el-button size="small" link type="primary" @click="openEdit(m)">编辑</el-button>
            <el-button size="small" link type="danger" @click="onDelete(m)">删除</el-button>
          </div>
        </div>
        <div class="member-grid">
          <div v-if="m.birth_date"><span class="k">出生</span>{{ m.birth_date }}</div>
          <div v-if="m.id_number"><span class="k">证件</span>{{ m.id_number }}</div>
          <div v-if="m.passport_no"><span class="k">护照</span>{{ m.passport_no }}</div>
          <div v-if="m.nationality"><span class="k">国籍</span>{{ m.nationality }}</div>
          <div v-if="m.phone"><span class="k">电话</span>{{ m.phone }}</div>
          <div v-if="m.email"><span class="k">邮箱</span>{{ m.email }}</div>
          <div v-if="m.current_address" class="full"><span class="k">现居</span>{{ m.current_address }}</div>
          <div v-if="m.company_name"><span class="k">公司</span>{{ m.company_name }}</div>
          <div v-if="m.position"><span class="k">职位</span>{{ m.position }}</div>
          <div v-if="m.school_name"><span class="k">学校</span>{{ m.school_name }}</div>
          <div v-if="m.degree"><span class="k">学位</span>{{ m.degree }}</div>
          <div v-if="m.major"><span class="k">专业</span>{{ m.major }}</div>
          <div v-if="m.birth_cert_no"><span class="k">出生证</span>{{ m.birth_cert_no }}</div>
          <div v-if="m.birth_hospital"><span class="k">出生医院</span>{{ m.birth_hospital }}</div>
        </div>
      </div>
    </div>

    <!-- 编辑/新增弹窗 -->
    <el-dialog v-model="dialogOpen" :title="editingId ? '编辑家庭成员' : '新增家庭成员'" width="640px">
      <el-form :model="form" label-width="100px" label-position="right">
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="关系" required>
              <el-select v-model="form.relation" style="width: 100%">
                <el-option v-for="r in RELATIONS" :key="r" :label="r" :value="r" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="是否随行">
              <el-switch v-model="form.will_accompany" />
            </el-form-item>
          </el-col>

          <el-col :span="12">
            <el-form-item label="姓名" required>
              <el-input v-model="form.name" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="英文名">
              <el-input v-model="form.name_en" />
            </el-form-item>
          </el-col>

          <el-col :span="8">
            <el-form-item label="性别">
              <el-select v-model="form.gender" clearable style="width: 100%">
                <el-option label="男" value="男" />
                <el-option label="女" value="女" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="出生日期">
              <el-date-picker v-model="form.birth_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="国籍">
              <el-input v-model="form.nationality" />
            </el-form-item>
          </el-col>

          <el-col :span="12">
            <el-form-item label="证件号">
              <el-input v-model="form.id_number" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="护照号">
              <el-input v-model="form.passport_no" />
            </el-form-item>
          </el-col>

          <el-col :span="12">
            <el-form-item label="电话">
              <el-input v-model="form.phone" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="邮箱">
              <el-input v-model="form.email" />
            </el-form-item>
          </el-col>

          <el-col :span="24">
            <el-form-item label="现居地址">
              <el-input v-model="form.current_address" />
            </el-form-item>
          </el-col>

          <el-col :span="12">
            <el-form-item label="公司">
              <el-input v-model="form.company_name" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="职位">
              <el-input v-model="form.position" />
            </el-form-item>
          </el-col>

          <el-col :span="12">
            <el-form-item label="学校">
              <el-input v-model="form.school_name" />
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="学位">
              <el-input v-model="form.degree" />
            </el-form-item>
          </el-col>
          <el-col :span="6">
            <el-form-item label="专业">
              <el-input v-model="form.major" />
            </el-form-item>
          </el-col>

          <el-col :span="8">
            <el-form-item label="毕业日期">
              <el-date-picker v-model="form.graduation_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="毕业证编号">
              <el-input v-model="form.graduation_cert_no" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="学位证编号">
              <el-input v-model="form.degree_cert_no" />
            </el-form-item>
          </el-col>

          <el-col :span="8">
            <el-form-item label="出生证编号">
              <el-input v-model="form.birth_cert_no" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="出生医院">
              <el-input v-model="form.birth_hospital" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="出生地">
              <el-input v-model="form.birth_place" />
            </el-form-item>
          </el-col>

          <el-col :span="24">
            <el-form-item label="备注">
              <el-input v-model="form.notes" type="textarea" :rows="2" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { Plus } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listFamily, createFamily, updateFamily, deleteFamily } from '../api.js'

const props = defineProps({
  clientId: { type: Number, required: true },
})
const emit = defineEmits(['changed'])

const RELATIONS = ['配偶', '子', '女', '父', '母', '兄', '弟', '姐', '妹', '紧急联系人']

const list = ref([])
const dialogOpen = ref(false)
const editingId = ref(null)
const saving = ref(false)

const emptyForm = () => ({
  relation: '配偶',
  name: '',
  name_en: '',
  gender: '',
  birth_date: '',
  nationality: '',
  id_number: '',
  passport_no: '',
  phone: '',
  email: '',
  current_address: '',
  company_name: '',
  position: '',
  school_name: '',
  degree: '',
  major: '',
  graduation_date: '',
  graduation_cert_no: '',
  degree_cert_no: '',
  birth_cert_no: '',
  birth_hospital: '',
  birth_place: '',
  will_accompany: false,
  notes: '',
})

const form = ref(emptyForm())

async function load() {
  try {
    const data = await listFamily(props.clientId)
    list.value = data.items || []
  } catch (err) {
    ElMessage.error('加载家庭成员失败：' + (err.response?.data?.detail || err.message))
  }
}

function openCreate() {
  editingId.value = null
  form.value = emptyForm()
  dialogOpen.value = true
}

function openEdit(m) {
  editingId.value = m.id
  form.value = { ...emptyForm(), ...m }
  dialogOpen.value = true
}

async function onSave() {
  if (!form.value.relation) {
    ElMessage.warning('请选择关系')
    return
  }
  if (!form.value.name?.trim()) {
    ElMessage.warning('姓名不能为空')
    return
  }
  saving.value = true
  try {
    const payload = {}
    for (const [k, v] of Object.entries(form.value)) {
      if (v != null && v !== '') payload[k] = v
    }
    if (editingId.value) {
      await updateFamily(editingId.value, payload)
      ElMessage.success('已更新')
    } else {
      await createFamily(props.clientId, payload)
      ElMessage.success('已新增')
    }
    dialogOpen.value = false
    await load()
    emit('changed')
  } catch (err) {
    ElMessage.error('保存失败：' + (err.response?.data?.detail || err.message))
  } finally {
    saving.value = false
  }
}

async function onDelete(m) {
  try {
    await ElMessageBox.confirm(`确定删除 ${m.relation}「${m.name}」？`, '确认', { type: 'warning' })
  } catch { return }
  try {
    await deleteFamily(m.id)
    ElMessage.success('已删除')
    await load()
    emit('changed')
  } catch (err) {
    ElMessage.error('删除失败：' + (err.response?.data?.detail || err.message))
  }
}

function relTagType(rel) {
  if (rel === '配偶') return 'success'
  if (['子', '女'].includes(rel)) return 'warning'
  if (['父', '母'].includes(rel)) return 'info'
  return ''
}

watch(() => props.clientId, load)
onMounted(load)
defineExpose({ reload: load })
</script>

<style scoped>
.tab-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.muted { font-size: 12px; color: #94a3b8; }
.empty-inline {
  text-align: center;
  color: #94a3b8;
  padding: 32px 0;
  font-size: 13px;
}
.member-list { display: flex; flex-direction: column; gap: 10px; }
.member-card {
  background: #f8fafc;
  border-radius: 10px;
  padding: 12px 16px;
  border: 1px solid #e2e8f0;
}
.member-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.m-name {
  font-size: 15px;
  font-weight: 600;
  color: #1e293b;
}
.m-name-en {
  font-size: 12px;
  color: #94a3b8;
}
.actions {
  margin-left: auto;
  display: flex;
  gap: 4px;
}
.member-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px 16px;
  font-size: 12px;
  color: #475569;
}
.member-grid .full { grid-column: 1 / -1; }
.member-grid .k {
  display: inline-block;
  width: 56px;
  color: #94a3b8;
  font-weight: 500;
}
</style>
