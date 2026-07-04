import QtQuick 2.15
import QtQuick.Controls.Basic 2.15 as QQC
import QtQuick.Layouts 2.15
import QtWebEngine
import RinUI

FluentPage {
    id: page
    title: "浏览器"
    wrapperWidth: 1080
    contentSpacing: 16

    Frame {
        Layout.fillWidth: true
        hoverable: false
        implicitHeight: 74

        RowLayout {
            anchors.fill: parent
            anchors.margins: 14
            spacing: 10

            TextField {
                id: addressField
                Layout.fillWidth: true
                text: Backend.browserUrl
                placeholderText: "about:blank"
                onAccepted: webView.url = text
            }

            Button {
                text: "打开"
                highlighted: true
                icon.name: "ic_fluent_arrow_right_20_regular"
                onClicked: webView.url = addressField.text
            }

            Button {
                text: "刷新"
                icon.name: "ic_fluent_arrow_clockwise_20_regular"
                onClicked: webView.reload()
            }

            Button {
                text: "停止"
                icon.name: "ic_fluent_dismiss_20_regular"
                onClicked: webView.stop()
            }
        }
    }

    Frame {
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.minimumHeight: 520
        hoverable: false
        clip: true

        WebEngineView {
            id: webView
            anchors.fill: parent
            anchors.margins: 1
            url: Backend.browserUrl

            onUrlChanged: addressField.text = url

            Connections {
                target: Backend
                function onBrowserUrlChanged() {
                    addressField.text = Backend.browserUrl
                    webView.url = Backend.browserUrl
                }
            }
        }
    }
}
