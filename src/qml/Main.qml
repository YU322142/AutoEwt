import QtQuick 2.15
import QtQuick.Controls.Basic 2.15
import QtQuick.Layouts 2.15
import RinUI

FluentWindow {
    id: window
    visible: true
    width: 1180
    height: 760
    minimumWidth: 960
    minimumHeight: 640
    title: "AutoEwt"
    appLayerEnabled: true
    defaultPage: "RunPage.qml"

    navigationItems: [
        {
            title: "运行",
            icon: "ic_fluent_play_20_regular",
            page: "RunPage.qml"
        },
        {
            title: "浏览器",
            icon: "ic_fluent_globe_20_regular",
            page: "BrowserPage.qml"
        },
        {
            title: "配置",
            icon: "ic_fluent_settings_20_regular",
            page: "ConfigPage.qml"
        }
    ]
}
