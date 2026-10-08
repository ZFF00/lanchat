# LanChat 局域网传输

两台电脑之间像聊天一样互传文本和文件：打开一个对话框，想发什么发什么，对方也在对话框里收到。

- 回车发文本，Shift+回车换行，每条文本可一键复制
- 点 📎 选文件、把文件拖进窗口，或直接 Ctrl+V 粘贴截图
- 图片在对话里直接显示；点文件即用系统默认程序打开（浏览器中为新标签页预览），也可点"下载"另存
- 文件以卡片显示，按类型配色角标（Word、Excel、PDF、压缩包等）；卡片下方工具栏可打开、定位所在目录、另存为，"⋯"或右键卡片还能复制文件路径和所在目录路径
- 为安全起见，exe、bat、ps1 等可执行文件不会点开即运行，需下载后手动运行
- 聊天记录保留，重开还在
- 无需账号、不经过任何外部服务器，数据只在局域网内传输

## 下载使用（Windows）

到 [Releases](../../releases) 下载 `LanChat.exe`，两台电脑都放一份。exe 不需要安装，放哪里都能双击运行；想像普通软件一样从桌面和开始菜单打开，见下面的[安装与快捷方式](#安装与快捷方式)。

双击运行后：

1. **一台选"作为主机启动"**：聊天记录和文件存在这台上，使用时需保持开着。窗口顶部会显示地址和访问码，例如 `另一台连接：192.168.3.136:8765 · 访问码 0650`。
2. **另一台选"连接另一台"**：点"自动搜索"，或手动输入主机显示的地址，首次连接输入一次访问码。

之后再打开会自动进入上次的模式；右上角"切换"可重新选择。

连接过的主机会自动加入**常用设备**，下次在启动页点一下即可连接，卡片上的名字可以改（默认是对方的电脑名）。聊天页左上角的设备按钮显示当前连着哪台，点开可直接切换到其他常用设备。每台主机有一个首次运行时随机生成的设备编号，对方换了 IP 时，app 会先试上次的地址，不对再自动搜索同一编号找到它的新地址。

> 两台都选"作为主机"会变成两个互不相通的聊天室——必须一台主机、一台连接。

**两台都保存完整记录**：连接端会在后台每隔几秒与主机双向同步聊天记录和文件，所以不管哪台当主机，两台都留有一份完整的记录。换另一台当主机后，以前的消息和文件照样都在。同步需要两台都是 v1.0.8 及以上。

### 安装与快捷方式

推荐把 exe 放到 Windows 给单用户程序的标准位置 `%LOCALAPPDATA%\Programs\LanChat`，再建桌面和开始菜单快捷方式。下载 `LanChat.exe` 到"下载"文件夹后，在 PowerShell 里执行（不需要管理员权限）：

```powershell
$dir = Join-Path $env:LOCALAPPDATA 'Programs\LanChat'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Get-Process LanChat -ErrorAction SilentlyContinue | Stop-Process   # 升级时先关掉正在运行的旧版
Copy-Item "$env:USERPROFILE\Downloads\LanChat.exe" $dir -Force
$exe = Join-Path $dir 'LanChat.exe'
$sh = New-Object -ComObject WScript.Shell
foreach ($folder in [Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs')) {
    $lnk = $sh.CreateShortcut((Join-Path $folder 'LanChat.lnk'))
    $lnk.TargetPath = $exe; $lnk.WorkingDirectory = $dir; $lnk.IconLocation = "$exe,0"; $lnk.Save()
}
Start-Process $exe
```

- 升级新版时再执行一次即可：覆盖 exe，快捷方式不变。
- 聊天记录、文件、常用设备都在 `%APPDATA%\LanChat`，与 exe 放在哪里无关，换位置、升级都不会丢。
- 卸载：删除 `%LOCALAPPDATA%\Programs\LanChat` 和两个快捷方式；要连数据一起删，再删 `%APPDATA%\LanChat`。
- 命令行工具 `lanchat-cli.exe` 也可以放进同一个目录，并把该目录加进用户 PATH，之后在任意位置直接运行 `lanchat-cli`。

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
- **任务栏图标是空白文件**：exe 放在了网盘同步文件夹（WPS 云盘、OneDrive 等）里，尤其是已经不再同步的旧同步文件夹，Windows 取不到图标。按上面的方法装到 `%LOCALAPPDATA%\Programs\LanChat` 即可。
- **任务栏图标还是旧的**：Windows 缓存了旧图标，重启"Windows 资源管理器"（任务管理器里右键 → 重新启动）或注销一次即可。
- **数据存在哪**：`%APPDATA%\LanChat`（聊天记录 `messages.jsonl`、收到的文件 `files\`、访问码 `pin.txt`）。

### 连接状态

- 主机窗口顶部显示连着本机的设备：绿点为在线；对方关掉 app、断网或改去连别的主机，约 10 秒后显示"已断开 · 多久之前"
- 连接端与主机断开时，顶部显示红字"与主机的连接已断开，重试中…"，恢复后自动继续

## 命令行工具

Releases 里的 `lanchat-cli.exe`（或源码里的 `python cli.py`）与桌面 app 共用配置、常用设备和访问码，app 不开也能用：

```powershell
lanchat-cli status                    # 本机模式、连着谁、谁连着本机、常用设备是否在线、最近同步
lanchat-cli status --json             # 同上，输出 JSON；主机连不上时退出码为 1
lanchat-cli send 你好                  # 发给当前连着的主机（本机是主机时发到本机）
lanchat-cli send 报告.pdf 截图.png      # 参数是已存在的文件就当文件发
lanchat-cli send -t 笔记本HP 你好       # -t：常用设备名、电脑名、IP[:端口] 或 本机
lanchat-cli watch                     # 持续显示新消息，连接断开/恢复时提示
lanchat-cli pull -o 收件               # 下载聊天里的所有文件，已下载的跳过
lanchat-cli login -t 192.168.3.136    # 输入并记住访问码
```

主机还提供 `GET /api/status`（谁连着、各自最后活动和最后同步时间），需要访问码，本机访问免。

## 不装 app：浏览器方式

需要 Python 3.10+，无第三方依赖。主机上运行：

```powershell
python lanchat.py
```

两台电脑的浏览器都打开终端里显示的地址即可（另一台可以是 Mac、Linux 或手机）。这种方式的数据存在脚本旁的 `data\` 目录。

## 从源码打包

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install pywebview pyinstaller pillow
.venv\Scripts\python.exe make_icon.py   # 改了图标设计才需要重新生成 icon.ico
.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name LanChat --icon icon.ico --add-data "index.html;." --add-data "setup.html;." --add-data "icon.ico;." app.py
```

产物在 `dist\LanChat.exe`。窗口依赖系统自带的 WebView2（Windows 10/11 通常已预装）。

## 文件说明

| 文件 | 作用 |
|---|---|
| `lanchat.py` | 聊天服务（仅标准库）：消息、文件上传下载、访问码、局域网广播应答、连接状态 |
| `common.py` | 主机服务、app、命令行共用的路径和局域网工具 |
| `cli.py` | 命令行工具 |
| `index.html` | 聊天界面 |
| `app.py` | 桌面 app 外壳（pywebview），负责主机/连接模式选择 |
| `setup.html` | 首次启动的模式选择页 |
| `start.bat` | 双击以浏览器方式启动服务 |
| `icon.ico` / `make_icon.py` | app 图标及其生成脚本 |

## 安全说明

- 服务监听局域网所有地址，4 位访问码只能挡住随手访问，不是强认证；请只在可信网络中使用，不要暴露到公网。
- 通信为明文 HTTP，同网络内可被抓包。
- 收到的文件除图片外一律以下载方式提供，不在页面内打开。

## 许可证

[MIT](LICENSE)
