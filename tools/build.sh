#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash tools/bootstrap.sh
sdk="${PICOUSB_DEPS_ROOT:-$PWD/build/deps}/pico-sdk"
build_dir="${PICOUSB_BUILD_DIR:-$PWD/build/firmware}"
cmake -S firmware -B "$build_dir" -G Ninja -DPICO_SDK_PATH="$sdk" -DPICO_BOARD=pico2_w -DCMAKE_BUILD_TYPE=Release -DPICOUSB_DIAGNOSTICS=OFF
cmake --build "$build_dir" --parallel 4
if [ "$build_dir" != "$PWD/build/firmware" ]; then
  mkdir -p build/firmware
  cp "$build_dir/picousb.uf2" "$build_dir/picousb.bin" "$build_dir/picousb.elf" build/firmware/
fi
python3 tools/check_image.py build/firmware/picousb.bin
