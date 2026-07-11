# Universal Video Downloader for Windows

一个用于学习和研究视频下载流程的 Windows 桌面工具。项目提供 HLS/m3u8、普通视频直链和 YouTube 链接的解析与下载能力，重点演示播放列表解析、分片下载、断点续传、音视频合并和桌面客户端交互。

> 本项目仅作为学术研究、技术学习和个人合法备份示例开源。请勿用于商业用途，请勿下载、传播或再分发没有授权的内容，请勿规避 DRM、访问控制、付费限制或版权保护机制。

当前版本：`v1.1.0`。发布记录见 [CHANGELOG.md](CHANGELOG.md)。

## 功能

- 输入网页 URL、`.m3u8` URL、普通视频直链或 YouTube 视频 URL。
- 自动扫描网页源码和外链脚本中的 HLS/m3u8 地址。
- 静态扫描没有结果时，自动切换到 `yt-dlp` 通用网页解析器。
- 自动识别普通视频直链，例如 `.mp4`、`.webm`、`.mov`、`.mkv`、`.m4v`。
- 使用 `yt-dlp` 支持 YouTube 视频解析、下载和音视频合并。
- 自动解析 HLS master playlist，并默认选择分辨率/码率较高的清晰度。
- HLS 分片并发下载，支持暂停、继续、停止和失败后续传。
- 普通 HTTP 视频使用 `.part` 文件和 Range 请求实现断点续传。
- 续传时校验服务器 `Content-Range` 起点并使用未压缩字节流，响应不匹配时自动安全重建，避免文件静默损坏。
- 支持常见 HLS AES-128 加密分片。
- 自动重试临时网络错误，并尊重服务器的 `Retry-After` 限流指示。
- 按访问拒绝、链接失效、限流、服务器异常、磁盘权限等类别提供恢复建议。
- 本地任务记录支持完成、失败、停止和异常退出后的状态恢复。
- 下载进度采用限频刷新、平滑速度和 ETA，长播放列表使用聚合状态块避免界面卡顿。
- 日志和历史记录会移除 URL 用户信息、查询参数、临时签名及常见 Token 字段。
- 使用适配 Windows 16/32/64/256px 的专业品牌图标，窗口、EXE 与桌面快捷方式保持一致。
- 可保留本地缓存，便于下载中断后继续。

## 支持的视频类型

| 类型 | 支持情况 | 说明 |
| --- | --- | --- |
| HLS / m3u8 | 支持 | 支持 master playlist、多清晰度、分片缓存和 AES-128 |
| 普通视频直链 | 支持 | 支持 mp4、webm、mov、mkv、m4v 等常见扩展 |
| YouTube | 支持 | 通过 yt-dlp 解析，通常需要 ffmpeg 合并音视频 |
| 通用网页媒体 | 支持 | 静态扫描失败后通过 yt-dlp 的站点提取器尝试解析 |
| DASH / MPD | 部分支持 | yt-dlp 能识别的公开资源可下载，尚无原生 MPD 解析器 |
| DRM 内容 | 不支持 | 不绕过 Widevine、FairPlay、PlayReady 等 DRM |

## 安装运行

建议使用 Python 3.11 或更高版本。当前代码也可在 Python 3.10 下运行，但 `yt-dlp` 已提示未来会逐步弃用 Python 3.10。

```powershell
python -m pip install -r requirements.txt
python m3u8_desktop_app.py
```

默认保存目录：

```text
%USERPROFILE%\Downloads\Video Downloader
```

## 使用流程

1. 在“添加媒体链接”中粘贴视频页面 URL、`.m3u8`、普通视频直链或 YouTube 链接。
2. 点击“解析媒体”，客户端会按静态扫描、HLS 解析、通用提取器的顺序查找媒体，并自动选择推荐画质。
3. 确认右侧保存目录和文件名，点击“开始下载”。
4. 需要手动设置 `Referer`、并发数或缓存策略时，展开“高级选项”。
5. 在“任务记录”中查看本机历史、打开输出目录，或重新填入已脱敏的来源地址。
6. 中断后再次使用相同来源和输出路径开始下载，会复用分片缓存或 `.part` 文件继续下载。

## 本地数据与隐私

任务记录默认保存在：

```text
%LOCALAPPDATA%\UniversalVideoDownloader\history.json
```

历史文件只用于本机任务管理。来源地址写入前会移除用户名、密码、查询参数和片段；错误日志会隐藏常见 Cookie、Authorization、Token 与签名字段。输出文件路径会保留，以便从客户端打开保存目录。应用不会把任务历史上传到服务器。

## 当前限制

- 工具不会导入浏览器 Cookie、登录会话或受保护的用户数据。必须登录后播放的页面可能无法解析。
- 工具不执行网页 JavaScript，也不监听浏览器实时网络请求；高度动态的网站仍可能需要站点支持或用户提供公开媒体地址。
- 原生 HLS 下载暂未实现 `EXT-X-BYTERANGE` 字节范围分片；这类公开媒体会优先尝试通用解析器。
- `yt-dlp` 的站点支持会随上游网站变化。遇到解析回归时，先升级 `yt-dlp` 并查看活动日志。
- YouTube、DASH 以及分离音视频流通常需要系统 PATH 中可访问 `ffmpeg`。

## 打包为 exe

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

构建完成后可执行文件位于：

```text
dist\UniversalVideoDownloader\UniversalVideoDownloader.exe
```

当前版本会把图标和 `yt-dlp` 一起打入 exe。若需要更稳定的 YouTube 音视频合并，请确保系统 PATH 中可访问 `ffmpeg`。

品牌主图位于 `assets/app_brand_v2.png`，Windows 多尺寸图标位于 `assets/app_icon_v2.ico`。旧版图标仍保留在仓库中用于版本追溯。

正式便携包同时包含 `README.md`、`CHANGELOG.md`、`LICENSE` 和 `THIRD_PARTY_NOTICES.md`，并提供独立 SHA-256 校验文件。

## 测试

```powershell
python -m pytest -q
```

测试用例只使用合成页面、本地 HTTP 服务、示例域名和协议解析样例，不包含真实视频资源、账号、Cookie、Token 或私有站点信息。测试覆盖播放列表解析、断点续传、通用网页回退、候选去重、错误分类、历史原子写入、敏感信息脱敏和 UI 事件合并。

## 开源脱敏说明

- 仓库不包含真实下载任务、历史记录、浏览器 Cookie、认证头、账号密码、Token 或个人路径配置。
- 测试和文档使用 `example.com`、`example.test` 等示例域名，不包含真实资源站点链接。
- 项目只保留通用协议解析、下载调度、断点续传和桌面 UI 功能。
- 不提供规避 DRM、破解付费访问、绕过登录授权或批量侵权分发的能力。

## 合规声明

本项目仅用于学术研究、协议学习、个人合法备份和软件工程练习。使用者必须自行确认下载行为符合当地法律法规、网站服务条款和版权授权范围。项目作者不鼓励、不支持、不承担任何未经授权下载、传播、商用或侵犯著作权行为产生的责任。

## License

本项目使用 [Academic Research and Non-Commercial Use License](LICENSE)。该许可证允许学术研究、技术学习和个人合法备份场景使用，不允许未经授权的商业用途、侵权用途或 DRM/访问控制绕过用途。
