#!/usr/bin/env bash
set -e
set -x

rm -rf build
mkdir build
cd build
cmake .. -GNinja "$@"
ninja-build -v
cd ..
