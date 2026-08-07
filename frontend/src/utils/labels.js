/**
 * 共享标签/文案映射(画像页、复核抽屉、复核中心页共用)。
 */

const _DOC_TYPES = {
  id_card: '身份证', hukou: '户口本', degree_cert: '学位证',
  birth_cert: '出生证明', passport: '护照', kyc_form: 'KYC表',
  marriage_cert: '结婚证', property_cert: '房产证', no_crime: '无犯罪',
  approval: '批复', submission: '递交包', receipt: '签收回执', other: '其他',
}

export function docTypeLabel(t) {
  return _DOC_TYPES[t] || t || '-'
}

// 关系显示:DB 里主申请人关系值为「户主」,页面统一显示为「客户」(数据值不变)
export function relationLabel(rel) {
  return rel === '户主' ? '客户' : (rel || '')
}

/**
 * 案件显示名:项目案件取二级项目名||一级项目名,默认案件回退 case_type。
 * @param {{ projectname_detailed?: string|null, projectname?: string|null, case_type?: string }} c
 * @returns {string}
 */
export function caseTitle(c) {
  return c.projectname_detailed || c.projectname || c.case_type || '未命名案件'
}

export const DOC_TYPE_OPTIONS = Object.entries(_DOC_TYPES).map(([value, label]) => ({ value, label }))

const _REVIEW_REASONS = {
  no_text: '无文本',
  garbled: 'OCR乱码',
  ocr_short: '文本过短',
  low_confidence: '分类置信低',
  extract_error: '提取失败',
  no_person: '无法归属',
  masked_id: '证件号脱敏',
  field_validation: '字段校验待确认',
}

export function reviewReasonLabel(reason) {
  return _REVIEW_REASONS[reason] || reason || '待复核'
}

export const REVIEW_REASON_OPTIONS = Object.entries(_REVIEW_REASONS).map(([value, label]) => ({ value, label }))

// AI/LLM 调用记录操作码 → 【业务线】中文名(AiApiCallsPage 操作列/筛选用;查询仍传原编码)
const _AI_OPERATIONS = {
  detect_archival: ['文件留底检测', '单文件留底判定'],
  judge_batch_overall: ['文件留底检测', '批次总体判定'],
  judge_batch_overall_2: ['文件留底检测', '批次总体判定2'],
  generate_archive_prompt2: ['文件留底检测', '留底标准生成(提示词2)'],
  summarize_batch: ['文件留底检测', '批次摘要(兜底)'],
  detect_large_table_doc: ['文件留底检测', '大表格检测'],
  recognize_doc_type: ['客户画像', '证件类型识别'],
  extract_doc_fields: ['客户画像', '证件字段提取'],
  extract_doc_fields_multi: ['客户画像', '多人字段提取'],
  asset_dedup: ['客户画像', '资产去重判定'],
  asset_merge: ['客户画像', '资产合并建议'],
  classify_one_page: ['PDF拆分', '单页证件分类'],
  detect_page_ranges: ['PDF拆分', '页边界判定'],
  summarize_text: ['URL摘要', '文件摘要'],
  enrich_anchors_with_llm: ['Word模板', '锚点智能补充'],
  match_anchors_to_person: ['Word模板', '锚点匹配人员'],
}

export function operationLabel(op) {
  const hit = _AI_OPERATIONS[op]
  return hit ? `【${hit[0]}】${hit[1]}` : (op || '-')
}

// 筛选下拉选项:label 中文、value 原编码
export const AI_OPERATION_OPTIONS = Object.entries(_AI_OPERATIONS)
  .map(([value, [biz, label]]) => ({ value, label: `【${biz}】${label}` }))

const _FIELD_LABELS = {
  name: '姓名', gender: '性别', ethnicity: '民族', birth_date: '出生日期',
  id_number: '公民身份号码', hukou_address: '住址', issue_authority: '签发机关',
  issuing_authority: '签发机关', valid_period: '有效期限', spouse_name: '配偶姓名',
  sponsor_name: '主卡/主签持证人', expiry_date: '有效期至',
  approval_expiry_date: '准证/批复有效期至', id_card_expiry_date: '身份证有效期至',
}

export function fieldLabelOf(key) {
  return _FIELD_LABELS[key] || key
}

// ---- 文件留底检测:批次/文件状态与判定映射(检测批次管理页、每日报告页、批次详情弹窗共用) ----

export function verdictLabel(v) {
  return { match: '符合', partial: '部分符合', mismatch: '不符合', no_text: '无文字' }[v] || '-'
}

export function verdictTag(v) {
  if (v === 'match') return 'success'
  if (v === 'partial') return 'warning'
  if (v === 'no_text') return 'info'
  return 'info'
}

export function batchStatusLabel(s) {
  return { running: '进行中', done: '完成', error: '失败', pending: '排队中', fetching: '下载中', ocr: 'OCR中', llm: 'AI分析中' }[s] || s || '-'
}

export function batchStatusTag(s) {
  if (s === 'done') return 'success'
  if (s === 'error') return 'danger'
  return 'warning'
}

export function sourceKindLabel(v) {
  return { batch: '业务', recheck: '重审', upload: '上传', url: 'URL' }[v] || v || '-'
}

// 字段分组(画像成员卡按组分段展示:基础个人信息/护照信息/公司收入/其他证件)
const _FIELD_GROUP_OF = {
  name: 'basic', name_en: 'basic', gender: 'basic', birth_date: 'basic', birth_place: 'basic',
  nationality: 'basic', ethnicity: 'basic', id_number: 'basic', hukou_address: 'basic', marital_status: 'basic',
  spouse_name: 'basic', sponsor_name: 'basic',
  passport_no: 'passport', passport_issue_date: 'passport', passport_expiry_date: 'passport',
  phone: 'income', email: 'income', current_address: 'income', postal_code: 'income',
  occupation: 'income', employer: 'income', business_nature: 'income', annual_income: 'income',
  shareholding: 'income', source_of_funds: 'income', planned_deposit: 'income', residence_plan: 'income',
  birth_cert_no: 'other', birth_hospital: 'other', marriage_date: 'other', marriage_authority: 'other',
  marriage_cert_no: 'other', no_crime_cert_no: 'other', no_crime_issue_date: 'other', approval_no: 'other',
  approval_date: 'other', approval_expiry_date: 'other', id_card_expiry_date: 'other',
  school_name: 'other', major: 'other', degree: 'other', graduation_date: 'other',
  graduation_cert_no: 'other', degree_cert_no: 'other',
}
const _FIELD_GROUP_LABELS = { basic: '基础个人信息', passport: '护照信息', income: '公司收入', other: '其他证件' }
const _FIELD_GROUP_ORDER = ['basic', 'passport', 'income', 'other']

// 把扁平 fields 按 4 大分组,返回 [{group,label,items}],只含有字段的组,按固定顺序
export function groupPersonFields(fields) {
  const buckets = {}
  for (const f of fields) {
    const g = _FIELD_GROUP_OF[f.field] || 'other'
    if (!buckets[g]) buckets[g] = []
    buckets[g].push(f)
  }
  return _FIELD_GROUP_ORDER
    .filter(g => buckets[g] && buckets[g].length)
    .map(g => ({ group: g, label: _FIELD_GROUP_LABELS[g], items: buckets[g] }))
}
