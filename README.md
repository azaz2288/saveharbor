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

## 后续里程碑

1. 经过模拟恢复验证的保留策略及双阶段垃圾回收，不能直接删去唯一版本。
2. 密码派生、认证加密、错误密码和数据损坏故障测试。
3. 中断继续、对象索引与大量小文件性能测量。
4. Windows桌面存档选择/版本预览/恢复确认UI。
5. 多设备同步、冲突审阅和正式版本发布。

新作品集项目，不宣称符合飞书活动原有私有仓库准入。
