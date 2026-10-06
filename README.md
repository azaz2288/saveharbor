# SaveHarbor

为游戏存档与个人工程保存多个真实版本，并实际恢复验证。不是仅比较目录摘要：文件内容存进SHA256寻址对象库，相同内容复用；每次成功备份发布独立版本清单。

Python 3.12+，无运行时第三方依赖。本地NTFS/ext4等支持hard link的文件系统；不支持的文件系统失败关闭，不降级为可能覆盖的写法。

```sh
python examples/demo.py
python -m saveharbor init new-store
python -m saveharbor backup my-saves new-store --exclude cache
python -m saveharbor list new-store
python -m saveharbor verify new-store SNAPSHOT_ID
python -m saveharbor restore new-store SNAPSHOT_ID new-restored-directory
python -m saveharbor diff new-store FIRST_ID SECOND_ID
python -m unittest discover -s tests -v
python -m pip install .
saveharbor --help
```

SNAPSHOT_ID由backup输出或list取得。退出码0成功，2失败。初始化库和恢复目标都必须是**新目录**，不会覆盖既有存档。demo使用临时合成存档，不读写你的游戏文件。

## 已实现

- SHA256内容去重、UTC版本时间、版本列表和内容/目录差异。
- 备份开始/结束核对整个树与每文件身份/大小/时间，读取时核对文件handle，检测增删和普通并发改写；未完成时不发布完整版本。
- 清单和对象验证、路径穿越/Windows保留名/大小写冲突/链接/目录结构拒绝；保存空目录和Unicode路径。
- 恢复前校验全部对象，损坏输入不创建目标；恢复时再次核对实际写出bytes。只用exclusive创建目标/文件，错误不返回“恢复成功”。
- 确认Windows path stat和handle fstat的ctime语义可能不同：跨API比较身份/size/mtime，同API前后保留完整指纹保护。诊断脚本在tools。

## 数据保护与限制

目前无加密、签名、远端存储或自动清理。库里的原内容及清单路径都可能敏感，默认保存在你控制的私有磁盘。SHA256防意外损坏，不防攻击者同时改写清单与对象。

仅在可信本地目录使用，不是防恶意并发链接替换的安全沙箱，也不是原子文件系统快照。运行中的游戏应先保存并退出，或提供不可变导出。恢复过程中磁盘/I/O错误可能留下一份**不完整的新目标**，不会删除它或修改既有目标；请检查错误并换另一个新目录重试。

只保证文件内容与目录结构，不恢复ACL、时间戳、扩展属性、稀疏布局和符号链接。禁止source/store重叠，排除是明确相对路径前缀，不是glob。失败备份可能留下未引用的完整对象，不伪装成可用版本；暂不做垃圾回收。100,000项及8MiB清单限额，未设总数据容量限制，备份前需检查空间。

v0.1.1发布前检查清单大小，不能写出超过loader限额的“成功版本”；加载时验证创建时间确实带时区。v0.1.2在完整验证已有对象后省去重复对象的fsync/link，但仍读取源文件、写临时数据，不是零写入去重。实际1,000-file备份/去重/恢复两次基准在benchmarks/README.md，可重复运行；本地合成测量不是性能保证。

## v0.1.3恢复一致性与故障验收

v0.1.3修复恢复一致性：只读取一次合法清单，完整核验其全部对象后按**同一份内存清单**恢复，不再在核验后重读磁盘清单。复制期间仍重新核对实际内容的SHA256与大小，并执行文件fsync。独立清单改写回归先在旧版复现恢复了另一版本，修复后恢复原预验收版本。

新增8项合成故障验收（总32tests，Windows有1项POSIX链接测试skip）：清单换版/单次加载、多对象预验收、预验收后对象变更、部分写入ENOSPC、恢复fsync错误、清单fsync与发布失败。故障目标保留且不覆盖重试，CLI失败返回2与complete:false；原对象/旧版本保持不变（显式注入对象损坏的用例除外）。ENOSPC为注入错误，不填满真实磁盘；不宣称断电目录持久性或恶意目录替换防护。CI同时用源码外安装的wheel运行这8项故障测试。

## 后续里程碑

1. 已有只读保留策略和实际恢复预演；后续实现持久审批、可恢复隔离及双阶段垃圾回收，不能直接删去唯一版本。
2. 密码派生、认证加密、错误密码和数据损坏故障测试。
3. 中断继续、对象索引与大量小文件性能测量。
4. Windows桌面存档选择/版本预览/恢复确认UI。
5. 多设备同步、冲突审阅和正式版本发布。

新作品集项目，不宣称符合飞书活动原有私有仓库准入。

## v0.2 保留策略审阅与真实恢复预演

```sh
saveharbor retention store --keep-latest 3
saveharbor retention store --keep-latest 3 --protect SNAPSHOT_ID --rehearse new-rehearsal
python examples/retention.py
```

`--keep-latest` 必填，1至1000的严格整数；按创建时间转换为UTC从新到旧选择，时间相同按snapshot ID倒序稳定决胜，不按带不同时区的字符串排序。可重复 `--protect` 指定不同的现有版本，它们与最新N份取并集。空库、未知/重复保护ID、无时区/越界时间、损坏清单或对象均拒绝；N大于现有数量就全部保留。至少保留一份，不自动删“最后一个”。

默认只审阅：输出 `keep`、`retire_candidates`、`object_candidates_if_retired`、`candidate_content_bytes` 与 `plan_sha256`。所有版本和现有对象（包括孤立对象）必须先通过校验；任意保留版本引用的共享对象都不是候选。对象分为仅候选版本引用和无版本引用两种。字节数是内容大小总和，**不是可立即释放的磁盘空间**，不含元数据、硬链接/压缩/稀疏语义。

`--rehearse` 必须给新目录且不与库重叠；工具在其下用版本ID创建子目录，逐保留版本实际写出文件、fsync、校验内容，再重新读取目标核对每个内容摘要和目录结构（包括空目录）。全部成功后才给 `restore_rehearsal.verified:true`。不加载另一份清单来恢复；审阅/复制/目标复验共享钉住的内存清单。原 `restore` 行为保持兼容。

API：`from saveharbor.core import retention_plan, rehearse_retention`，分别调用 `retention_plan(store, keep_latest=3, protect=[id])` 和 `rehearse_retention(store, new_target, keep_latest=3, protect=[id])`。默认审阅的 `complete:true` 不表示已验证真实恢复，必须另看 `restore_rehearsal.verified`。退出码0完成、2失败；失败不输出部分候选或恢复成功报告，新目标可能不完整并保留，不自动清理，重试用另一个新目录。

这是**不可执行删除的候选计划**：始终 `executable:false`，不删除/移动任何版本或对象，不提供apply命令。即使恢复预演通过也不是删除授权；持久审批、可恢复隔离、跨进程排他与最后回收仍未实现。plan摘要规范绑定明确策略、全catalog清单字节SHA及对象SHA/大小，使用sort_keys/ensure_ascii/紧凑JSON的UTF-8计算；不是签名、身份或持续有效承诺，不应交给外部脚本盲目删除。库后续变化应重新审阅和预演。

请暂停该库的其他写入后使用：检查前后完整目录条目及文件身份/size/mtime/ctime，出现pending/非规范文件、链接或普通并发变动则失败关闭；不自动清除pending。只适用于工具格式的可信本地库，不防恶意同权限修改、恢复元数据伪装或链接竞态，不是原子文件系统快照。保留审阅另限1000版本、100000对象、合计100000路径项/32MiB清单；单清单8MiB限制不变。没有总内容容量、硬CPU/RSS/磁盘限额；对象核验读全量内容，预演会实际占用保留版本的空间，需预留容量。

候选报告虽不列原文件名，版本ID/时间/数量/内容摘要仍可能敏感，SHA不是匿名化；实际恢复文件也含原内容。没有加密、签名、GUI或真实断电恢复保证。
