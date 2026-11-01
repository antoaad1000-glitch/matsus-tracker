# Builds "Matsu's Tracker.exe" and the release zip into .\share
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$ico = Join-Path $root "tracker.ico"
$out = Join-Path $root "share\build"

python -m pip install --quiet pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name "Matsu's Tracker" `
    --icon $ico --add-data "$ico;." `
    --distpath $out --workpath "$env:TEMP\matsus_tracker_build" --specpath "$env:TEMP\matsus_tracker_build" `
    (Join-Path $root "tracker.py")

Copy-Item (Join-Path $root "release\README.txt") $out -Force
Compress-Archive -Path (Join-Path $out "Matsu's Tracker.exe"), (Join-Path $out "README.txt") `
    -DestinationPath (Join-Path $root "share\Matsus-Tracker.zip") -Force
Write-Host "Built share\Matsus-Tracker.zip"
