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

const _REVIEW_REASONS = {
  no_text: '无文本',
  garbled: 'OCR乱码',
  ocr_short: '文本过短',
  low_confidence: '分类置信低',
  extract_error: '提取失败',
  no_person: '无法归属',
  masked_id: '证件号脱敏',
}

export function reviewReasonLabel(reason) {
  return _REVIEW_REASONS[reason] || reason || '待复核'
}

export const REVIEW_REASON_OPTIONS = Object.entries(_REVIEW_REASONS).map(([value, label]) => ({ value, label }))

const _FIELD_LABELS = {
  name: '姓名', gender: '性别', ethnicity: '民族', birth_date: '出生日期',
  id_number: '公民身份号码', hukou_address: '住址', issue_authority: '签发机关',
  issuing_authority: '签发机关', valid_period: '有效期限',
}

export function fieldLabelOf(key) {
  return _FIELD_LABELS[key] || key
}
