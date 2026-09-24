# LanChat 局域网传输

两台电脑之间像聊天一样互传文本和文件：打开一个对话框，想发什么发什么，对方也在对话框里收到。

- 回车发文本，Shift+回车换行，每条文本可一键复制
- 点 📎 选文件、把文件拖进窗口，或直接 Ctrl+V 粘贴截图
- 图片在对话里直接显示，其他文件点"下载"保存
- 聊天记录保留，重开还在
- 无需账号、不经过任何外部服务器，数据只在局域网内传输

## 下载使用（Windows）

到 [Releases](../../releases) 下载 `LanChat.exe`，两台电脑都放一份，双击运行：

1. **一台选"作为主机启动"**：聊天记录和文件存在这台上，使用时需保持开着。窗口顶部会显示地址和访问码，例如 `另一台连接：192.168.3.136:8765 · 访问码 0650`。
2. **另一台选"连接另一台"**：点"自动搜索"，或手动输入主机显示的地址，首次连接输入一次访问码。

之后再打开会自动进入上次的模式；右上角"切换"可重新选择。

> 两台都选"作为主机"会变成两个互不相通的聊天室——必须一台主机、一台连接。

### 主机需放行防火墙

首次运行若弹出 Windows 防火墙对话框，点"允许"即可；或用管理员 PowerShell 执行：

```powershell
New-NetFirewallRule -DisplayName "LanChat" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private,Domain
New-NetFirewallRule -DisplayName "LanChat discovery" -Direction Inbound -Protocol UDP -LocalPort 8766 -Action Allow -Profile Private,Domain
```

### 常见问题

- **自动搜索找不到**：搜索靠 UDP 广播，跨网段（如 `192.168.3.x` 与 `192.168.8.x`）收不到，手动输入地址即可。
- **手动输入也连不上**：检查主机防火墙；公司/公共 Wi-Fi 可能开了 AP 隔离，禁止设备互访。
- **exe 被 Windows 拦截**：未签名程序可能被 SmartScreen / 智能应用控制拦下，稍等片刻再运行，或选择"仍要运行"。
- **数据存在哪**：`%APPDATA%\LanChat`（聊天记录 `messages.jsonl`、收到的文件 `files\`、访问码 `pin.txt`）。

## 不装 app：浏览器方式

需要 Python 3.10+，无第三方依赖。主机上运行：

```powershell
python lanchat.py
```

两台电脑的浏览器都打开终端里显示的地址即可（另一台可以是 Mac、Linux 或手机）。这种方式的数据存在脚本旁的 `data\` 目录。

## 从源码打包

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install pywebview pyinstaller
.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name LanChat --add-data "index.html;." --add-data "setup.html;." app.py
```

产物在 `dist\LanChat.exe`。窗口依赖系统自带的 WebView2（Windows 10/11 通常已预装）。

## 文件说明

| 文件 | 作用 |
|---|---|
| `lanchat.py` | 聊天服务（仅标准库）：消息、文件上传下载、访问码、局域网广播应答 |
| `index.html` | 聊天界面 |
| `app.py` | 桌面 app 外壳（pywebview），负责主机/连接模式选择 |
| `setup.html` | 首次启动的模式选择页 |
| `start.bat` | 双击以浏览器方式启动服务 |

## 安全说明

- 服务监听局域网所有地址，4 位访问码只能挡住随手访问，不是强认证；请只在可信网络中使用，不要暴露到公网。
- 通信为明文 HTTP，同网络内可被抓包。
- 收到的文件除图片外一律以下载方式提供，不在页面内打开。

## 许可证

[MIT](LICENSE)
