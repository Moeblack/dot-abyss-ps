# 本地主数据接入

当前使用 `Luo-Lio/dotabyss` 的 20261012 发布缓存作为输入，保持原始 MessagePack 字节不变。来源标记为 Windows 缓存，读取兼容性依据当前 FANZA 修改版 `1.14.4` 的原生 formatter，而不是平台名称相近的推断。

## 校验结果

- 11,967,959 字节，406 张表、209,422 行。
- SHA-256：`e980d2f6a6dfd9de9c20891c1ca7001be62de99d59e29291b0f90e2154fed0e5`。
- 客户端要求的 364 张表全部存在，已有字段类型和整数范围通过校验。
- `MFirstDownloadFormatter` 的未知键分支调用 `MessagePackReader.Skip`，因此额外 42 表原样保留。
- `m_event_bonus_quest_chapters` 的 42 行缺少末尾 `m_side_story_stage_id`；原生 formatter 只读取实际数组长度，未出现的末列保留 Int64 默认值 0。校验策略附有这个方法的 token、RVA 和客户端 metadata 哈希，没有改写原始数据。
- `m_encounter_quest_view_drops` 有 36 行重复 ID。没有证据表明这个视图的 ID 必须唯一，因此只报告，不擅自删除。只有附有独立依据的唯一键约束才作为拒绝条件。

Google Play `1.15.0` 需要另外三张表，并有两张表的字段类型/顺序变化，现有数据不满足其契约。不能用同一版本字符串把它声明为兼容。

## 导出与检查

在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m tools.export_master_schema --unpacked ..\dotabyss_unpacked --output artifacts\master-schema.json
.\.venv\Scripts\python.exe -m dotabyss_ps.masterdata inspect --payload data\master\first-download.msgpack --schema artifacts\master-schema-fanza-1.14.4.json
```

自动导出的 schema 默认严格匹配。经过原生反汇编确认的未知表跳过和末列默认策略，保存在 `master-schema-fanza-1.14.4.json`，只针对该文件记录的客户端 metadata。不要将其复制到不同版本而不重新核对。

## 暂存、激活与回滚

```powershell
.\.venv\Scripts\python.exe -m dotabyss_ps.masterdata stage --payload data\master\first-download.msgpack --schema artifacts\master-schema-fanza-1.14.4.json --source 'GitHub release 20261012 master cache'
.\.venv\Scripts\python.exe -m dotabyss_ps.masterdata activate local-e980d2f6a6dfd9de-75bb0baadba0
```

`stage` 只在全部校验通过后保存不可变 blob 和来源记录，不改变活动版本。`activate` 再次验证 blob，并在一个 SQLite 事务中更新活动数据集和 `resource_version`。已有用户和会话保留；失败或客户端版本不符时不切换。

本地版本由 payload 与 schema 的 SHA-256 派生，明确带 `local-` 前缀。回滚使用同一 `activate` 命令选择以前暂存的版本，不删除旧数据。激活后 `/local/runtime` 不能将 `resource_version` 改成与数据集不一致的任意值；客户端登记和数据集切换在事务内合并，避免并发丢字段。

文件存放在 `data/master/blobs/<sha256>.msgpack`，元数据存入现有 SQLite 的新增 `master_datasets` 表。`business_validated=false` 表示尚未完成游戏业务验证，不能因格式校验通过就把主页或战斗标为可用。

## HTTP 与原生请求

- `/local/master` 返回活动数据集的元信息。
- `/local/master/<revision>/first.msgpack` 是诊断读取路径。
- `/data/<revision>` 与客户端原生首下路径对应，返回完全相同的 MessagePack 字节及 SHA-256 ETag。

服务器先读取并校验一份字节快照，再把同一快照作为响应体发送。即使文件在此后被修改，也不会出现“旧 ETag 对应新字节”的情况。未知版本、损坏或截断的 blob 明确失败；合法条件请求可以返回 304。

原生路径由 `RuntimeConfig.GetMasterUrl() + masterVersion` 拼接；`SecureLinkUtil` 会重建 HTTPS URI 并丢掉自定义端口。现有本地适配器在 `UnityWebRequest.SendWebRequest` 处统一保留路径并改到本地 origin，恢复正确的本地协议与端口。官方链接的签名查询只在本地被忽略，不转发到官方服务器。

原生磁盘缓存名为 `SHA256(UTF8(environment + '_' + revision))` 的前 16 字节十六进制加 `.dat`。因此只复制旧缓存文件名不能代替真正的下载与内存加载流程；当前通过 HTTP 提供数据，后续仍需验证原生 `DownloadFirstAsync` 将表加载进 `MasterDataStore`。
