# 本地 Android 资源接入

资源导入以解码后的 catalog URL 索引为依据，每条 URL 精确对应 `cache_key`、Hash128、provider 和声明长度。它不根据文件名猜测 URL，也不把 Hash128 当作文件 MD5。服务端按文件内容的 SHA-256 保存 blob，避免将很长的 URL 变成 Windows 文件路径。

导入前需要已经激活匹配客户端的主数据。资源 manifest 同时绑定主数据版本、内容哈希、schema 哈希和完整客户端身份；Google Play 与 FANZA R18 使用不同命名空间。切换主数据后，旧资源 manifest 会明确报错，直到激活匹配资源。导入不会改写运行版本字段或旧存档。

## 暂存与激活

```powershell
.\.venv\Scripts\python.exe -m dotabyss_ps.assets --data-dir .\data-play-1.15.0 stage --index <resource-url-index.json> --catalog <catalogcached.bin> --catalog-hash <catalogcached.hash> --cache-root <Shared目录> --source '本地已下载缓存'
.\.venv\Scripts\python.exe -m dotabyss_ps.assets --data-dir .\data-play-1.15.0 activate <stage返回的assets版本>
.\.venv\Scripts\python.exe -m dotabyss_ps.assets --data-dir .\data-play-1.15.0 status
```

默认要求索引中全部远程文件存在；缺文件不会发布 manifest 或修改当前激活指针。明确添加 `--allow-partial` 可以暂存已验证文件，manifest 保留所有缺失路径和原因。部分集合可以显式激活，但缺失 URL 仍返回 404，不会由静态目录中的同名文件补充。

catalog 与 hash 原始字节始终作为诊断材料存入 blob。只有同时提供 `--catalog-relative-path` 和 `--catalog-hash-relative-path`，才会将它们绑定到指定的完整 `resources/...` URL。未取得实际请求路径时不要填写；`catalogcached.bin` 是本地缓存名，不能据此猜测远端名称为 `catalog_1.bin`。

## 缓存校验

缓存定位严格使用 `Shared/<cache_key>/<Hash128>/__info`。当前已核对的 `__info` 为四行 ASCII：`-1`、数字时间戳、文件数 `1`、文件名。UnityFS 文件名为 `__data`；CRI 文件名必须与索引 URL 的最后一段一致。不会选取其他 Hash128 目录中的文件。

- catalog 文件的 SHA-256 必须等于索引记录。随同提供的 hash 文件记录观测到的 catalog 版本；它与 catalog 内部 build-result hash 含义不同，分别保存。若索引含 `catalog_version_hash`，必须与 hash 文件一致；否则以调用者明确提供的源文件配对为关联依据。
- URL 必须位于显式远端基地址和所选客户端命名空间中；拒绝路径逃逸、不同源 URL 和冲突的重复路径或缓存键。
- 缓存文件长度必须匹配 catalog。UnityFS 校验签名、版本、声明长度和 block-info 范围；AFS2 校验头部、表长度、对齐后的偏移及最终文件长度。AFS2 布局依据公开解析器的[格式处理](https://github.com/vgmstream/vgmstream/blob/master/src/meta/awb.c)核对。
- CRI provider 同时包含 AFS2 音频和 CRID/USM 视频，按实际签名分别处理。USM 逐块检查声明长度、载荷偏移、padding、流块签名及文件末尾覆盖，不将视频误作音频。字段位置依据公开的 [USM 容器解析](https://github.com/donmai-me/WannaCRI/blob/master/wannacri/usm/chunk.py)核对。
- 校验范围不包含解压后的 Unity 对象、音频解码或运行时依赖解析；不会将这些检查描述为游戏可游玩验证。

文件以 `assets/blobs/<sha256>` 保存，manifest 位于 `assets/manifests/<assets版本>.json`。新文件先写临时文件并落盘，再原子发布；激活前复验 blob，最后原子替换 `assets/active.json`。失败不会覆盖旧激活指针，已有 blob 保留供其他 manifest 和回滚使用。

## HTTP 行为

`GET /resources/...` 优先查询当前 manifest。活动前缀内缺失文件返回 404，blob 损坏或主数据绑定不匹配返回 503；不会静默回退。活动前缀以外继续使用原有静态目录，没有激活资源时也保持原行为。

每次响应读取并验证一份有大小上限的字节快照，然后用这份快照生成响应；SHA-256 ETag 与正文一致。条件请求命中返回 304 前仍验证 blob。文件名带对应 Hash128 后缀的资源允许长期缓存；catalog、hash 和其他固定 URL 使用 `no-cache`，切换 manifest 后会重新验证。manifest 在进程内按不可变版本缓存，服务重启或切换版本会重新验证其内容哈希。诊断接口只返回计数、绑定和错误，不枚举完整资源内容。
