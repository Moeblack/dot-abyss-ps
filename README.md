# dot-abyss-ps

《ドットアビスX》PC 版的本地服务端、客户端适配插件与管理启动器，当前适配 **1.15.0**。

- 常驻养成、主线剧情与教程、战斗模式、活动及本地复刻。
- 多存档管理、数据编辑、备份恢复与客户端同步。
- 在控制台中启动、停止或重启服务，并查看日志。
- 在启动器中一键安装或更新繁体翻译，查看进度与日志。
- 按功能支持热更新；部分改动在切换页面、下场战斗或重新登录后生效。

部分玩法采用可配置的本地规则，各模式仍在持续进行实机验证。（99%的内容已填充完毕）

## 使用

需要 Windows、Python 3.11+，以及另行准备的匹配 PC 客户端、主数据、游戏资源和插件运行环境。编译客户端插件另需 .NET SDK。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\start-console.cmd
```

环境准备完成后，点击“启动服务”，选择或创建存档，再启动游戏。首次配置见 [启动器说明](docs/launcher.md)，日常操作见 [控制台说明](console/README.md)。

## 运行库兼容补丁

根目录提供 [Il2CppInterop.Runtime.dll](Il2CppInterop.Runtime.dll) 和 [Il2CppInterop.HarmonySupport.dll](Il2CppInterop.HarmonySupport.dll)，用于修复 Windows x64 值类型参数传递和泛型字段装箱的兼容问题。

1. 完全退出游戏，按[版本与哈希说明](third_party/Il2CppInterop/COMPATIBILITY.md)核对原文件；已经是修补版则无需重复替换。
2. 备份游戏目录 `BepInEx/core/` 中的两个同名 DLL。
3. 将本仓库根目录的两个 DLL **一并复制到游戏的 `BepInEx/core/`**，覆盖原文件。
4. 重新启动游戏。需要回滚时，退出游戏，再将备份的两个 DLL 一并还原。

源码、修改记录和许可证位于 [third_party/Il2CppInterop](third_party/Il2CppInterop/)，无需复制到游戏目录。补丁不修改存档，也无需重启私服服务

## 中文翻译

繁体译文使用 [s88037zz/dotabyss-translation](https://github.com/s88037zz/dotabyss-translation)，翻译插件使用 [s88037zz/AbyssMod](https://github.com/s88037zz/AbyssMod)。提供本地缓存，并补充技能说明、新手引导、通用界面、任务和商店译文；启动器更新时会自动合并补译。

配置与更新方式见 [翻译集成说明](pc-translation/README.md)。AbyssMod 的 MIT 许可保存在 [LICENSE.AbyssMod](pc-translation/LICENSE.AbyssMod)，译文许可按译文项目的上游说明处理。

仓库包含源码、必要的结构描述、配置示例、文档及上述运行库兼容补丁，**不包含游戏本体、素材、主数据原包、个人存档或管理凭据**。

## 参与贡献

欢迎大家提交 Issue 和 PR！
