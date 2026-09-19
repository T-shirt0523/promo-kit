# capture_game.ps1 - record a real desktop game window to MP4.
#
# ASCII-ONLY FILE ON PURPOSE. PowerShell 5.1 reads .ps1 as ANSI; any non-ASCII
# byte in this file breaks the parser. Keep all comments and strings English.
#
# What it does:
#   1. launch the game exe (or attach to an already running process)
#   2. wait for its main window, restore it, bring it to the foreground
#   3. read the CLIENT rect (game pixels only, no title bar / border)
#   4. start ffmpeg gdigrab region capture on that rect, mouse pointer excluded
#   5. replay a timed input plan (keyboard holds/taps + mouse clicks)
#   6. stop, verify the file, print ASCII key=value report
#
# Usage:
#   powershell -File capture_game.ps1 -Exe D:\Game\game.exe -Out D:\out\clip.mp4 `
#       -Duration 20 -Fps 30 -PlanJson D:\out\plan.json
#
# Plan JSON format (all times are seconds relative to recording start):
#   {
#     "settle_ms": 1200,
#     "steps": [
#       {"at": 1.0, "hold": ["W"], "ms": 1500},
#       {"at": 3.0, "tap":  ["E"]},
#       {"at": 4.5, "mouse": {"x": 0.5, "y": 0.5, "click": "L"}}
#     ]
#   }

param(
    [Parameter(Mandatory=$true)][string]$Exe,
    [Parameter(Mandatory=$true)][string]$Out,
    [int]$Duration = 20,
    [int]$Fps = 30,
    [string]$PlanJson = "",
    [string]$Ffmpeg = "",
    [string]$WindowTitleMatch = "",
    [int]$ScaleHeight = 0,
    [string]$Bitrate = "12M",
    [switch]$HideConsole,
    [switch]$KeepProcess,
    [switch]$Reuse,
    [int]$LaunchWaitSec = 25
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [System.Text.Encoding]::ASCII

function Say($s) { Write-Output $s }

# ---------------------------------------------------------------- P/Invoke
$sig = @'
using System;
using System.Runtime.InteropServices;
public class Cap {
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags, int dx, int dy, uint data, UIntPtr extra);
    [DllImport("user32.dll")] public static extern int MapVirtualKey(uint code, uint mapType);
    [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr ctx);
    [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow();
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr h, int c);
    public delegate bool EnumProc(IntPtr h, IntPtr p);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
}
'@
Add-Type -TypeDefinition $sig -ErrorAction Stop

# Physical pixels, not logical: without this GetWindowRect lies on scaled displays.
[void][Cap]::SetProcessDPIAware()
# Optionally hide the console window so it never lands on top of the capture region.
if ($HideConsole) {
    $con = [Cap]::GetConsoleWindow()
    if ($con -ne [IntPtr]::Zero) { [void][Cap]::ShowWindowAsync($con, 0) }
}

# ---------------------------------------------------------------- helpers
$VK = @{}
$letters = @('A','B','C','D','E','F','G','H','I','J','K','L','M','N','O','P','Q','R','S','T','U','V','W','X','Y','Z')
foreach ($c in $letters) { $VK[$c] = [int][char]$c }
for ($i = 0; $i -le 9; $i++) { $VK["$i"] = 0x30 + $i }
# raylib-style names (KEY_ONE, KEY_TWO ...) so plans can be lifted straight from source
$wordNum = @('ZERO','ONE','TWO','THREE','FOUR','FIVE','SIX','SEVEN','EIGHT','NINE')
for ($i = 0; $i -lt $wordNum.Count; $i++) { $VK[$wordNum[$i]] = 0x30 + $i }
$VK['SPACE'] = 0x20; $VK['ENTER'] = 0x0D; $VK['ESC'] = 0x1B; $VK['ESCAPE'] = 0x1B
$VK['TAB'] = 0x09; $VK['BACKSPACE'] = 0x08; $VK['SHIFT'] = 0x10; $VK['CTRL'] = 0x11
$VK['ALT'] = 0x12; $VK['UP'] = 0x26; $VK['DOWN'] = 0x28; $VK['LEFT'] = 0x25; $VK['RIGHT'] = 0x27
$VK['MINUS'] = 0xBD; $VK['EQUAL'] = 0xBB; $VK['LBRACKET'] = 0xDB; $VK['RBRACKET'] = 0xDD
$VK['SEMICOLON'] = 0xBA; $VK['COMMA'] = 0xBC; $VK['PERIOD'] = 0xBE; $VK['SLASH'] = 0xBF
$VK['F1']=0x70;$VK['F2']=0x71;$VK['F3']=0x72;$VK['F4']=0x73;$VK['F5']=0x74;$VK['F6']=0x75
$VK['F7']=0x76;$VK['F8']=0x77;$VK['F9']=0x78;$VK['F10']=0x79;$VK['F11']=0x7A;$VK['F12']=0x7B

$KEYUP  = 0x0002
$MB = @{ L=0x0002; R=0x0008; M=0x0020 }

function Resolve-Vk([string]$name) {
    $k = $name.ToUpper().Trim()
    if ($k.StartsWith('KEY_')) { $k = $k.Substring(4) }
    if ($k -eq 'ESCAPE') { $k = 'ESC' }
    if ($VK.ContainsKey($k)) { return [byte]$VK[$k] }
    throw "unknown key name: $name"
}

function KeyDown([string]$n) { $v = Resolve-Vk $n; [Cap]::keybd_event($v, [byte][Cap]::MapVirtualKey($v,0), 0, [UIntPtr]::Zero) }
function KeyUp([string]$n)   { $v = Resolve-Vk $n; [Cap]::keybd_event($v, [byte][Cap]::MapVirtualKey($v,0), $KEYUP, [UIntPtr]::Zero) }

function RelMouse($cx, $cy, $w, $h, $rx, $ry) {
    $x = [int]($cx + [double]$rx * $w)
    $y = [int]($cy + [double]$ry * $h)
    [void][Cap]::SetCursorPos($x, $y)
    Start-Sleep -Milliseconds 120
}

# ---------------------------------------------------------------- ffmpeg
# No hardcoded install paths: -Ffmpeg param, then PROMO_FFMPEG, then PATH.
function Find-Ffmpeg {
    if ($Ffmpeg -and (Test-Path $Ffmpeg)) { return $Ffmpeg }
    if ($env:PROMO_FFMPEG -and (Test-Path $env:PROMO_FFMPEG)) { return $env:PROMO_FFMPEG }
    $cands = @(
        (Join-Path $env:ProgramFiles "ffmpeg\bin\ffmpeg.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "ffmpeg\bin\ffmpeg.exe"),
        "C:\ffmpeg\bin\ffmpeg.exe"
    )
    foreach ($c in $cands) { if ($c -and (Test-Path $c)) { return $c } }
    $cmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

$ff = Find-Ffmpeg
if (-not $ff) { Say "ERR ffmpeg_not_found"; exit 4 }
Say "FFMPEG $ff"

# ---------------------------------------------------------------- launch
$proc = $null
$name = [System.IO.Path]::GetFileNameWithoutExtension($Exe)
$running = @(Get-Process -Name $name -ErrorAction SilentlyContinue)
if ($running.Count -gt 0 -and $Reuse) {
    $proc = $running[0]
    Say "ATTACH pid=$($proc.Id) name=$name"
} else {
    # Deterministic start: a stale instance would leave the game in some arbitrary
    # state and make every recording unreproducible.
    if ($running.Count -gt 0) {
        foreach ($r in $running) { try { $r.Kill() } catch {} }
        Start-Sleep -Milliseconds 1200
        Say "KILLED_STALE count=$($running.Count)"
    }
    if (-not (Test-Path $Exe)) { Say "ERR exe_not_found $Exe"; exit 2 }
    $proc = Start-Process -FilePath $Exe -WorkingDirectory (Split-Path $Exe) -PassThru
    Say "LAUNCH pid=$($proc.Id)"
}

$script:targetPid = $proc.Id
$main = [IntPtr]::Zero
$deadline = (Get-Date).AddSeconds($LaunchWaitSec)
while ((Get-Date) -lt $deadline -and $main -eq [IntPtr]::Zero) {
    Start-Sleep -Milliseconds 500
    $script:picked = $null
    $script:bestArea = 0
    $titleBytes = $null
    if ($WindowTitleMatch) { $titleBytes = [Text.Encoding]::UTF8.GetBytes($WindowTitleMatch) }
    $cb = [Cap+EnumProc]{
        param($h, $l)
        $ppid = 0
        [Cap]::GetWindowThreadProcessId($h, [ref]$ppid) | Out-Null
        if ($ppid -eq $script:targetPid -and [Cap]::IsWindowVisible($h) -and -not [Cap]::IsIconic($h)) {
            $r = New-Object Cap+RECT
            [Cap]::GetWindowRect($h, [ref]$r) | Out-Null
            $a = ($r.R - $r.L) * ($r.B - $r.T)
            if ($a -gt 200000 -and $a -gt $script:bestArea) {
                $skip = $false
                if ($script:wantTitle) {
                    $len = [Cap]::GetWindowTextLength($h)
                    $sb = New-Object System.Text.StringBuilder ($len + 2)
                    [Cap]::GetWindowText($h, $sb, $sb.Capacity) | Out-Null
                    if ($sb.ToString().IndexOf($script:wantTitle, [StringComparison]::OrdinalIgnoreCase) -lt 0) { $skip = $true }
                }
                if (-not $skip) { $script:bestArea = $a; $script:picked = $h }
            }
        }
        return $true
    }
    $script:wantTitle = $WindowTitleMatch
    [Cap]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null
    if ($script:picked) { $main = [IntPtr]$script:picked }
}

if ($main -eq [IntPtr]::Zero) {
    Say "ERR window_not_found pid=$($proc.Id)"
    if (-not $KeepProcess) { try { $proc.Kill() } catch {} }
    exit 3
}

[void][Cap]::ShowWindow($main, 5)          # SW_SHOW
[void][Cap]::ShowWindow($main, 9)          # SW_RESTORE
[void][Cap]::SetForegroundWindow($main)
Start-Sleep -Milliseconds 900

# ---------------------------------------------------------------- rect (client area)
$cr = New-Object Cap+RECT
[void][Cap]::GetClientRect($main, [ref]$cr)
$pt = New-Object Cap+POINT
$pt.X = 0; $pt.Y = 0
[void][Cap]::ClientToScreen($main, [ref]$pt)
$cw = $cr.R - $cr.L
$ch = $cr.B - $cr.T
if ($cw -lt 64 -or $ch -lt 64) { $cw = 1280; $ch = 720; $pt.X = 0; $pt.Y = 0 }
# H.264 requires even dimensions
$cw = $cw - ($cw % 2)
$ch = $ch - ($ch % 2)
Say "CLIENT x=$($pt.X) y=$($pt.Y) w=$cw h=$ch"

$w = $cw; $h = $ch
$cx = $pt.X; $cy = $pt.Y

# ---------------------------------------------------------------- input timeline
$events = New-Object System.Collections.ArrayList
$settleMs = 1200
if ($PlanJson -and (Test-Path $PlanJson)) {
    $plan = Get-Content -Raw -Encoding UTF8 $PlanJson | ConvertFrom-Json
    if ($plan.settle_ms) { $settleMs = [int]$plan.settle_ms }
    foreach ($s in $plan.steps) {
        $at = [double]$s.at
        if ($s.hold) {
            $ms = if ($s.ms) { [int]$s.ms } else { 1000 }
            foreach ($k in $s.hold) {
                [void]$events.Add([pscustomobject]@{ t = ($at * 1000); kind = 'down'; arg = $k })
                [void]$events.Add([pscustomobject]@{ t = ($at * 1000 + $ms); kind = 'up'; arg = $k })
            }
        }
        if ($s.tap) {
            foreach ($k in $s.tap) {
                [void]$events.Add([pscustomobject]@{ t = ($at * 1000); kind = 'down'; arg = $k })
                [void]$events.Add([pscustomobject]@{ t = ($at * 1000 + 90); kind = 'up'; arg = $k })
            }
        }
        if ($s.mouse) {
            [void]$events.Add([pscustomobject]@{
                t = ($at * 1000); kind = 'mousemove'
                rx = [double]$s.mouse.x; ry = [double]$s.mouse.y
                click = "$($s.mouse.click)"
            })
        }
    }
}
$sorted = @($events | Sort-Object t)
Say "PLAN events=$($sorted.Count) settle_ms=$settleMs"

# ---------------------------------------------------------------- start recording
$outDir = Split-Path -Parent $Out
if ($outDir -and -not (Test-Path $outDir)) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }
if (Test-Path $Out) { Remove-Item -Force $Out }

$vf = ""
if ($ScaleHeight -gt 0) { $vf = " -vf scale=-2:$ScaleHeight`:flags=neighbor" }

$ffArgs = @(
    '-hide_banner', '-nostdin', '-y',
    '-f', 'gdigrab', '-framerate', "$Fps", '-draw_mouse', '0',
    '-offset_x', "$cx", '-offset_y', "$cy",
    '-video_size', "${cw}x${ch}",
    '-i', 'desktop',
    '-t', "$Duration",
    '-c:v', 'h264_mf', '-b:v', $Bitrate,
    '-pix_fmt', 'yuv420p', '-movflags', '+faststart'
)
if ($vf) { $ffArgs += $vf.Trim().Split(' ') }
$ffArgs += $Out

Say "REC_START duration=$Duration fps=$Fps target=${cw}x${ch}"
$ffProc = Start-Process -FilePath $ff -ArgumentList $ffArgs -PassThru -NoNewWindow -RedirectStandardError "$Out.fflog"

$t0 = Get-Date
Start-Sleep -Milliseconds $settleMs

# bring the window back to front right before gameplay input
[void][Cap]::SetForegroundWindow($main)

$lastHold = @{}
foreach ($e in $sorted) {
    $target = $t0.AddMilliseconds($settleMs + $e.t)
    $wait = ($target - (Get-Date)).TotalMilliseconds
    if ($wait -gt 0) { Start-Sleep -Milliseconds ([int]$wait) }
    if ($e.kind -eq 'down')   { KeyDown $e.arg; $lastHold[$e.arg] = $true }
    elseif ($e.kind -eq 'up') { KeyUp $e.arg;   $lastHold.Remove($e.arg) | Out-Null }
    elseif ($e.kind -eq 'mousemove') {
        RelMouse $cx $cy $cw $ch $e.rx $e.ry
        if ($e.click -and $MB.ContainsKey($e.click.ToUpper())) {
            $f = $MB[$e.click.ToUpper()]
            [Cap]::mouse_event($f, 0, 0, 0, [UIntPtr]::Zero)
            Start-Sleep -Milliseconds 110
            [Cap]::mouse_event($f -bor 0x0004, 0, 0, 0, [UIntPtr]::Zero)
        }
        [void][Cap]::SetForegroundWindow($main)
    }
}
# release anything still held
foreach ($k in @($lastHold.Keys)) { KeyUp $k }

$remaining = $Duration - ((Get-Date) - $t0).TotalSeconds
if ($remaining -gt 0) { Start-Sleep -Milliseconds ([int]($remaining * 1000)) }

$ffProc.WaitForExit(30000) | Out-Null
$ffExit = $ffProc.ExitCode
Say "REC_STOP ffmpeg_exit=$ffExit"

if (-not $KeepProcess) {
    try { $proc.Kill(); Say "KILLED pid=$($proc.Id)" } catch { Say "KILL_FAILED" }
}

if (-not (Test-Path $Out)) { Say "ERR no_output"; if (Test-Path "$Out.fflog") { Get-Content "$Out.fflog" | Select-Object -Last 6 | ForEach-Object { Say "FFLOG $_" } }; exit 5 }
$size = (Get-Item $Out).Length
Say "OUT_PATH $Out"
Say "OUT_BYTES $size"
if ($size -lt 20000) { Say "WARN suspiciously_small" }
Say "OK"
