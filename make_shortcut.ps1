# 바탕화면에 "Etsy 주문서 관리" 아이콘 만들기 (검은 창 없이 실행) — 바탕화면_아이콘_만들기.bat 이 실행
$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$desk = [Environment]::GetFolderPath('Desktop')
$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) { $pyw = (Get-Command pyw.exe -ErrorAction SilentlyContinue).Source }
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path $desk 'Etsy 주문서 관리.lnk'))
if ($pyw) {
    $lnk.TargetPath = $pyw
    $lnk.Arguments = '"' + (Join-Path $dir 'etsy_app.py') + '"'
} else {
    $lnk.TargetPath = Join-Path $dir '5_app.bat'
    $lnk.WindowStyle = 7
}
$lnk.WorkingDirectory = $dir
$lnk.IconLocation = (Join-Path $dir 'etsy_order.ico') + ',0'
$lnk.Description = 'Etsy 주문서 출력 관리'
$lnk.Save()
# 예전 바로 가기 정리
foreach ($old in @('5_app.bat - 바로 가기.lnk', '5_app.bat - Shortcut.lnk', '5_app.lnk')) {
    $p = Join-Path $desk $old
    if (Test-Path $p) { Remove-Item $p -Force }
}
if ($pyw) { Write-Output 'OK' } else { Write-Output 'OK_BAT' }
