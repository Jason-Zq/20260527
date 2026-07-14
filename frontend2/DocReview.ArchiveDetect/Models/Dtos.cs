namespace DocReview.ArchiveDetect.Models;

/// <summary>业务批量提交请求体。</summary>
public record BusinessBatchPayload
{
    /// <summary>审核标准 / 判定提示词(必填),会拼进 LLM prompt。</summary>
    public string Criteria { get; init; } = "";

    /// <summary>审核阶段:pre_submit(递交前) | post_submit(递交后)。留空默认 post_submit。</summary>
    public string? Stage { get; init; }

    /// <summary>客户信息(按 client_code upsert)。</summary>
    public ClientDto? Client { get; init; }

    /// <summary>进展包信息(按 client_id + progress_oid upsert)。</summary>
    public ProgressDto? Progress { get; init; }

    /// <summary>待检测文件列表(每项含 file_id / filename / url)。</summary>
    public List<ItemDto>? Items { get; init; }
}

/// <summary>客户信息。</summary>
public record ClientDto
{
    /// <summary>客户业务编码(稳定标识,upsert 主键)。</summary>
    public string? ClientCode { get; init; }
    /// <summary>客户姓名。</summary>
    public string? Name { get; init; }
}

/// <summary>进展包信息。</summary>
public record ProgressDto
{
    /// <summary>进展 OID(业务方标识,与 client 组合唯一)。</summary>
    public string? ProgressOid { get; init; }
    /// <summary>办理人姓名。</summary>
    public string? Handler { get; init; }
    /// <summary>项目名称。</summary>
    public string? ProjectName { get; init; }
    /// <summary>项目编码。</summary>
    public string? ProjectCode { get; init; }
    /// <summary>项目详情名称。</summary>
    public string? ProjectDetailName { get; init; }
    /// <summary>项目详情编码。</summary>
    public string? ProjectDetailCode { get; init; }
    /// <summary>进展名称。</summary>
    public string? ProgressName { get; init; }
}

/// <summary>单个待检测文件。</summary>
public record ItemDto
{
    /// <summary>文件业务标识(增量复用 key)。</summary>
    public string? FileId { get; init; }
    /// <summary>文件名(权威可读名,优先保留)。</summary>
    public string? Filename { get; init; }
    /// <summary>文件下载地址(必须 http/https)。</summary>
    public string Url { get; init; } = "";
}

/// <summary>提交批次的返回结果。</summary>
public record SubmitBatchResult(string BatchId, int ProgressId, int TotalFiles);
