# Run an ANU regression pack on the Pi from a /tmp upload (no sudo).
# Usage: powershell -File tools/pi-run-pack.ps1 -Pack tests/run_v0_9_0_regression.py
param(
  [Parameter(Mandatory = $true)][string]$Pack,
  [string]$HostName = "anu-vps1947@raspberrypi"
)
$ErrorActionPreference = "Stop"
$stamp = Get-Date -Format "yyyyMMddHHmmss"
$remoteDir = "/tmp/anu-pack-$stamp"
$script = @"
#!/bin/bash
set -euo pipefail
cd $remoteDir
export ANU_BASE=$remoteDir
python3 $Pack
"@
$localScript = Join-Path $env:TEMP "anu-pack-$stamp.sh"
# UTF8 no BOM — bash rejects BOM on shebang
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($localScript, $script, $utf8NoBom)
ssh $HostName "mkdir -p $remoteDir"
scp -r bin tests migrations config "${HostName}:${remoteDir}/"
scp $localScript "${HostName}:/tmp/anu-pack-run-$stamp.sh"
ssh $HostName "sed -i 's/\r$//' /tmp/anu-pack-run-$stamp.sh; bash /tmp/anu-pack-run-$stamp.sh"
