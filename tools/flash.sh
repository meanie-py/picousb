#!/usr/bin/env bash
# No tracing: Config/Initial consume secrets only inside provisioning tools.
set -euo pipefail
set +x
cd "$(dirname "$0")/.."
part=Firmware
mount_path=""
while (($#)); do
  case "$1" in
    --part) part="${2:?missing part}"; shift 2 ;;
    --mount) mount_path="${2:?missing BOOTSEL mount path}"; shift 2 ;;
    *) echo 'Usage: bash tools/flash.sh [--part Firmware|Config|Initial] [--mount /mnt/f]' >&2; exit 2 ;;
  esac
done
python_bin="${PYTHON:-python3}"
case "$part" in
  Firmware) source=build/firmware/picousb.uf2 ;;
  Config)
    "$python_bin" tools/config_block.py --input .env.flash --output build/config.uf2
    source=build/config.uf2 ;;
  Initial)
    "$python_bin" tools/provision.py --firmware build/firmware/picousb.uf2 --config .env.flash --output build/provisioned.uf2
    source=build/provisioned.uf2 ;;
  *) echo 'Invalid part.' >&2; exit 2 ;;
esac
test -f "$source" || { echo 'Build artifact missing.' >&2; exit 1; }
if [[ -n "$mount_path" ]]; then
  test -f "$mount_path/INFO_UF2.TXT" || { echo 'BOOTSEL mount unavailable.' >&2; exit 1; }
  # Validate the requested destination, not an arbitrary mounted filesystem.
  grep -q 'Board-ID: RP2350' "$mount_path/INFO_UF2.TXT" || { echo 'Expected RP2350 BOOTSEL volume.' >&2; exit 1; }
  cp -- "$source" "$mount_path/picousb.uf2"
elif command -v picotool >/dev/null 2>&1; then
  picotool load "$source"
  picotool reboot -a
else
  echo 'Install picotool or supply --mount with the RP2350 BOOTSEL volume.' >&2
  exit 1
fi
echo 'Transferred. Verify USB and Wi-Fi after reboot; no configuration values displayed.'
