import QtQuick

Canvas {
    id: icon
    property string name: "settings"
    property color ink: "white"
    onNameChanged: requestPaint()
    onInkChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    onPaint: {
        const c = getContext("2d")
        c.reset()
        c.scale(width / 100, height / 100)
        c.strokeStyle = ink
        c.fillStyle = ink
        c.lineWidth = 5
        c.lineCap = "round"
        c.lineJoin = "round"
        function line(points, close, fill) {
            c.beginPath(); c.moveTo(points[0][0], points[0][1])
            for (let i = 1; i < points.length; i++) c.lineTo(points[i][0], points[i][1])
            if (close) c.closePath()
            if (fill) c.fill(); else c.stroke()
        }
        if (name === "youtube") {
            c.beginPath(); c.roundedRect(6, 21, 88, 58, 17, 17); c.fill()
            c.fillStyle = "#111111"; line([[43, 35], [67, 50], [43, 65]], true, true)
        } else if (name === "kodi") {
            line([[50, 4], [70, 24], [50, 44], [30, 24]], true, true)
            line([[26, 28], [46, 48], [26, 68], [6, 48]], true, true)
            line([[50, 52], [70, 72], [50, 92], [30, 72]], true, true)
            line([[74, 28], [94, 48], [74, 68], [54, 48]], true, true)
        } else if (name === "moonlight") {
            c.beginPath(); c.arc(49, 50, 39, 0, Math.PI * 2); c.fill()
            c.globalCompositeOperation = "destination-out"
            c.beginPath(); c.arc(68, 33, 36, 0, Math.PI * 2); c.fill()
            c.globalCompositeOperation = "source-over"
        } else if (name === "wifi") {
            for (let r of [34, 23, 12]) {
                c.beginPath(); c.arc(50, 72, r, Math.PI * 1.23, Math.PI * 1.77); c.stroke()
            }
            c.beginPath(); c.arc(50, 75, 3, 0, Math.PI * 2); c.fill()
        } else if (name === "bluetooth") {
            line([[48, 10], [74, 31], [29, 69], [48, 86], [48, 10]], false, false)
            line([[29, 31], [74, 69], [48, 90]], false, false)
        } else if (name === "power") {
            c.beginPath(); c.arc(50, 54, 32, -Math.PI * 0.3, Math.PI * 1.3); c.stroke()
            line([[50, 10], [50, 46]], false, false)
        } else if (name === "back") {
            line([[62, 21], [33, 50], [62, 79]], false, false)
        } else if (name === "more") {
            for (let x of [23, 50, 77]) { c.beginPath(); c.arc(x, 50, 5, 0, Math.PI * 2); c.fill() }
        } else {
            c.lineWidth = 7
            for (let i = 0; i < 8; i++) {
                let a = i * Math.PI / 4
                line([[50 + 28 * Math.cos(a), 50 + 28 * Math.sin(a)], [50 + 40 * Math.cos(a), 50 + 40 * Math.sin(a)]], false, false)
            }
            c.beginPath(); c.arc(50, 50, 29, 0, Math.PI * 2); c.stroke()
            c.lineWidth = 5; c.beginPath(); c.arc(50, 50, 10, 0, Math.PI * 2); c.stroke()
        }
    }
}
