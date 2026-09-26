#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
deps="${PICOUSB_DEPS_ROOT:-$PWD/build/deps}"
mkdir -p "$deps"
if [ ! -d "$deps/pico-sdk/.git" ]; then
  git clone --depth 1 --branch 2.2.0 https://github.com/raspberrypi/pico-sdk.git "$deps/pico-sdk"
fi
sdk="$deps/pico-sdk"
test "$(git -C "$sdk" rev-parse HEAD)" = a1438dff1d38bd9c65dbd693f0e5db4b9ae91779
git -C "$sdk" submodule update --init --depth 1 lib/cyw43-driver lib/lwip lib/mbedtls
if [ ! -f "$sdk/lib/tinyusb/src/tusb.c" ]; then
  git -C "$sdk" submodule update --init --depth 1 lib/tinyusb
fi
usb="$sdk/lib/tinyusb"
if [ "$(git -C "$usb" rev-parse HEAD)" != 331c263400dd5ccf77264cab1fa6bbd9f374b44e ]; then
  git -C "$usb" fetch --depth 1 origin tag 0.19.0
  git -C "$usb" checkout --detach 331c263400dd5ccf77264cab1fa6bbd9f374b44e
fi
python3 tools/patch_tinyusb.py "$usb/src/class/msc/msc_device.c"
python3 tools/patch_usb_observer.py "$usb/src/device/usbd.c"
