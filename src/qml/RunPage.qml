import QtQuick 2.15
import QtQuick.Controls.Basic 2.15 as QQC
import QtQuick.Layouts 2.15
import RinUI

FluentPage {
    id: page
    title: "运行"
    wrapperWidth: 1080
    contentSpacing: 16

    Frame {
        Layout.fillWidth: true
        hoverable: false
        implicitHeight: 170

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 18
            spacing: 14

            RowLayout {
                Layout.fillWidth: true

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 4

                    Text {
                        text: "AutoEwt"
                        typography: Typography.Subtitle
                    }

                    Text {
                        text: Backend.status
                        typography: Typography.Body
                        color: Theme.currentTheme.colors.textSecondaryColor
                    }
                }

                Button {
                    text: "启动"
                    highlighted: true
                    enabled: !Backend.running
                    icon.name: "ic_fluent_play_20_regular"
                    onClicked: Backend.startRunner()
                }

                Button {
                    text: "停止"
                    enabled: Backend.running
                    icon.name: "ic_fluent_dismiss_20_regular"
                    onClicked: Backend.stopRunner()
                }
            }

            RowLayout {
                Layout.fillWidth: true

                Text {
                    text: Backend.progressTitle
                    typography: Typography.BodyStrong
                }

                Item { Layout.fillWidth: true }

                Text {
                    text: Backend.progressText
                    typography: Typography.Caption
                    color: Theme.currentTheme.colors.textSecondaryColor
                }
            }

            ProgressBar {
                Layout.fillWidth: true
                from: 0
                to: 1
                value: Backend.progressValue
                indeterminate: Backend.progressIndeterminate
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 16

        Frame {
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            Layout.preferredHeight: 132
            hoverable: false

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 18
                spacing: 8

                Text {
                    text: "当前任务"
                    typography: Typography.BodyStrong
                }

                Text {
                    text: Backend.config.mode === "paper" ? "做题" : "刷课"
                    typography: Typography.Title
                }

                Text {
                    text: "从第 " + Backend.config.day_to_start_on + " 天开始"
                    typography: Typography.Caption
                    color: Theme.currentTheme.colors.textSecondaryColor
                }
            }
        }

        Frame {
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            Layout.preferredHeight: 132
            hoverable: false

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 18
                spacing: 8

                Text {
                    text: "浏览器"
                    typography: Typography.BodyStrong
                }

                Text {
                    text: Backend.config.browser || "Chrome"
                    typography: Typography.Title
                }

                Text {
                    text: Backend.config.options || "无额外参数"
                    typography: Typography.Caption
                    color: Theme.currentTheme.colors.textSecondaryColor
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }
        }

        Frame {
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            Layout.preferredHeight: 132
            hoverable: false

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 18
                spacing: 8

                Text {
                    text: "课程入口"
                    typography: Typography.BodyStrong
                }

                Text {
                    text: Backend.config.list_url ? "已配置" : "未配置"
                    typography: Typography.Title
                }

                Text {
                    text: Backend.config.list_url || "请先在配置页填写课程列表 URL"
                    typography: Typography.Caption
                    color: Theme.currentTheme.colors.textSecondaryColor
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }
        }
    }

    Frame {
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.minimumHeight: 360
        hoverable: false

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 18
            spacing: 10

            RowLayout {
                Layout.fillWidth: true

                Text {
                    text: "控制台"
                    typography: Typography.BodyStrong
                }

                Item { Layout.fillWidth: true }

                Button {
                    text: "清空"
                    icon.name: "ic_fluent_broom_20_regular"
                    onClicked: Backend.clearLogs()
                }
            }

            ScrollableTextArea {
                Layout.fillWidth: true
                Layout.fillHeight: true
                text: Backend.logText
                readOnly: true
                wrapMode: QQC.TextArea.NoWrap
                placeholderText: "日志会显示在这里"
            }
        }
    }
}
