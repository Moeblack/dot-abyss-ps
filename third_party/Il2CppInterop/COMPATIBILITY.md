# Il2CppInterop 兼容构建

上游：[BepInEx/Il2CppInterop](https://github.com/BepInEx/Il2CppInterop)。基线提交：`dbda1cb353b0f4253345dc45136d170b9e50a5a0`。

本目录保留构建根目录两个 DLL 所需的 Common、Runtime、HarmonySupport 项目及上游构建引用。修改后的源代码已包含补丁，不需要再次应用 `local-changes.patch`。

## 适用环境

已验证：Windows x64、游戏 PC 1.15.0（Unity 6000.3.8f1）、BepInEx `6.0.0-be.791+b782da4`、Il2CppInterop `1.5.3.0`、.NET `6.0.7`。版本号相同不代表 DLL 相同，应同时核对 SHA256。

## 文件核对

在游戏目录中运行：

```powershell
Get-FileHash .\BepInEx\core\Il2CppInterop.Runtime.dll, .\BepInEx\core\Il2CppInterop.HarmonySupport.dll -Algorithm SHA256
```

每个文件应分别匹配下方的原版或修补版哈希。两个文件都已是修补版时无需重复替换；未知哈希先核对来源和运行环境。退出游戏后备份两个文件，再按根目录 README 成对替换；回滚也应恢复两个文件。

### Il2CppInterop.Runtime.dll

- 原版：`65ab051a681c2c1e52df7596ff738be401aae72cff882ff18241cf54a3ea38a4`
- 修补版：`9bd74dc51718bfb4ba7b92d36db6104d651d7083adca9d6dd378847eb74f0bcb`

### Il2CppInterop.HarmonySupport.dll

- 原版：`f7612ea2e5a4d473b771f34ee3b20cf32dc0c4ecea0185416474999a17fbcc07`
- 修补版：`bd77755f1525cb46fc408e2f2ac3df962e657c82dd01feac6a48f08bf3c754d8`

## 本地修改

- `Il2CppInterop.HarmonySupport/Il2CppDetourMethodPatcher.cs` 和新增 `ValueTypeArgument.cs`：按 Windows x64 ABI 处理小型值类型的按值参数，保留实例和引用参数的传递方式。
- `Il2CppInterop.Runtime/IL2CPP.cs`：避免已经解包的泛型值类型再次装箱。

根目录分发的两个 DLL 与本地已验证的构建产物哈希一致。

## 构建

安装 .NET SDK 8（按 `global.json` 选择），在本目录运行：

```powershell
dotnet build Il2CppInterop.HarmonySupport/Il2CppInterop.HarmonySupport.csproj -c Release
```

首次构建需要恢复项目声明的 NuGet 依赖。输出位于 `bin/Il2CppInterop.Runtime/net6.0/` 和 `bin/Il2CppInterop.HarmonySupport/net6.0/`。构建路径或 SDK 不同可能导致产物哈希不同。

`Il2CppInterop.Runtime/Libs/Il2Cppmscorlib.dll` 是上游仓库自带的构建引用，不需要复制到游戏中。

## 许可

Il2CppInterop 使用 LGPL-3.0-only，见 [LICENSE](LICENSE)；其引用的 GPLv3 全文见 [COPYING.GPL-3.0](COPYING.GPL-3.0)。原有作者和版权说明均保留。
