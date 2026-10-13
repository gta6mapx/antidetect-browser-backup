"""
JavaScript script generator for browser fingerprint randomization.
Generates CDP-compatible injection scripts based on FingerprintProfile.
"""

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .fingerprinter import FingerprintProfile


class ScriptGenerator:
    """Generates all JavaScript fingerprint randomization scripts."""

    def __init__(self, profile: "FingerprintProfile"):
        self.p = profile

    def build(self) -> str:
        """Build the complete injection script combining all modules."""
        scripts = [
            self._canvas(),
            self._webgl(),
            self._audio(),
            self._navigator(),
            self._screen(),
            self._webrtc(),
            self._timezone(),
            self._fonts(),
            self._battery(),
            self._permissions(),
            self._plugins_mime(),
        ]
        return "\n;\n".join(f"(function(){{{s}}})();" for s in scripts if s)

    # ---- Individual modules ----

    def _canvas(self) -> str:
        r, g, b = self.p.canvas_noise
        return f"""
const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
const _origToBlob = HTMLCanvasElement.prototype.toBlob;
const _origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
const _addNoise = function(c) {{
    const ctx = c.getContext('2d');
    if (!ctx) return;
    try {{
        const d = ctx.getImageData(0, 0, c.width, c.height);
        for (let i = 0; i < d.data.length; i += 4) {{
            d.data[i] = (d.data[i] + {r}) & 255;
            d.data[i+1] = (d.data[i+1] + {g}) & 255;
            d.data[i+2] = (d.data[i+2] + {b}) & 255;
        }}
        ctx.putImageData(d, 0, 0);
    }} catch(e) {{}}
}};
HTMLCanvasElement.prototype.toDataURL = function(){{ _addNoise(this); return _origToDataURL.apply(this, arguments); }};
HTMLCanvasElement.prototype.toBlob = function(){{ _addNoise(this); return _origToBlob.apply(this, arguments); }};
CanvasRenderingContext2D.prototype.getImageData = function(){{ const d = _origGetImageData.apply(this, arguments); for(let i=0;i<d.data.length;i+=4){{ d.data[i]=(d.data[i]+{r})&255; d.data[i+1]=(d.data[i+1]+{g})&255; d.data[i+2]=(d.data[i+2]+{b})&255; }} return d; }};
"""

    def _webgl(self) -> str:
        return f"""
const _glGetParam = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(p) {{
    if (p === 37445) return '{self.p.webgl_vendor}';
    if (p === 37446) return '{self.p.webgl_renderer}';
    return _glGetParam.call(this, p);
}};
if (typeof WebGL2RenderingContext !== 'undefined') {{
    const _gl2GetParam = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function(p) {{
        if (p === 37445) return '{self.p.webgl_vendor}';
        if (p === 37446) return '{self.p.webgl_renderer}';
        return _gl2GetParam.call(this, p);
    }};
}}
"""

    def _audio(self) -> str:
        return f"""
const _AudioCtx = window.AudioContext || window.webkitAudioContext;
if (_AudioCtx) {{
    const _origCreateOsc = _AudioCtx.prototype.createOscillator;
    if (_origCreateOsc) {{
        _AudioCtx.prototype.createOscillator = function() {{
            const osc = _origCreateOsc.call(this);
            const _origStart = osc.start;
            osc.start = function() {{
                try {{ osc.frequency.value += {self.p.audio_noise}; }} catch(e) {{}}
                return _origStart.apply(this, arguments);
            }};
            return osc;
        }};
    }}
}}
"""

    def _navigator(self) -> str:
        return f"""
Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {self.p.hardware_concurrency} }});
Object.defineProperty(navigator, 'deviceMemory', {{ get: () => {self.p.device_memory} }});
Object.defineProperty(navigator, 'maxTouchPoints', {{ get: () => {self.p.max_touch_points} }});
Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});
Object.defineProperty(navigator, 'platform', {{ get: () => '{self.p.platform}' }});
Object.defineProperty(navigator, 'languages', {{ get: () => {json.dumps(self.p.languages)} }});
Object.defineProperty(navigator, 'language', {{ get: () => '{self.p.languages[0]}' }});
"""

    def _screen(self) -> str:
        p = self.p
        return f"""
Object.defineProperty(screen, 'width', {{ get: () => {p.screen_width} }});
Object.defineProperty(screen, 'height', {{ get: () => {p.screen_height} }});
Object.defineProperty(screen, 'availWidth', {{ get: () => {p.screen_width} }});
Object.defineProperty(screen, 'availHeight', {{ get: () => {p.screen_height - 40} }});
Object.defineProperty(screen, 'colorDepth', {{ get: () => {p.color_depth} }});
Object.defineProperty(screen, 'pixelDepth', {{ get: () => {p.pixel_depth} }});
Object.defineProperty(window, 'outerWidth', {{ get: () => {p.screen_width} }});
Object.defineProperty(window, 'outerHeight', {{ get: () => {p.screen_height} }});
"""

    def _webrtc(self) -> str:
        return """
if (window.RTCPeerConnection) {
    const _origRTC = window.RTCPeerConnection;
    window.RTCPeerConnection = function(config) {
        config = config || {};
        config.iceCandidatePoolSize = 0;
        return new _origRTC(config);
    };
    window.RTCPeerConnection.prototype = _origRTC.prototype;
}
"""

    def _timezone(self) -> str:
        tz = self.p.timezone_offset
        sign = '+' if tz > 0 else ''
        hours = abs(tz) // 60
        mins = abs(tz) % 60
        tz_str = f"GMT{sign}{hours:02d}:{mins:02d}"
        return f"""
const _origToString = Date.prototype.toString;
const _origGetTZ = Intl.DateTimeFormat.prototype.resolvedOptions;
Date.prototype.toString = function() {{
    const s = _origToString.call(this);
    const tzMatch = s.match(/\\(([^)]+)\\)$/);
    if (tzMatch) return s.replace(tzMatch[0], '({tz_str})');
    return s.replace(/GMT[+-]\\d{{4}}/, '{tz_str}');
}};
if (Intl && Intl.DateTimeFormat) {{
    Intl.DateTimeFormat.prototype.resolvedOptions = function() {{
        const opts = _origGetTZ.call(this);
        opts.timeZone = 'Etc/GMT{sign}{hours}';
        return opts;
    }};
}}
"""

    def _fonts(self) -> str:
        if not self.p.fonts or len(self.p.fonts) < 5:
            return ""
        fonts_json = json.dumps(self.p.fonts)
        return f"""
const _fonts = new Set({fonts_json});
const _origCheckFont = document.fonts ? document.fonts.check : null;
if (_origCheckFont) {{
    document.fonts.check = function(font, text) {{
        const family = font.split(' ')[0].replace(/['"]/g, '');
        if (_fonts.has(family)) return true;
        return _origCheckFont.call(document.fonts, font, text);
    }};
}}
"""

    def _battery(self) -> str:
        return """
if (navigator.getBattery) {
    const _origGetBattery = navigator.getBattery;
    navigator.getBattery = function() {
        return _origGetBattery.call(navigator).then(function(bm) {
            Object.defineProperty(bm, 'charging', { get: () => true });
            Object.defineProperty(bm, 'level', { get: () => Math.random() * 0.4 + 0.6 });
            return bm;
        });
    };
}
"""

    def _permissions(self) -> str:
        return """
const _origQuery = window.navigator.permissions ? window.navigator.permissions.query : null;
if (_origQuery) {
    window.navigator.permissions.query = function(params) {
        if (params.name === 'notifications' || params.name === 'clipboard-read' || params.name === 'clipboard-write') {
            return Promise.resolve({ state: 'prompt', onchange: null });
        }
        return _origQuery.call(window.navigator.permissions, params);
    };
}
"""

    def _plugins_mime(self) -> str:
        return """
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const arr = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format', length: 1 },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '', length: 1 },
            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '', length: 2 },
        ];
        arr.item = function(i) { return this[i]; };
        arr.namedItem = function(n) { return this.find(p => p.name === n); };
        arr.refresh = function() {};
        Object.defineProperty(arr, 'length', { get: () => 3 });
        return arr;
    }
});
Object.defineProperty(navigator, 'mimeTypes', {
    get: () => {
        const arr = [
            { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
            { type: 'text/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
        ];
        arr.item = function(i) { return this[i]; };
        arr.namedItem = function(n) { return this.find(m => m.type === n); };
        Object.defineProperty(arr, 'length', { get: () => 2 });
        return arr;
    }
});
"""
