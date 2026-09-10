import QtQuick
import QtQuick.Controls
import QtQuick.Window

FocusScope {
    id: root
    width: 1600; height: 900
    property var s: backend.state
    property real u: Math.min(width / 1600, height / 900)
    property real textScale: s.settings.scale === "large" ? 1.16 : s.settings.scale === "small" ? 0.9 : 1
    property bool overlay: s.page === "quick" || s.page === "power"
    property var now: new Date()
    property string selectedApp: "kodi"
    property var sections: [
        {id: "devices", title: "Устройства"}, {id: "display", title: "Экран"},
        {id: "network", title: "Сеть"}, {id: "zapret", title: "Сетевые правила"}, {id: "system", title: "Система"}
    ]
    property var descriptions: {
        "kodi": "Твоя медиатека. На большом экране.", "youtube": "Любимые каналы. Новые открытия.",
        "moonlight": "Игры с твоего компьютера — на телевизоре.", "browser": "Сайты и поиск. На большом экране.",
        "settings": "Всё под твоим контролем."
    }
    property string lastInput: "Нажми кнопку на контроллере"
    property var rememberedRow: null
    property var pageFocus: ({})
    property var editorReturnFocus: null
    property var confirmReturnFocus: null
    focus: true

    function candidates(item, list) {
        if (!item.visible || !item.enabled) return
        if (item.activeFocusOnTab) list.push(item)
        for (let child of item.children) candidates(child, list)
    }
    function navigationScope() {
        if (s.countdown > 0) return displayPanel
        if (s.confirm.title) return confirmPanel
        if (s.editor.title) return editorPanel
        if (overlay) return quickPanel
        return body
    }
    function firstFocus() {
        let items = []; candidates(navigationScope(), items)
        if (!s.editor.title && !s.confirm.title && !s.countdown && items.indexOf(pageFocus[s.page]) >= 0) {
            pageFocus[s.page].forceActiveFocus(); return
        }
        if (navigationScope() === body) {
            if (s.page === "home") {
                let tile = items.find(item => item.appId === root.selectedApp)
                if (tile) { tile.forceActiveFocus(); return }
            } else if (s.page === "settings") {
                let section = items.find(item => item.objectName === "section_" + s.section)
                if (section) { section.forceActiveFocus(); return }
            }
        }
        if (items.length) items[0].forceActiveFocus()
    }
    function restoreFocus(item) {
        let items = []; candidates(navigationScope(), items)
        if (items.indexOf(item) >= 0) item.forceActiveFocus()
        else firstFocus()
    }
    function moveFocus(direction) {
        let items = []; candidates(navigationScope(), items)
        let current = root.Window.window.activeFocusItem
        if (!current || items.indexOf(current) < 0) { firstFocus(); return }
        if (current.tvListIndex !== undefined && (direction === "up" || direction === "down")) {
            const step = direction === "down" ? 1 : -1
            let i = current.tvListIndex + step
            while (i >= 0 && i < s.rows.length && !s.rows[i].enabled) i += step
            if (i >= 0 && i < s.rows.length) {
                settingsList.positionViewAtIndex(i, ListView.Contain)
                Qt.callLater(function() { const target = settingsList.itemAtIndex(i); if (target) target.forceActiveFocus() })
                return
            }
        }
        let p = current.mapToItem(root, current.width / 2, current.height / 2)
        let best = null, score = Infinity
        for (let item of items) {
            if (item === current) continue
            let q = item.mapToItem(root, item.width / 2, item.height / 2)
            let dx = q.x - p.x, dy = q.y - p.y
            let horizontal = direction === "left" || direction === "right"
            let forward = direction === "left" ? -dx : direction === "right" ? dx : direction === "up" ? -dy : dy
            if (forward <= 2) continue
            let sideways = Math.abs(horizontal ? dy : dx)
            let cost = forward + sideways * 4
            if (cost < score) { best = item; score = cost }
        }
        if (best) best.forceActiveFocus(Qt.TabFocusReason)
    }
    function input(action) {
        lastInput = ({up: "Вверх", down: "Вниз", left: "Влево", right: "Вправо", accept: "X · Выбрать", back: "O · Назад"})[action] || action
        if (["left", "right", "up", "down"].indexOf(action) >= 0) moveFocus(action)
        else if (action === "back") backend.action("back", "")
        else if (action === "accept") {
            let current = root.Window.window.activeFocusItem
            let items = []; candidates(navigationScope(), items)
            if (current && current.enabled && items.indexOf(current) >= 0 && current.clicked) current.clicked()
        }
    }
    Keys.priority: Keys.AfterItem
    Keys.onPressed: function(event) {
        let actions = {}; actions[Qt.Key_Left] = "left"; actions[Qt.Key_Right] = "right"
        actions[Qt.Key_Up] = "up"; actions[Qt.Key_Down] = "down"
        if (actions[event.key]) { moveFocus(actions[event.key]); event.accepted = true }
        else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) { root.input("accept"); event.accepted = true }
        else if (event.key === Qt.Key_Escape || event.key === Qt.Key_Back) { backend.action("back", ""); event.accepted = true }
        else if (event.key === Qt.Key_Home || event.key === Qt.Key_F1) { backend.action("home", ""); event.accepted = true }
        else if (event.key === Qt.Key_Menu || event.key === Qt.Key_F2) { backend.action("menu", ""); event.accepted = true }
        else if (event.key === Qt.Key_F3) { backend.action("power", ""); event.accepted = true }
    }
    Connections {
        target: backend
        function onNavigation(action) { root.input(action) }
        function onSurfaceChanging() {
            if (!root.s.editor.title && !root.s.confirm.title && !root.s.countdown)
                root.pageFocus[root.s.page] = root.Window.window.activeFocusItem
        }
        function onSurface(page) { Qt.callLater(root.firstFocus) }
        function onRowsChanging() {
            const item = root.Window.window.activeFocusItem
            root.rememberedRow = item && item.tvListIndex !== undefined ?
                {section: root.s.section, action: item.modelData.action, value: item.modelData.value} : null
        }
        function onRowsChanged() {
            const saved = root.rememberedRow
            if (!saved || saved.section !== root.s.section) return
            const index = root.s.rows.findIndex(row => row.enabled && row.action === saved.action && row.value === saved.value)
            if (index < 0) return
            Qt.callLater(function() {
                if (root.navigationScope() !== body) return
                settingsList.positionViewAtIndex(index, ListView.Contain)
                const item = settingsList.itemAtIndex(index)
                if (item) item.forceActiveFocus()
            })
        }
        function onNotice(title, message) {
            toast.title = title; toast.message = message; toast.visible = true; toastTimer.restart()
        }
    }
    Timer { interval: 1000; running: true; repeat: true; onTriggered: root.now = new Date() }
    Component.onCompleted: Qt.callLater(function() { kodiTile.forceActiveFocus() })

    Rectangle { anchors.fill: parent; color: root.overlay ? "#66000000" : "#000000" }

    Item {
        id: body
        anchors.fill: parent
        visible: !root.overlay
        Item {
            x: 84 * root.u; y: 60 * root.u
            width: parent.width - 168 * root.u; height: 62 * root.u
            Row {
                spacing: 12 * root.u
                anchors.verticalCenter: parent.verticalCenter
                Rectangle { width: 8 * root.u; height: 8 * root.u; radius: width / 2; color: "#ff1841"; anchors.verticalCenter: parent.verticalCenter }
                Text { text: "raspberry tv"; color: "white"; font.pixelSize: 24 * root.u; font.letterSpacing: 0.5 * root.u }
            }
            Row {
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                spacing: 25 * root.u
                Text {
                    text: root.now.toLocaleDateString(Qt.locale("ru_RU"), "d MMMM")
                    color: "#949494"; font.pixelSize: 21 * root.u
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    text: Qt.formatTime(root.now, "HH:mm")
                    color: "white"; font.pixelSize: 24 * root.u
                    anchors.verticalCenter: parent.verticalCenter
                }
                Icon { name: "wifi"; ink: root.s.wifi ? "white" : "#949494"; width: 31 * root.u; height: width; anchors.verticalCenter: parent.verticalCenter }
                Icon { name: "bluetooth"; ink: root.s.bluetooth ? "white" : "#949494"; width: 25 * root.u; height: width; anchors.verticalCenter: parent.verticalCenter }
                TvButton {
                    objectName: "headerPower"
                    unit: root.u; width: 53 * root.u; height: width; padding: 12 * root.u; horizontalPadding: 12 * root.u
                    Accessible.name: "Питание"
                    contentItem: Icon { name: "power"; ink: parent.activeFocus ? "black" : "white" }
                    onClicked: backend.action("power", "")
                }
            }
        }

        Item {
            id: homePage
            anchors.fill: parent
            visible: root.s.page === "home"
            Text {
                x: 90 * root.u; y: 215 * root.u
                text: "Твой большой экран."
                color: "white"; font.pixelSize: 58 * root.u; font.weight: Font.Normal
                font.letterSpacing: -1.5 * root.u
            }
            Text {
                x: 92 * root.u; y: 292 * root.u
                text: "Хороший вечер начинается с выбора."
                color: "#949494"; font.pixelSize: 24 * root.u
            }
            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                y: 393 * root.u
                spacing: 28 * root.u
                TvTile { id: kodiTile; objectName: "tileKodi"; appId: "kodi"; text: "Kodi"; unit: root.u; running: root.s.running.indexOf(appId) >= 0; onClicked: backend.action("launch", appId); onActiveFocusChanged: if (activeFocus) root.selectedApp = appId }
                TvTile { objectName: "tileYoutube"; appId: "youtube"; text: "YouTube"; unit: root.u; running: root.s.running.indexOf(appId) >= 0; onClicked: backend.action("launch", appId); onActiveFocusChanged: if (activeFocus) root.selectedApp = appId }
                TvTile { objectName: "tileMoonlight"; appId: "moonlight"; text: "Moonlight"; unit: root.u; running: root.s.running.indexOf(appId) >= 0; onClicked: backend.action("launch", appId); onActiveFocusChanged: if (activeFocus) root.selectedApp = appId }
                TvTile { objectName: "tileBrowser"; appId: "browser"; text: "Браузер"; unit: root.u; running: root.s.running.indexOf(appId) >= 0; onClicked: backend.action("launch", appId); onActiveFocusChanged: if (activeFocus) root.selectedApp = appId }
                TvTile { objectName: "tileSettings"; appId: "settings"; text: "Настройки"; unit: root.u; onClicked: backend.action("launch", appId); onActiveFocusChanged: if (activeFocus) root.selectedApp = appId }
            }
            Text {
                x: 93 * root.u; y: 693 * root.u
                text: root.descriptions[root.selectedApp]
                color: "#949494"; font.pixelSize: 23 * root.u
            }
            TvButton {
                visible: !!root.s.activeApp
                anchors.right: parent.right; anchors.rightMargin: 90 * root.u; y: 685 * root.u
                unit: root.u; text: "Продолжить просмотр"
                onClicked: backend.action("resume", "")
            }
        }

        Item {
            id: settingsPage
            visible: root.s.page === "settings"
            x: 85 * root.u; y: 170 * root.u
            width: parent.width - 170 * root.u; height: parent.height - 265 * root.u
            Text { text: "Настройки"; color: "white"; font.pixelSize: 45 * root.u }
            Column {
                y: 94 * root.u; spacing: 15 * root.u
                Repeater {
                    model: root.sections
                    TvButton {
                        required property var modelData
                        unit: root.u; width: 286 * root.u; height: 66 * root.u
                        text: modelData.title; accent: root.s.section === modelData.id
                        objectName: "section_" + modelData.id
                        onClicked: backend.action("section", modelData.id)
                    }
                }
                TvButton { unit: root.u; width: 286 * root.u; text: "На главный экран"; onClicked: backend.action("home", "") }
            }
            ListView {
                id: settingsList
                objectName: "settingsList"
                x: 350 * root.u; y: 85 * root.u
                width: parent.width - x; height: parent.height - y
                clip: true
                spacing: 10 * root.u
                model: root.s.rows
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                delegate: AbstractButton {
                    id: settingRow
                    required property var modelData
                    required property int index
                    property int tvListIndex: index
                    width: settingsList.width - 14 * root.u
                    height: 103 * root.u * root.textScale
                    activeFocusOnTab: modelData.enabled
                    enabled: modelData.enabled
                    hoverEnabled: true
                    onHoveredChanged: if (hovered) forceActiveFocus(Qt.MouseFocusReason)
                    onActiveFocusChanged: if (activeFocus) settingsList.positionViewAtIndex(index, ListView.Contain)
                    background: Rectangle {
                        radius: 18 * root.u
                        color: settingRow.activeFocus ? "#212121" : "#101010"
                        border.width: settingRow.activeFocus ? 2 * root.u : 0
                        border.color: "white"
                    }
                    contentItem: Item {
                        Text {
                            x: 25 * root.u; y: 18 * root.u
                            width: parent.width - 75 * root.u
                            text: settingRow.modelData.title
                            color: "white"; font.pixelSize: 25 * root.u * root.textScale
                            elide: Text.ElideRight
                        }
                        Text {
                            x: 25 * root.u; y: 54 * root.u * root.textScale
                            width: parent.width - 75 * root.u
                            text: settingRow.modelData.subtitle
                            color: "#949494"; font.pixelSize: 20 * root.u * root.textScale
                            elide: Text.ElideRight
                        }
                        Text { visible: settingRow.enabled; text: "›"; color: "#949494"; font.pixelSize: 32 * root.u; anchors.right: parent.right; anchors.rightMargin: 25 * root.u; anchors.verticalCenter: parent.verticalCenter }
                    }
                    onClicked: backend.action(modelData.action, modelData.value)
                    Keys.onDownPressed: {
                        let i = index + 1
                        while (i < root.s.rows.length && !root.s.rows[i].enabled) i++
                        if (i < root.s.rows.length) {
                            settingsList.positionViewAtIndex(i, ListView.Contain)
                            Qt.callLater(function() { const item = settingsList.itemAtIndex(i); if (item) item.forceActiveFocus() })
                        }
                    }
                    Keys.onUpPressed: {
                        let i = index - 1
                        while (i >= 0 && !root.s.rows[i].enabled) i--
                        if (i >= 0) {
                            settingsList.positionViewAtIndex(i, ListView.Contain)
                            Qt.callLater(function() { const item = settingsList.itemAtIndex(i); if (item) item.forceActiveFocus() })
                        }
                    }
                }
            }
        }

        Column {
            visible: root.s.page === "controller"
            anchors.centerIn: parent; spacing: 30 * root.u
            Text { text: "Проверка контроллера"; color: "white"; font.pixelSize: 45 * root.u }
            Text { text: root.s.controller; color: "#949494"; font.pixelSize: 25 * root.u }
            Text { text: root.lastInput; color: "white"; font.pixelSize: 36 * root.u }
            Text { text: "PS — главный экран · Options — быстрое меню\nУдержание PS — питание\nТачпад: поддержка зависит от драйвера на Pi"; color: "#949494"; font.pixelSize: 23 * root.u; lineHeight: 1.5 }
            TvButton { unit: root.u; text: "Готово"; onClicked: backend.action("section", "devices") }
        }

        Row {
            x: 90 * root.u; anchors.bottom: parent.bottom; anchors.bottomMargin: 42 * root.u
            spacing: 31 * root.u
            Repeater {
                model: [{key: "X", label: "Выбрать"}, {key: "O", label: "Назад"}, {key: "PS", label: "Домой"}, {key: "≡", label: "Быстрое меню"}]
                Row {
                    required property var modelData
                    spacing: 10 * root.u
                    Text { text: modelData.key; color: "white"; font.pixelSize: 21 * root.u }
                    Text { text: modelData.label; color: "#949494"; font.pixelSize: 21 * root.u }
                }
            }
        }
        Text {
            visible: root.s.preview
            anchors.right: parent.right; anchors.rightMargin: 90 * root.u
            anchors.bottom: parent.bottom; anchors.bottomMargin: 43 * root.u
            text: "ПРЕДПРОСМОТР"
            color: "#949494"; font.pixelSize: 14 * root.u; font.letterSpacing: 2 * root.u
        }
    }

    Rectangle {
        id: quickPanel
        visible: root.overlay
        width: 470 * root.u; height: quickColumn.height + 90 * root.u
        anchors.right: parent.right; anchors.rightMargin: 45 * root.u; anchors.verticalCenter: parent.verticalCenter
        radius: 28 * root.u; color: "#101010"; border.color: "#444444"
        Column {
            id: quickColumn
            x: 38 * root.u; y: 40 * root.u; width: parent.width - 76 * root.u
            spacing: 16 * root.u
            Text { text: root.s.page === "power" ? "Питание" : "Быстрые действия"; color: "white"; font.pixelSize: 31 * root.u; bottomPadding: 18 * root.u }
            Repeater {
                model: root.s.page === "power" ? [{title: "Выключить", action: "poweroff"}, {title: "Перезагрузить", action: "reboot"},
                    {title: "Спящий режим · недоступен", action: "suspend"}, {title: "Отмена", action: "back"}] :
                    [{title: "На главный экран", action: "home"}].concat(["browser", "youtube", "kodi"].indexOf(root.s.activeApp) >= 0 ?
                    [{title: "Клавиатура", action: "keyboard"}] : []).concat(root.s.activeApp === "browser" ?
                    [{title: "Открыть адрес", action: "browser_address"}, {title: "Назад по истории", action: "browser_back"},
                     {title: "Вперёд по истории", action: "browser_forward"}, {title: "Обновить страницу", action: "browser_reload"}] : []).concat(
                    [{title: "Свернуть", action: "minimize"}, {title: "Закрыть", action: "close"}, {title: "Настройки", action: "section"}, {title: "Отмена", action: "back"}])
                TvButton {
                    required property var modelData
                    objectName: "quick_" + modelData.action
                    unit: root.u; width: quickColumn.width; height: 54 * root.u
                    text: modelData.title
                    enabled: modelData.action !== "suspend" && (modelData.action !== "keyboard" || root.s.keyboardAvailable) &&
                             (["close", "minimize"].indexOf(modelData.action) < 0 || !!root.s.activeApp)
                    onClicked: backend.action(modelData.action, modelData.action === "section" ? "devices" : "")
                }
            }
            Text {
                visible: root.s.page === "power"
                width: parent.width; wrapMode: Text.WordWrap
                text: "Сон и пробуждение на этой приставке ещё не проверены."
                color: "#949494"; font.pixelSize: 19 * root.u
            }
        }
    }

    Rectangle {
        anchors.fill: parent; color: "#cc000000"
        visible: !!root.s.editor.title
        onVisibleChanged: {
            if (visible) {
                root.editorReturnFocus = root.Window.window.activeFocusItem
                editorText.text = root.s.editor.text || ""
                keyboard.reveal = true; keyboard.shifted = false; keyboard.symbols = false
                Qt.callLater(function() { if (root.s.editor.secret || root.s.editor.external) passwordText.forceActiveFocus(); else editorText.forceActiveFocus() })
            } else {
                editorText.text = ""; passwordText.text = ""
                Qt.callLater(function() { root.restoreFocus(root.editorReturnFocus) })
            }
        }
        Rectangle {
            id: editorPanel
            width: 1170 * root.u; height: 830 * root.u
            anchors.centerIn: parent; radius: 26 * root.u; color: "#101010"; border.color: "#494949"
            Column {
                x: 38 * root.u; y: 28 * root.u; width: parent.width - 76 * root.u
                spacing: 12 * root.u
                Text { text: root.s.editor.title || ""; color: "white"; font.pixelSize: 34 * root.u }
                Text { text: root.s.editor.subtitle || ""; color: "#949494"; font.pixelSize: 22 * root.u }
                ScrollView {
                    visible: !root.s.editor.secret && !root.s.editor.external
                    width: parent.width; height: 112 * root.u
                    TextArea {
                        id: editorText
                        objectName: "editorText"
                        activeFocusOnTab: true
                        wrapMode: TextEdit.Wrap
                        color: "white"; font.pixelSize: 26 * root.u
                        selectByMouse: true
                        Keys.onReturnPressed: function(event) {
                            if (root.s.editor.multiline) event.accepted = false
                            else backend.action("editor_save", text)
                        }
                        // Passwords are entered in the dedicated single-line field below.
                        visible: !root.s.editor.secret && !root.s.editor.external
                        background: Rectangle { color: "#181818"; border.color: editorText.activeFocus ? "#ff1841" : "#494949"; radius: 12 * root.u }
                    }
                }
                TextField {
                    id: passwordText
                    objectName: "passwordText"
                    visible: !!root.s.editor.secret || !!root.s.editor.external
                    activeFocusOnTab: true
                    width: parent.width; height: 112 * root.u
                    maximumLength: root.s.editor.external ? 512 : 32767
                    echoMode: root.s.editor.external && keyboard.reveal ? TextInput.Normal : TextInput.Password
                    inputMethodHints: Qt.ImhNoPredictiveText | Qt.ImhSensitiveData
                    Keys.onReturnPressed: backend.action("editor_save", text)
                    color: "white"; font.pixelSize: 26 * root.u
                    background: Rectangle { color: "#181818"; border.color: passwordText.activeFocus ? "#ff1841" : "#494949" }
                    onVisibleChanged: if (visible) { text = ""; forceActiveFocus() }
                }
                Item {
                    id: keyboard
                    width: parent.width; height: 295 * root.u
                    property bool russian: false
                    property bool shifted: false
                    property bool reveal: true
                    property bool symbols: false
                    function field() { return root.s.editor.secret || root.s.editor.external ? passwordText : editorText }
                    property string letters: symbols ? "0123456789.,:;!?@#$%&*+-_=()[]{}<>/\\|\"'`~" :
                        russian ? "йцукенгшщзхъфывапролджэячсмитьбюё.,-/@:_0123456789" : "qwertyuiopasdfghjklzxcvbnm0123456789.,-/@:_?=&%+#!"
                    Grid {
                        columns: 12; spacing: 7 * root.u
                        Repeater {
                            model: keyboard.letters.split("")
                            TvButton {
                                required property string modelData
                                unit: root.u; width: 83 * root.u; height: 51 * root.u; padding: 0
                                text: keyboard.shifted ? modelData.toUpperCase() : modelData
                                onClicked: {
                                    let field = keyboard.field()
                                    if (field.selectionStart !== field.selectionEnd) field.remove(field.selectionStart, field.selectionEnd)
                                    field.insert(field.cursorPosition, text)
                                }
                            }
                        }
                    }
                }
                Row {
                    spacing: 12 * root.u
                    TvButton { unit: root.u; width: 136 * root.u; text: keyboard.russian ? "ABC" : "АБВ"; onClicked: keyboard.russian = !keyboard.russian }
                    TvButton { unit: root.u; width: 90 * root.u; text: "Aa"; onClicked: keyboard.shifted = !keyboard.shifted }
                    TvButton { unit: root.u; width: 110 * root.u; text: keyboard.symbols ? "Буквы" : "123 #"; onClicked: keyboard.symbols = !keyboard.symbols }
                    TvButton { unit: root.u; width: 158 * root.u; text: "Пробел"; onClicked: { let f = keyboard.field(); f.insert(f.cursorPosition, " ") } }
                    TvButton { unit: root.u; width: 158 * root.u; text: "Стереть"; onClicked: { let f = keyboard.field(); if (f.selectionStart !== f.selectionEnd) f.remove(f.selectionStart, f.selectionEnd); else if (f.cursorPosition) f.remove(f.cursorPosition - 1, f.cursorPosition) } }
                    TvButton { unit: root.u; width: 85 * root.u; text: "←"; onClicked: { let f = keyboard.field(); f.cursorPosition = Math.max(0, f.cursorPosition - 1) } }
                    TvButton { unit: root.u; width: 85 * root.u; text: "→"; onClicked: { let f = keyboard.field(); f.cursorPosition = Math.min(f.length, f.cursorPosition + 1) } }
                    TvButton { unit: root.u; width: 186 * root.u; visible: !!root.s.editor.external; text: keyboard.reveal ? "Скрыть текст" : "Показать"; onClicked: keyboard.reveal = !keyboard.reveal }
                }
                Row {
                    spacing: 12 * root.u
                    TvButton { objectName: "editorSubmit"; unit: root.u; text: root.s.editor.submit || "Сохранить"; accent: true; onClicked: backend.action("editor_save", keyboard.field().text) }
                    TvButton { objectName: "editorSendEnter"; unit: root.u; visible: !!root.s.editor.external; text: "Вставить и Enter"; onClicked: backend.action("editor_send_enter", keyboard.field().text) }
                    TvButton { unit: root.u; text: "Очистить"; onClicked: keyboard.field().text = "" }
                    TvButton { unit: root.u; text: "Отмена"; onClicked: backend.action("back", "") }
                }
            }
        }
    }

    Rectangle {
        visible: !!root.s.confirm.title
        anchors.fill: parent; color: "#cc000000"
        onVisibleChanged: {
            if (visible) { root.confirmReturnFocus = root.Window.window.activeFocusItem; Qt.callLater(root.firstFocus) }
            else Qt.callLater(function() { root.restoreFocus(root.confirmReturnFocus) })
        }
        Rectangle {
            id: confirmPanel
            anchors.centerIn: parent; width: 920 * root.u; height: 320 * root.u
            radius: 26 * root.u; color: "#101010"; border.color: "#494949"
            Column {
                x: 40 * root.u; y: 35 * root.u; spacing: 24 * root.u
                Text { text: root.s.confirm.title || ""; color: "white"; font.pixelSize: 34 * root.u }
                Text { text: root.s.confirm.body || ""; color: "#949494"; font.pixelSize: 23 * root.u; width: 840 * root.u; wrapMode: Text.WordWrap }
                Row {
                    spacing: 15 * root.u
                    TvButton { unit: root.u; text: "Отмена"; onClicked: backend.action("back", "") }
                    TvButton { unit: root.u; text: "Подтвердить"; accent: true; onClicked: backend.action("confirmed", "") }
                }
            }
        }
    }

    Rectangle {
        id: displayPanel
        visible: root.s.countdown > 0
        width: 880 * root.u; height: 225 * root.u
        anchors.centerIn: parent; color: "#101010"; radius: 26 * root.u; border.color: "white"
        onVisibleChanged: if (visible) Qt.callLater(root.firstFocus)
        Column {
            x: 36 * root.u; y: 28 * root.u; spacing: 26 * root.u
            Text { text: "Изображение видно? Откат через " + root.s.countdown + " с"; color: "white"; font.pixelSize: 30 * root.u }
            Row {
                spacing: 15 * root.u
                TvButton { unit: root.u; text: "Вернуть прежний режим"; onClicked: backend.action("display_rollback", "") }
                TvButton { unit: root.u; text: "Оставить"; accent: true; onClicked: backend.action("display_keep", "") }
            }
        }
    }

    Rectangle {
        id: toast
        visible: false
        property string title: ""
        property string message: ""
        width: Math.min(root.width - 100 * root.u, 900 * root.u); height: 106 * root.u
        anchors.horizontalCenter: parent.horizontalCenter; y: 32 * root.u
        radius: 23 * root.u; color: "#181818"; border.color: "#494949"
        Rectangle { x: 22 * root.u; y: 27 * root.u; width: 5 * root.u; height: 50 * root.u; radius: 2 * root.u; color: "#ff1841" }
        Column {
            x: 47 * root.u; y: 18 * root.u; width: parent.width - 100 * root.u; spacing: 9 * root.u
            Text { text: toast.title; color: "white"; font.pixelSize: 23 * root.u }
            Text { text: toast.message; color: "#949494"; font.pixelSize: 20 * root.u; width: parent.width; elide: Text.ElideRight }
        }
        MouseArea { anchors.fill: parent; onClicked: toast.visible = false }
    }
    Timer { id: toastTimer; interval: 10000; onTriggered: toast.visible = false }
    Rectangle {
        visible: root.s.busy
        width: 230 * root.u; height: 52 * root.u; radius: height / 2
        color: "#212121"; anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom; anchors.bottomMargin: 30 * root.u
        Text { anchors.centerIn: parent; text: "Секунду…"; color: "white"; font.pixelSize: 22 * root.u }
    }
}
