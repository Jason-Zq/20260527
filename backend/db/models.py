"""
ORM 模型定义
核心表：clients（客户主档）、family_members（家庭成员）、assets（资产）、
documents（文档/解析记录）、client_info（KV 兜底）、templates、template_fills、split_tasks。

clients/family_members/assets 是移民客户档案的强 schema。
client_info 仍保留作为没纳入强 schema 的字段的 KV 兜底。
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Float, Numeric, Boolean, Date,
    DateTime, ForeignKey, Index, CheckConstraint
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """ORM 基类"""
    pass


class Client(Base):
    """客户主表（移民客户档案）。
    身份/联系/护照/教育/工作/婚姻/业务标签，~33 字段。
    """
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # ---- 身份（12） ----
    client_code = Column(String(30), unique=True, nullable=True, comment="客户编号（手动填入）")
    name = Column(String(100), nullable=False, comment="客户姓名")
    name_en = Column(String(100), nullable=True, comment="拼音/英文名")
    former_name = Column(String(100), nullable=True, comment="曾用名")
    gender = Column(String(10), nullable=True, comment="性别")
    birth_date = Column(Date, nullable=True, comment="出生日期")
    birth_place = Column(String(200), nullable=True, comment="出生地")
    ethnicity = Column(String(50), nullable=True, comment="民族")
    nationality = Column(String(50), nullable=True, comment="国籍")
    id_number = Column(String(50), unique=True, nullable=True, comment="身份证号")
    hukou_address = Column(String(300), nullable=True, comment="户籍地址")
    marital_status = Column(String(20), nullable=True, comment="婚姻状况")

    # ---- 联系方式（3） ----
    phone = Column(String(30), nullable=True, comment="手机")
    email = Column(String(100), nullable=True, comment="邮箱")
    current_address = Column(String(300), nullable=True, comment="现家庭住址")

    # ---- 护照（4） ----
    passport_no = Column(String(50), nullable=True, comment="护照号")
    passport_issue_date = Column(Date, nullable=True, comment="护照签发日期")
    passport_expiry_date = Column(Date, nullable=True, comment="护照到期日期")
    passport_issuing_authority = Column(String(100), nullable=True, comment="护照签发机关")

    # ---- 教育（7，最高学历） ----
    school_name = Column(String(200), nullable=True, comment="学校名")
    school_name_en = Column(String(200), nullable=True, comment="英文校名")
    major = Column(String(100), nullable=True, comment="专业")
    degree = Column(String(50), nullable=True, comment="学位")
    graduation_date = Column(Date, nullable=True, comment="毕业日期（NULL=在读）")
    graduation_cert_no = Column(String(50), nullable=True, comment="毕业证编号")
    degree_cert_no = Column(String(50), nullable=True, comment="学位证编号")

    # ---- 工作（4，当前工作） ----
    company_name = Column(String(200), nullable=True, comment="公司名")
    position = Column(String(100), nullable=True, comment="职位")
    employment_start_date = Column(Date, nullable=True, comment="入职日期")
    monthly_salary = Column(Numeric(12, 2), nullable=True, comment="月薪")

    # ---- 婚姻（3，结婚证） ----
    marriage_date = Column(Date, nullable=True, comment="结婚登记日期")
    marriage_authority = Column(String(100), nullable=True, comment="结婚登记机关")
    marriage_cert_no = Column(String(50), nullable=True, comment="结婚证编号")

    # ---- 业务+审计 ----
    visa_type = Column(String(50), nullable=True, comment="业务类型标签")
    consultant = Column(String(100), nullable=True, comment="所属顾问（保留兼容）")
    notes = Column(Text, nullable=True, comment="备注")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")

    # 关系
    documents = relationship("Document", back_populates="client")
    info_items = relationship("ClientInfo", back_populates="client")
    family_members = relationship("FamilyMember", back_populates="client", cascade="all, delete-orphan")
    assets = relationship("Asset", back_populates="client", cascade="all, delete-orphan")

    # 索引
    __table_args__ = (
        Index("ix_clients_passport_expiry", "passport_expiry_date"),
        Index("ix_clients_visa_type", "visa_type"),
    )

    def __repr__(self):
        return f"<Client(id={self.id}, name='{self.name}', client_code='{self.client_code}')>"


class FamilyMember(Base):
    """家庭成员子表。
    容纳：配偶 / 子女 / 父母 / 紧急联系人。
    relation 区分；配偶教育字段与主申一致；子女出生信息来自出生医学证。
    """
    __tablename__ = "family_members"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, comment="关联客户")

    # ---- 基本（10） ----
    relation = Column(String(20), nullable=False, comment="配偶/子/女/父/母/紧急联系人")
    name = Column(String(100), nullable=False, comment="姓名")
    name_en = Column(String(100), nullable=True)
    gender = Column(String(10), nullable=True)
    birth_date = Column(Date, nullable=True)
    nationality = Column(String(50), nullable=True)
    id_number = Column(String(50), nullable=True, comment="身份证号")
    phone = Column(String(30), nullable=True)

    # ---- POA 模板必需（5） ----
    passport_no = Column(String(50), nullable=True)
    email = Column(String(100), nullable=True)
    current_address = Column(String(300), nullable=True)
    company_name = Column(String(200), nullable=True)
    position = Column(String(100), nullable=True)

    # ---- 配偶教育（7） ----
    school_name = Column(String(200), nullable=True)
    school_name_en = Column(String(200), nullable=True)
    major = Column(String(100), nullable=True)
    degree = Column(String(50), nullable=True)
    graduation_date = Column(Date, nullable=True)
    graduation_cert_no = Column(String(50), nullable=True)
    degree_cert_no = Column(String(50), nullable=True)

    # ---- 子女出生（3，来自出生医学证） ----
    birth_cert_no = Column(String(50), nullable=True, comment="出生医学证编号")
    birth_hospital = Column(String(200), nullable=True, comment="出生医院")
    birth_place = Column(String(200), nullable=True)

    # ---- 其他 ----
    will_accompany = Column(Boolean, default=False, comment="是否随行")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    client = relationship("Client", back_populates="family_members")

    __table_args__ = (
        Index("ix_family_members_client_relation", "client_id", "relation"),
    )

    def __repr__(self):
        return f"<FamilyMember(id={self.id}, client_id={self.client_id}, relation='{self.relation}', name='{self.name}')>"


class Asset(Base):
    """资产子表（房产/存款/银行流水/股票/车辆/其他）。
    asset_type 区分；房产用 location_*/area/usage 等列；银行用 bank_name/account_no/period_* 等列。
    """
    __tablename__ = "assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, comment="关联客户")

    asset_type = Column(String(20), nullable=False, comment="房产/存款/银行流水/股票/车辆/其他")
    asset_name = Column(String(300), nullable=True, comment="资产名称（地址或银行+期次）")
    owner_name = Column(String(100), nullable=True, comment="权利人/户名")
    co_owners = Column(String(300), nullable=True, comment="共有人（房产）")
    value_amount = Column(Numeric(18, 2), nullable=True, comment="金额或估值")
    currency = Column(String(10), nullable=True, comment="币种")
    certificate_no = Column(String(50), nullable=True, comment="产权证号/存单号/证明编号")

    # 房产专用
    location_address = Column(String(300), nullable=True, comment="坐落地址")
    area_sqm = Column(Numeric(10, 2), nullable=True, comment="面积（平米）")
    usage_type = Column(String(20), nullable=True, comment="住宅/商业/工业")
    acquired_date = Column(Date, nullable=True, comment="取得日期")

    # 银行专用
    bank_name = Column(String(100), nullable=True)
    account_no = Column(String(50), nullable=True)
    period_start = Column(Date, nullable=True, comment="起息日/流水起")
    period_end = Column(Date, nullable=True, comment="到期日/流水止")
    frozen_until = Column(Date, nullable=True, comment="冻结期")

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    client = relationship("Client", back_populates="assets")

    __table_args__ = (
        Index("ix_assets_client_type", "client_id", "asset_type"),
    )

    def __repr__(self):
        return f"<Asset(id={self.id}, client_id={self.client_id}, asset_type='{self.asset_type}')>"


class Document(Base):
    """文档/解析记录表"""
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True, comment="关联客户（可为空）")
    task_id = Column(String(200), unique=True, nullable=False, comment="任务ID，对应 output/ 目录名")
    filename = Column(String(500), nullable=False, comment="原始文件名")
    doc_type = Column(String(50), nullable=True, comment="证件类型")
    file_path = Column(String(500), nullable=True, comment="文件存储相对路径")
    ocr_text = Column(Text, nullable=True, comment="OCR 全文")
    extracted_fields = Column(JSONB, nullable=True, comment="AI 提取的结构化字段")
    confidence_avg = Column(Float, nullable=True, comment="平均置信度")
    reviewed = Column(Boolean, default=False, comment="是否已人工复核")
    status = Column(String(20), default="ocr", comment="状态: ocr/llm/done/error")
    error_msg = Column(Text, nullable=True, comment="错误信息")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")

    # 关系
    client = relationship("Client", back_populates="documents")
    info_items = relationship("ClientInfo", back_populates="source_doc")

    # 索引
    __table_args__ = (
        Index("ix_documents_extracted_fields", "extracted_fields", postgresql_using="gin"),
        Index("ix_documents_status", "status"),
        Index("ix_documents_created_at", "created_at"),
    )

    def __repr__(self):
        return f"<Document(id={self.id}, task_id='{self.task_id}', doc_type='{self.doc_type}')>"


class ClientInfo(Base):
    """客户关键信息表（KV 兜底，存未纳入强 schema 的字段）"""
    __tablename__ = "client_info"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False, comment="关联客户")
    info_key = Column(String(100), nullable=False, comment="字段名（如：身份证号、有效期限）")
    info_value = Column(Text, nullable=True, comment="字段值")
    source_doc_id = Column(Integer, ForeignKey("documents.id"), nullable=True, comment="来源文档")
    valid_from = Column(Date, nullable=True, comment="生效日期")
    valid_until = Column(Date, nullable=True, comment="到期日期（供定时任务用）")
    confirmed = Column(Boolean, default=False, comment="是否人工确认")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")

    # 关系
    client = relationship("Client", back_populates="info_items")
    source_doc = relationship("Document", back_populates="info_items")

    # 索引
    __table_args__ = (
        Index("ix_client_info_valid_until", "valid_until"),
        Index("ix_client_info_client_key", "client_id", "info_key"),
    )

    def __repr__(self):
        return f"<ClientInfo(id={self.id}, client_id={self.client_id}, key='{self.info_key}')>"


class Template(Base):
    """Word 模板表"""
    __tablename__ = "templates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, comment="模板名称（用户命名）")
    filename = Column(String(500), nullable=True, comment="原始上传文件名")
    file_path = Column(String(500), nullable=True, comment="docx 模板存储相对路径")
    placeholders = Column(JSONB, nullable=True, comment="占位符列表 [{id,description,original_text}]")
    created_by = Column(String(100), nullable=True, comment="上传人（预留）")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")

    fills = relationship("TemplateFill", back_populates="template", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Template(id={self.id}, name='{self.name}')>"


class TemplateFill(Base):
    """模板填充历史表（同时充当 (template_id,client_id) 映射缓存）"""
    __tablename__ = "template_fills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    template_id = Column(Integer, ForeignKey("templates.id"), nullable=False, comment="关联模板")
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True, comment="关联客户（手动填写时为空）")
    placeholder_values = Column(JSONB, nullable=True, comment="占位符值快照 {strN: value}")
    output_pdf = Column(String(500), nullable=True, comment="生成的 PDF 路径")
    created_at = Column(DateTime, default=datetime.now, comment="创建时间")

    template = relationship("Template", back_populates="fills")

    __table_args__ = (
        Index("ix_template_fills_template", "template_id"),
        Index("ix_template_fills_client", "client_id"),
    )

    def __repr__(self):
        return f"<TemplateFill(id={self.id}, template_id={self.template_id}, client_id={self.client_id})>"


class SplitTask(Base):
    """PDF 拆分任务记录表(持久化拆分流水线状态与结果)。

    任务状态流转: ocr → llm → splitting → done(成功) / error(失败)。
    成功后 ranges 字段存拆分结果数组,可直接喂给前端 SplitEntryPage 表格渲染。
    7 天清理任务会删 output/{task_id}/ 整个目录(原 PDF + images + 子 PDF),
    DB 记录保留并置 files_cleaned=true,前端历史页可见但下载/预览按钮置灰。
    """
    __tablename__ = "split_tasks"

    task_id = Column(String(200), primary_key=True, comment="任务ID,对应 output/ 目录名")
    filename = Column(String(500), nullable=False, comment="原始上传文件名")
    total_pages = Column(Integer, nullable=True, comment="原 PDF 总页数")
    status = Column(String(20), default="ocr", nullable=False, comment="ocr|llm|splitting|done|error")
    error = Column(Text, nullable=True, comment="error 状态时的失败信息")
    ranges = Column(JSONB, nullable=True, comment="拆分结果 ranges 数组")
    duration_sec = Column(Float, nullable=True, comment="upload→done 总耗时(秒)")
    files_cleaned = Column(Boolean, default=False, nullable=False, comment="7 天清理后置 true")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False, comment="更新时间")

    __table_args__ = (
        Index("ix_split_tasks_status", "status"),
        Index("ix_split_tasks_created_at", "created_at"),
    )

    def __repr__(self):
        return f"<SplitTask(task_id='{self.task_id}', status='{self.status}')>"


class Summary(Base):
    """通用文件摘要历史表。

    存：URL → 下载 → OCR/文本抽取 → LLM 摘要+相关性判断 的完整结果。
    每条记录是一次"文件解析"操作，独立于客户档案体系。
    """
    __tablename__ = "summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    url = Column(Text, nullable=False, comment="原始文件 URL")
    progress_name = Column(String(200), nullable=True, comment="用户输入的进展名称")
    filename = Column(String(500), nullable=True)
    mime_type = Column(String(100), nullable=True)
    source = Column(String(20), nullable=True, comment="pdf_text/pdf_ocr/image_ocr/docx_text")
    page_count = Column(Integer, nullable=True)
    char_count = Column(Integer, nullable=True)
    extracted_text = Column(Text, nullable=True, comment="OCR/抽取的全文")
    title = Column(String(300), nullable=True, comment="LLM 生成的一句话定性")
    summary = Column(Text, nullable=True, comment="LLM 摘要正文")
    key_points = Column(JSONB, nullable=True)
    doc_category = Column(String(50), nullable=True)
    relevance = Column(String(20), nullable=True, comment="strong/weak/unrelated")
    relevance_score = Column(Integer, nullable=True, comment="0-100")
    relevance_reason = Column(Text, nullable=True)
    elapsed_sec = Column(Numeric(8, 2), nullable=True)
    status = Column(String(20), default="done", nullable=False)
    error_msg = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    __table_args__ = (
        Index("ix_summaries_created_at", "created_at"),
        Index("ix_summaries_doc_category", "doc_category"),
        Index("ix_summaries_progress_name", "progress_name"),
    )

    def __repr__(self):
        return f"<Summary(id={self.id}, progress='{self.progress_name}', filename='{self.filename}')>"


class ArchiveDetectProgress(Base):
    """文件审核：进展包表（业务事件维度，稳定）。

    9 个业务字段里 8 个在此（客户编码/姓名在 clients）。
    一个进展包 = 一个客户的一个项目详情的一个进展。
    (client_id, progress_oid) 组合唯一——同客户内 OID 不重，不同客户可重。
    """
    __tablename__ = "archive_detect_progress"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False, comment="关联客户")
    handler = Column(String(100), nullable=True, comment="办理人（进展包属性，只存名字）")
    project_name = Column(String(200), nullable=True, comment="项目名称")
    project_code = Column(String(100), nullable=True, comment="项目编码")
    project_detail_name = Column(String(200), nullable=True, comment="项目详情名称")
    project_detail_code = Column(String(100), nullable=True, comment="项目详情编码")
    progress_oid = Column(String(100), nullable=False, comment="进展OID（业务方标识）")
    progress_name = Column(String(200), nullable=True, comment="进展名称")
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    batches = relationship("ArchiveDetectBatch", back_populates="progress")
    files = relationship("ArchiveDetectFile", back_populates="progress")
    summaries = relationship("ArchiveDetectFolderSummary", back_populates="progress",
                             cascade="all, delete-orphan")

    __table_args__ = (
        Index("ux_archive_detect_progress_client_oid", "client_id", "progress_oid", unique=True),
        Index("ix_archive_detect_progress_client", "client_id"),
    )

    def __repr__(self):
        return f"<ArchiveDetectProgress(id={self.id}, client_id={self.client_id}, progress_oid='{self.progress_oid}')>"


class ArchiveDetectBatch(Base):
    """文件留底检测/审核：批次表（一次提交动作）。

    一次提交对应一条 batch 记录；batch 下挂 N 个 file（多文件并发处理）。
    user_prompt 为判定标准（多行），拼接进 LLM prompt。
    source_kind: batch | recheck（历史数据可能保留 upload/url，已不再使用）。
    progress_id/overall_* 仅业务审核模式有值。
    overall_* = 当次总体判断快照；进展包维度滚动判断见 ArchiveDetectFolderSummary。
    """
    __tablename__ = "archive_detect_batches"

    batch_id = Column(String(40), primary_key=True, comment="任务批次ID")
    user_prompt = Column(Text, nullable=False, comment="用户输入的留底判定标准（多行）")
    source_kind = Column(String(10), nullable=False, comment="batch | recheck")
    stage = Column(String(20), nullable=True, comment="审核阶段 pre_submit|post_submit;worker 读取传给 LLM")
    total_files = Column(Integer, nullable=False, comment="文件总数（1-20）")
    done_files = Column(Integer, default=0, nullable=False, comment="已完成（含成功+失败）")
    status = Column(String(20), default="running", nullable=False, comment="running|done|error")
    error = Column(Text, nullable=True, comment="batch 级错误（极少触发）")
    progress_id = Column(Integer, ForeignKey("archive_detect_progress.id"), nullable=True, comment="关联进展包")
    overall_verdict = Column(String(20), nullable=True, comment="当次总体判断 match|partial|mismatch")
    overall_score = Column(Integer, nullable=True, comment="当次总体匹配度 0-100")
    overall_reason = Column(Text, nullable=True, comment="当次总体判断依据（脱敏后）")
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    files = relationship(
        "ArchiveDetectFile",
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="ArchiveDetectFile.idx",
    )
    progress = relationship("ArchiveDetectProgress", back_populates="batches")

    __table_args__ = (
        Index("ix_archive_detect_batches_created_at", "created_at"),
        Index("ix_archive_detect_batches_progress", "progress_id"),
    )

    def __repr__(self):
        return f"<ArchiveDetectBatch(batch_id='{self.batch_id}', status='{self.status}', total={self.total_files})>"


class ArchiveDetectFile(Base):
    """文件留底检测/审核：单文件结果。

    OCR 原文不持久化（敏感信息最小留存）；reason / key_points 写库前已脱敏。
    增量字段（progress_id/file_id/version/content_sha256/match_score/verdict/deleted）
    仅业务审核模式有值；匿名 batch 这些列为 NULL。
    file 同时属于一个 batch（当次）和一个 progress（历史演进）。
    """
    __tablename__ = "archive_detect_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(
        String(40),
        ForeignKey("archive_detect_batches.batch_id", ondelete="CASCADE"),
        nullable=False,
    )
    idx = Column(Integer, nullable=False, comment="文件在 batch 中的顺序（0-based）")

    # 进展包关联 + 增量去重
    progress_id = Column(Integer, ForeignKey("archive_detect_progress.id"), nullable=True, comment="关联进展包")
    file_id = Column(String(200), nullable=True, comment="调用方传的显式文件标识（增量 key）")
    version = Column(Integer, nullable=True, default=1, comment="同 file_id 的检测版本号")
    content_sha256 = Column(String(64), nullable=True, comment="文件内容哈希")
    deleted = Column(Boolean, nullable=True, default=False, comment="软删标记")

    # 文件来源
    source_url = Column(Text, nullable=True, comment="URL 模式下的原始 URL")
    local_path = Column(Text, nullable=True, comment="upload 模式下的本地文件路径(worker 直读)")
    filename = Column(String(500), nullable=True)
    mime_type = Column(String(100), nullable=True)

    # 抽取产物（不存全文）
    page_count = Column(Integer, nullable=True)
    char_count = Column(Integer, nullable=True)
    ocr_text = Column(Text, nullable=True, comment="OCR识别文字（已脱敏）")

    # LLM 判定结果（已脱敏后才写）
    is_archival = Column(Boolean, nullable=True)
    confidence = Column(Integer, nullable=True, comment="0-100（旧字段，= match_score 语义）")
    match_score = Column(Integer, nullable=True, comment="匹配度 0-100")
    verdict = Column(String(20), nullable=True, comment="match|partial|mismatch")
    reason = Column(Text, nullable=True, comment="判定依据（已脱敏）")
    key_points = Column(JSONB, nullable=True, comment="要点列表（已脱敏）")
    doc_category = Column(String(50), nullable=True)

    # 状态机
    status = Column(String(20), default="pending", nullable=False,
                    comment="pending|leased|fetching|ocr|llm|done|error")
    error_msg = Column(Text, nullable=True)
    elapsed_sec = Column(Numeric(8, 2), nullable=True)

    # 重审/重跑入队复用: 非空时 worker 跳过下载+OCR,直接用它跑 LLM
    reuse_ocr_text = Column(Text, nullable=True,
                            comment="重审入队时预填的源 OCR 文本;worker 非空则跳过下载+OCR")

    # Worker lease (方案二 2b: 多 worker 进程通过 DB 抢任务)
    worker_lease_until = Column(DateTime, nullable=True,
                                comment="worker 抢到后写入,超时则 watchdog 回收")
    retry_count = Column(Integer, default=0, nullable=False,
                         comment="worker 失败后的重试次数,>= 1 时不再 retry")

    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    batch = relationship("ArchiveDetectBatch", back_populates="files")
    progress = relationship("ArchiveDetectProgress", back_populates="files")

    __table_args__ = (
        Index("ix_archive_detect_files_batch_id", "batch_id"),
        Index("ux_archive_detect_files_batch_idx", "batch_id", "idx", unique=True),
        Index("ix_archive_detect_files_progress", "progress_id"),
        Index("ix_archive_detect_files_fileid_version", "progress_id", "file_id", "version"),
        Index("ix_archive_detect_files_fileid_hash", "progress_id", "file_id", "content_sha256"),
        Index("ix_archive_detect_files_deleted", "deleted"),
    )

    def __repr__(self):
        return f"<ArchiveDetectFile(batch='{self.batch_id}', idx={self.idx}, status='{self.status}')>"


class ArchiveDetectFolderSummary(Base):
    """文件审核：进展包维度滚动总体判断（多版本）。

    随文件增删改查动态更新，每次更新新增一个 version，不覆盖旧版。
    取最新 version = 该进展包当前总体状态。
    summary JSONB: {match/partial/mismatch 统计 + LLM 概述 + 风险提示}。
    """
    __tablename__ = "archive_detect_folder_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    progress_id = Column(Integer, ForeignKey("archive_detect_progress.id", ondelete="CASCADE"),
                         nullable=False, comment="关联进展包")
    version = Column(Integer, nullable=False, comment="汇总版本号")
    criteria = Column(Text, nullable=True, comment="本次汇总用的审核标准")
    summary = Column(JSONB, nullable=True, comment="汇总内容（统计+LLM概述）")
    file_count = Column(Integer, nullable=True, comment="参与汇总的文件数")
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    progress = relationship("ArchiveDetectProgress", back_populates="summaries")

    __table_args__ = (
        Index("ux_archive_detect_summaries_progress_version", "progress_id", "version", unique=True),
        Index("ix_archive_detect_summaries_progress_created", "progress_id", "created_at"),
    )

    def __repr__(self):
        return f"<ArchiveDetectFolderSummary(progress={self.progress_id}, version={self.version})>"


class ClientProfileGenerationTask(Base):
    """客户资料结构化生成任务。

    基于 archive_detect_files.ocr_text 直接生成并写入客户结构化档案。
    source_file_ids/source_files_snapshot 用于追溯本次生成使用了哪些文件。
    """
    __tablename__ = "client_profile_generation_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, comment="客户ID")
    status = Column(String(20), default="running", nullable=False, comment="running|done|error")
    source_file_ids = Column(JSONB, nullable=True, comment="本次使用的 archive_detect_files.id 数组")
    source_files_snapshot = Column(JSONB, nullable=True, comment="本次使用文件的摘要快照")
    source_file_count = Column(Integer, default=0, nullable=False, comment="本次使用文件数")
    extracted_summary = Column(JSONB, nullable=True, comment="AI 抽取汇总结果")
    created_count = Column(JSONB, nullable=True, comment="写入数量统计")
    error = Column(Text, nullable=True, comment="错误信息")
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    __table_args__ = (
        Index("ix_client_profile_generation_client", "client_id"),
        Index("ix_client_profile_generation_status", "status"),
        Index("ix_client_profile_generation_created", "created_at"),
    )

    def __repr__(self):
        return f"<ClientProfileGenerationTask(id={self.id}, client_id={self.client_id}, status='{self.status}')>"



class SystemEvent(Base):
    """业务事件流。区别于 journald 中的运行日志:这里只记有业务含义的节点。
    用于 /events 后台页面查询(批次失败、OCR 超时、DB 错误、worker 崩溃等)。
    保留 30 天,由 _split_cleanup_loop 周期 GC。
    """
    __tablename__ = "system_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at = Column(DateTime, default=datetime.now, nullable=False, comment="事件发生时间")
    severity = Column(String(10), nullable=False, comment="info | warn | error | critical")
    category = Column(String(40), nullable=False, comment="事件类别,见 event_service 常量")
    message = Column(String(500), nullable=False, comment="一句话中文描述(不含堆栈)")
    context = Column(JSONB, nullable=True, comment="结构化字段 batch_id/file_id/error_class 等")

    __table_args__ = (
        Index("ix_system_events_occurred", occurred_at.desc()),
        Index("ix_system_events_severity_occurred", "severity", occurred_at.desc()),
        Index("ix_system_events_category_occurred", "category", occurred_at.desc()),
        CheckConstraint(
            "severity IN ('info','warn','error','critical')",
            name="system_events_severity_check",
        ),
    )

    def __repr__(self):
        return f"<SystemEvent(id={self.id}, severity={self.severity}, category={self.category})>"


class ApiRequestLog(Base):
    """API 请求记录。middleware 自动拦截 /api/archive-detect/* 写入,保留 30 天。"""
    __tablename__ = "api_request_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    source = Column(String(20), default="other", nullable=False, comment="business/admin/poll/other")
    method = Column(String(10), nullable=False, comment="GET/POST")
    path = Column(String(300), nullable=False, comment="请求路径")
    client_ip = Column(String(45), nullable=True)
    request_body = Column(JSONB, nullable=True, comment="传参 JSON(multipart 只存元数据)")
    response_status = Column(Integer, nullable=True, comment="HTTP 状态码")
    elapsed_ms = Column(Integer, nullable=True, comment="请求耗时毫秒")

    __table_args__ = (
        Index("ix_request_logs_created", created_at.desc()),
        Index("ix_request_logs_path", "path", created_at.desc()),
    )


class ExternalApiLog(Base):
    """出站外部接口调用记录:URL 刷新(getFileDownloadUrl)/ LLM。保留 30 天。"""
    __tablename__ = "external_api_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    service = Column(String(20), nullable=False, comment="refresh_url | llm")
    url = Column(Text, nullable=True, comment="请求地址")
    request_params = Column(JSONB, nullable=True, comment="请求参数(LLM 存 prompt 全文)")
    response_summary = Column(JSONB, nullable=True, comment="返回结果(LLM 存返回全文)")
    status = Column(String(10), nullable=False, comment="ok | error")
    error_msg = Column(Text, nullable=True)
    elapsed_ms = Column(Integer, nullable=True, comment="耗时毫秒")
    batch_id = Column(String(40), nullable=True, comment="关联批次(如有)")
    file_id = Column(String(64), nullable=True, comment="关联业务文件编码(如有)")

    __table_args__ = (
        Index("ix_external_api_logs_created", created_at.desc()),
        Index("ix_external_api_logs_service_created", "service", created_at.desc()),
        Index("ix_external_api_logs_status_created", "status", created_at.desc()),
    )


class AiApiCall(Base):
    """AI/LLM API 调用记录。持久化所有大模型调用，用于审计、排查和成本分析。保留 30 天。"""
    __tablename__ = "ai_api_calls"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="调用时间")
    operation = Column(String(64), nullable=True, comment="LLM wrapper/操作名,如 detect_archival")
    model = Column(String(64), nullable=True, comment="模型 ID")
    prompt = Column(Text, nullable=True, comment="prompt 全文")
    response_raw = Column(Text, nullable=True, comment="原始返回文本")
    status = Column(String(10), nullable=False, comment="ok | error")
    error_msg = Column(Text, nullable=True, comment="错误信息")
    elapsed_ms = Column(Integer, nullable=True, comment="耗时毫秒")
    batch_id = Column(String(40), nullable=True, comment="关联批次")
    file_id = Column(String(64), nullable=True, comment="关联业务文件编码")
    client_code = Column(String(40), nullable=True, comment="关联客户编码")
    task_id = Column(String(64), nullable=True, comment="关联任务/摘要 ID")

    __table_args__ = (
        Index("ix_ai_api_calls_created", created_at.desc()),
        Index("ix_ai_api_calls_operation_created", "operation", created_at.desc()),
        Index("ix_ai_api_calls_model_created", "model", created_at.desc()),
        Index("ix_ai_api_calls_batch_id_created", "batch_id", created_at.desc()),
        Index("ix_ai_api_calls_file_id_created", "file_id", created_at.desc()),
        Index("ix_ai_api_calls_client_code_created", "client_code", created_at.desc()),
        Index("ix_ai_api_calls_status_created", "status", created_at.desc()),
    )


class ProfileImportTask(Base):
    """客户画像-文件清单导入任务。一次 Excel 导入一行,记录进度与各类计数。"""
    __tablename__ = "profile_import_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(500), nullable=False, comment="上传的 Excel 文件名")
    client_name = Column(String(100), nullable=False, comment="主客户姓名(客户姓名列众数)")
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, comment="归属客户")
    status = Column(String(20), nullable=False, default="running", comment="running/done/error")
    total_files = Column(Integer, nullable=False, default=0, comment="文件总数")
    processed_files = Column(Integer, nullable=False, default=0, comment="已处理数(含成败)")
    reused_count = Column(Integer, nullable=False, default=0, comment="复用 archive_detect 脱敏 OCR 的文件数")
    relinked_count = Column(Integer, nullable=False, default=0, comment="命中本库已有 done 行直接复用的文件数")
    fresh_ocr_count = Column(Integer, nullable=False, default=0, comment="新下载+OCR 的文件数")
    failed_count = Column(Integer, nullable=False, default=0, comment="失败文件数")
    extracted_count = Column(Integer, nullable=False, default=0, comment="完成提取的文件数")
    id_card_count = Column(Integer, nullable=False, default=0, comment="筛出身份证数")
    hukou_count = Column(Integer, nullable=False, default=0, comment="筛出户口本数")
    degree_cert_count = Column(Integer, nullable=False, default=0, comment="筛出学位证数")
    birth_cert_count = Column(Integer, nullable=False, default=0, comment="筛出出生证明数")
    current_file = Column(String(500), nullable=True, comment="正在处理的文件名")
    error = Column(Text, nullable=True, comment="任务级错误信息")
    household_id = Column(Integer, ForeignKey("profile_households.id", ondelete="SET NULL"), nullable=True, comment="生成的家庭")
    needs_review_count = Column(Integer, nullable=False, default=0, comment="待复核文件数")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False, comment="更新时间")

    __table_args__ = (
        Index("ix_profile_import_tasks_status", "status"),
        Index("ix_profile_import_tasks_client", "client_id"),
        Index("ix_profile_import_tasks_created", created_at.desc()),
    )


class CustomerFile(Base):
    """客户文件库:客户名下每个文件一行,存全量 OCR 文本(fresh=原文,reused=脱敏),供后续各类提取复用。"""
    __tablename__ = "customer_files"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    file_code = Column(String(200), nullable=False, unique=True, comment="业务文件编码(全局唯一)")
    import_task_id = Column(Integer, ForeignKey("profile_import_tasks.id", ondelete="CASCADE"), nullable=False, comment="最近一次导入它的任务")
    client_name = Column(String(100), nullable=True, comment="Excel 行级客户姓名")
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, comment="归属客户")
    filename = Column(String(500), nullable=True, comment="文件名")
    folder_name = Column(String(300), nullable=True, comment="售后文件夹名称")
    rel_path = Column(String(500), nullable=True, comment="相对路径")
    status = Column(String(20), nullable=False, default="pending", comment="pending/fetching/ocr/done/error")
    ocr_source = Column(String(10), nullable=False, default="none", comment="fresh(原文)/reused(脱敏)/none")
    ocr_text = Column(Text, nullable=True, comment="OCR 全文;fresh=未脱敏原文(与 ai_api_calls 存原文的既定业务决策一致),reused=archive_detect 脱敏文本")
    mime_type = Column(String(100), nullable=True, comment="MIME 类型")
    page_count = Column(Integer, nullable=True, comment="页数")
    char_count = Column(Integer, nullable=True, comment="字符数")
    doc_type = Column(String(32), nullable=True, comment="识别类型:id_card/hukou/degree_cert/birth_cert/other")
    classify_by = Column(String(10), nullable=False, default="none", comment="分类方式:keyword/llm/none")
    classify_score = Column(Integer, nullable=True, comment="matcher 分数或 LLM confidence")
    error_msg = Column(Text, nullable=True, comment="错误信息")
    local_path = Column(String(500), nullable=True, comment="原件落盘相对路径(output/customer_files/...)")
    file_keep_until = Column(DateTime, nullable=True, comment="原件保留截止时间(到期 GC 删除;DB/OCR 永久保留)")
    review_status = Column(String(16), nullable=False, default="none", comment="复核状态:none/needs_review/reviewed")
    review_reason = Column(String(200), nullable=True, comment="待复核原因:no_text/garbled/ocr_short/low_confidence/extract_error/no_person/masked_id")
    quality_score = Column(Integer, nullable=True, comment="质量分 0-100(越小越急需复核)")
    person_id = Column(Integer, nullable=True, comment="归属人(手动指定;权威归属载体,与 write_stats 归因并集使用)")
    affter_entryoid = Column(String(64), nullable=True, comment="售后项目OID(项目案件路由键);NULL=扁平形态/旧数据")
    project_name = Column(String(300), nullable=True, comment="项目显示名(反范式: projectname_detailed || projectname)")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False, comment="更新时间")

    __table_args__ = (
        Index("ix_customer_files_task", "import_task_id"),
        Index("ix_customer_files_doc_type", "doc_type"),
        Index("ix_customer_files_client", "client_id"),
        Index("ix_customer_files_status", "status"),
        Index("ix_customer_files_review", "review_status", "quality_score"),
        Index("ix_customer_files_person", "person_id"),
        Index("ix_customer_files_entryoid", "affter_entryoid"),
    )


class DocExtractResult(Base):
    """一次提取运行一行:记录用的哪套规则、抽出了什么、写到了哪里,全程可回溯。"""
    __tablename__ = "doc_extract_results"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    customer_file_id = Column(BigInteger, ForeignKey("customer_files.id", ondelete="CASCADE"), nullable=False, comment="来源客户文件行")
    import_task_id = Column(Integer, ForeignKey("profile_import_tasks.id", ondelete="CASCADE"), nullable=False, comment="所属导入任务")
    file_id = Column(String(200), nullable=True, comment="业务文件编码")
    client_id = Column(Integer, nullable=True, comment="归属客户")
    doc_type = Column(String(32), nullable=False, comment="识别出的证件类型")
    rule_id = Column(BigInteger, nullable=True, comment="使用的规则;无 active 规则时 NULL")
    rule_version = Column(Integer, nullable=True, comment="规则版本")
    status = Column(String(16), nullable=False, comment="done/error/skipped")
    skip_reason = Column(String(64), nullable=True, comment="no_active_rule/no_client/no_person 等")
    extracted = Column(JSONB, nullable=True, comment="LLM 抽取原始字段(未脱敏,同 ai_api_calls 原始留存策略)")
    mapped = Column(JSONB, nullable=True, comment="逐字段写入明细 [{key,column,entity,entity_id,action}]")
    write_stats = Column(JSONB, nullable=True, comment="{matched_by, client_fields, member_fields, member_created}")
    error_msg = Column(Text, nullable=True, comment="错误信息")
    elapsed_ms = Column(Integer, nullable=True, comment="耗时毫秒")
    review_status = Column(String(16), nullable=False, default="pending", comment="复核状态:pending/confirmed/corrected/dismissed")
    corrected = Column(JSONB, nullable=True, comment="人工修正后的字段(留痕,与 extracted 对照)")
    reviewed_by = Column(String(64), nullable=True, comment="复核人")
    reviewed_at = Column(DateTime, nullable=True, comment="复核时间")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")

    __table_args__ = (
        Index("ix_doc_extract_results_task", "import_task_id"),
        Index("ix_doc_extract_results_customer_file", "customer_file_id"),
        Index("ix_doc_extract_results_file_id", "file_id"),
        Index("ix_doc_extract_results_doc_type", "doc_type"),
    )


class ProfileHousehold(Base):
    """家庭/客户组(画像 v2 独立领域模型;与老 clients 仅软关联,不写老表)。"""
    __tablename__ = "profile_households"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, comment="家庭/客户组名称(主客户姓名)")
    legacy_client_id = Column(Integer, ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, comment="软关联老 clients.id(仅链接,不写老表)")
    main_person_id = Column(Integer, nullable=True, comment="主申请人 profile_persons.id")
    customer_code = Column(String(100), nullable=True, comment="业务方客户编号(首个非空值,只补空)")
    crm_oid = Column(String(64), nullable=True, comment="业务方 CRM OID(只补空)")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False, comment="更新时间")

    __table_args__ = (
        Index("ix_profile_households_name", "name"),
    )


class ProfilePerson(Base):
    """人(骨架)。字段级档案在 profile_person_fields。"""
    __tablename__ = "profile_persons"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("profile_households.id", ondelete="CASCADE"), nullable=False, comment="所属家庭")
    name = Column(String(100), nullable=False, comment="姓名")
    relation_to_main = Column(String(20), nullable=False, default="待确认", comment="与主申请人关系:户主/配偶/子/女/父/母/待确认")
    is_main = Column(Boolean, nullable=False, default=False, comment="是否主申请人")
    avatar_file_id = Column(BigInteger, nullable=True, comment="头像证件照 customer_files.id")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False, comment="更新时间")

    __table_args__ = (
        Index("ix_profile_persons_household", "household_id"),
    )


class ProfilePersonField(Base):
    """字段级档案 + 证据链:一人一行/字段,记当前值/可信度层/来源/确认状态。"""
    __tablename__ = "profile_person_fields"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    person_id = Column(Integer, ForeignKey("profile_persons.id", ondelete="CASCADE"), nullable=False, comment="所属人")
    field = Column(String(50), nullable=False, comment="字段名:如 id_number/occupation")
    value = Column(Text, nullable=True, comment="当前值")
    layer = Column(String(16), nullable=False, default="verified", comment="可信度层:verified=官方证件/declared=自报")
    source_file_id = Column(BigInteger, nullable=True, comment="来源文件 customer_files.id")
    source_result_id = Column(BigInteger, nullable=True, comment="来源提取结果 doc_extract_results.id")
    status = Column(String(16), nullable=False, default="ai", comment="ai(待复核)/confirmed/corrected")
    updated_by = Column(String(64), nullable=True, comment="最后操作人(AI 或复核员)")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False, comment="更新时间")

    __table_args__ = (
        Index("ix_profile_person_fields_person", "person_id"),
    )


class ProfileAsset(Base):
    """资产(房产/存款/流水等),权属到人或家庭。"""
    __tablename__ = "profile_assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("profile_households.id", ondelete="CASCADE"), nullable=False, comment="所属家庭")
    owner_person_id = Column(Integer, ForeignKey("profile_persons.id", ondelete="SET NULL"), nullable=True, comment="权属人")
    asset_type = Column(String(30), nullable=False, comment="房产/存款/银行流水/股票/车辆/其他")
    name = Column(String(200), nullable=False, comment="资产名称")
    attrs = Column(JSONB, nullable=True, comment="类型特定字段{地址,面积,证号,金额,币种,...}")
    source_file_id = Column(BigInteger, nullable=True, comment="来源文件 customer_files.id")
    status = Column(String(16), nullable=False, default="ai", comment="ai(待复核)/confirmed/corrected")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False, comment="更新时间")

    __table_args__ = (
        Index("ix_profile_assets_household", "household_id"),
    )


class ProfileCase(Base):
    """案件 + 时间线:一个售后项目=一个案件(按 affter_entryoid 路由);entryoid NULL=默认案件(旧数据/扁平形态)。"""
    __tablename__ = "profile_cases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    household_id = Column(Integer, ForeignKey("profile_households.id", ondelete="CASCADE"), nullable=False, comment="所属家庭")
    case_type = Column(String(100), nullable=False, comment="案件类型:项目案件=项目名;默认案件=AI 抽取提示")
    status = Column(String(30), nullable=False, default="进行中", comment="进行中/已递交/已获批/已交付/已签收")
    milestones = Column(JSONB, nullable=True, comment="时间线 [{name,date,source_file_id}]")
    affter_entryoid = Column(String(64), nullable=True, comment="售后项目OID;NULL=默认案件(旧数据/扁平形态路由)")
    projectno = Column(String(50), nullable=True, comment="办理的项目编码")
    projectname = Column(String(200), nullable=True, comment="办理的项目名称")
    projectno_detailed = Column(String(50), nullable=True, comment="二级项目编码")
    projectname_detailed = Column(String(200), nullable=True, comment="二级项目名称")
    project_created_at = Column(DateTime, nullable=True, comment="项目创建时间(接口 create_time)")
    created_at = Column(DateTime, default=datetime.now, nullable=False, comment="创建时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False, comment="更新时间")

    __table_args__ = (
        Index("ix_profile_cases_household", "household_id"),
        Index("ux_profile_cases_household_entryoid", "household_id", "affter_entryoid",
              unique=True, postgresql_where=affter_entryoid.isnot(None)),
        Index("ux_profile_cases_household_default", "household_id",
              unique=True, postgresql_where=affter_entryoid.is_(None)),
    )
