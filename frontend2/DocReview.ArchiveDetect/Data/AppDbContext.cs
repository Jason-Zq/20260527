using Microsoft.EntityFrameworkCore;
using DocReview.ArchiveDetect.Models;

namespace DocReview.ArchiveDetect.Data;

public class AppDbContext : DbContext
{
    public AppDbContext(DbContextOptions<AppDbContext> options) : base(options) { }

    public DbSet<Client> Clients => Set<Client>();
    public DbSet<ArchiveDetectProgress> Progresses => Set<ArchiveDetectProgress>();
    public DbSet<ArchiveDetectBatch> Batches => Set<ArchiveDetectBatch>();
    public DbSet<ArchiveDetectFile> Files => Set<ArchiveDetectFile>();

    protected override void OnModelCreating(ModelBuilder mb)
    {
        mb.Entity<ArchiveDetectBatch>().HasKey(b => b.BatchId);
        // 现有库时间戳列是 timestamp without time zone,用 DateTime(Unspecified)对齐,禁用 UTC 转换
        base.OnModelCreating(mb);
    }
}
