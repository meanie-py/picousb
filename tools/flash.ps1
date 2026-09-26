param(
    [ValidateSet('Firmware', 'Config', 'Initial')][string]$Part = 'Firmware',
    [string]$Drive = 'F:\',
    [string]$Picotool = ''
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $Picotool) {
    $bundled = Join-Path $root 'build\tools\picotool\picotool\picotool.exe'
    $installed = Get-Command picotool -ErrorAction SilentlyContinue
    if ($installed) { $Picotool = $installed.Source }
    elseif (Test-Path -LiteralPath $bundled) { $Picotool = $bundled }
}
if (-not $Picotool -and -not (Test-Path -LiteralPath (Join-Path $Drive 'INFO_UF2.TXT'))) {
    throw 'BOOTSEL drive unavailable. Hold BOOTSEL while connecting the Pico.'
}
if ($Part -eq 'Initial') {
    & python (Join-Path $PSScriptRoot 'provision.py') --firmware (Join-Path $root 'build/firmware/picousb.uf2') --config (Join-Path $root '.env.flash') --output (Join-Path $root 'build/provisioned.uf2')
    if ($LASTEXITCODE -ne 0) { throw 'Provisioning failed; values suppressed.' }
    $source = Join-Path $root 'build/provisioned.uf2'
} elseif ($Part -eq 'Config') {
    & python (Join-Path $PSScriptRoot 'config_block.py') --input (Join-Path $root '.env.flash') --output (Join-Path $root 'build/config.uf2')
    if ($LASTEXITCODE -ne 0) { throw 'Provisioning failed; values suppressed.' }
    $source = Join-Path $root 'build/config.uf2'
} else {
    $source = Join-Path $root 'build/firmware/picousb.uf2'
}
if ($Picotool) {
    & $Picotool load $source
    if ($LASTEXITCODE -ne 0) { throw 'USB bootloader load failed.' }
    & $Picotool reboot -a
    if ($LASTEXITCODE -ne 0) { throw 'Application reboot failed.' }
} else {
    Copy-Item -LiteralPath $source -Destination (Join-Path $Drive 'picousb.uf2')
}
Write-Output 'Firmware/config transferred; no configuration values displayed. Verify USB and Wi-Fi after reboot.'
