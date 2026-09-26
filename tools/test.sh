#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python_bin="${PYTHON:-python3}"
export UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1
bash tools/bootstrap.sh
lwip="${PICOUSB_DEPS_ROOT:-$PWD/build/deps}/pico-sdk/lib/lwip"
cmake -S firmware -B build/native -G Ninja -DDEVBOX_NATIVE=ON -DCMAKE_BUILD_TYPE=Debug \
  -DDEVBOX_LWIP_PATH="$lwip" \
  -DCMAKE_C_FLAGS="-fsanitize=address,undefined -fno-omit-frame-pointer --coverage"
cmake --build build/native --parallel 4
ctest --test-dir build/native --output-on-failure
"$python_bin" -m ruff check --config ruff.toml host tools/*.py
"$python_bin" -m ruff format --config ruff.toml --check host tools/*.py
"$python_bin" -m mypy --config-file host/pyproject.toml host/src
"$python_bin" -m pytest host/tests --cov=pico_devbox --cov-report=term-missing --cov-fail-under=85
