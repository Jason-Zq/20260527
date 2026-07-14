using System.ComponentModel.DataAnnotations.Schema;

namespace DocReview.ArchiveDetect.Models;

// 复刻 Python backend/db/models.py 的 archive_detect 家族 + clients。
// 仅映射 PoC 需要的列;列名用 [Column] 显式对齐 snake_case,避免 EF 默认 PascalCase 映射错位。

[Table("clients")]
public class Client
{
    [Column("id")] public int Id { get; set; }
    [Column("client_code")] public string? ClientCode { get; set; }
    [Column("name")] public string Name { get; set; } = "";
    [Column("created_at")] public DateTime? CreatedAt { get; set; }
    [Column("updated_at")] public DateTime? UpdatedAt { get; set; }
}

[Table("archive_detect_progress")]
public class ArchiveDetectProgress
{
    [Column("id")] public int Id { get; set; }
    [Column("client_id")] public int ClientId { get; set; }
    [Column("handler")] public string? Handler { get; set; }
    [Column("project_name")] public string? ProjectName { get; set; }
    [Column("project_code")] public string? ProjectCode { get; set; }
    [Column("project_detail_name")] public string? ProjectDetailName { get; set; }
    [Column("project_detail_code")] public string? ProjectDetailCode { get; set; }
    [Column("progress_oid")] public string ProgressOid { get; set; } = "";
    [Column("progress_name")] public string? ProgressName { get; set; }
    [Column("created_at")] public DateTime CreatedAt { get; set; }
    [Column("updated_at")] public DateTime UpdatedAt { get; set; }
}

[Table("archive_detect_batches")]
public class ArchiveDetectBatch
{
    [Column("batch_id")] public string BatchId { get; set; } = "";
    [Column("user_prompt")] public string UserPrompt { get; set; } = "";
    [Column("source_kind")] public string SourceKind { get; set; } = "batch";
    [Column("stage")] public string? Stage { get; set; }
    [Column("total_files")] public int TotalFiles { get; set; }
    [Column("done_files")] public int DoneFiles { get; set; }
    [Column("status")] public string Status { get; set; } = "running";
    [Column("error")] public string? Error { get; set; }
    [Column("progress_id")] public int? ProgressId { get; set; }
    [Column("overall_verdict")] public string? OverallVerdict { get; set; }
    [Column("overall_score")] public int? OverallScore { get; set; }
    [Column("overall_reason")] public string? OverallReason { get; set; }
    [Column("created_at")] public DateTime CreatedAt { get; set; }
    [Column("updated_at")] public DateTime UpdatedAt { get; set; }
}

[Table("archive_detect_files")]
public class ArchiveDetectFile
{
    [Column("id")] public int Id { get; set; }
    [Column("batch_id")] public string BatchId { get; set; } = "";
    [Column("idx")] public int Idx { get; set; }
    [Column("progress_id")] public int? ProgressId { get; set; }
    [Column("file_id")] public string? FileId { get; set; }
    [Column("version")] public int? Version { get; set; }
    [Column("content_sha256")] public string? ContentSha256 { get; set; }
    [Column("deleted")] public bool? Deleted { get; set; }
    [Column("source_url")] public string? SourceUrl { get; set; }
    [Column("local_path")] public string? LocalPath { get; set; }
    [Column("filename")] public string? Filename { get; set; }
    [Column("mime_type")] public string? MimeType { get; set; }
    [Column("page_count")] public int? PageCount { get; set; }
    [Column("char_count")] public int? CharCount { get; set; }
    [Column("ocr_text")] public string? OcrText { get; set; }
    [Column("is_archival")] public bool? IsArchival { get; set; }
    [Column("confidence")] public int? Confidence { get; set; }
    [Column("match_score")] public int? MatchScore { get; set; }
    [Column("verdict")] public string? Verdict { get; set; }
    [Column("reason")] public string? Reason { get; set; }
    // key_points 是 JSONB;PoC 用 string 存原始 JSON,写时手动序列化
    [Column("key_points", TypeName = "jsonb")] public string? KeyPoints { get; set; }
    [Column("doc_category")] public string? DocCategory { get; set; }
    [Column("status")] public string Status { get; set; } = "pending";
    [Column("error_msg")] public string? ErrorMsg { get; set; }
    [Column("elapsed_sec")] public decimal? ElapsedSec { get; set; }
    [Column("reuse_ocr_text")] public string? ReuseOcrText { get; set; }
    [Column("worker_lease_until")] public DateTime? WorkerLeaseUntil { get; set; }
    [Column("retry_count")] public int RetryCount { get; set; }
    [Column("created_at")] public DateTime CreatedAt { get; set; }
    [Column("updated_at")] public DateTime UpdatedAt { get; set; }
}
