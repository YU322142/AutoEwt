import QtQuick 2.15
import QtQuick.Controls.Basic 2.15 as QQC
import QtQuick.Layouts 2.15
import RinUI

FluentPage {
    id: page
    title: "配置"
    wrapperWidth: 1080
    contentSpacing: 12

    function loadValues() {
        usernameField.text = Backend.config.username || ""
        passwordField.text = Backend.config.password || ""
        urlField.text = Backend.config.list_url || ""
        modeBox.currentIndex = Backend.config.mode === "paper" ? 1 : 0
        browserBox.currentIndex = browserIndex(Backend.config.browser || "Chrome")
        optionsField.text = Backend.config.options || ""
        daySpin.value = Number(Backend.config.day_to_start_on || 1)
        delayField.text = String(Backend.config.delay_multiplier || 1.0)
        chooseCorrectlyBox.checked = Backend.config.choose_correctly === undefined ? true : Backend.config.choose_correctly
        reportIdField.text = Backend.config.report_id || ""
        driverPathField.text = Backend.config.driver_path || ""
        browserBinaryField.text = Backend.config.browser_binary || ""
    }

    function browserIndex(value) {
        if (value === "Edge") return 1
        if (value === "Firefox") return 2
        return 0
    }

    function saveValues() {
        Backend.saveConfig({
            "username": usernameField.text,
            "password": passwordField.text,
            "list_url": urlField.text,
            "mode": modeBox.currentIndex === 1 ? "paper" : "video",
            "browser": browserBox.currentText,
            "options": optionsField.text,
            "day_to_start_on": daySpin.value,
            "delay_multiplier": Number(delayField.text),
            "choose_correctly": chooseCorrectlyBox.checked,
            "report_id": reportIdField.text,
            "driver_path": driverPathField.text,
            "browser_binary": browserBinaryField.text
        })
    }

    Component.onCompleted: loadValues()

    Connections {
        target: Backend
        function onConfigChanged() {
            loadValues()
        }
    }

    SettingCard {
        Layout.fillWidth: true
        title: "账号"
        description: "用于登录课程列表页面"
        icon.name: "ic_fluent_person_20_regular"

        TextField {
            id: usernameField
            Layout.preferredWidth: 520
            placeholderText: "用户名"
        }
    }

    SettingCard {
        Layout.fillWidth: true
        title: "密码"
        description: "保存到本地 config.yml"
        icon.name: "ic_fluent_password_20_regular"

        TextField {
            id: passwordField
            Layout.preferredWidth: 520
            placeholderText: "密码"
            echoMode: QQC.TextInput.Password
        }
    }

    SettingCard {
        Layout.fillWidth: true
        title: "课程列表 URL"
        description: "README 中的 list_url"
        icon.name: "ic_fluent_link_20_regular"

        TextField {
            id: urlField
            Layout.preferredWidth: 640
            placeholderText: "https://..."
        }
    }

    SettingCard {
        Layout.fillWidth: true
        title: "模式"
        description: "刷课对应 video，做题对应 paper"
        icon.name: "ic_fluent_target_arrow_20_regular"

        ComboBox {
            id: modeBox
            Layout.preferredWidth: 240
            model: ["刷课", "做题"]
        }
    }

    SettingCard {
        Layout.fillWidth: true
        title: "从第几天开始"
        description: "跳过前面已处理的任务"
        icon.name: "ic_fluent_calendar_20_regular"

        SpinBox {
            id: daySpin
            Layout.preferredWidth: 180
            from: 1
            to: 999
            value: 1
        }
    }

    SettingCard {
        Layout.fillWidth: true
        title: "延迟倍率"
        description: "页面慢时可适当调大"
        icon.name: "ic_fluent_timer_20_regular"

        TextField {
            id: delayField
            Layout.preferredWidth: 180
            placeholderText: "1.0"
        }
    }

    SettingCard {
        Layout.fillWidth: true
        title: "做题选项"
        description: "开启后会按 report_id 获取答案"
        icon.name: "ic_fluent_checkbox_checked_20_regular"

        CheckBox {
            id: chooseCorrectlyBox
            text: "选择正确答案"
        }
    }

    SettingCard {
        Layout.fillWidth: true
        title: "report_id"
        description: "做题模式需要"
        icon.name: "ic_fluent_document_20_regular"

        TextField {
            id: reportIdField
            Layout.preferredWidth: 520
            placeholderText: "reportId"
        }
    }

    SettingCard {
        Layout.fillWidth: true
        title: "浏览器"
        description: "推荐 Chrome 或 Edge"
        icon.name: "ic_fluent_window_20_regular"

        ComboBox {
            id: browserBox
            Layout.preferredWidth: 240
            model: ["Chrome", "Edge", "Firefox"]
        }
    }

    SettingCard {
        Layout.fillWidth: true
        title: "浏览器参数"
        description: "例如 --mute-audio"
        icon.name: "ic_fluent_code_20_regular"

        TextField {
            id: optionsField
            Layout.preferredWidth: 520
            placeholderText: "--mute-audio"
        }
    }

    SettingCard {
        Layout.fillWidth: true
        title: "驱动路径"
        description: "chromedriver / msedgedriver / geckodriver"
        icon.name: "ic_fluent_usb_plug_20_regular"

        TextField {
            id: driverPathField
            Layout.preferredWidth: 520
            placeholderText: ".\\chromedriver.exe"
        }

        Button {
            text: "浏览"
            icon.name: "ic_fluent_folder_20_regular"
            onClicked: {
                const path = Backend.selectFile(driverPathField.text, "WebDriver (*.exe);;All files (*.*)")
                if (path.length > 0) driverPathField.text = path
            }
        }
    }

    SettingCard {
        Layout.fillWidth: true
        title: "浏览器路径"
        description: "便携 Chrome 可填写 chrome.exe"
        icon.name: "ic_fluent_app_folder_20_regular"

        TextField {
            id: browserBinaryField
            Layout.preferredWidth: 520
            placeholderText: ".\\chrome-win64\\chrome.exe"
        }

        Button {
            text: "浏览"
            icon.name: "ic_fluent_folder_20_regular"
            onClicked: {
                const path = Backend.selectFile(browserBinaryField.text, "Browser (*.exe);;All files (*.*)")
                if (path.length > 0) browserBinaryField.text = path
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 10

        Button {
            text: "保存"
            highlighted: true
            icon.name: "ic_fluent_save_20_regular"
            onClicked: saveValues()
        }

        Button {
            text: "重载"
            icon.name: "ic_fluent_arrow_sync_20_regular"
            onClicked: Backend.loadConfig()
        }

        Item { Layout.fillWidth: true }
    }
}
