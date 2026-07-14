using System.Text;
using System.Text.Json;

namespace DocReview.ArchiveDetect.Services;

// 复刻 Python backend/llm_service.py 的 detect_archival + _build_archive_detect_prompt。
// prompt 文案逐字迁移,分类体系 ARCHIVE_CATEGORIES_FULL 原样保留(业务知识,与语言无关)。
public class LlmService
{
    private readonly HttpClient _http;
    private readonly string _apiKey;
    private readonly string _baseUrl;
    private readonly string _model;
    private readonly double _temperature;
    private readonly ILogger<LlmService> _log;

    private const int InputLimitChars = 12000;

    public LlmService(HttpClient http, IConfiguration cfg, ILogger<LlmService> log)
    {
        _http = http;
        _log = log;
        _apiKey = cfg["Llm:ApiKey"] ?? "";
        _baseUrl = (cfg["Llm:BaseUrl"] ?? "").TrimEnd('/');
        _model = cfg["Llm:Model"] ?? "";
        _temperature = double.TryParse(cfg["Llm:Temperature"], out var t) ? t : 0.1;
    }

    // 公司售后留底分类体系(硬编码,逐字来自 llm_service.ARCHIVE_CATEGORIES_FULL)
    private const string ArchiveCategoriesFull = """
【递交前阶段应上传的 5 大类】
A. 客户基础文件:护照、身份证、中文信息表、个人简历、出生证明类文件、户口本、毕业证书&学位证书、结婚证&离婚证等婚姻状态文件、港澳通行证、房产证、工作证明信等
B. 客户个人文件:职业&专业证书、照片、社保/个税、不反对移民申明、无犯罪记录证明、体检类文件、地址证明、在读证明、资产/资信文件、公证/认证文件、成就类文件、学生签证、成绩单、疫苗本、翻译类文件、证书或获奖记录、客户访校指南、单身证明、个人文件等
C. 客户公司文件:营业执照、章程、股东会决议、验资报告、公司财报/审计报告、银行流水、业务合同/合作协议、组织架构图、股东名册、雇佣合同、公司介绍、办公室照片、办公室租赁合同等
D. 其他备用文件:律师文件、投资文件、批复留底文件、使馆申请表、合规部KYC留底文件、开户KYC文件、投资证明、购房文件、入境处申请表格、劳工卡申请、正签信、工作签证留底、商业计划书等
E. 转款凭证:服务过程中涉及的转账凭证等

【递交后阶段应上传的 4 大类】
F. 文案制作的递交文件类:递交全套留底、递交后补料留底等
G. 获批/失败:筛选/名额通知函、使馆信、入境处信件、批复函、录取通知、补料信、打款通知、体检通知、获身份文件、拒签信、撤案信等任何客户相关批复类文件
H. 其他文件:客户获批后协助客户留存的重要证件信息,如更新护照、入境小白条等
I. 停滞/放弃类文件:客户明确表示撤案、不再继续办理、不启动了/放弃办理了等主观原因不再继续办理项目的邮件、聊天等截图
""";

    public record ArchivalResult(string Verdict, int MatchScore, bool IsArchival,
        int Confidence, string Reason, List<string> KeyPoints, string DocCategory);

    public async Task<ArchivalResult> DetectArchivalAsync(
        string text, string userPrompt, string? stage, string? clientName, string? handler,
        CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(text)) throw new ArgumentException("文件内容为空,无法判定");
        if (string.IsNullOrWhiteSpace(userPrompt)) throw new ArgumentException("判定标准不能为空");

        var src = text.Trim();
        if (src.Length > InputLimitChars)
        {
            int head = InputLimitChars / 2, tail = InputLimitChars - head;
            src = src[..head] + $"\n\n...[省略 {text.Length - InputLimitChars} 字]...\n\n" + src[^tail..];
        }

        var prompt = BuildPrompt(src, userPrompt.Trim(), stage, clientName, handler);
        var raw = await CallLlmAsync(prompt, ct);

        // 容错解析 JSON:取首个 { 到末个 }
        int s = raw.IndexOf('{'), e = raw.LastIndexOf('}');
        var jsonStr = (s >= 0 && e > s) ? raw[s..(e + 1)] : raw;
        JsonElement data;
        try { data = JsonDocument.Parse(jsonStr).RootElement; }
        catch (JsonException) { throw new InvalidOperationException($"LLM 返回非合法 JSON: {raw[..Math.Min(200, raw.Length)]}"); }

        var verdict = (data.TryGetProperty("verdict", out var v) ? v.GetString() ?? "" : "").Trim().ToLower();
        if (verdict is not ("match" or "partial" or "mismatch")) verdict = "mismatch";

        int score = 0;
        if (data.TryGetProperty("match_score", out var ms))
        {
            if (ms.ValueKind == JsonValueKind.Number) ms.TryGetInt32(out score);
            else int.TryParse(ms.GetString(), out score);
        }
        score = Math.Max(0, Math.Min(100, score));

        var reason = data.TryGetProperty("reason", out var r) ? (r.GetString() ?? "").Trim() : "";
        var docCat = data.TryGetProperty("doc_category", out var dc) ? (dc.GetString() ?? "").Trim() : "";
        if (string.IsNullOrEmpty(docCat)) docCat = "其他";

        var keyPoints = new List<string>();
        if (data.TryGetProperty("key_points", out var kp) && kp.ValueKind == JsonValueKind.Array)
            foreach (var item in kp.EnumerateArray())
            {
                var val = item.GetString()?.Trim();
                if (!string.IsNullOrEmpty(val)) keyPoints.Add(val);
            }

        return new ArchivalResult(verdict, score, verdict == "match", score, reason, keyPoints, docCat);
    }

    private static string BuildPrompt(string text, string userPrompt, string? stage, string? clientName, string? handler)
    {
        var stageLabel = stage == "pre_submit" ? "递交前" : "递交后";
        var stageHint = (stage == "pre_submit" || stage == "post_submit")
            ? $"\n当前阶段: {stage} ({stageLabel})\n" : "";

        var nameParts = new List<string>();
        if (!string.IsNullOrEmpty(clientName)) nameParts.Add($"客户姓名：{clientName}");
        if (!string.IsNullOrEmpty(handler)) nameParts.Add($"办理人：{handler}");
        var nameHeader = nameParts.Count > 0 ? "本进展" + string.Join("；", nameParts) + "。\n" : "";

        var handlerClause = !string.IsNullOrEmpty(handler)
            ? "官方回执、递交确认、受理通知等文件上的「申请人」姓名通常对应本进展办理人而非客户本人,若文件上姓名与客户不一致但与办理人(含拼音/英文转写)一致,视为本进展有效留底,不判 mismatch。"
            : "";

        return
            "你是一个公司文件留底审核助手。请根据下方公司分类标准 + 用户判定提示词,审核文件。\n\n" +
            "---用户判定标准开始---\n" + userPrompt + "\n---用户判定标准结束---\n\n" +
            "---公司分类标准开始---\n" + stageHint + ArchiveCategoriesFull + "\n---公司分类标准结束---\n\n" +
            "重要判定指南:\n" + nameHeader +
            "- 本次审核只判断文件是否与公司留底分类体系相关,不核对具体项目名称、投资金额、转账金额等细节。同一客户的文件集合中,属于该客户配偶、子女、父母、共同申请人的文件也视为相关。不要因为文件上的人名与客户姓名不一致而判为 mismatch。只要文件内容可归入分类体系且与客户/办理人相关,即视为符合。\n" +
            "- **文件中出现的姓名若匹配客户姓名、办理人姓名或用户判定标准里列出的其他关联人(家庭成员/共同申请人等),包括其拼音或英文转写,即视为与本进展强关联,应判 match 或 partial,不要因当事人姓名与客户本人不一致就判 mismatch。**授权委托书(Power of Attorney)、公证认证文件等,其委托人/当事人常为客户的家属或本进展的办理人,属有效留底;" + handlerClause + "\n" +
            "- 如果一份文件明显是某类证件的标准格式(如身份证、护照、房产证),即使 OCR 提取质量差、部分文字乱码,也应判为 match 或 partial,不要因 OCR 噪声判 mismatch。\n" +
            "- **服务合同、客户确认类聊天记录/邮件/截图属于有效留底证据**,它们能证明相关服务已启动或客户已确认事项。合同归入 C 类,客户确认/放弃类沟通归入对应分类,**不要因为它们不是标准证件格式就判 mismatch**。\n" +
            "- 若文件体现了服务已启动或客户已确认某项服务内容,请在 key_points 中明确标注,供总体判定参考。\n" +
            "- **阶段错配不作硬性否决**:某文件更适用于其他阶段,只要文件本身可归入分类体系且与客户/办理人相关,最多判 partial,不要仅因阶段不符就判 mismatch。\n\n" +
            "请输出:\n" +
            "1. verdict: 三选一 (\"match\"|\"partial\"|\"mismatch\")\n" +
            "2. match_score: 0-100 整数\n" +
            "3. doc_category: 字母编号+子类名, 如 \"A-护照\"、\"G-批复函\"\n" +
            "4. reason: 30-120 字判断依据,遇敏感信息用 [金额]/[手机号]/[身份证]/[银行卡] 占位\n" +
            "5. key_points: 3-6 条要点\n\n" +
            "返回严格 JSON,不要 markdown 代码块:\n" +
            "{\"verdict\": \"match\", \"match_score\": 0, \"doc_category\": \"A-护照\", \"reason\": \"...\", \"key_points\": [\"...\"]}\n\n" +
            "文件内容：\n---\n" + text + "\n---\n";
    }

    private async Task<string> CallLlmAsync(string prompt, CancellationToken ct)
    {
        var body = new
        {
            model = _model,
            temperature = _temperature,
            messages = new[] { new { role = "user", content = prompt } }
        };
        using var req = new HttpRequestMessage(HttpMethod.Post, $"{_baseUrl}/chat/completions");
        req.Headers.Add("Authorization", $"Bearer {_apiKey}");
        req.Content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");

        using var resp = await _http.SendAsync(req, ct);
        var respText = await resp.Content.ReadAsStringAsync(ct);
        if (!resp.IsSuccessStatusCode)
            throw new InvalidOperationException($"LLM HTTP {(int)resp.StatusCode}: {respText[..Math.Min(300, respText.Length)]}");

        var root = JsonDocument.Parse(respText).RootElement;
        return root.GetProperty("choices")[0].GetProperty("message").GetProperty("content").GetString() ?? "";
    }
}
