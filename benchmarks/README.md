# Synthetic versioning/recovery benchmark

Run `python benchmarks/scale.py`. It creates 1,000 unique 1,024-byte synthetic files in its own temporary directory, makes two versions, verifies and restores one. It asserts content restoration and that the second version does not double the object count.

Local Windows/Python 3.12 measurement on 2026-10-06 with tracemalloc enabled:

| Stage | v0.1.1 seconds | v0.1.2 seconds |
| --- | ---: | ---: |
| First backup | 17.596 | 10.365 |
| Deduplicated backup | 9.912 | 7.689 |
| Verify | 0.964 | 0.942 |
| Restore | 4.550 | 4.377 |

Source 1,024,000 bytes, two snapshot manifests, 1,000 unique objects; Python allocation peak 2,826,028 bytes (v0.1.1) and 2,825,984 bytes (v0.1.2). One local sample per version only: tracemalloc overhead, filesystem caches, small-file/fsync latency and hardware affect results. The first-backup change also reflects run-to-run variation; no causal speedup claim follows from two samples. Not whole-process RSS or a universal speed claim.

v0.1.2 avoids redundant object fsync/link when the same object already exists, after rehashing the stored object. It still reads/validates every source file and writes temporary duplicate bytes; it is not a zero-write deduplication implementation. Source-change and restore integrity tests remain required. No timing threshold in CI.
