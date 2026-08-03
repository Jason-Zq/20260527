<template>
  <div class="profile-page">
    <div class="profile-header">
      <div class="profile-title">
        <span class="title-indicator"></span>
        客户画像
      </div>
      <div class="header-actions">
        <el-button type="primary" style="margin-right: 12px" @click="openImportDialog">
          <el-icon style="margin-right: 4px"><MagicStick /></el-icon>
          选择客户生成画像
        </el-button>
        <el-button style="margin-right: 12px" :loading="resuming" @click="onResumeStale">
          恢复中断任务
        </el-button>
        <el-button @click="loadTasks" :loading="loading">
          <el-icon style="margin-right: 4px"><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <div class="profile-main">
      <section class="card filter-card">
        <div class="filter-grid">
          <el-select v-model="taskStatus" placeholder="状态" clearable size="small" style="width: 110px">
            <el-option label="进行中" value="running" />
            <el-option label="完成" value="done" />
            <el-option label="失败" value="error" />
          </el-select>
          <el-input v-model="taskClientName" placeholder="客户名" clearable size="small" style="width: 140px" @keyup.enter="onTaskQuery" />
          <el-input v-model="taskCustomerCode" placeholder="客户编码" clearable size="small" style="width: 180px" @keyup.enter="onTaskQuery" />
          <el-button size="small" type="primary" @click="onTaskQuery">查询</el-button>
          <el-button size="small" @click="onTaskReset">重置</el-button>
        </div>
      </section>
      <section class="card">
        <el-table :data="tasks" v-loading="loading" stripe empty-text="暂无任务,点击右上角选择客户生成画像开始" style="width: 100%">
          <el-table-column label="ID" width="70" align="center" prop="id" />
          <el-table-column label="客户" min-width="100" show-overflow-tooltip prop="client_name" />
          <el-table-column label="客户编码" min-width="150" show-overflow-tooltip>
            <template #default="{ row }"><span class="mono dim">{{ row.customer_code || '-' }}</span></template>
          </el-table-column>
          <!-- 2026-07-28 需求:暂时隐藏「家庭资产/护照签发/护照到期」3 列,保留以便恢复(后端字段仍在返回) -->
          <!-- <el-table-column label="家庭资产" width="90" align="center">
            <template #default="{ row }">
              <span :class="{ dim: !row.asset_count }">{{ row.asset_count || 0 }}</span>
            </template>
          </el-table-column>
          <el-table-column label="护照签发" width="110" align="center">
            <template #default="{ row }">
              <span v-if="row.main_passport_issue_date" class="mono">{{ row.main_passport_issue_date }}</span>
              <span v-else class="dim">-</span>
            </template>
          </el-table-column>
          <el-table-column label="护照到期" width="110" align="center">
            <template #default="{ row }">
              <span v-if="row.main_passport_expiry_date"
                    class="mono"
                    :class="passportExpiryClass(row.main_passport_expiry_date)">
                {{ row.main_passport_expiry_date }}
              </span>
              <span v-else class="dim">-</span>
            </template>
          </el-table-column> -->
          <el-table-column label="进度" width="110" align="center">
            <template #default="{ row }">{{ row.processed_files }}/{{ row.total_files }}</template>
          </el-table-column>
          <!-- 2026-07-28 需求:暂时隐藏「四类证件」列,保留以便恢复(画像弹窗摘要行仍展示同类计数) -->
          <!-- <el-table-column label="四类证件" min-width="210">
            <template #default="{ row }">
              <span class="type-counts">
                身份证 {{ row.id_card_count }} · 户口 {{ row.hukou_count }} · 学位 {{ row.degree_cert_count }} · 出生 {{ row.birth_cert_count }}
              </span>
            </template>
          </el-table-column> -->
          <el-table-column label="状态" width="90" align="center">
            <template #default="{ row }">
              <el-tag :type="taskTag(row.status)" size="small">{{ taskLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <!-- <el-table-column label="清单文件" min-width="180" show-overflow-tooltip prop="filename" /> -->
          <el-table-column label="当前文件" min-width="100" show-overflow-tooltip>
            <template #default="{ row }"><span class="dim">{{ row.current_file || '-' }}</span></template>
          </el-table-column>
          <el-table-column label="创建时间" min-width="150">
            <template #default="{ row }"><span class="mono dim">{{ row.created_at }}</span></template>
          </el-table-column>
          <!-- 终态(done/error)任务的 updated_at 即识别完成时间,与审核管理后台同口径 -->
          <el-table-column label="识别完成时间" min-width="150">
            <template #default="{ row }">
              <span class="mono dim">{{ ['done', 'error'].includes(row.status) ? row.updated_at : '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="170" align="center" fixed="right">
            <template #default="{ row }">
              <el-button size="small" type="primary" link @click="openProfile(row)">查看画像</el-button>
              <el-button size="small" type="danger" link @click.stop="onDeleteTask(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="pagination-row">
          <el-pagination
            v-model:current-page="taskPage"
            v-model:page-size="taskPageSize"
            :page-sizes="[10, 25, 50, 100]"
            :total="taskTotal"
            layout="total, sizes, prev, pager, next"
            @current-change="loadTasks"
            @size-change="onTaskSizeChange"
          />
        </div>
      </section>
    </div>

    <!-- 接口导入客户文件清单 -->
    <el-dialog v-model="importVisible" title="选择客户生成画像" width="760px" top="6vh">
      <el-form label-width="90px" inline>
        <el-form-item label="客户编号">
          <el-input v-model="importForm.customer_code" clearable style="width: 240px"
                    placeholder="留空查询最近 100 条" @keyup.enter="onPreview" />
        </el-form-item>
      </el-form>
      <div style="margin-bottom: 8px">
        <el-button type="primary" :loading="previewLoading" @click="onPreview">查询预览</el-button>
        <span class="dim" style="margin-left: 8px; font-size: 12px">接口固定返回最近 100 条,不支持修改</span>
        <span v-if="previewCustomers.length" class="dim" style="margin-left: 8px; font-size: 12px">
          共 {{ previewCustomers.length }} 个客户,勾选后确认导入
        </span>
      </div>
      <el-table v-if="previewCustomers.length" ref="previewTableRef" :data="previewCustomers"
                height="380" stripe @selection-change="onPreviewSelectionChange">
        <el-table-column type="selection" width="45" />
        <el-table-column label="客户姓名" min-width="110" show-overflow-tooltip prop="customer_name" />
        <el-table-column label="客户编号" min-width="170" show-overflow-tooltip prop="customer_code">
          <template #default="{ row }">
            <div><span class="mono dim">{{ row.customer_code || '-' }}</span></div>
            <!-- <div v-if="row.crm_oid" class="mono dim" style="font-size: 11px">CRM {{ row.crm_oid }}</div> -->
          </template>
        </el-table-column>
        <el-table-column label="文件" width="80" align="center" prop="file_count" />
        <el-table-column label="项目" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tooltip v-if="row.projects?.length" placement="top">
              <template #content>
                <div v-for="(p, i) in row.projects" :key="i" style="line-height: 1.7">
                  <div>{{ p.projectname_detailed || p.projectname || '未命名项目' }}
                    <span v-if="p.projectno" class="mono" style="opacity:.75; font-size: 11px"> {{ p.projectno }}<template v-if="p.projectno_detailed">/{{ p.projectno_detailed }}</template></span>
                  </div>
                  <div v-if="p.affter_entryoid || p.project_create_time" class="mono" style="opacity:.6; font-size: 11px">
                    <template v-if="p.affter_entryoid">OID {{ p.affter_entryoid }}</template><template v-if="p.project_create_time"> · {{ p.project_create_time }}</template>
                  </div>
                </div>
              </template>
              <span>{{ row.projects.length }} 个项目</span>
            </el-tooltip>
            <span v-else class="dim">-</span>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="importVisible = false">取消</el-button>
        <el-button type="primary" :loading="importLoading" :disabled="!selectedNames.length"
                   @click="onConfirmImport">
          确认导入(已选 {{ selectedNames.length }} 户)
        </el-button>
      </template>
    </el-dialog>

    <!-- 客户画像详情 -->
    <el-dialog v-model="profileVisible" top="4vh" :modal="true" modal-class="side-drawer-overlay" :width="profileDialogWidth" :style="{ marginRight: profileShift > 0 ? (profileShift + 20) + 'px' : null }">
      <template #header>
        <div class="profile-dialog-header">
          <div style="display: flex; align-items: baseline; gap: 12px">
            <span class="profile-dialog-title">客户画像 · {{ profile?.task?.client_name || '' }}</span>
            <span v-if="profile?.household?.customer_code" class="profile-dialog-title mono">{{ profile.household.customer_code }}</span>
            <!-- <span v-if="profile?.household?.crm_oid" class="dim mono" style="font-size: 12px">CRM {{ profile.household.crm_oid }}</span> -->
          </div>
          <el-button size="small" type="warning" :loading="regenerating"
                     :disabled="!profile?.household?.id || profile?.task?.status === 'running'"
                     @click="onRegenerate">重新生成画像</el-button>
        </div>
      </template>
      <div v-if="profileLoading" style="padding: 40px; text-align: center"><el-icon class="is-loading"><Loading /></el-icon> 加载中...</div>
      <div v-else-if="profile" class="profile-body">
        <div class="task-summary">
          <el-tag :type="taskTag(profile.task.status)" size="small">{{ taskLabel(profile.task.status) }}</el-tag>
          <span class="dim">进度 {{ profile.task.processed_files }}/{{ profile.task.total_files }}</span>
          <span class="dim">新 OCR {{ profile.task.fresh_ocr_count }}</span>
          <span class="dim">复用 {{ profile.task.reused_count + profile.task.relinked_count }}</span>
          <!-- <span class="dim" :class="{ 'err-text': profile.task.failed_count > 0 }">失败 {{ profile.task.failed_count }}</span> -->
          <span class="type-counts">
            身份证 {{ profile.type_counts.id_card }} · 户口 {{ profile.type_counts.hukou }} · 学位 {{ profile.type_counts.degree_cert }} · 出生 {{ profile.type_counts.birth_cert }}
          </span>
        </div>

        <el-tabs v-model="activeTab">
          <el-tab-pane label="客户档案" name="profile">
            <div v-if="passportAlerts.length" class="expiry-banner" :class="{ expired: passportAlerts.some(a => a.level === 'expired') }">
              <el-icon><WarningFilled /></el-icon>
              <span>护照到期提醒:{{ passportAlerts.map(a => a.text).join(';') }}</span>
            </div>
            <div v-if="conflictAlerts.length" class="conflict-banner">
              <el-icon><WarningFilled /></el-icon>
              <span>多源字段提醒(多来源不一致,点字段「可信度」徽标核对):{{ conflictAlerts.join(';') }}</span>
            </div>
            <div class="person-grid">
              <section v-for="p in profile.persons" :key="p.id" class="card inner-card person-card">
                <div class="person-head">
                  <div class="person-avatar" :class="{ main: p.is_main }">{{ (p.name || '?')[0] }}</div>
                  <div class="person-title">
                    <span class="person-name">{{ p.name }}</span>
                    <el-tag v-if="p.is_main" type="primary" size="small" effect="dark">客户</el-tag>
                    <template v-else-if="editingPersonId === p.id">
                      <el-select v-model="editRelation" size="small" style="width: 110px" placeholder="关系">
                        <el-option v-for="r in relationOptions" :key="r" :label="r" :value="r" />
                      </el-select>
                    </template>
                    <el-tag v-else-if="p.relation_to_main === '待确认'" type="warning" size="small">待确认</el-tag>
                    <el-tag v-else size="small" effect="plain">{{ relationLabel(p.relation_to_main) }}</el-tag>
                  </div>
                  <div class="person-actions">
                    <template v-if="editingPersonId === p.id">
                      <el-button size="small" type="success" :loading="editSaving" @click="saveEdit(p)">保存</el-button>
                      <el-button size="small" @click="cancelEdit">取消</el-button>
                    </template>
                    <template v-else>
                      <el-button size="small" type="primary" @click="startEdit(p)">编辑</el-button>
                    </template>
                    <el-button size="small" @click="openPersonFiles(p)">查看文件</el-button>
                    <el-button v-if="profile.persons.length > 1 && editingPersonId !== p.id"
                               size="small" type="danger" plain @click="openMerge(p)">合并</el-button>
                  </div>
                </div>
                <div class="person-fields">
                  <template v-for="grp in groupPersonFields(p.fields)" :key="grp.group">
                    <div class="field-group-title">{{ grp.label }}</div>
                    <div class="field-group-grid">
                      <div v-for="f in grp.items" :key="f.field" class="field-row">
                        <span class="field-label">{{ f.label || f.field }}</span>
                        <span class="field-value">
                          <el-input v-if="editingPersonId === p.id" v-model="editBuffer[f.field]" size="small" />
                          <template v-else>
                            {{ f.value || '-' }}
                            <span v-if="f.status === 'confirmed'" class="status-mark confirmed">已确认</span>
                            <span v-else-if="f.status === 'corrected'" class="status-mark corrected">已修正</span>
                            <span v-if="f.field === 'passport_expiry_date' && p.passport_expiry && p.passport_expiry.level !== 'ok'"
                                  class="expiry-tag" :class="p.passport_expiry.level">{{ expiryTagText(p.passport_expiry) }}</span>
                            <el-tooltip v-if="f.credibility" placement="top">
                              <template #content>
                                <div>可信度 {{ f.credibility.score }} 分</div>
                                <div v-for="(r, i) in f.credibility.reasons" :key="i">{{ r }}</div>
                                <div style="margin-top: 2px">点击查看数据来源({{ (f.credibility.sources || []).length }} 条)</div>
                              </template>
                              <span class="cred-badge" :class="f.credibility.level" @click.stop="openCredibility(p, f)">{{ f.credibility.level }}</span>
                            </el-tooltip>
                          </template>
                        </span>
                      </div>
                    </div>
                  </template>
                  <div v-if="!p.fields.length" class="dim" style="padding: 8px 0">暂无提取字段</div>
                </div>
              </section>
              <el-empty v-if="!profile.persons?.length" description="任务完成后自动生成家庭成员档案" :image-size="60" style="grid-column: 1 / -1" />
            </div>

            <section v-if="profile.assets?.length" class="card inner-card">
              <div class="card-title asset-card-title">
                <span>家庭资产({{ profile.assets.length }})</span>
                <div>
                  <el-button size="small" @click="openAssetFiles">查看文件</el-button>
                  <el-button size="small" type="primary" :loading="dedupePreviewLoading" @click="openDedupePreview">
                    <el-icon style="margin-right: 4px"><MagicStick /></el-icon>AI 处理
                  </el-button>
                </div>
              </div>
              <section v-for="a in profile.assets" :key="a.id" class="card inner-card person-card asset-card">
                <div class="person-head">
                  <div class="person-avatar asset">产</div>
                  <div class="person-title">
                    <span class="person-name">{{ a.name }}</span>
                    <el-tag size="small" effect="plain">{{ a.asset_type }}</el-tag>
                  </div>
                </div>
                <div class="person-fields">
                  <div v-if="a.owner_person_id" class="field-row">
                    <span class="field-label">权属人</span>
                    <span class="field-value">{{ personName(a.owner_person_id) }}</span>
                  </div>
                  <div v-for="(v, k) in a.attrs" :key="k" class="field-row">
                    <span class="field-label">{{ assetAttrLabel(k) }}</span>
                    <span class="field-value">{{ v }}</span>
                  </div>
                </div>
              </section>
            </section>
          </el-tab-pane>

          <el-tab-pane label="完备度矩阵" name="matrix">
            <el-table :data="matrix.persons" border size="small" v-loading="matrixLoading" empty-text="任务完成后生成">
              <el-table-column label="成员" width="130">
                <template #default="{ row }">
                  <b>{{ row.name }}</b>
                  <span class="dim" style="margin-left: 4px">{{ relationLabel(row.relation_to_main) }}</span>
                </template>
              </el-table-column>
              <el-table-column v-for="col in matrix.columns" :key="col.key" :label="col.label" align="center" min-width="95">
                <template #default="{ row }">
                  <span class="matrix-cell" :class="cellOf(row, col).status" @click="onCellClick(row, col)">
                    {{ cellText(cellOf(row, col).status) }}
                  </span>
                </template>
              </el-table-column>
            </el-table>
            <div class="matrix-legend dim">
              <span class="matrix-cell ok">✓</span> 材料齐 &nbsp;
              <span class="matrix-cell warn">!</span> 有文件但待复核(点击直达) &nbsp;
              <span class="matrix-cell missing">✗</span> 缺失 &nbsp;
              <span class="matrix-cell na">—</span> 不适用
            </div>
          </el-tab-pane>

          <el-tab-pane label="案件时间线" name="cases">
            <div v-if="profile.cases?.length" class="case-list">
              <section v-for="c in profile.cases" :key="c.id" class="card inner-card">
                <div class="card-title case-head">
                  <span>{{ caseTitle(c) }}<span v-if="c.projectno" class="dim mono" style="margin-left: 8px; font-size: 12px">{{ c.projectno }}<template v-if="c.projectno_detailed">/{{ c.projectno_detailed }}</template></span></span>
                  <el-tag :type="caseStatusTag(c.status)" size="small" effect="dark">{{ c.status }}</el-tag>
                </div>
                <div v-if="c.affter_entryoid || c.projectname" class="case-meta dim">
                  <span v-if="c.affter_entryoid">售后OID <span class="mono">{{ c.affter_entryoid }}</span></span>
                  <span v-if="c.projectname">一级项目 <span v-if="c.projectno" class="mono">{{ c.projectno }}</span> {{ c.projectname }}</span>
                  <span v-if="c.projectname_detailed">二级项目 <span v-if="c.projectno_detailed" class="mono">{{ c.projectno_detailed }}</span> {{ c.projectname_detailed }}</span>
                  <span v-if="c.project_created_at">项目创建 {{ String(c.project_created_at).replace('T', ' ').slice(0, 16) }}</span>
                </div>
                <el-timeline class="case-timeline">
                  <el-timeline-item
                    v-for="(m, i) in c.milestones" :key="m.name"
                    :timestamp="m.date" placement="top"
                    :type="i === c.milestones.length - 1 ? 'primary' : ''"
                    :hollow="i !== c.milestones.length - 1"
                  >
                    <div class="milestone-row">
                      <b>{{ m.name }}</b>
                      <span v-if="m.source_filename" class="dim milestone-src">来源:{{ m.source_filename }}</span>
                    </div>
                  </el-timeline-item>
                </el-timeline>
              </section>
            </div>
            <el-empty v-else description="暂无案件里程碑,递交/批复/签收类文件提取后自动生成" :image-size="60" />
          </el-tab-pane>

          <el-tab-pane :label="`文件清单(${filesTotal})`" name="files">
            <el-table :data="files" v-loading="filesLoading" stripe size="small" empty-text="暂无文件" @row-click="selectFile" highlight-current-row>
              <el-table-column label="文件编码" min-width="95" show-overflow-tooltip prop="file_code" />
              <el-table-column label="文件名" min-width="170" show-overflow-tooltip prop="filename" />
              <el-table-column label="文件夹" min-width="110" show-overflow-tooltip prop="folder_name" />
              <el-table-column label="项目" min-width="130" show-overflow-tooltip>
                <template #default="{ row }">
                  <div v-if="row.project_name">{{ row.project_name }}</div>
                  <div v-if="row.affter_entryoid" class="mono dim" style="font-size: 11px">{{ row.affter_entryoid }}</div>
                  <span v-if="!row.project_name && !row.affter_entryoid" class="dim">-</span>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="90" align="center">
                <template #default="{ row }">
                  <el-tag :type="fileTag(row.status)" size="small">{{ fileLabel(row.status) }}</el-tag>
                </template>
              </el-table-column>
              <!-- <el-table-column label="提取" width="90" align="center">
                <template #default="{ row }">
                  <el-tag v-if="row.latest_extract_status === 'done'" type="success" size="small">完成</el-tag>
                  <el-tag v-else-if="row.latest_extract_status === 'error'" type="danger" size="small">失败</el-tag>
                  <el-tag v-else-if="row.latest_extract_status === 'skipped'" type="info" size="small">跳过</el-tag>
                  <span v-else class="dim">-</span>
                </template>
              </el-table-column> -->
              <el-table-column label="OCR 来源" width="100" align="center">
                <template #default="{ row }">{{ ocrSourceLabel(row.ocr_source) }}</template>
              </el-table-column>
              <!-- <el-table-column label="识别类型" width="110" align="center">
                <template #default="{ row }">
                  <span v-if="row.doc_type">{{ docTypeLabel(row.doc_type) }}</span>
                  <span v-else class="dim">-</span>
                </template>
              </el-table-column>
              <el-table-column label="分类依据" width="110" align="center">
                <template #default="{ row }">
                  <span v-if="row.classify_by !== 'none'">{{ classifyLabel(row.classify_by) }}{{ row.classify_score != null ? ' ' + row.classify_score : '' }}</span>
                  <span v-else class="dim">-</span>
                </template>
              </el-table-column>
              <el-table-column label="错误" min-width="150" show-overflow-tooltip>
                <template #default="{ row }"><span class="err-text">{{ row.error_msg || '' }}</span></template>
              </el-table-column> -->
              <el-table-column label="操作" width="110" align="center" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" type="primary" link @click.stop="selectFile(row)">查看详情</el-button>
                </template>
              </el-table-column>
            </el-table>
            <div class="pagination-row">
              <el-pagination
                v-model:current-page="filesPage"
                v-model:page-size="filesPageSize"
                :page-sizes="[10, 20, 50, 100]"
                :total="filesTotal"
                layout="total, sizes, prev, pager, next"
                @current-change="onFilesPageChange"
                @size-change="onFilesSizeChange"
              />
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
    </el-dialog>

    <!-- 复核中心抽屉(共享组件;复核中心页用同一组件,不传 importTaskId 即全局队列) -->
    <ReviewDrawer ref="reviewDrawerRef" :import-task-id="profile?.task?.id || null" @done="onReviewDone" />
    <FileListDrawer v-model:visible="fileListVisible" v-model:drawerWidth="personFilesDrawerWidth"
                    :title="fileListTitle" :files="fileListFiles" :loading="fileListLoading" />

    <!-- 文件详情弹窗:原件 | OCR 文本 | 提取结果详情 -->
    <el-dialog v-model="fileDetailVisible" title="文件详情" width="90%" top="4vh" append-to-body>
            <div v-if="selectedFile" class="file-detail-layout">
              <div class="pane">
                <div class="pane-title">原件 · {{ ocrFileData?.filename || selectedFile.filename || selectedFile.file_code }}</div>
                <div class="pane-body raw-view">
                  <img v-if="rawState.url && rawState.isImage" :src="rawState.url" class="raw-img" alt="原件" />
                  <iframe v-else-if="rawState.url" :src="rawState.url" class="raw-iframe" title="原件"></iframe>
                  <span v-else class="dim">{{ rawState.hint || '原件加载中…' }}</span>
                </div>
              </div>
              <div class="pane">
                <div class="pane-title">OCR 文本</div>
                <pre class="pane-body ocr-view">{{ ocrFileData?.ocr_text || '(无文本)' }}</pre>
              </div>
              <div class="pane">
                <div class="pane-title">提取结果详情</div>
                <div class="pane-body extract-view">
                  <div v-if="!fileExtractions.length" class="dim">该文件无提取记录</div>
                  <template v-else>
                    <div class="extract-list">
                      <div v-for="ex in fileExtractions" :key="ex.id" class="extract-item" :class="{ active: selectedExtraction?.id === ex.id }" @click="selectedExtraction = ex">
                        <span class="dim mono">v{{ ex.rule_version ?? '-' }}</span>
                        <el-tag size="small" :type="extractTag(ex.status)">{{ extractLabel(ex) }}</el-tag>
                        <span class="dim">{{ writeStatsText(ex.write_stats) }}</span>
                      </div>
                    </div>
                    <div v-if="selectedExtraction" class="extract-detail">
                      <div class="json-block">
                        <div class="json-title">提取字段</div>
                        <pre>{{ prettyJson(selectedExtraction.extracted) }}</pre>
                      </div>
                      <div class="json-block">
                        <div class="json-title">写入明细</div>
                        <pre>{{ prettyJson(selectedExtraction.mapped) }}</pre>
                      </div>
                      <div class="json-block">
                        <div class="json-title">写入统计</div>
                        <pre>{{ prettyJson(selectedExtraction.write_stats) }}</pre>
                      </div>
                      <div v-if="selectedExtraction.error_msg" class="json-block">
                        <div class="json-title err-text">错误</div>
                        <pre class="err-text">{{ selectedExtraction.error_msg }}</pre>
                      </div>
                    </div>
                  </template>
                </div>
              </div>
            </div>
    </el-dialog>

    <!-- 字段来源与可信度抽屉:上编辑+确定,中可信度构成,下来源文件列表(点「查看」弹 FilePreviewDialog 大预览) -->
    <el-drawer v-model="credVisible" :title="`字段来源与可信度 · ${credData?.label || ''}`" :size="credDrawerWidth + 'px'" :modal="false" modal-class="side-drawer-overlay" append-to-body>
      <div class="drawer-resize-handle" @mousedown="startResizeCred"></div>
      <div v-if="credData" class="conflict-dialog-body">
        <div class="conflict-edit">
          <span class="conflict-field-label">{{ credData.label }}</span>
          <el-input v-model="credEditValue" size="small" style="flex: 1" />
          <el-button type="primary" size="small" :loading="credSaving" @click="saveCredibility">确定</el-button>
        </div>
        <div v-if="credData.credibility" class="cred-summary">
          <el-tag :type="credTagType(credData.credibility.level)" size="small" effect="dark">可信度 {{ credData.credibility.level }}</el-tag>
          <span class="cred-score">{{ credData.credibility.score }} 分</span>
          <span v-for="(r, i) in credData.credibility.reasons" :key="i" class="cred-reason">{{ r }}</span>
        </div>
        <el-divider />
        <div class="conflict-source-list">
          <div v-for="(src, i) in credSources" :key="i" class="conflict-source-item">
            <el-tag :type="src.agrees ? 'success' : 'danger'" size="small" effect="plain" class="cred-agree-tag">{{ src.agrees ? '一致' : '不一致' }}</el-tag>
            <span class="cred-src-value" :title="src.value">{{ src.value || '-' }}</span>
            <span class="conflict-source-name" :title="src.source">{{ src.source }}</span>
            <el-tag size="small" effect="plain">{{ docTypeLabel(src.doc_type) }}</el-tag>
            <el-button size="small" type="primary" link @click="previewCredSource(src)">查看</el-button>
          </div>
          <div v-if="!credSources.length" class="dim" style="padding: 12px">无来源文件</div>
        </div>
        <div class="dim cred-footnote">可信度由来源层级(官方证件/客户自报)、确认状态(人工/AI)、多文件互证与冲突扣分综合计算;上方修改并「确定」后为人工值,可信度最高。</div>
      </div>
      <FilePreviewDialog v-model:visible="credPreviewVisible" :file="credPreviewFile" />
    </el-drawer>

    <!-- AI 家庭资产合并预览 -->
    <el-dialog v-model="dedupeVisible" title="AI 家庭资产合并预览" width="70%" top="6vh" append-to-body>
      <div v-if="dedupePreviewLoading" style="padding: 40px; text-align: center">
        <el-icon class="is-loading"><Loading /></el-icon> AI 分析中,请稍候…
      </div>
      <div v-else-if="!dedupeGroups.length" class="dim" style="padding: 24px; text-align: center">
        AI 分析未发现明显重复,当前家庭资产无需合并。
      </div>
      <div v-else>
        <div class="dim" style="margin-bottom: 12px">
          AI 建议合并以下 {{ dedupeGroups.length }} 组资产,请核对合并后的信息,确认无误再点「确认合并」。
          <span v-if="dedupeManualCount" class="warn-text">(另有 {{ dedupeManualCount }} 条人工修正过的资产不参与合并)</span>
        </div>
        <section v-for="(g, gi) in dedupeGroups" :key="gi" class="card dedupe-group">
          <div class="card-title">
            <span>合并组 #{{ gi + 1 }}</span>
            <span class="dim" style="font-weight: normal; margin-left: 10px">{{ g.reason }}</span>
          </div>
          <div class="dedupe-src">
            <div class="dedupe-src-title dim">被合并的原始资产 ({{ g.merge_ids.length + 1 }} 条):</div>
            <div v-for="aid in [g.keep_id, ...g.merge_ids]" :key="aid" class="dedupe-src-item">
              <el-tag v-if="aid === g.keep_id" type="success" size="small">保留</el-tag>
              <el-tag v-else type="info" size="small">合并</el-tag>
              <span class="mono dim">id={{ aid }}</span>
              <span>{{ dedupeAssetOf(aid)?.name || '(未知)' }}</span>
              <span class="dim">证号 {{ dedupeAssetOf(aid)?.attrs?.cert_no || '-' }}</span>
            </div>
          </div>
          <div class="dedupe-target">
            <div class="dedupe-target-title">合并后:</div>
            <div class="field-row">
              <span class="field-label">名称</span>
              <span class="field-value">{{ g.merged_name || '-' }}</span>
            </div>
            <div v-for="(v, k) in g.merged_attrs" :key="k" class="field-row">
              <span class="field-label">{{ assetAttrLabel(k) }}</span>
              <span class="field-value">{{ v }}</span>
            </div>
          </div>
        </section>
      </div>
      <template #footer>
        <el-button @click="dedupeVisible = false">取消</el-button>
        <el-button type="primary" :loading="dedupeCommitLoading"
                   :disabled="!dedupeGroups.length" @click="commitDedupe">
          确认合并
        </el-button>
      </template>
    </el-dialog>

    <!-- 人员手动合并(同名双卡) -->
    <el-dialog v-model="mergeVisible" title="合并人员" width="480px" append-to-body>
      <div v-if="mergeSource" style="margin-bottom: 12px">
        将 <b>{{ mergeSource.name }}</b>(id={{ mergeSource.id }},{{ mergeSource.fields.length }} 个字段)
        合并到下面选中的人,<span class="warn-text">「{{ mergeSource.name }}」这张卡将被删除,不可撤销</span>;
        字段冲突时人工值优先,同为 AI 值以较新者为准。
      </div>
      <el-radio-group v-model="mergeTargetId" style="display: flex; flex-direction: column; gap: 8px">
        <el-radio v-for="p in mergeCandidates" :key="p.id" :value="p.id" style="height: auto">
          {{ p.name }}(id={{ p.id }},{{ relationLabel(p.relation_to_main) }},{{ p.fields.length }} 个字段)
        </el-radio>
      </el-radio-group>
      <div v-if="mergeTargetPerson && mergeSource && mergeTargetPerson.name !== mergeSource.name" style="margin-top: 14px">
        <div style="margin-bottom: 6px">合并后保留的姓名:</div>
        <el-radio-group v-model="mergeKeepName" style="display: flex; flex-direction: column; gap: 6px">
          <el-radio :value="mergeTargetPerson.name" style="height: auto">{{ mergeTargetPerson.name }}(目标人的名字)</el-radio>
          <el-radio :value="mergeSource.name" style="height: auto">{{ mergeSource.name }}(被合并人的名字)</el-radio>
        </el-radio-group>
      </div>
      <template #footer>
        <el-button @click="mergeVisible = false">取消</el-button>
        <el-button type="danger" :loading="merging" :disabled="!mergeTargetId" @click="confirmMerge">
          确认合并
        </el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup>
defineOptions({ name: 'ProfilePage' })
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading, MagicStick, Refresh, WarningFilled } from '@element-plus/icons-vue'
import FileListDrawer from './FileListDrawer.vue'
import FilePreviewDialog from './FilePreviewDialog.vue'
import ReviewDrawer from './ReviewDrawer.vue'
import { docTypeLabel, fieldLabelOf, groupPersonFields, caseTitle, relationLabel } from '../utils/labels'
import {
  correctPersonField,
  dedupeAssetsCommit,
  dedupeAssetsPreview,
  deleteProfileTask,
  fetchCustomerFileRawUrl,
  fetchCustomerFilePreviewPdfUrl,
  getCustomerFile,
  listHouseholdAssetFiles,
  listPersonFiles,
  mergeProfilePersons,
  getProfileTaskMatrix,
  getProfileTaskProfile,
  importProfileRemote,
  listProfileTaskFiles,
  listProfileTasks,
  previewProfileRemoteImport,
  regenerateHouseholdProfile,
  resumeStaleProfileTasks,
} from '../api'

const tasks = ref([])
const loading = ref(false)
const taskStatus = ref('')
const taskClientName = ref('')
const taskCustomerCode = ref('')
const taskPage = ref(1)
const taskPageSize = ref(10)
const taskTotal = ref(0)

// 接口导入弹窗
const importVisible = ref(false)
const importForm = ref({ customer_code: '', operation_user: 'Jason邹启' })
const previewLoading = ref(false)
const importLoading = ref(false)
const previewCustomers = ref([])
const previewTableRef = ref(null)
const selectedNames = ref([])

const profileVisible = ref(false)
const profileLoading = ref(false)
const profile = ref(null)
const regenerating = ref(false)
const activeTab = ref('profile')
const files = ref([])
const filesTotal = ref(0)
const filesLoading = ref(false)
const filesPage = ref(1)
const filesPageSize = ref(10)

const selectedFile = ref(null)
const ocrFileData = ref(null)
const selectedExtraction = ref(null)
const fileDetailVisible = ref(false)
const credVisible = ref(false)
const credDrawerWidth = ref(Math.round(window.innerWidth / 3))
const fileListVisible = ref(false)
const personFilesDrawerWidth = ref(Math.round(window.innerWidth / 3))
const fileListTitle = ref('查看文件')
const fileListFiles = ref([])
const fileListLoading = ref(false)
const profileShift = computed(() => {
  if (credVisible.value) return credDrawerWidth.value
  if (fileListVisible.value) return personFilesDrawerWidth.value
  return 0
})
const profileDialogWidth = computed(() => {
  if (profileShift.value > 0) return (window.innerWidth - profileShift.value - 40) + 'px'
  return '88%'
})
const credData = ref(null)
const credEditValue = ref('')
const credSources = ref([])
const credSaving = ref(false)
const credPreviewVisible = ref(false)
const credPreviewFile = ref(null)

function credTagType(level) {
  return { 高: 'success', 中: 'warning', 低: 'danger' }[level] || 'info'
}

function previewCredSource(src) {
  credPreviewFile.value = { id: src.customer_file_id, filename: src.source }
  credPreviewVisible.value = true
}

// AI 家庭资产合并预览
const dedupeVisible = ref(false)
const dedupePreviewLoading = ref(false)
const dedupeCommitLoading = ref(false)
const dedupeGroups = ref([])
const dedupeAssetsAll = ref([])  // 预览拉的资产快照,用于展示原始信息
const dedupeManualCount = ref(0)

function dedupeAssetOf(aid) {
  return dedupeAssetsAll.value.find(a => a.id === aid)
}

async function openDedupePreview() {
  const hid = profile.value?.task?.household_id
  if (!hid) {
    ElMessage.warning('该任务无家庭上下文')
    return
  }
  dedupeVisible.value = true
  dedupePreviewLoading.value = true
  dedupeGroups.value = []
  dedupeAssetsAll.value = []
  dedupeManualCount.value = 0
  try {
    const r = await dedupeAssetsPreview(hid)
    dedupeAssetsAll.value = r.assets || []
    dedupeGroups.value = r.groups || []
    dedupeManualCount.value = (r.assets || []).filter(a => a.status !== 'ai').length
    if (r.fallback) ElMessage.warning('AI 分析异常已降级,请稍后再试')
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
    dedupeVisible.value = false
  } finally {
    dedupePreviewLoading.value = false
  }
}

async function commitDedupe() {
  const hid = profile.value?.task?.household_id
  if (!hid || !dedupeGroups.value.length) return
  dedupeCommitLoading.value = true
  try {
    const r = await dedupeAssetsCommit(hid, dedupeGroups.value.map(g => ({
      keep_id: g.keep_id,
      merge_ids: g.merge_ids,
      merged_attrs: g.merged_attrs || {},
      merged_name: g.merged_name || '',
      reason: g.reason || '',
    })))
    ElMessage.success(`已合并 ${r.merged_groups} 组,删除 ${r.deleted_rows} 条重复资产`)
    dedupeVisible.value = false
    if (profile.value?.task?.id) await reloadProfile(profile.value.task.id)
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    dedupeCommitLoading.value = false
  }
}

// 人员手动合并(同名双卡;字段仲裁:人工>AI,同 AI 晚者胜)
const mergeVisible = ref(false)
const mergeSource = ref(null)
const mergeTargetId = ref(null)
const mergeKeepName = ref('')
const merging = ref(false)
const mergeCandidates = computed(() =>
  (profile.value?.persons || []).filter(p => p.id !== mergeSource.value?.id))
const mergeTargetPerson = computed(() =>
  mergeCandidates.value.find(p => p.id === mergeTargetId.value) || null)

watch(mergeTargetId, (id) => {
  mergeKeepName.value = mergeCandidates.value.find(p => p.id === id)?.name || ''
})

function openMerge(p) {
  mergeSource.value = p
  mergeTargetId.value = null
  mergeKeepName.value = ''
  mergeVisible.value = true
}

async function confirmMerge() {
  if (!mergeSource.value || !mergeTargetId.value) return
  const target = mergeCandidates.value.find(p => p.id === mergeTargetId.value)
  const keepName = mergeKeepName.value || target?.name
  try {
    await ElMessageBox.confirm(
      `确定把「${mergeSource.value.name}」并入「${target?.name}」?合并后姓名保留为「${keepName}」,被合并的卡将删除,不可撤销。`,
      '确认合并',
      { confirmButtonText: '确认合并', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  merging.value = true
  try {
    const r = await mergeProfilePersons(mergeTargetId.value, mergeSource.value.id, keepName)
    ElMessage.success(`已合并:迁移 ${r.fields_moved} 个字段,重挂 ${r.files_repointed} 个文件`)
    mergeVisible.value = false
    if (profile.value?.task?.id) await reloadProfile(profile.value.task.id)
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    merging.value = false
  }
}
const fileExtractions = computed(() => {
  const sid = selectedFile.value?.id
  if (!sid) return []
  return (profile.value?.extractions || []).filter((e) => e.customer_file_id === sid)
})
const rawState = ref({ url: '', isImage: false, hint: '原件加载中…', _revoke: null })

const reviewDrawerRef = ref(null)
const editingPersonId = ref(null)
const editBuffer = ref({})
const editRelation = ref('')
const editSaving = ref(false)

const relationOptions = ['配偶', '子', '女', '父', '母', '待确认']

const matrix = ref({ persons: [], columns: [], cells: {} })
const matrixLoading = ref(false)

async function loadTasks() {
  loading.value = true
  try {
    const data = await listProfileTasks({
      status: taskStatus.value || undefined,
      client_name: taskClientName.value || undefined,
      customer_code: taskCustomerCode.value || undefined,
      limit: taskPageSize.value,
      offset: (taskPage.value - 1) * taskPageSize.value,
    })
    tasks.value = data.items
    taskTotal.value = data.total
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    loading.value = false
  }
}

const resuming = ref(false)

async function onResumeStale() {
  try {
    await ElMessageBox.confirm(
      '将恢复所有因服务重启而中断的导入任务(已完成的文件不会重跑),任务较多时会跑较久。继续?',
      '恢复中断任务',
      { type: 'warning', confirmButtonText: '恢复', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  resuming.value = true
  try {
    const r = await resumeStaleProfileTasks()
    if (r.resumed?.length) {
      ElMessage.success(`已恢复 ${r.resumed.length} 个中断任务,后台继续运行,可点刷新查看进度`)
    } else {
      ElMessage.info(r.skipped_running ? '所有进行中的任务都在正常运行,无需恢复' : '没有中断的任务')
    }
    loadTasks()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    resuming.value = false
  }
}

function onTaskQuery() {
  taskPage.value = 1
  loadTasks()
}

function onTaskReset() {
  taskStatus.value = ''
  taskClientName.value = ''
  taskCustomerCode.value = ''
  taskPage.value = 1
  loadTasks()
}

function onTaskSizeChange() {
  taskPage.value = 1
  loadTasks()
}

async function onDeleteTask(row) {
  let msg = row.household_id
    ? `确定删除任务 #${row.id}（${row.client_name}）的画像？将删除该家庭的客户画像数据(人员/字段/资产/案件)；文件记录、提取结果与 OCR 文本全部保留，重新导入可直接复用 OCR 重建画像。`
    : `确定删除任务 #${row.id}（${row.client_name}）？将删除该任务的文件/OCR/提取结果及磁盘原件。删除后无法恢复。`
  if (row.status === 'running') {
    msg += ' 任务正在运行，删除后后台处理将停止。'
  }
  try {
    await ElMessageBox.confirm(msg, '确认删除', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    await deleteProfileTask(row.id)
    ElMessage.success('已删除')
  } catch (err) {
    if (err === 'cancel') return
    ElMessage.error('删除失败：' + (err.response?.data?.detail || err.message))
    return
  }
  // 删除的是当前打开画像弹窗的任务,或整家庭强删波及同家庭任务时,关掉弹窗(右侧抽屉由 watch 联动关闭)
  if (profileVisible.value && (profile.value?.task?.id === row.id
      || (row.household_id && profile.value?.task?.household_id === row.household_id))) {
    profileVisible.value = false
  }
  loadTasks()
}

async function onRegenerate() {
  const household = profile.value?.household
  if (!household?.id) {
    ElMessage.warning('该任务没有关联家庭,无法重新生成')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定重新生成「${household.name}」的画像？将直接刷新当前这条画像的内容：家庭名下全部文件按当前规则重跑分类/提取/归因（已有 OCR 直接复用，缺 OCR 重新识别，缺文件重新下载），不新建任务。人工已确认/修正的字段不会被覆盖。`,
      '确认重新生成',
      { type: 'warning', confirmButtonText: '重新生成', cancelButtonText: '取消' })
  } catch { return }
  regenerating.value = true
  try {
    const r = await regenerateHouseholdProfile(household.id, profile.value?.task?.id)
    ElMessage.success(`已开始重新生成,共 ${r.total_files} 个文件,后台运行中`)
    if (r.task_id && r.task_id === profile.value?.task?.id) {
      pollRegenerate(r.task_id)
    } else {
      // 兜底:宿主任务不是当前弹窗任务时,关弹窗回任务列表看进度
      profileVisible.value = false
      loadTasks()
    }
  } catch (err) {
    ElMessage.error('重新生成失败:' + (err.response?.data?.detail || err.message))
  } finally {
    regenerating.value = false
  }
}

let regenPollTimer = null
function stopRegenPoll() {
  if (regenPollTimer) { clearTimeout(regenPollTimer); regenPollTimer = null }
}
function pollRegenerate(taskId) {
  stopRegenPoll()
  const tick = async () => {
    regenPollTimer = null
    if (!profileVisible.value || profile.value?.task?.id !== taskId) return
    await reloadProfile(taskId, true)
    if (profile.value?.task?.status === 'running') {
      regenPollTimer = setTimeout(tick, 3000)
    } else {
      loadTasks()
    }
  }
  regenPollTimer = setTimeout(tick, 2000)
}

function openImportDialog() {
  previewCustomers.value = []
  selectedNames.value = []
  importVisible.value = true
}

async function onPreview() {
  previewLoading.value = true
  try {
    const r = await previewProfileRemoteImport({
      customer_code: importForm.value.customer_code.trim(),
      operation_user: importForm.value.operation_user.trim(),
    })
    previewCustomers.value = r.customers || []
    selectedNames.value = []
    if (!previewCustomers.value.length) {
      ElMessage.warning('接口未返回客户数据')
      return
    }
    await nextTick()
    previewTableRef.value?.toggleAllSelection()
  } catch (err) {
    ElMessage.error('预览失败:' + (err.response?.data?.detail || err.message))
  } finally {
    previewLoading.value = false
  }
}

function onPreviewSelectionChange(rows) {
  selectedNames.value = rows.map(r => r.customer_name)
}

async function onConfirmImport() {
  importLoading.value = true
  try {
    const r = await importProfileRemote({
      customer_code: importForm.value.customer_code.trim(),
      operation_user: importForm.value.operation_user.trim(),
      customer_names: selectedNames.value,
    })
    const created = r.tasks || []
    const skipped = r.skipped_running || []
    const totalFiles = created.reduce((s, t) => s + (t.total_files || 0), 0)
    if (created.length) {
      ElMessage.success(`已创建 ${created.length} 个导入任务,共 ${totalFiles} 个文件,后台运行中`)
    }
    if (skipped.length) {
      ElMessage.warning(`以下客户已有运行中的任务,已跳过:${skipped.join('、')}`)
    }
    importVisible.value = false
    await loadTasks()
    if (created.length === 1) openProfile({ id: created[0].task_id })
  } catch (err) {
    ElMessage.error('导入失败:' + (err.response?.data?.detail || err.message))
  } finally {
    importLoading.value = false
  }
}

async function openProfile(row) {
  profileVisible.value = true
  activeTab.value = 'profile'
  filesPage.value = 1
  selectedFile.value = null
  await reloadProfile(row.id)
}

watch(profileVisible, (v) => {
  if (!v) {
    stopRegenPoll()
    credVisible.value = false
    fileListVisible.value = false
  }
})

async function reloadProfile(taskId, silent = false) {
  if (!silent) profileLoading.value = true
  try {
    profile.value = await getProfileTaskProfile(taskId)
    await loadFiles(taskId, silent)
    loadMatrix(taskId)
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    if (!silent) profileLoading.value = false
  }
}

async function loadMatrix(taskId) {
  matrixLoading.value = true
  try {
    matrix.value = await getProfileTaskMatrix(taskId)
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    matrixLoading.value = false
  }
}

function cellOf(person, col) {
  return (matrix.value.cells?.[person.id] || {})[col.key] || { status: 'missing', files: [] }
}

function cellText(status) {
  return { ok: '✓', warn: '!', missing: '✗', na: '—' }[status] || '?'
}

// 护照到期提醒:后端 attach_passport_expiry 已算好 level/days_left,这里只做文案
const passportAlerts = computed(() => {
  const out = []
  for (const p of profile.value?.persons || []) {
    const e = p.passport_expiry
    if (!e || e.level === 'ok') continue
    out.push({
      level: e.level,
      text: e.level === 'expired'
        ? `${p.name}护照已过期 ${-e.days_left} 天(${e.date})`
        : `${p.name} ${e.date} 到期(剩 ${e.days_left} 天)`,
    })
  }
  return out
})

function expiryTagText(e) {
  return e.level === 'expired' ? `已过期${-e.days_left}天` : `剩${e.days_left}天`
}

// 多源字段提醒:字段可信度里 conflict_count>0 的字段汇总(覆盖全字段,不再仅限 8 个身份字段)
const conflictAlerts = computed(() => {
  const out = []
  for (const p of profile.value?.persons || []) {
    const labels = (p.fields || [])
      .filter((f) => (f.credibility?.conflict_count || 0) > 0)
      .map((f) => f.label || f.field)
    if (labels.length) out.push(`${p.name} ${labels.join('/')}`)
  }
  return out
})

async function onCellClick(person, col) {
  const cell = cellOf(person, col)
  if (cell.status === 'warn') {
    const target = cell.files.find((f) => f.review_status === 'needs_review') || cell.files[0]
    if (target) await openReviewCenter({ id: target.id })
  } else if (cell.status === 'ok' && cell.files.length) {
    activeTab.value = 'files'
    await selectFile({ id: cell.files[0].id })
  }
}

async function loadFiles(taskId, silent = false) {
  if (!silent) filesLoading.value = true
  try {
    const f = await listProfileTaskFiles(taskId, {
      limit: filesPageSize.value,
      offset: (filesPage.value - 1) * filesPageSize.value,
    })
    files.value = f.items
    filesTotal.value = f.total
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    if (!silent) filesLoading.value = false
  }
}

function onFilesPageChange() {
  if (profile.value?.task?.id) loadFiles(profile.value.task.id)
}

function onFilesSizeChange() {
  filesPage.value = 1
  onFilesPageChange()
}

async function selectFile(row) {
  selectedFile.value = row
  fileDetailVisible.value = true
  ocrFileData.value = null
  selectedExtraction.value = null
  loadRaw(row.id, row.filename)
  try {
    ocrFileData.value = await getCustomerFile(row.id)
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  }
  const exs = fileExtractions.value
  if (exs.length) selectedExtraction.value = exs[0]
}

// ---- 字段来源与可信度(徽标点击 → 统一抽屉) ----

function startResizeCred(e) {
  e.preventDefault()
  const onMove = (ev) => {
    const w = window.innerWidth - ev.clientX
    credDrawerWidth.value = Math.min(Math.max(w, 300), window.innerWidth - 120)
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

function openCredibility(p, f) {
  credData.value = { personId: p.id, field: f.field, label: f.label || f.field, value: f.value || '', credibility: f.credibility || null }
  credEditValue.value = f.value || ''
  credVisible.value = true
  // 来源按 (customer_file_id, value) 去重(重复导入/重新生成会产生重复提取记录)
  const seen = new Set()
  const sources = []
  for (const s of f.credibility?.sources || []) {
    const k = `${s.customer_file_id}|${s.value}`
    if (!seen.has(k)) {
      seen.add(k)
      sources.push(s)
    }
  }
  credSources.value = sources
}

async function saveCredibility() {
  // 原样保存不提交:避免未改动字段被后端误标 status=corrected(「已修正」)
  if ((credEditValue.value ?? '').toString().trim() === (credData.value.value || '').trim()) {
    ElMessage.info('没有改动')
    credVisible.value = false
    return
  }
  credSaving.value = true
  try {
    await correctPersonField(credData.value.personId, { [credData.value.field]: credEditValue.value })
    ElMessage.success('已更新')
    credVisible.value = false
    if (profile.value?.task?.id) await reloadProfile(profile.value.task.id)
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    credSaving.value = false
  }
}

// ---- 原件查看(blob 带鉴权;图片内联,PDF/Office 转 PDF 走 iframe) ----

const OFFICE_EXTS = ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']

function isOfficeFile(filename) {
  const lower = (filename || '').toLowerCase()
  return OFFICE_EXTS.some((e) => lower.endsWith(e))
}

async function loadRaw(fileId, filename) {
  if (rawState.value._revoke) rawState.value._revoke()
  rawState.value = { url: '', isImage: false, hint: '原件加载中…', _revoke: null }
  // Office 原件:优先 soffice 转 PDF 预览;失败回落提示
  if (isOfficeFile(filename)) {
    try {
      const raw = await fetchCustomerFilePreviewPdfUrl(fileId)
      rawState.value = { url: raw.blobUrl, isImage: false, hint: '', _revoke: raw.revoke }
    } catch (err) {
      rawState.value = { url: '', isImage: false, hint: '该文件类型不支持在线预览', _revoke: null }
    }
    return
  }
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

// ---- 复核中心(抽屉已抽为 ReviewDrawer 组件,全局复核中心页复用) ----

function openReviewCenter(targetItem = null) {
  reviewDrawerRef.value?.open(targetItem)
}

async function openPersonFiles(p) {
  fileListTitle.value = `人员文件 · ${p.name || ''}`
  fileListVisible.value = true
  await loadFileList(() => listPersonFiles(p.id))
}

async function openAssetFiles() {
  const hid = profile.value?.household?.id
  if (!hid) return
  fileListTitle.value = '资产相关文件'
  fileListVisible.value = true
  await loadFileList(() => listHouseholdAssetFiles(hid))
}

async function loadFileList(fetcher) {
  fileListLoading.value = true
  fileListFiles.value = []
  try {
    fileListFiles.value = await fetcher()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    fileListLoading.value = false
  }
}

function startEdit(p) {
  editingPersonId.value = p.id
  editBuffer.value = Object.fromEntries((p.fields || []).map((f) => [f.field, f.value || '']))
  editRelation.value = p.is_main ? '' : (p.relation_to_main || '待确认')
}

function cancelEdit() {
  editingPersonId.value = null
  editBuffer.value = {}
  editRelation.value = ''
}

async function saveEdit(p) {
  // 只提交真正改动的字段:后端 correct_person_field 是"永远覆盖并标 corrected"语义,
  // 整包 editBuffer 提交会把未改动字段也误标成「已修正」
  const orig = Object.fromEntries((p.fields || []).map((f) => [f.field, (f.value || '').trim()]))
  const changedFields = {}
  for (const [field, raw] of Object.entries(editBuffer.value)) {
    if ((raw ?? '').toString().trim() !== (orig[field] ?? '')) changedFields[field] = raw
  }
  const relationChanged = !p.is_main && editRelation.value && editRelation.value !== p.relation_to_main
  if (!Object.keys(changedFields).length && !relationChanged) {
    ElMessage.info('没有改动')
    cancelEdit()
    return
  }
  editSaving.value = true
  try {
    await correctPersonField(
      p.id, changedFields, undefined,
      relationChanged ? editRelation.value : undefined,
    )
    ElMessage.success('已保存')
    editingPersonId.value = null
    editBuffer.value = {}
    editRelation.value = ''
    if (profile.value?.task?.id) await reloadProfile(profile.value.task.id)
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    editSaving.value = false
  }
}

function onReviewDone() {
  loadTasks()
  if (profile.value?.task?.id) reloadProfile(profile.value.task.id)
}

function taskTag(s) {
  return { running: 'warning', done: 'success', error: 'danger' }[s] || 'info'
}
function taskLabel(s) {
  return { running: '进行中', done: '完成', error: '失败' }[s] || s
}
function passportExpiryClass(dateStr) {
  // 与画像弹窗一致的 180 天预警阈值,过期红色/临期橙色
  if (!dateStr) return ''
  const d = Date.parse(dateStr)
  if (isNaN(d)) return ''
  const days = Math.floor((d - Date.now()) / 86400000)
  if (days < 0) return 'err-text'
  if (days <= 180) return 'warn-text'
  return ''
}
function fileTag(s) {
  return { done: 'success', error: 'danger', fetching: 'warning', ocr: 'warning', pending: 'info' }[s] || 'info'
}
function fileLabel(s) {
  return { pending: '待处理', fetching: '下载中', ocr: 'OCR中', done: '完成', error: '失败' }[s] || s
}
function caseStatusTag(s) {
  return { 已签收: 'success', 已获批: 'primary', 已交付: 'warning', 已递交: 'warning' }[s] || 'info'
}
function ocrSourceLabel(s) {
  return { fresh: '新识别', reused: '复用', none: '-' }[s] || s || '-'
}
function assetAttrLabel(k) {
  return {
    address: '坐落', area: '面积(㎡)', usage: '用途', right_type: '权利类型',
    right_status: '权利状态', register_date: '登记日期', cert_no: '权证号',
    co_ownership: '共有情况', land_term: '土地使用期限', other_rights: '他项权利',
  }[k] || k
}
function personName(pid) {
  const p = (profile.value?.persons || []).find((x) => x.id === pid)
  return p ? p.name : `#${pid}`
}
function classifyLabel(s) {
  return { keyword: '关键词', llm: 'AI' }[s] || s
}
function extractTag(s) {
  return { done: 'success', error: 'danger', skipped: 'info' }[s] || 'info'
}
function extractLabel(row) {
  if (row.status === 'skipped') return `跳过(${row.skip_reason || ''})`
  return { done: '完成', error: '失败' }[row.status] || row.status
}
function writeStatsText(ws) {
  if (!ws) return '-'
  const parts = []
  if (ws.person_count > 1) parts.push(`${ws.person_count}人`)
  if (ws.client_fields) parts.push(`客户+${ws.client_fields}`)
  if (ws.member_fields) parts.push(`成员+${ws.member_fields}`)
  if (ws.member_created) parts.push('新建成员')
  if (ws.asset_created) parts.push('新建资产')
  if (ws.asset_updated) parts.push('更新资产')
  if (ws.milestone_created) parts.push(`里程碑+${ws.milestone_created}`)
  if (ws.milestone_updated) parts.push('更新里程碑')
  if (ws.matched_by) parts.push(`匹配:${{ id_number: '证件号', name: '姓名', name_en: '拼音名' }[ws.matched_by] || ws.matched_by}`)
  return parts.join(' · ') || '无写入'
}
function prettyJson(v) {
  if (v == null) return '-'
  return JSON.stringify(v, null, 2)
}

onMounted(() => {
  loadTasks()
})
</script>

<style scoped>
.profile-dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-right: 32px; /* 避开右上角关闭按钮 */
}
.profile-dialog-title {
  font-size: 16px;
  font-weight: 600;
}
.profile-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #f0f2f8;
  overflow: hidden;
}
.profile-header {
  height: 56px;
  flex-shrink: 0;
  padding: 0 24px;
  background: #fff;
  border-bottom: 1px solid #e8ebf5;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.profile-main {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
  padding: 18px 24px 32px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.profile-title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 16px;
  font-weight: 700;
  color: #1e293b;
}
.title-indicator {
  display: inline-block;
  width: 3px;
  height: 16px;
  background: linear-gradient(180deg, #409eff, #337ecc);
  border-radius: 2px;
}
.card {
  background: #fff;
  border: 1px solid #e8ebf5;
  border-radius: 12px;
  padding: 16px 18px;
}
.card-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 10px;
}
.filter-grid {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.inner-card {
  margin-bottom: 14px;
}
.dim {
  color: #909399;
}
.mono {
  font-family: Consolas, monospace;
}
.err-text {
  color: #f56c6c;
}
.warn-text {
  color: #e6a23c;
}
.asset-card-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.dedupe-group {
  margin-bottom: 14px;
  background: #f8f9fb;
}
.dedupe-src {
  margin-bottom: 12px;
}
.dedupe-src-title {
  font-size: 12px;
  margin-bottom: 6px;
}
.dedupe-src-item {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 4px 8px;
  font-size: 13px;
  border-bottom: 1px dashed #ebeef5;
}
.dedupe-src-item:last-child {
  border-bottom: none;
}
.dedupe-target {
  background: #fff;
  padding: 10px 12px;
  border: 1px solid #c2e7b0;
  border-radius: 6px;
}
.dedupe-target-title {
  font-size: 13px;
  font-weight: 600;
  color: #67c23a;
  margin-bottom: 6px;
}
.type-counts {
  font-size: 12px;
  color: #606266;
}
.task-summary {
  display: flex;
  gap: 16px;
  align-items: center;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.profile-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}
@media (max-width: 1200px) {
  .profile-columns {
    grid-template-columns: 1fr;
  }
}
.detail-meta {
  display: flex;
  gap: 18px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.pagination-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

/* ---- 护照到期提醒 ---- */
.expiry-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  margin-bottom: 14px;
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 8px;
  color: #e6a23c;
  font-size: 13px;
}
.expiry-banner.expired {
  background: #fef0f0;
  border-color: #fde2e2;
  color: #f56c6c;
}
.expiry-tag {
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 4px;
  font-size: 12px;
}
.expiry-tag.expiring {
  background: #fdf6ec;
  color: #e6a23c;
}
.expiry-tag.expired {
  background: #fef0f0;
  color: #f56c6c;
}

/* ---- 字段冲突提醒 ---- */
.conflict-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  margin-bottom: 14px;
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 8px;
  color: #b88230;
  font-size: 13px;
}
/* ---- 字段可信度徽标 + 来源抽屉 ---- */
.cred-badge {
  margin-left: 6px;
  padding: 0 6px;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
  border: 1px solid;
}
.cred-badge.高 {
  background: #f0f9eb;
  color: #67c23a;
  border-color: #e1f3d8;
}
.cred-badge.中 {
  background: #fdf6ec;
  color: #b88230;
  border-color: #faecd8;
}
.cred-badge.低 {
  background: #fef0f0;
  color: #f56c6c;
  border-color: #fde2e2;
}
.cred-summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
  font-size: 12px;
}
.cred-score {
  color: #606266;
  font-weight: 600;
}
.cred-reason {
  color: #909399;
  background: #f4f4f5;
  border-radius: 4px;
  padding: 1px 6px;
}
.cred-agree-tag {
  flex-shrink: 0;
}
.cred-src-value {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #303133;
  flex-shrink: 0;
}
.cred-footnote {
  margin-top: 10px;
  font-size: 12px;
}
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
.conflict-dialog-body {
  padding: 0 4px;
  display: flex;
  flex-direction: column;
  height: calc(100vh - 140px);
}
.conflict-edit {
  display: flex;
  align-items: center;
  gap: 10px;
}
.conflict-field-label {
  width: 110px;
  flex-shrink: 0;
  color: #909399;
  font-size: 13px;
}
.conflict-source-list {
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  overflow-y: auto;
  flex: 1;
  min-height: 0;
}
.conflict-source-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  font-size: 12px;
  border-bottom: 1px solid #f0f0f0;
}
.conflict-source-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ---- 成员卡 ---- */
.person-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 14px;
  margin-bottom: 14px;
}
.person-card {
  margin-bottom: 0 !important;
}
.person-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  padding-bottom: 10px;
  border-bottom: 1px solid #ebeef5;
}
.person-title {
  flex: 1;
}
.person-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.person-avatar {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: #e4e7ed;
  color: #606266;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;
  font-weight: 600;
}
.person-avatar.main {
  background: #409eff;
  color: #fff;
}
.person-name {
  font-size: 15px;
  font-weight: 600;
  margin-right: 8px;
}
.field-group-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 2px 16px;
}
.field-group-title {
  margin-top: 10px;
  padding-top: 6px;
  border-top: 1px solid #ebeef5;
  font-size: 12px;
  color: #909399;
  font-weight: 600;
}
.field-group-title:first-of-type {
  margin-top: 0;
  padding-top: 0;
  border-top: none;
}
.field-row {
  display: flex;
  padding: 3px 0;
  font-size: 13px;
}
.field-label {
  width: 88px;
  flex-shrink: 0;
  color: #909399;
}
.field-value {
  color: #303133;
  word-break: break-all;
}
.status-mark {
  margin-left: 6px;
  font-size: 11px;
}
.status-mark.corrected {
  color: #409eff;
}
.status-mark.confirmed {
  color: #67c23a;
}

/* ---- OCR 对照弹窗面板 ---- */
.ocr-panes {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
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
  height: 520px;
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
.ocr-view {
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  user-select: text;
  margin: 0;
}
.raw-iframe {
  width: 100%;
  height: 100%;
  border: none;
}

/* ---- 文件清单三栏详情 ---- */
.file-detail-layout {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 12px;
  margin-top: 14px;
}
.extract-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 8px;
  padding-bottom: 8px;
  border-bottom: 1px solid #e4e7ed;
}
.extract-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
}
.extract-item:hover {
  background: #ecf5ff;
}
.extract-item.active {
  background: #d9ecff;
}

/* ---- 完备度矩阵 ---- */
.matrix-cell {
  display: inline-block;
  width: 26px;
  height: 26px;
  line-height: 26px;
  border-radius: 50%;
  font-weight: 700;
  cursor: pointer;
}
.matrix-cell.ok {
  background: #f0f9eb;
  color: #67c23a;
  border: 1px solid #c2e7b0;
}
.matrix-cell.warn {
  background: #fdf6ec;
  color: #e6a23c;
  border: 1px solid #f5dab1;
}
.matrix-cell.missing {
  background: #fef0f0;
  color: #f56c6c;
  border: 1px solid #fde2e2;
  cursor: default;
}
.matrix-cell.na {
  background: #f4f4f5;
  color: #c0c4cc;
  border: 1px solid #e9e9eb;
  cursor: default;
}
.matrix-legend {
  margin-top: 12px;
  font-size: 12px;
}
.matrix-legend .matrix-cell {
  width: 18px;
  height: 18px;
  line-height: 18px;
  font-size: 11px;
  cursor: default;
}
.case-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.case-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  font-size: 12px;
  margin-top: 6px;
}
.case-timeline {
  margin-top: 12px;
  padding-left: 4px;
}
.milestone-row {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.milestone-src {
  font-size: 12px;
}
.json-block {
  margin-bottom: 12px;
}
.json-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 4px;
}
.json-block pre {
  background: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 10px;
  max-height: 260px;
  overflow: auto;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}
.ocr-text {
  background: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 12px;
  max-height: 60vh;
  overflow: auto;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  user-select: text;
}
</style>

<style>
.side-drawer-overlay {
  pointer-events: none;
}
.side-drawer-overlay .el-drawer,
.side-drawer-overlay .el-dialog {
  pointer-events: auto;
}
</style>
