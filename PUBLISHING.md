# GitHub 发布清单

这是源码发布工程，和本机 `D:\Apps\VideoToGPT` 的已验证 Windows 可执行程序不是一回事。

## 首次发布

1. 在 GitHub 新建 `VideoToGPT` 仓库，建议先设为 **Private** 进行预览和审查，然后再决定是否转为 Public。
2. 上传本包目录**内部**的源码文件和 README（不要把模型、视频、评论、浏览器 Cookie、`output/` 上传）。
3. 决定原始应用代码的许可证。选择许可证前不要声称已经正式开源；分发随附的第三方依赖需额外核对许可。
4. 发布代码版本标记 `v0.3.2-beta`，如有 Windows 已验证 ZIP，再经 GitHub **Releases → Draft a new release** 上传便携版。不要把几百 MB 的 EXE ZIP 加入 Git 普通源码历史。
5. 用另一台 Win10/11 x64 电脑解压独立版，验证能启动、模型能加载、实际处理公开图文和视频；如果没有完整验证，标记 Beta。

## 安全自查

- `.gitignore` 阻止新文件加入，但已经提交过的敏感文件不会被自动抹掉。
- 检查 ZIP、日志和配置中是否含有账户/私人聊天、Cookie、扫码会话数据或分享视频内容。
- 检查 `build_windows.py` 是否在当前开发机能定位 Whisper 模型及 FFmpeg 工具。
- 请勿把 `D:\VideoText` 整个目录上传。

## 向朋友发送

最容易给非开发者使用的是 GitHub Releases 的便携版 ZIP；仅分享源代码仓库会让对方自己配置 Python、FFmpeg、Whisper 模型。
