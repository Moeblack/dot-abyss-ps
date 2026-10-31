# Android 本地协议适配器

该插件针对本目录来源 APK 的 Android ARM64 Unity IL2CPP 环境构建，依赖其已有 LemonLoader / MelonLoader 0.7.4、.NET 10/11 及 interop 程序集。它是本地协议诊断适配器，当前正常游戏流程先被缺失的 Android catalog/资源阻塞，尚未到达自然登录或主页。

## 已实现

- JSON 配置开关，默认关闭；启用后重启生效。
- `ObserveOnly=false` 为默认本地模式。设为 `true` 后只安装脱敏请求/响应、RootLogin 路由、Android 登录进入/返回结构观察钩子，不创建本地会话、不修改 URL、加密、版本或平台登录。
- `RuntimeConfig.GetApiUrl()` 与 `ApiManager.RefreshConfig(string)` 指向配置的本地 origin。
- 返回给原生客户端的 API base URL 保留末尾 `/`，兼容客户端直接拼接 `maintenance`、`api/home` 等不以 `/` 开头的路径；原生 getter 自检也比较完整 base URL。
- 原游戏 `UnityWebRequest` 的 HTTP/HTTPS 请求保留 path/query 改到本地 origin，禁止跟随重定向；添加本地 `x-olg-session`、`api-token`。这也会将经过该引擎接口的资产、遥测等请求导向本地，未知路径由服务器明确报错。
- 只替换 `ApiCrypt` 的 `EncryptBody`、`DecryptBody`、`EncryptLaravel`、`DecryptLaravel`，以便本地服务器接收明文协议。不修改通用加密库。
- `POST /local/login` 创建独立本地会话；`POST /local/runtime` 登记客户端 app/bundle 版本。原生 Android 登录替换调用 `/login`，只发送本地 session/token，不使用真实平台账号。
- `POST /local/diagnostics` 上报 `local_transport_ready`、`local_session_installed` 和 `native_login_completed` 等事件，区分 HTTP、客户端会话安装和指定原生函数完成；这些事件不代表游戏主页已就绪。
- 平台 SDK 初始化由本地会话取代，平台 UUID 读取使用本地 UUID；请求与响应只记 JSON 结构、长度和状态，不输出值、token 或请求头，URL query 全部隐藏。
- 平台初始化同时覆盖 `DmmSdk.Initialize` 与已由本 APK 原生调用链确认的 `DmmSdkUnityIap.Initialize`。后者被调用时上报 `unity_iap_initialized`，只表示本地平台初始化替代完成，不表示游戏账号登录完成。
- 可选 `IgnoreVersionChecks` 使用本 APK 中已核实的 `ApiManager.SkipCheckVersion` 字段；默认关闭。
- 若原生 `RootLoginRequestEntity` 被构造，记录其实际 `ApiName` 路径，避免把猜测路由当作已验证协议。
- 可选 `CaptureRedactedResponses=true` 将可解析 JSON 响应保存至 `UserData/DotAbyssLocal/responses`；递归遮盖 token/session/凭据/用户标识等字段并移除 URL query，保留其他业务字段用于检查 fixture。默认关闭，单个响应上限 2 MiB；导入前仍需人工检查非标准字段中的个人信息。

## 当前阻塞

原生反汇编已确认 `LoginDmmAndroidAsync(TermsAgree[], CancellationToken)` 返回 `(user_id, server_time)`，并确认 `RootLoginResponseEntity.IsIntegrityTokenVerified` 将 `token_status == 0` 视为已验证。适配器先校验本地 `/login` 的成功状态、错误码、时间格式及身份/会话一致性，再写入本地 UUID 静态缓存与 Prefs、调用原生 `SetEncryptUuidToken`、通过 `Engine.Get<ApiManager>()` 设置本地 session 与 api-token，最后返回上述元组。外层 `Login.RunAsync` 保留，继续执行原游戏的用户 ID 初始化。

适配器不调用平台完整性缓存保存或 UUID 验证同步。取消在网络前后检查；握手、状态写入或原生调用失败均返回失败 UniTask，不回退平台登录。`native_login_completed` 只表示这个替换函数已准备结果，后续主数据、资源与主页仍须分别验证。

已在 MuMu 验证：实验性远跳板开启后，GetApiUrl prefix 实际执行并返回本地地址；独立 .NET HTTP 完成本地会话、设备版本登记和诊断上报；实际 UnityIap 初始化入口被替换；Unity 原生业务请求收到本地 maintenance HTTP 200/Success，并通过客户端版本校验。设备读取的 app_version_code=`1`、bundle_version=`0.1.0`、Unity Application.version=`1.14.4` 是不同字段，客户端版本数组应匹配最后一项。原生 ARM64 真机尚未测试。

当前缺少匹配的 Android catalog hash/bin 与资源缓存，正常流程尚未进入 LoginDmmAndroidAsync；Windows 离线项目的资源不能据此当成 Android 资源。候选主数据另行保留，独立登录诊断不导入主数据、不写 master/resource 版本、不绕过资源加载。完整登录与主页仍未验收。

`UniTask.FromException<T>(Il2CppSystem.Exception)` 已由本 APK interop 元数据确认并通过编译，其原生泛型实例化仍需上机验证；此验证不等于已验证游戏的异常恢复流程。

独立 MOD 或平台原生 SDK 直接使用其他网络库的请求不受 UnityWebRequest 钩子覆盖。该插件不是设备级网络隔离工具。

## 纯观察模式

在退出游戏后，将配置设为 `Enabled=true`、`ObserveOnly=true`。该模式保留原游戏联网与平台登录行为，不自动登录账号，也不会上报本地 `/local/diagnostics`。观察钩子内部读取失败只记录异常类型，不替换原操作。

日志记录 `LoginDmmAndroidAsync` 进入、返回任务状态与结果类型；仅同步成功且无异步 source 的结果读取第二项的长度/能否解析为日期，第一项始终遮盖。异步结果不会被额外等待或调用 `GetResult()`，避免抢先消费原游戏的任务。状态机仅记录指定字段名和类型，不导出 token、用户 ID、UUID、serverTime 值。

纯观察模式仍默认关闭响应落盘。需要明确启用 `CaptureRedactedResponses` 才保存脱敏响应；该模式有真实游戏业务数据时，导入 fixture 前需人工复查。

## 独立原生登录诊断

`StandaloneNativeLoginProbe` 默认 `false`，只在本地模式且平台初始化成功后执行一次；ObserveOnly 模式禁用。诊断实际调用已安装补丁的 LoginDmmAndroidAsync，复用正常替换路径的 `LocalIdentity.Install`，仅消费这次自建的 UniTask，核验返回元组、原生 UUID 缓存/Prefs、EncryptToken 是否存在、ApiManager session/api-token 和外层 user_id 是否未变。

此操作使用同一个本地 profile，会写入其本地 UUID 偏好；它不是无状态观察。普通 `native_login_completed` 等事件被抑制，只上报 `standalone_native_login_probe`，明确 `normal_game_login=false`、`resource_flow_bypassed=false`，不执行外层 Login.RunAsync，也不跳过或推动资源流程。报告后停止当前测试游戏进程；下一次正常启动前必须恢复开关为 `false`。该诊断通过只能证明这个独立调用链，不代表完整游戏登录或主页加载成功。

首次实测在 `invoke_native_login` 阶段报 NullReferenceException，尚未进入登录 prefix，不能称原生登录已验证。已从旧 DLL 的 IL 确认当时 `new Il2CppReferenceArray<TermsAgree>(0)` 绑定到了原生指针构造器，传入空指针即抛异常；现改为 `0L`，明确使用长度构造器，并用编译后 IL 回归检查锁定该签名。

诊断日志分别记录空数组分配、长度验证、原生调用、返回 wrapper、awaiter 和结果读取阶段；失败堆栈只保留异常类型及方法帧，不输出异常消息、参数值、token 或源码路径。这些阶段用于区分已修复的空数组问题与后续返回值桥问题，不能把构建或托管测试成功当作完整原生登录通过。

修复空数组后的一次实测已进入登录 prefix，并完成 UUID/偏好状态写入及 awaiter 结果读取，但返回元组匹配检查为 false，独立诊断仍未通过。本包生成的 `UniTask<T>(T)` 包装器会将表示 native 值类型的 CLR 代理 class 当成对象指针传入；登录结果现改用 `(source:null, token:0)` 构造任务，再通过元数据字段 setter 将已解箱的 tuple 值复制到 `result`。

探针分别记录新建 tuple、旧泛型构造候选、字段复制后的 task.result、跨钩子返回的 task.result、awaiter 内部 task.result 与最终 GetResult 的匹配标志。旧候选仅用于比较，不被返回；非空字符串指针若不匹配最初 tuple 的已知指针，不会解引用，只报告未检查。日志只含 long 是否匹配/为零、string 是否匹配/为 null、长度与日期可解析性等特征，不输出具体值或指针地址。修复路径所有阶段必须匹配，不能用最后一步掩盖中间失配；仍待新的同次设备验证。

最新实机结论仍是 `native-login-fixed.log`：`0L` 修复后已进入 prefix，UUID 缓存和 Prefs 检查为 true，已读取 awaiter 结果，但 `tuple_matches=false`、`passed=false`。新的 `(source:null, token:0)` 加 result 字段复制版本仅完成源码构建与 Release/Debug 各 55 项托管检查，尚未部署到设备，不能标为原生登录通过。对应 DLL SHA256：`19AE81011D58797ED898E0BA7B5F2D9DD2F785424743677DA5D07180A5A2D452`。

## 构建

### MuMu trampoline 实验开关

`AllowFarTrampolines` 默认 `false`。当前 MuMu 日志已确认原包 AbyssMod 在本适配器之前调用 NativeHookAttach，并出现 `trampoline.near_unavailable`。在这一个已确认的顺序下，显式开启此选项会在安装本插件钩子前调用本 APK `libmain.so` 导出的 `dobby_set_near_trampoline(byte 1)`，保留优先近跳板但允许失败后尝试远跳板；不修改 APK 或 Loader 文件。

这是进程级实验，**不是通用修复**。本包 bootstrap 在首次 NativeHookAttach 中通过 `call_once` 强制 required-near；若设置早于该首次调用，会被覆盖。当前实现不制造一次无效 hook 来绕过初始化，也不自动停用或修改 AbyssMod。退出/初始化失败时恢复该包的 required-near 策略；强制结束进程也清除运行时设置。

启用插件后、安装钩子前，只读记录 `/proc/self/maps` 中包含 `libil2cpp.so` 或 `libmain.so` 的映射行，供同次运行的目标地址与 ELF 偏移关联；不请求 root、调试权限或修改映射。

同一阶段还直接构造请求 DTO 读取 `RootLoginRequestEntity.ApiName`，并读取 RuntimeConfig 的 app/bundle 版本、API/Master/AssetBundle origin/path，以及 Unity Application.version，不发送网络请求，不记录 URL query。未初始化的项只记 unavailable，纯观察模式可在场景加载时重试。设备缺少 `Application.Quit` ICall 时只报告退出请求不可用，不能当作进程已经停止；应通过 ADB 或模拟器手动停止。

本地模式在安装钩子后直接调用 GetApiUrl，比较实际 URL 和 prefix 进入计数。随后独立验证本地 HTTP 会话并使用设备 RuntimeConfig 登记版本，上报 `local_transport_ready`；该事件不表示原生钩子已经生效。若 getter 未经过 prefix 或未返回本地地址，另报 `native_hook_inactive`。钩子执行或本地 HTTP 验证失败时结束当前游戏进程，避免继续原平台登录；纯观察模式不执行这项阻断。

远跳板可能需要覆盖更多入口指令，短函数和 MuMu ARM 转译缓存仍可能导致崩溃；仅此导出存在不能证明兼容性。出现原生崩溃应恢复 `false` 并停止此方向。观察模式也只有在这个独立选项明确开启时才改变 Dobby 策略，默认观察配置不改变它。

### 编译命令

在 Windows PowerShell 中执行：

```powershell
pwsh -File .\android\build.ps1
```

需要 .NET SDK 10 与默认位置 `..\..\dotabyss_unpacked` 的 APK 解包文件；可用 `-GameRoot` 指定其他解包目录。构建结果在 `android/dist/DotAbyss.Local.dll`，不复制游戏或加载器 DLL，也不修改 APK。

## 安装与诊断

1. 启动项目本地 Python 服务，监听 `127.0.0.1:18081`。
2. 设备已启用 ADB 后执行 `adb reverse tcp:18081 tcp:18081`。
3. 将 `dist/DotAbyss.Local.dll` 放到设备上该游戏 MelonLoader 的 `Mods` 目录。目录位置由现有加载器决定，不能将其假定为一个固定 Android 路径。
4. 第一次运行生成 `UserData/DotAbyssLocal/config.json`；退出游戏，以 `config.example.json` 的内容启用，再重新运行。
5. 在加载器日志中确认全部 `Hook installed`，观察本地 `/diagnostics` 的分阶段事件与明确错误；不能仅凭钩子安装或登录函数完成判断私服已可游玩。

仅支持回环地址和 Android 标准模拟器主机地址 `10.0.2.2`，不接受任意公网域名或非回环 LAN 地址。推荐 ADB reverse。配置 URL 不能包含路径、凭据、query 或 fragment。

移除插件或设置 `Enabled=false` 后重启即可撤回插件行为。初始化钩子失败会撤销该插件已安装的钩子、请求退出应用并报告错误。服务不可达时不会调用原平台登录；修复连接后重新启动。

## 验证

```powershell
dotnet run --project .\android\tests\AdapterChecks.csproj -c Release
```

这些检查覆盖地址限制、日志脱敏与本地会话契约，不会运行游戏或证明原生钩子生效。

对已经启动的项目服务器验证真实 HTTP 契约，可执行以下命令（会创建独立的 `adapter-contract-check` 本地档案并登记测试版本）：

```powershell
dotnet run --project .\android\tests\AdapterChecks.csproj -c Release -- --server http://127.0.0.1:18081
```

若服务器正在与设备联调，可用 `--game-server` 替代 `--server`，只检验独立测试档案的会话与 `/login` 契约，不覆盖设备登记的版本值。
