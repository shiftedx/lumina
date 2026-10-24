# Durable photo-preview storage qualification (2026-10-24)

## Result

The durable registry preserves every catalog-referenced preview under storage pressure and keeps publication and maintenance work indexed at both one million and three million preview assets. Three million assets represent one million photos with the configured 400, 1200, and 2400 px renditions.

The legacy production LRU deleted all 500 referenced files in each of five pressure trials, requiring 500 re-decodes per trial. The durable service deleted none after restart, returned the actual retryable `preview_storage_full` error for a new set, and enumerated no derivative-tree entries. The legacy implementation enumerated 1,001 entries per trial.

## Indexed production-service timings

All values are milliseconds over five trials except seed time and database size. The benchmark bulk-seeds the production `photo_preview_assets` and `photo_derivatives` tables, then calls the working-tree `reserve_set`, `publish_set`, `release_publication`, `recover`, and `trim` implementations with real WebP products.

| Registry qualification | 1M assets + 1M refs | 3M assets + 3M refs |
| --- | ---: | ---: |
| Seed time | 17.369 s | 38.718 s |
| Database files | 850,165,760 B | 2,558,849,024 B |
| Reserve median / p95 | 0.911 / 2.160 | 0.723 / 2.002 |
| Publish median / p95 | 1.354 / 1.694 | 1.069 / 1.833 |
| Release median / p95 | 2.748 / 3.103 | 1.185 / 1.572 |
| Restart recovery query median / p95 | 68.417 / 70.654 | 184.103 / 188.410 |
| Referenced-page trim median / p95 | 39.204 / 57.522 | 32.742 / 35.328 |
| Registry assets examined per trim | 256 every trial | 256 every trial |
| Derivative filesystem entries enumerated | 0 every trial | 0 every trial |

SQLite selected the same bounded covering-index plan at both sizes:

```text
SEARCH photo_preview_assets USING COVERING INDEX ix_photo_preview_assets_reclaim_page
  (state=? AND reservation_id=? AND source_key>?)
```

Recovery increased roughly with registry size but remained below 189 ms at p95 for three million assets. Publication latency did not grow with registry size. The persisted reclaim cursor advanced exactly 256 assets in every measured call, rather than evaluating the full referenced catalog.

## Method and provenance

- Working-tree Git base: `944fdbe12cf3b324aff0afb41b6cd8e6582f6e28`.
- Working-tree `photo_cache.py` SHA-256: `a1cf168c88e13d477117d9a27e0e98b16792dadbf476ea641e85bcc632e66b97`.
- Archived schema-35 LRU SHA-256: `5eeca4c79d0a99a4fe2141a4a250f979d5571f0a4fe60a52b3b7daa8e419d564`.
- The baseline module was preserved from Git revision `944fdbe` at `output/photo-scale-baseline/backend/app/services/photo_cache.py` and executed directly by the benchmark.
- Pressure trials used 1,000 valid WebP files, 500 real production `PhotoDerivative` references, and a valid 36,969,000-byte configured quota.
- Raw trials, p95 calculations, query plan, counters, versions, and parameters are retained in [the 1M JSON](photo-preview-storage-2026-10-24.json) and [the 3M JSON](photo-preview-storage-3m-2026-10-24.json).

Reproduce the archived baseline and the three-million-asset run from the repository root:

```bash
mkdir -p output/photo-scale-baseline/backend/app/services
git show 944fdbe:backend/app/services/photo_cache.py > output/photo-scale-baseline/backend/app/services/photo_cache.py
backend/.venv/bin/python scripts/perf/photo_preview_storage.py \
  --assets 3000000 \
  --baseline output/photo-scale-baseline/backend/app/services/photo_cache.py \
  --output docs/performance/photo-preview-storage-3m-2026-10-24.json
```

## Limits

- The scale qualification creates three million registry rows and three million catalog-reference rows, not three million preview files. Only the pressure test creates an actual preview file set; creating a multi-terabyte rendition corpus is outside this benchmark's purpose.
- Bulk seed time is fixture construction, not importer throughput. Publication timings cover storage registry and atomic file publication only, not image decoding, NAS reads, face analysis, API handling, or the surrounding catalog transaction.
- The three-million-row run overlapped an authorized single-CPU face-index HNSW build on the same host. Treat its wall-clock timings as loaded-host qualification numbers, not isolated hardware microbenchmarks.
- Database size includes the SQLite database, WAL, and shared-memory files present while the production-configured engine was open.
- The pressure fixture deliberately orders referenced files as the oldest entries to expose the legacy LRU's catalog-safety failure. It is a correctness adversary, not a claim about typical access order.
- Five trials are sufficient for a regression qualification but not a statistical capacity forecast across different disks, filesystems, or hosts.
