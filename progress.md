# Verified progress

## 2026-10-06 initial v0.1
22 tests (Windows21 passed/1 POSIX symlink skipped), real temporary old-save restore demo, independent wheel installation and installed CLI passed. Published62d68da4d0d8f5578113cf01510a1d71250d629a; same-SHA Windows/Linux CI success.

Windows path-based and handle-based ctime differed for164 of1,000 synthetic files. Fixed cross-API comparison to identity/size/mtime and kept full metadata checks within each API, with explicit regression. No source-change guard removed.

## 2026-10-06 v0.1.1 metadata/manifest protection
Validate timezone-aware creation metadata before listing/restoring. Refuse a manifest larger than the loader's8MiB limit before publication, so a writer cannot publish an unreadable supposedly complete version.23 tests (Windows22 passed/1 skipped) passed. Synthetic1,000-file/two-version benchmark recorded honestly in benchmarks/README.md; no universal speed or crash-durability claim.

Next: non-destructive deduplicated-write optimization and fault injection, then retention/GC design with restore verification; encryption/UI remain unimplemented.

## 2026-10-06 v0.1.2 redundant durability work
Regression first reproduced two fsync calls during a one-file duplicate backup. Now only the new manifest is fsynced when the stored object passes full hash/size verification. New objects retain fsync and exclusive hard-link publication, including the concurrent-writer collision check.24 tests (Windows23 passed/1 skipped) passed; source-change and corrupt-object regressions retained. Second synthetic benchmark recorded in benchmarks/README.md; temporary duplicate bytes are still written, and timings are not a causal or universal speedup claim.
