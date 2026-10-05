# Synthetic versioning/recovery benchmark

Run `python benchmarks/scale.py`. It creates 1,000 unique 1,024-byte synthetic files in its own temporary directory, makes two versions, verifies and restores one. It asserts content restoration and that the second version does not double the object count.

Local Windows/Python 3.12 measurement on 2026-10-06 with tracemalloc enabled:

| Stage | Seconds |
| --- | ---: |
| First backup | 17.596 |
| Deduplicated backup | 9.912 |
| Verify | 0.964 |
| Restore | 4.550 |

Source 1,024,000 bytes, two snapshot manifests, 1,000 unique objects; Python allocation peak 2,826,028 bytes. One local sample only: tracemalloc overhead, filesystem caches, small-file/fsync latency and hardware affect results. Not whole-process RSS or a universal speed claim. Current second backup still reads/validates all files and publishes each via temporary writes; future performance work must retain source-change and restore integrity tests. No timing threshold in CI.
