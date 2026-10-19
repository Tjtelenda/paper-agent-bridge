#!/bin/sh
set -eu
# Host binary is for tests only. Tablet packaging requires a Qt cross-compilation toolchain.
cd "$(dirname "$0")"
: "${CXX:=g++}"
: "${BUILD_DIR:=/tmp/goodbot-device-build}"
: "${LIBS:=-lcurl}"
mkdir -p "$BUILD_DIR/backend"
# Compiler/linker flags intentionally use standard shell word splitting.
$CXX ${CPPFLAGS:-} ${CXXFLAGS:-} -std=c++17 -O2 -Wall -Wextra -Werror backend.cpp ${LDFLAGS:-} -o "$BUILD_DIR/backend/entry" $LIBS
if [ "${1:-}" = '--package' ]; then
    file "$BUILD_DIR/backend/entry" | grep -Eq 'aarch64|ARM' || { printf 'Refusing to package a host binary as a tablet app\n' >&2; exit 1; }
    : "${RCC:?Set RCC to the firmware-compatible Qt resource compiler}"
    : "${CMAKE_TOOLCHAIN_FILE:?Set CMAKE_TOOLCHAIN_FILE to your Qt cross-compilation toolchain}"
    "$RCC" --binary resources.qrc -o "$BUILD_DIR/resources.rcc"
    cmake -S native -B "$BUILD_DIR/native" \
        -DCMAKE_TOOLCHAIN_FILE="$CMAKE_TOOLCHAIN_FILE" \
        -DCMAKE_BUILD_TYPE=Release
    cmake --build "$BUILD_DIR/native" --parallel 2
    mkdir -p "$BUILD_DIR/qml/GoodBot/Bridge"
    cp "$BUILD_DIR/native/GoodBot/Bridge/libgoodbotbridgeplugin.so" native/qmldir \
        native/Session.qml native/Panel.qml "$BUILD_DIR/qml/GoodBot/Bridge/"
fi
printf 'Build output: %s\n' "$BUILD_DIR"
