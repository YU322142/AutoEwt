# AutoEwt

> 本项目仅用于 Python、浏览器自动化与跨平台客户端技术研究。请不要将它用于违反学校纪律、平台规则或法律法规的行为。

AutoEwt 目前包含两条实现线：

- **桌面版**：基于 Python + Selenium，保留命令行入口，并提供 Windows GUI。
- **Android 实验版**：基于 GeckoView + WebExtension + Compose/Material3，目标是把浏览器内核一并打包，降低用户配置成本。

## 声明

- 本软件遵循 [GNU General Public License v3.0](LICENSE) 开源许可证。
- 本软件的目的仅在研究技术实现，不鼓励、不支持任何不当使用。
- 因使用本软件造成的任何后果，由使用者自行承担。
- 开始使用即视为你已阅读并同意本声明。

## 目录结构

```text
src/                    Python 桌面版核心逻辑、CLI 与 GUI
android-geckoview/       Android GeckoView 实验版
.github/workflows/       GitHub Actions 云端构建
```

## 桌面版快速开始

### 1. 准备环境

- Python 3.12
- Chrome 或 Edge 浏览器

推荐使用系统安装的 Edge 或 Chrome。默认留空 `driver_path` 和
`browser_binary` 时，Selenium Manager 会自动定位浏览器并准备匹配驱动；
只有使用便携浏览器或自动解析失败时，才需要手动配置二者。

常用下载地址：

- [ChromeDriver](https://googlechromelabs.github.io/chrome-for-testing/)
- [Microsoft Edge WebDriver](https://developer.microsoft.com/microsoft-edge/tools/webdriver/)
- [GeckoDriver](https://github.com/mozilla/geckodriver/releases)

### 2. 配置

复制默认配置：

```powershell
Copy-Item src/config.yml.default src/config.yml
```

然后编辑 `src/config.yml`：

```yaml
username: 用户名
password: 密码
list_url: 课程列表页面的链接
browser: Chrome
driver_path: ''
browser_binary: ''
options: --mute-audio --headless
mode: video
delay_multiplier: 1.0
parallelism: 2
task_urls: []
manual_handoff_enabled: true
system_notifications: true
choose_correctly: true
report_id:
```

关键字段：

- `mode: video`：刷课模式
- `mode: paper`：做题模式
- `driver_path`：可选，手动指定浏览器驱动路径；留空时使用 Selenium Manager
- `browser_binary`：可选，便携浏览器路径；留空时使用系统浏览器
- `options`：浏览器启动参数
- 视频任务始终从页面第一天开始逐日扫描，优先处理“已学完但漏检”的课程
- `parallelism`：桌面批量任务的并发账号数；同一账号的任务严格串行，不会同时启动两个电脑端浏览器
- `task_urls`：GUI 保存的批量任务 URL 列表
- `accounts`：GUI 管理的多账户列表；每个任务会绑定一个账户
- `manual_handoff_enabled`：无头任务遇到真人验证时，显示同一个后台浏览器窗口并保留原验证页面
- `system_notifications`：真人验证或人工窗口排队时发送 Windows 系统通知
- `report_id`：做题模式需要时填写

### 3. 命令行启动

```powershell
python -m pip install -r requirements.txt
python src/main.py
```

程序异常崩溃后会自动等待并重启；正常完成或用户停止时才退出。

## Windows GUI

GUI 与核心自动化逻辑分离，命令行入口仍然保留。

安装 GUI 依赖：

```powershell
python -m pip install -r requirements-gui.txt
```

首次使用或 OOBE 版本升级时，GUI 会启动五步设置向导：管理多个账户并选择默认账户、导入账号任务表格、配置浏览器与人工验证、设置运行模式/账号并发，最后确认后才原子写入配置。旧版 `task_urls`/详情 `list_url` 会迁移到默认账户；损坏的 YAML 会备份为 `config.yml.invalid.bak` 并进入恢复向导。后续可在“账户”页继续添加账号，或导入含 `账户名称 / 用户名 / 密码 / 启用 / 任务名称 / 任务URL` 列的 `.xlsx`、`.csv`、`.tsv` 表格。表格空白字段不会覆盖已有账户资料。

启动 QFluentWidgets GUI：

```powershell
python src/gui.py
```

无外部控制台窗口启动：

```powershell
pythonw src/gui.pyw
```

QFluentWidgets GUI 提供首次运行向导、多账户密码库、CSV/TSV/XLSX 表格导入、普通/暑假任务批量发现、任务多选、可调账号并发、逐任务进度和分层高级设置。同一账号的多个任务按列表顺序串行执行，不同账号才会并行。运行中心保留完整日志，并可按账号、任务和线程筛选。无头模式使用独立复选框控制，不需要手写 `--headless` 参数。桌面端不再嵌入 Qt WebEngine：未运行任务的“预览”交给系统浏览器；运行中的后台任务会直接显示它自己的 Selenium 窗口，不会关闭、刷新或重建当前页面，其他并发任务继续在后台运行。

验证码不会被自动破解或拖动。生产环境默认使用无头模式；识别到真人验证后会发送 Windows 系统通知，并且只显示对应任务的浏览器窗口。验证完成后同一会话会隐藏到后台，下一次需要人工时再恢复；用户主动点击“预览”时窗口会保持可见。这个功能要求程序运行在已经登录的交互式 Windows 桌面会话中，Windows 服务 Session 0 无法显示通知或浏览器窗口。

普通检查点仍由程序按严格文案和检查点容器自动点击。检查点类型会在同一账号的整个浏览器会话中记忆：如果上一次出现的是拼图验证，那么之后任意课程第一次遇到普通检查点时会额外发送一条系统通知，然后继续自动处理；该提醒不限于同一节课。

Windows GUI 的云端打包入口已显式设为 `src/gui.py`，而不是由打包器猜测；Nuitka 使用 `pyside6` 插件并关闭控制台窗口。命令行入口 `src/main.py` 保持独立。

## Android 实验版

Android 版本位于 [android-geckoview](android-geckoview/)。

特性：

- 内置 GeckoView 浏览器内核，无需用户安装 WebDriver。
- 使用 WebExtension/native messaging 与页面通信。
- UI 使用 Compose/Material3。
- 支持浏览器界面显示/隐藏、视频进度、日志完整/单行/隐藏。
- 最低兼容 Android 8.0 / API 26。

本地构建：

```powershell
cd android-geckoview
gradle :app:assembleDebug
```

APK 输出：

```text
android-geckoview/app/build/outputs/apk/debug/app-debug.apk
```

安装到已连接设备：

```powershell
adb install -r android-geckoview/app/build/outputs/apk/debug/app-debug.apk
```

更多 Android 说明见 [android-geckoview/README.md](android-geckoview/README.md)。

## 云端构建

Android APK 由 GitHub Actions 构建：

- Workflow: `.github/workflows/android-geckoview.yml`
- 触发方式：推送 `android-geckoview/**` 或手动 `workflow_dispatch`
- Artifact: `autoewt-geckoview-debug-apk`

推送分支后，在 GitHub 仓库的 **Actions** 页面进入 `Android GeckoView Prototype`，下载构建产物即可。

## 开发说明

- Python 核心逻辑位于 `src/auto_base.py`、`src/auto_video/`、`src/auto_paper/`。
- 桌面运行循环位于 `src/runner.py`，负责日志、异常恢复和进度回调。
- Android 自动化脚本位于 `android-geckoview/app/src/main/assets/autoewt/content-script.js`。
- Android UI 位于 `android-geckoview/app/src/main/java/io/github/autoewt/gecko/AutoEwtComposeUi.kt`。
- Android 浏览器生命周期与 GeckoView bridge 位于 `MainActivity.java`。

## 兼容性边界

- 桌面版继续依赖用户本机浏览器和 WebDriver。
- Android 主线最低 Android 8.0，因为当前 GeckoView 要求 API 26+。
- 如需 Android 6/7，需要单独 legacy flavor，并更换或锁定旧浏览器内核；这不作为当前主线目标。

## 常见问题

### 为什么 Android 版不用系统 WebView？

系统 WebView 版本由设备决定，不利于减少用户学习和排错成本。GeckoView 可以把浏览器内核随 APK/AAB 分发，行为更可控。

### 登录验证码怎么办？

桌面版和 Android 版都不会绕过验证码或短信验证。应用最多填入账号密码，验证码仍需要用户手动完成。

### GUI 崩溃会直接退出吗？

桌面运行循环会捕获异常并自动重启核心任务。GUI 只是查看和控制层，核心异常恢复逻辑在 `src/runner.py`。
