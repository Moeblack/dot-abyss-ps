# 繁体翻译集成

翻译来自 https://github.com/s88037zz/dotabyss-translation ，插件来自 https://github.com/s88037zz/AbyssMod 。保留原繁体译文，使用完整本地缓存，不依赖游戏运行时访问 CDN。MIT 许可随插件保存为 LICENSE.AbyssMod。

## 启动器一键安装

准备好本地 PC 客户端、Git 和 .NET SDK 后，打开启动器，在“繁体中文”栏点击“一键安装”或“更新翻译”。无需先启动游戏服务。首次使用会获取固定版本的插件源码、兼容补丁及经过哈希校验的字体和依赖，同时获取译文；后续更新使用相同流程。

阶段进度、结果和日志显示在启动器中。安装前需要退出游戏；更新期间启动器会阻止打开游戏。安装完成后，下次启动游戏生效。关闭浏览器窗口不会中断后台任务。

更新会保留无关配置、备份原有翻译。检测到上游仓库有额外本地修改，或本地字典与新译文冲突时，会停止并显示原因。翻译字典、下载缓存及备份保留在本机，不进入 Git。

## 当前安装

- 插件源码：88357b9757414cc2a07913be90a6f7b2c3f28d08，版本 1.2.0。
- 翻译数据：028834fe5f6801cfde54270b1419355168ae5a52。
- 1,029 个 JSON：1 份清单和 1,028 份字典；包括 887 份剧情字典，共 52,003 条记录。
- Utility.dll 和字体 ttcuyuanj 从上游 v1.2.0 发布包提取并校验 SHA256。
- 插件以现有 PC 1.15.0 的 BepInEx core 与 interop 编译，未替换 BepInEx、winhttp.dll 或现有私服插件。

实际路径为 `dotabyss_pc/local-client/BepInEx/plugins/AbyssMod/`，配置在同一客户端的 `BepInEx/config/AbyssMod.cfg`。翻译启用、语言为 zh_Hant、CDN 为空；机翻和采集关闭，General 四项为 true 以保留游戏原行为。原始上游清单另存，实际缓存清单由本地文件重新生成。

## 本地兼容处理

排除主表 MNgWords 和字典类型 m_ng_words 的翻译映射，保持名字过滤规则。`input-text-compat.patch` 保护 TMP_InputField 的实际输入值，使文字 setter、OnEnable、周期刷新和直接 UI 翻译都跳过用户输入；输入框占位提示仍可翻译。

插件构建项目仅引用当前本地运行环境，不使用上游 csproj 的浮动 NuGet 版本。源码改动仅位于独立的 reference/AbyssMod-translator 副本，原有私服代码不受影响。

## 更新

退出游戏后，在项目根目录运行：

```powershell
.\update-translation.ps1 -Fetch
```

这会快进更新独立译文仓库，验证 JSON、重新生成本地清单、重新构建当前兼容插件，并备份后安装。只重新安装现有内容则省略 `-Fetch`。上游插件源码不会自动升级，升级插件需重新核对兼容补丁。

安装记录与逐文件哈希在 `artifacts/translation/installed.json`，备份位于 `artifacts/translation/backups/`。更新器保留不属于上次安装清单、且不与新译文冲突的本地补充文件。若译文仓库有本地修改，Fetch 会停止以保留修改。

F8 是上游的翻译开关；F10 仅重载配置，不能完整更换已载入字典、语言或字体。安装和更新后重启游戏生效，无需重启私服服务。

## 已知范围

原译包不是完整游戏的覆盖保证，缺少译文时仍显示日文。上游清单存在过期哈希与缺失的 static 引用，本地已按实际文件重建。原译文存在少量 size 标签不平衡，按用户要求未改译文内容。

记录数量包含原文相同和空值：52,001 条非空，50,557 条与原文不同。m_characters 中的重叠名字仍按插件原有 names 优先级处理。

## 本次实机验证

已重开本地客户端：中文字体 TTCuYuanJ SDF 加载成功，120 类主表记录出现翻译日志，剧情 mas_1001000101 的 21 条译文从本地缓存读入。画面已确认 UI 按钮和剧情气泡显示繁体中文，私服插件正常就绪并进入 Novel 场景。完整结果记录在 `artifacts/translation/verification.json`，这不代表尚未浏览的全部文本都已验证。
