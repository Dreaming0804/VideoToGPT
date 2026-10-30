# VideoToGPT

**将公开可访问的在线视频链接、图文页面整理成方便普通 ChatGPT 网页聊天读取的资料包。**

> 版本：**v0.3.2 源码测试版**（Windows / Python 3.12）。本仓库公开的是工程源码；可直接双击运行的 Windows 便携 EXE 需要单独构建并在 GitHub Releases 发布。当前版本未经广泛跨机器验证。

## 功能

- **视频**：B站、YouTube、小红书等可访问的视频，以及 yt-dlp / 网页解析能够发现的其他视频链接；下载音视频、CPU 语音转录、按时间间隔抽帧、输出 ZIP。
- **图文**：小红书公开图文笔记及部分普通公开网页，保存正文及可访问图片。
- **小红书评论（可选）**：使用单独 Edge 浏览器配置读取浏览器正常加载的评论和回复；默认不采集评论。报告记录已采集范围，不保证平台全量。
- **多语言**：依赖 faster-whisper small 模型；语言识别质量随视频语音、背景音乐而变化。
- **离线分析材料**：生成 `UploadToChatGPT.zip` 后，可上传到普通网页端 ChatGPT 提问，无需 Work 模式或大模型 API。

## 使用方式

1. 通过 GitHub Releases 下载**已验证的 Windows 便携版**（如果维护者已发布）。解压完整文件夹，运行 `VideoToGPT.exe`。
2. 在窗口粘贴链接，点击开始处理。小红书评论仅在你主动勾选后尝试读取。
3. 完成后打开软件指定的输出目录，将 `UploadToChatGPT.zip` 上传到 ChatGPT。

**注意：GitHub 的绿色 Code → Download ZIP 下载的是源码，不是包含 FFmpeg、Whisper 模型的完整 EXE 软件。**

## 源码开发（Windows）

推荐 Python 3.12，使用独立虚拟环境：

```powershell
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

还需要自行准备 `yt-dlp.exe`、`ffmpeg.exe`、`ffprobe.exe`、`deno.exe` 与完整的 `faster-whisper-small` 模型。可将二进制放在项目的 `tools/`，模型放在 `models/small/`；或者设置 `VIDEOTOGPT_ASSET_DIR` 指向此前已能运行的便携版目录。模型目录需包含：`model.bin`、`config.json`、`tokenizer.json`、`vocabulary.txt`。

```powershell
$env:VIDEOTOGPT_ASSET_DIR = 'D:\Apps\VideoToGPT'
& .\.venv\Scripts\python.exe .\VideoToGPT.py
```

也可双击 `1_Run_Development.cmd`。源码开发模式依赖上述组件；未配置时程序不能处理视频。

### 构建可分享的 Windows EXE

已有 Python 环境、模型、外部工具后，双击 `2_Build_Windows.cmd`。构建脚本优先查找 `VIDEOTOGPT_TOOLS_DIR`、`VIDEOTOGPT_MODEL_DIR`，再搜索预置位置、系统 PATH 和常见模型缓存。输出目录可通过 `VIDEOTOGPT_INSTALL_DEST` 设置。

构建后必须在新的目录、不同机器上验证启动和实际视频处理。某些电脑需要 Microsoft Visual C++ x64 运行库；可能受 Windows SmartScreen 提示影响。**目前没有提供经过通用环境测试的官方 EXE 发布包。**

## 已知限制

- 不保证任意网站都能解析。登录、权限限制、验证码、DRM 保护、过期视频流可能导致失败；不会破解 DRM。
- 图文/视频探测尚不能可靠处理所有动态网站；**知乎问题、回答及评论尚未专门适配**。
- 视频固定抽帧可能使 ZIP 大于原视频。智能抽帧、压缩和按需提取属于后续优化方向。
- 评论采集可能中断或不完整；不要根据部分数据推断整个评论区。
- 不要将有个人信息、登录数据、下载作品的输出包提交到公开仓库。

## 测试

```powershell
& .\.venv\Scripts\python.exe .\离线测试.py
& .\.venv\Scripts\python.exe .\通用视频离线测试.py
```

上述测试主要是逻辑/模拟用例，不能替代针对真实平台和 Windows 便携版的端到端测试。

## 第三方技术与许可

项目通过 [yt-dlp](https://github.com/yt-dlp/yt-dlp)、[FFmpeg](https://ffmpeg.org/)、[faster-whisper](https://github.com/SYSTRAN/faster-whisper)、[PyAV](https://github.com/PyAV-Org/PyAV)、[Playwright](https://playwright.dev/) 等工具工作；这些算法、模型和二进制程序不属于本项目原创。

**本源码尚未选择适用的开源许可证。** 公布在 GitHub 不自动等于允许他人复制、修改或再分发。请由项目维护者选择并添加许可证后再作为正式开源项目发布。分发附带第三方二进制的 EXE 时须额外核查组件许可证、通知和源代码义务，详见 `第三方组件说明.txt`。

## 路线图

- [ ] 知乎问题、回答和文章的规范化提取（针对可访问内容）
- [ ] 评论自动停止与完整性报告改进
- [ ] 智能判断视频是否需要画面，自适应抽帧与压缩
- [ ] 跨机器的独立版安装/便携运行测试
