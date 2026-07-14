using System.Reflection;
using Microsoft.EntityFrameworkCore;
using DocReview.ArchiveDetect.Data;
using DocReview.ArchiveDetect.Services;

var builder = WebApplication.CreateBuilder(args);

// 现有 PG 库时间戳列为 timestamp without time zone,关掉 Npgsql 的 UTC 强制转换
AppContext.SetSwitch("Npgsql.EnableLegacyTimestampBehavior", true);

// ── EF Core ──
builder.Services.AddDbContext<AppDbContext>(opt =>
    opt.UseNpgsql(builder.Configuration.GetConnectionString("Default")));

// ── 业务服务 ──
builder.Services.AddScoped<ArchiveDetectService>();
builder.Services.AddHttpClient<LlmService>(c => c.Timeout = TimeSpan.FromSeconds(120));
builder.Services.AddHttpClient<FileFetcher>(c => c.Timeout = TimeSpan.FromSeconds(120));
builder.Services.AddSingleton<OcrService>();

// ── 后台 worker(抢任务 + finalize + watchdog) ──
builder.Services.AddHostedService<ArchiveWorker>();

// ── Controllers + JSON(snake_case,对齐前端契约) ──
builder.Services
    .AddControllers()
    .AddJsonOptions(o =>
    {
        o.JsonSerializerOptions.PropertyNamingPolicy = System.Text.Json.JsonNamingPolicy.SnakeCaseLower;
        o.JsonSerializerOptions.PropertyNameCaseInsensitive = true;
    });

// ── Swagger / OpenAPI ──
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddSwaggerGen(c =>
{
    c.SwaggerDoc("v1", new Microsoft.OpenApi.OpenApiInfo
    {
        Title = "文件留底检测 API (.NET 重构 PoC)",
        Version = "v1",
        Description = "审核批次后端 —— ASP.NET Core Web API,连 PostgreSQL,含 RapidOcrNet 原生 OCR + GLM 判定。"
                    + "对标 Python FastAPI backend 的 /api/archive-detect/* 契约。"
    });
    // 加载 XML 文档注释(csproj 已开启 GenerateDocumentationFile)
    var xmlFile = $"{Assembly.GetExecutingAssembly().GetName().Name}.xml";
    var xmlPath = Path.Combine(AppContext.BaseDirectory, xmlFile);
    if (File.Exists(xmlPath))
        c.IncludeXmlComments(xmlPath, includeControllerXmlComments: true);
});

builder.Services.AddCors(o => o.AddDefaultPolicy(p =>
    p.AllowAnyOrigin().AllowAnyHeader().AllowAnyMethod()));

var app = builder.Build();

// ── Swagger UI(开发环境启用;访问 /swagger) ──
if (app.Environment.IsDevelopment())
{
    app.UseSwagger();
    app.UseSwaggerUI(c =>
    {
        c.SwaggerEndpoint("/swagger/v1/swagger.json", "文件留底检测 API v1");
        c.RoutePrefix = "swagger";   // 访问地址 http://localhost:5001/swagger
    });
}

app.UseCors();
app.MapControllers();

// ── 临时 OCR 测试端点:POST 本地文件路径,直接返回识别文本。验证完删除。 ──
app.MapPost("/api/test/ocr", (DocReview.ArchiveDetect.Services.OcrService ocr, [Microsoft.AspNetCore.Mvc.FromBody] TestOcrReq req) =>
{
    try
    {
        var r = ocr.Extract(req.Path);
        return Results.Ok(new { text = r.Text, source = r.Source, page_count = r.PageCount, char_count = r.CharCount });
    }
    catch (Exception ex) { return Results.BadRequest(new { error = ex.Message }); }
});

app.Run();

public record TestOcrReq(string Path);
