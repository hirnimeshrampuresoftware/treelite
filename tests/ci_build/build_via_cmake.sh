#!/usr/bin/env bash
set -e
set -x

rm -rf build
mkdir build
cd build
cmake .. -GNinja "$@"
if [ `uname -m` == "aarch64" ]; then
   ninja-build -v
else
   ninja -v
fi
cd ..
