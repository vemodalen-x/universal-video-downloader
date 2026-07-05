# Universal Video Downloader for Windows

一个用于学习和研究视频下载流程的 Windows 桌面工具。项目提供 HLS/m3u8、普通视频直链和 YouTube 链接的解析与下载能力，重点演示播放列表解析、分片下载、断点续传、音视频合并和桌面客户端交互。

> 本项目仅作为学术研究、技术学习和个人合法备份示例开源。请勿用于商业用途，请勿下载、传播或再分发没有授权的内容，请勿规避 DRM、访问控制、付费限制或版权保护机制。

## 功能

- 输入网页 URL、`.m3u8` URL、普通视频直链或 YouTube 视频 URL。
- 自动扫描网页源码和外链脚本中的 HLS/m3u8 地址。
- 自动识别普通视频直链，例如 `.mp4`、`.webm`、`.mov`、`.mkv`、`.m4v`。
- 使用 `yt-dlp` 支持 YouTube 视频解析、下载和音视频合并。
- 自动解析 HLS master playlist，并默认选择分辨率/码率较高的清晰度。
- HLS 分片并发下载，支持暂停、继续、停止和失败后续传。
- 普通 HTTP 视频使用 `.part` 文件和 Range 请求实现断点续传。
- 支持常见 HLS AES-128 加密分片。
- 用色块展示下载状态：灰色待下载、黄色下载中、绿色完成、红色失败。
- 可保留本地缓存，便于下载中断后继续。

## 支持的视频类型

| 类型 | 支持情况 | 说明 |
| --- | --- | --- |
| HLS / m3u8 | 支持 | 支持 master playlist、多清晰度、分片缓存和 AES-128 |
| 普通视频直链 | 支持 | 支持 mp4、webm、mov、mkv、m4v 等常见扩展 |
| YouTube | 支持 | 通过 yt-dlp 解析，通常需要 ffmpeg 合并音视频 |
| DASH / MPD | 规划中 | 可后续通过 yt-dlp 或独立 DASH 解析器扩展 |
| DRM 内容 | 不支持 | 不绕过 Widevine、FairPlay、PlayReady 等 DRM |

## 安装运行

建议使用 Python 3.11 或更高版本。当前代码也可在 Python 3.10 下运行，但 `yt-dlp` 已提示未来会逐步弃用 Python 3.10。

```powershell
python -m pip install -r requirements.txt
python m3u8_desktop_app.py
```

默认保存目录：

```text
%USERPROFILE%\Downloads\M3U8视频
```

## 使用流程

1. 在“网页或 m3u8”输入框中粘贴视频页面 URL、`.m3u8`、`.mp4` 或 YouTube 链接。
2. 如果下载源需要防盗链来源，在 `Referer` 中填写视频页面地址；不填时会自动使用输入地址的站点来源。
3. 点击“分析”，客户端会列出候选视频并自动选择较优清晰度。
4. 确认保存目录和文件名，点击“开始下载”。
5. 中断后再次使用同一输出路径开始下载，会复用缓存或 `.part` 文件继续下载。

## 打包为 exe

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

构建完成后可执行文件位于：

```text
dist\M3U8Downloader\M3U8Downloader.exe
```

当前版本会把图标和 `yt-dlp` 一起打入 exe。若需要更稳定的 YouTube 音视频合并，请确保系统 PATH 中可访问 `ffmpeg`。

## 测试

```powershell
python -m pytest -q
```

测试用例只使用合成页面、示例域名和协议解析样例，不包含真实视频资源、账号、Cookie、Token 或私有站点信息。

## 开源脱敏说明

- 仓库不包含真实下载任务、历史记录、浏览器 Cookie、认证头、账号密码、Token 或个人路径配置。
- 测试和文档使用 `example.com`、`example.test` 等示例域名，不包含真实资源站点链接。
- 项目只保留通用协议解析、下载调度、断点续传和桌面 UI 功能。
- 不提供规避 DRM、破解付费访问、绕过登录授权或批量侵权分发的能力。

## 合规声明

本项目仅用于学术研究、协议学习、个人合法备份和软件工程练习。使用者必须自行确认下载行为符合当地法律法规、网站服务条款和版权授权范围。项目作者不鼓励、不支持、不承担任何未经授权下载、传播、商用或侵犯著作权行为产生的责任。
