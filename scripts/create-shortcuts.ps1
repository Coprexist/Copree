<#
  在 Windows 上创建 Copree 快捷方式（开始菜单 + 桌面）。

  为什么单独一个 .ps1：中文串放在 .bat 里必然要过一次「批处理文件编码 → 控制台代码页 →
  子进程」的转换，cmd 按 ANSI 读 UTF-8 文件就会把「启动器」写成「å¯åŠ¨å™¨」——
  快捷方式名字一旦落盘就是乱码，改不回来只能删了重建。
  PowerShell 读「带 BOM 的 UTF-8 .ps1」是按规范解码的，中文不走代码页，这条路才是稳的。
  .bat 里因此只留 ASCII。
#>
param(
  [Parameter(Mandatory = $true)][string]$InstallDir,
  [string]$ExeName = "Copree.exe",
  [string]$LinkName = "Copree 启动器",
  [string]$Description = "Copree 桌面启动器"
)

$ErrorActionPreference = "Stop"
$exe = Join-Path $InstallDir $ExeName
if (-not (Test-Path $exe)) { Write-Error "找不到可执行文件：$exe"; exit 1 }

# 图标取 exe 本身（里面已经嵌了 16~256 九档 ICO）；显式写上，免得某些系统缓存里取到旧图标
$icon = "$exe,0"

$shell = New-Object -ComObject WScript.Shell
$targets = @()

$startDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Copree"
if (-not (Test-Path $startDir)) { New-Item -ItemType Directory -Path $startDir -Force | Out-Null }
$targets += Join-Path $startDir "$LinkName.lnk"

$desktop = [Environment]::GetFolderPath("Desktop")
if ($desktop) { $targets += Join-Path $desktop "$LinkName.lnk" }

foreach ($lnkPath in $targets) {
  $sc = $shell.CreateShortcut($lnkPath)
  $sc.TargetPath = $exe
  $sc.WorkingDirectory = $InstallDir
  $sc.Description = $Description
  $sc.IconLocation = $icon
  $sc.Save()
  Write-Host "  [OK] $lnkPath"
}

# 让资源管理器立刻重读图标缓存（不刷的话新图标要等下次登录才生效）
if (Test-Path "$env:SystemRoot\System32\ie4uinit.exe") { & "$env:SystemRoot\System32\ie4uinit.exe" -show }
Write-Host "  快捷方式已创建：$($targets.Count) 个"

# ── 清掉以前编码写坏的那批快捷方式 ─────────────────────────────
# 旧版 install.bat 把中文写在批处理里（UTF-8 无 BOM + LF），cmd 按控制台代码页解码，
# 「Copree 启动器」落盘就成了「Copree å¯åŠ¨å™¨」。这种链接一旦建出来就会一直躺在桌面上，
# 用户不会自己去删——所以每次安装顺手清掉，再建正确的那一个。
$mojibake = @([char]0x00E5, [char]0x00C3, [char]0xFFFD)   # å / Ã / 替换字符：UTF-8 被当 ANSI 读的残骸
$scanDirs = @((Join-Path $env:USERPROFILE "Desktop"), (Join-Path $env:PUBLIC "Desktop"), $startDir) |
    Where-Object { $_ -and (Test-Path $_) }
foreach ($dir in $scanDirs) {
    Get-ChildItem -Path $dir -Filter *.lnk -ErrorAction SilentlyContinue | Where-Object {
        $name = $_.BaseName
        $name -like "*Copree*" -and ($mojibake | Where-Object { $name.Contains($_) })
    } | ForEach-Object {
        try { Remove-Item $_.FullName -Force; Write-Host "  [clean] $($_.Name)" }
        catch { Write-Host "  [skip]  $($_.Name)" }
    }
}
