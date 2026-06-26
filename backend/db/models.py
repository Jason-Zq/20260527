"""
ORM 模型定义
核心表：clients（客户主档）、family_members（家庭成员）、assets（资产）、
documents（文档/解析记录）、client_info（KV 兜底）、templates、template_fills、split_tasks。

clients/family_members/assets 是移民客户档案的强 schema。
client_info 仍保留作为没纳入强 schema 的字段的 KV 兜底。
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Float, Numeric, Boolean, Date,
    DateTime, ForeignKey, Index
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
    source_kind: upload | url | batch（业务审核模式）。
    progress_id/overall_* 仅业务审核模式有值（匿名 batch 为 NULL）。
    overall_* = 当次总体判断快照；进展包维度滚动判断见 ArchiveDetectFolderSummary。
    """
    __tablename__ = "archive_detect_batches"

    batch_id = Column(String(40), primary_key=True, comment="任务批次ID")
    user_prompt = Column(Text, nullable=False, comment="用户输入的留底判定标准（多行）")
    source_kind = Column(String(10), nullable=False, comment="upload | url | batch")
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
                    comment="pending|fetching|ocr|llm|done|error")
    error_msg = Column(Text, nullable=True)
    elapsed_sec = Column(Numeric(8, 2), nullable=True)

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
