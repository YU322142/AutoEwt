# AutoEwt Android GeckoView

这是 AutoEwt 的 Android 实验版，目标是在 APK 内打包浏览器内核，避免用户手动安装浏览器驱动。

## 当前状态

- 浏览器内核：GeckoView
- 页面通信：内置 WebExtension + native messaging
- UI：Compose + Material3
- 最低系统：Android 8.0 / API 26
- 构建产物：debug APK

> Android 6/7 不作为当前主线目标。若要支持，需要单独 legacy flavor，并承担旧浏览器内核带来的安全与兼容风险。

## 功能

- 打开课程列表 URL
- 保存账号、密码、课程列表 URL 等配置
- 模式选择：`刷课` / `做题`
- 自动填入登录信息
- 自动进入课程任务
- 实时处理视频检查点
- 视频进度显示
- 浏览器界面可显示/隐藏
- 日志可切换：完整 / 单行 / 隐藏
- Gecko 内容进程崩溃后恢复 session

## 项目结构

```text
android-geckoview/
  app/build.gradle.kts
  app/src/main/java/io/github/autoewt/gecko/
    MainActivity.java          GeckoView、session、WebExtension bridge
    AutoEwtComposeUi.kt        Compose/Material3 UI
  app/src/main/assets/autoewt/
    manifest.json              WebExtension manifest
    content-script.js          页面自动化脚本
  docs/compatibility.md        兼容性计划
```

## 本地构建

### 依赖

- JDK 17+
- Android SDK Platform 36
- Android SDK Build-Tools 36.0.0
- Gradle 9.4.1

### Windows PowerShell

```powershell
$env:JAVA_HOME='C:\Program Files\Eclipse Adoptium\jdk-17.0.18.8-hotspot'
$env:ANDROID_HOME="$env:USERPROFILE\AppData\Local\Android\Sdk"
$env:ANDROID_SDK_ROOT=$env:ANDROID_HOME

cd android-geckoview
gradle :app:assembleDebug
```

APK 输出：

```text
android-geckoview/app/build/outputs/apk/debug/app-debug.apk
```

## 安装测试

查看设备：

```powershell
adb devices
```

安装：

```powershell
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

启动：

```powershell
adb shell am start -n io.github.autoewt.gecko/.MainActivity
```

如果在仓库根目录执行安装：

```powershell
adb install -r android-geckoview/app/build/outputs/apk/debug/app-debug.apk
```

## 云端构建

GitHub Actions workflow：

```text
.github/workflows/android-geckoview.yml
```

触发方式：

- 推送修改到 `android-geckoview/**`
- 推送修改到 workflow 文件
- 在 GitHub Actions 页面手动运行 `workflow_dispatch`

构建完成后，在 Actions run 的 Artifacts 中下载：

```text
autoewt-geckoview-debug-apk
```

## 配置页

Android 版配置保存在应用私有数据中，不会读取桌面版的 `src/config.yml`。

主要字段：

- `账号`
- `密码`
- `课程列表 URL`
- `刷课 / 做题`
- `从第几天开始`
- `做题时选择正确答案`
- `report_id`
- `自动填入账号密码`
- `填入后自动登录`
- `桌面浏览器模式`

桌面浏览器模式默认开启，因为部分 EWT 页面在移动端 UA 下表现不同。

## 自动化边界

Android 版尽量复用 Python/Selenium 版的行为顺序：

1. 优先处理视频类课程入口。
2. 再处理 `去收听` / `去查看` 这类进入即完成的任务。
3. 视频播放页实时检测 `点击通过检查`、`跳过` 等检查点。
4. 视频被暂停后再处理 `我知道了`、`继续播放`、`确定` 等提示。
5. FM 任务页进入后即可退出。
6. 提示“错过了所有看课检测点，再认真观看一次吧！”的课程，会点击同一课程卡片内的 `已学完` 按钮重新刷课。

## 架构原则

UI 和浏览器自动化保持分离：

```text
Compose UI
  -> MainActivity controller
       -> GeckoView session
       -> WebExtension bridge
            -> content-script.js
```

Compose 只负责状态展示和按钮交互；浏览器生命周期、新窗口接管、原生点击和自动化消息都在 Activity/extension 层。

## 兼容性

- `minSdk = 26`
- `compileSdk = 36`
- `targetSdk = 35`
- GeckoView artifact：`geckoview-omni`
- GeckoView version：见 `gradle.properties`

`geckoview-omni` 方便测试多 ABI，但包体较大。正式发布建议使用 ABI split 或 AAB。

## 排错

### 页面能打开但无法登录

- 检查配置页账号、密码和课程列表 URL。
- 如果出现验证码或短信验证，需要手动完成。
- 可点 `填登录` 只填入账号密码。

### 页面布局异常

- 切换 `桌面浏览器模式` 后重启内核。
- Android 端默认使用桌面模式以接近电脑端行为。

### 日志太多

在浏览器页切换日志模式：

- `完整`
- `单行`
- `隐藏`

自动循环的页面探测日志默认不会刷屏；手动点击 `探测` 时才输出页面摘要。
