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
- `uv`
- 浏览器与对应版本的 WebDriver

推荐使用 Edge 或 Chrome。浏览器驱动需要与你本机浏览器版本匹配。

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
driver_path: .\chromedriver.exe
browser_binary: .\chrome-win64\chrome.exe
options: --mute-audio --headless
mode: video
delay_multiplier: 1.0
day_to_start_on: 1
choose_correctly: true
report_id:
```

关键字段：

- `mode: video`：刷课模式
- `mode: paper`：做题模式
- `driver_path`：浏览器驱动路径
- `browser_binary`：可选，便携浏览器路径
- `options`：浏览器启动参数
- `day_to_start_on`：从第几天开始扫描
- `report_id`：做题模式需要时填写

### 3. 命令行启动

```powershell
uv sync
uv run python src/main.py
```

程序异常崩溃后会自动等待并重启；正常完成或用户停止时才退出。

## Windows GUI

GUI 与核心自动化逻辑分离，命令行入口仍然保留。

安装 GUI 依赖：

```powershell
uv sync --extra gui
```

启动 QFluentWidgets GUI：

```powershell
uv run python src/gui.py
```

无外部控制台窗口启动：

```powershell
uv run python src/gui.pyw
```

RinUI/QML 实验入口：

```powershell
uv run python src/rin_gui.py
```

GUI 会提供配置页、运行日志、进度显示与停止控制。核心运行仍由 `src/runner.py` 管理，因此 GUI 与无 GUI 模式可以继续拆分维护。

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
