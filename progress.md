# Verified progress

## 2026-10-07 v0.2 retention review and restore rehearsal

Added explicit keep-latest/protected snapshot policy, actual UTC chronology with stable ID tie-breaking, bounded catalog review, all-object hash/size checks (including retired-only and orphan objects), shared-reference-safe candidates and a content/policy digest. Review is read-only and non-executable: no object or snapshot is deleted, moved or quarantined, and no apply command exists. Optional NEW-root rehearsal writes each kept version from the same pinned in-memory manifest; after all copies finish it re-audits every destination's content/tree and verifies the source-store inventory remained unchanged.

Initial 13 new methods produced one CLI failure and 25 API errors including subcases because retention did not exist. A helper-extraction patch initially matched verify instead of restore, introducing transient local NameErrors; corrected before publication without weakening the original tests. A later dedicated fault test reproduced a new false success when a later copy damaged an earlier already-audited target; auditing all copies after the final write fixes it. The complete final 58-method suite passes on Windows (56 pass, 2 symlink privilege skips), 27.338 seconds; both synthetic demos, compile/diff and 5 root maintenance tests passed. Includes 30 seeded set-reference comparisons, actual 1000/1001 manifest boundary, UTC overflow, source-change, copy/fsync failure, post-copy corruption, pinned load and independent canonical digest checks. No user backups/saves or real disk exhaustion tested.

Module version was stale at 0.1.2 while package metadata said 0.1.3; this release aligns both to 0.2.0 with a regression. Installed-wheel acceptance and exact-SHA Windows/Linux CI are recorded in the portfolio stage report outside the source repository. CLI-only work has no browser UI; real command/subprocess and installed-package checks apply. This stage completes read-only policy/rehearsal, not persistent approvals, two-phase garbage collection, encryption, GUI, atomic filesystem snapshots, hard OS quotas or power-loss recovery.

0.2.0 wheel installed only into this project's venv. Source-external cwd and isolated Python confirmed matching module/package versions and site-packages, all 8 original restore faults (0.748s) plus 26 retention methods (25 passed, 1 Windows symlink privilege skip, 17.384s), synthetic retention demo, console subcommand help and pip check. No repository hook or Release asset was changed. Full source 58 methods include two total Windows symlink privilege skips; Linux CI must verify both rather than inheriting old green.

## 2026-10-06 v0.1.3 pinned restore manifest and fault acceptance
Eight new synthetic fault tests initially produced two real failures: restore loaded the manifest twice and restored new-save after the preflighted old-save manifest was changed on disk. Restore now loads once and shares object preflight with verify, using that same validated in-memory manifest for copying. Full per-copy hash/size checking, file fsync and exclusive targets remain.

All32 tests passed locally (Windows31 passed/1 POSIX symlink skipped), plus temporary restore demo and compile checks. The other six fault tests already passed the previous implementation: this is added acceptance coverage, not six additional fixes. They cover all-object preflight, later object corruption, partial ENOSPC writes, CLI sync errors, manifest sync and publication failures. Only synthetic directories are touched. Failed new targets are retained, no existing target is overwritten, and old store bytes remain unchanged except deliberate fixture corruption. No real disk exhaustion, power-loss durability, malicious filesystem race protection or GC/encryption/UI completion claim.

Independent wheel0.1.3 installed into a new temporary environment; source-external -I execution verified site-packages/version, all8 fault tests, CLI module/console help and pip check. CI now repeats the installed fault suite on Windows/Linux. Same-SHA remote CI evidence will be recorded in the external maintenance report after publication. Next: verified retention policy/two-phase GC design and interruption coverage; deletion is still unimplemented.

First published commit bedead6 passed Linux CI but Windows publication-fault test did not raise: its injection compared raw store spelling against backup's resolved target. Added a portable source/../store alias to reproduce the failure locally, then normalized both injector paths (without removing the hit assertion or weakening the expected error). This is a test-harness correction, not a second product restore fix; the failed run remains recorded in maintenance.

## 2026-10-06 initial v0.1
22 tests (Windows21 passed/1 POSIX symlink skipped), real temporary old-save restore demo, independent wheel installation and installed CLI passed. Published62d68da4d0d8f5578113cf01510a1d71250d629a; same-SHA Windows/Linux CI success.

Windows path-based and handle-based ctime differed for164 of1,000 synthetic files. Fixed cross-API comparison to identity/size/mtime and kept full metadata checks within each API, with explicit regression. No source-change guard removed.

## 2026-10-06 v0.1.1 metadata/manifest protection
Validate timezone-aware creation metadata before listing/restoring. Refuse a manifest larger than the loader's8MiB limit before publication, so a writer cannot publish an unreadable supposedly complete version.23 tests (Windows22 passed/1 skipped) passed. Synthetic1,000-file/two-version benchmark recorded honestly in benchmarks/README.md; no universal speed or crash-durability claim.

Next: non-destructive deduplicated-write optimization and fault injection, then retention/GC design with restore verification; encryption/UI remain unimplemented.

## 2026-10-06 v0.1.2 redundant durability work
Regression first reproduced two fsync calls during a one-file duplicate backup. Now only the new manifest is fsynced when the stored object passes full hash/size verification. New objects retain fsync and exclusive hard-link publication, including the concurrent-writer collision check.24 tests (Windows23 passed/1 skipped) passed; source-change and corrupt-object regressions retained. Second synthetic benchmark recorded in benchmarks/README.md; temporary duplicate bytes are still written, and timings are not a causal or universal speedup claim.
