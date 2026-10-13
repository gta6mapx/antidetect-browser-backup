# Fingerprint Toolkit - 浏览器指纹工具箱

[![PyPI](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Cross--platform-lightgrey.svg)]()

多维度浏览器指纹随机化工具，适用于 Selenium、Playwright、undetected-chromedriver 及任何支持 CDP 的自动化框架。

> 📖 English: [README.md](README.md)

## 功能特性

| 模块 | 说明 |
|--------|-------------|
| 🎨 Canvas | 在 Canvas 渲染时注入不可见 RGB 噪点，彻底改变指纹哈希 |
| 🎮 WebGL | 伪装 GPU 厂商和渲染器（Intel / NVIDIA / AMD / Apple） |
| 🔊 Audio | 在 AudioContext 振荡器中添加微量频率偏移 |
| 🖥️ Navigator | 覆盖 hardwareConcurrency、deviceMemory、platform、languages、webdriver |
| 📺 Screen | 随机化分辨率、颜色深度、像素深度 |
| 🌐 WebRTC | 防止 WebRTC 泄露真实 IP |
| 🕐 时区 | 伪装时区字符串和 Intl.DateTimeFormat |
| 🔤 字体 | 注入自定义字体列表，绕过字体指纹检测 |
| 🔋 电池 | 伪装电池电量和充电状态 |
| 🔒 权限 | 掩盖通知/剪贴板权限状态 |

## 快速开始

### 安装

```bash
pip install git+https://github.com/xuweizhengo/fingerprint-toolkit.git
```

### 命令行

```bash
# 生成随机指纹
fingerprint-toolkit generate

# 生成并保存
fingerprint-toolkit generate -o profile.json

# 输出 JSON + JS 注入脚本
fingerprint-toolkit generate --json --script

# 查看已保存的配置
fingerprint-toolkit inspect profile.json

# 浏览器实时测试（需 undetected-chromedriver）
fingerprint-toolkit test
```

### Python API

```python
from fingerprint_toolkit import FingerprintKit

# 生成随机指纹
fpk = FingerprintKit()
print(fpk.get_profile_json())

# 注入到 Selenium
fpk.inject(driver)   # undetected-chromedriver / selenium

# 注入到 Playwright
fpk.inject(page)     # playwright async page

# 获取独立的 JS 脚本
js_script = fpk.to_script()

# 保存/加载配置（保持会话一致性）
fpk.save_profile("profile.json")
fpk2 = FingerprintKit.from_profile_file("profile.json")
```

### Selenium 示例

```python
import undetected_chromedriver as uc
from fingerprint_toolkit import FingerprintKit

driver = uc.Chrome()
FingerprintKit().inject(driver)  # 导航前注入
driver.get("https://browserleaks.com/canvas")
```

### Playwright 示例

```python
from playwright.sync_api import sync_playwright
from fingerprint_toolkit import FingerprintKit

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    FingerprintKit().inject(page)
    page.goto("https://browserleaks.com/canvas")
```

## 指纹配置

每次生成的指纹配置是**协调一致**的——相关参数保持真实感：

```
GPU: Intel → 平台: Windows → 分辨率: 桌面级
GPU: Apple M2 → 平台: MacIntel → 分辨率: MacBook 级
```

总唯一组合数：**240 万+**（1000 Canvas × 20 GPU × 6 CPU × 4 RAM × 5 分辨率）

## 测试网站

在以下网站验证指纹随机化效果：

- https://browserleaks.com/canvas
- https://browserleaks.com/webgl
- https://abrahamjuliot.github.io/creepjs/
- https://amiunique.org/
- https://coveryourtracks.eff.org/

## 技术架构

```
JavaScript 注入 (CDP Page.addScriptToEvaluateOnNewDocument)
       │
       ▼
┌──────────────────────────────────────────┐
│  Canvas  │ WebGL │ Audio │ Navigator     │
│  Screen  │ WebRTC│ 时区  │ 字体/电池     │
└──────────────────────────────────────────┘
       │
       ▼
  在任何页面脚本之前执行 —— 对检测方完全透明
```

## 合规使用

请将本工具用于自有系统、授权测试、QA 自动化和隐私研究。生成的指纹应与测试场景保持一致，不应被用于冒充真实用户、绕过访问控制或违反目标服务条款。

## 免责声明

本项目仅供学习和正当自动化测试使用。使用者需自行遵守目标网站的服务条款。



## 相关项目

- [aws-auto-register](https://github.com/xuweizhengo/aws-auto-register) — AWS 自动注册，内置本指纹工具箱
- [cursor-free-api](https://github.com/xuweizhengo/cursor-free-api) — Cursor 免费 API 转 OpenAI/Anthropic 格式
- [llm-api-purity](https://github.com/xuweizhengo/llm-api-purity) — OpenAI 与 Claude API 纯度检测工具
- [skills-hub](https://github.com/xuweizhengo/skills-hub) — AI 技能市场 & MCP 注册表

## 许可证

[MIT](LICENSE)
