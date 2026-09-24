#!/usr/bin/env bash
# Run a KiCad GUI from the build tree on a headless X display.
#
#   documentation/tools/xvfb-gui.sh start [board.kicad_pcb]
#   documentation/tools/xvfb-gui.sh shot  out.png
#   documentation/tools/xvfb-gui.sh stop
#
# Requires: xorg-server-xvfb, xdotool, imagemagick.
#
# Notes learned the hard way:
#
#   * No window manager runs under Xvfb, so xdotool's windowactivate and
#     key --window do nothing useful ("XGetInputFocus returned the focused
#     window of 1"). Drive the UI with `mousemove X Y click 1` instead, which
#     does not depend on focus.
#
#   * KiCad shows its first-run setup wizard whenever the config is fresh, and
#     cancelling it does not reliably persist "already shown" -- expect to
#     dismiss it (Cancel, then Yes) on each new config directory.
#
#   * KICAD_CONFIG_HOME isolates settings so the developer's real ~/.config/kicad
#     is untouched. api.enable_server must be true in kicad_common.json for the
#     GUI to start its IPC server.
#
#   * The GUI's API server listens on its own default path, /tmp/kicad/api.sock.
#     KICAD_API_SOCKET is what KiCad *passes to plugins*; setting it does not
#     move the server.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DISPLAY_NUM="${AUTOKICAD_DISPLAY:-:99}"
WORK="${AUTOKICAD_GUI_WORK:-/tmp/autokicad-gui}"
CFG="$WORK/config"
DEPS_LIB="$REPO/.deps/lib"

start() {
    mkdir -p "$WORK" "$CFG/10.99"

    if [[ ! -f "$CFG/10.99/kicad_common.json" ]]; then
        cat > "$CFG/10.99/kicad_common.json" <<'JSON'
{
  "api": { "enable_server": true, "interpreter_path": "" },
  "environment": { "show_warning_dialog": false }
}
JSON
    fi

    if ! pgrep -x Xvfb >/dev/null; then
        setsid nohup Xvfb "$DISPLAY_NUM" -screen 0 1600x1000x24 -nolisten tcp \
            > "$WORK/xvfb.log" 2>&1 < /dev/null &
        disown
        sleep 3
    fi

    DISPLAY="$DISPLAY_NUM" xdotool getdisplaygeometry >/dev/null

    if [[ ! -d "$DEPS_LIB" ]]; then
        echo "missing $DEPS_LIB — build-tree pcbnew needs archived SONAMEs there" >&2
        return 1
    fi

    setsid nohup env -u APPDIR -u APPIMAGE -u OWD -u WAYLAND_DISPLAY \
        DISPLAY="$DISPLAY_NUM" \
        GDK_BACKEND=x11 \
        QT_QPA_PLATFORM=xcb \
        LD_LIBRARY_PATH="$DEPS_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
        KICAD_RUN_FROM_BUILD_DIR=1 \
        KICAD_CONFIG_HOME="$CFG" \
        "$REPO/build/pcbnew/pcbnew" "$@" \
        > "$WORK/pcbnew.log" 2>&1 < /dev/null &
    disown

    sleep 20
    echo "display  : $DISPLAY_NUM"
    echo "config   : $CFG"
    echo "windows  : $(DISPLAY=$DISPLAY_NUM xdotool search --name '.' getwindowname %@ 2>/dev/null | tr '\n' ' ')"
    echo "api sock : $(ls /tmp/kicad/api.sock 2>/dev/null || echo 'not listening')"
}

shot() {
    DISPLAY="$DISPLAY_NUM" import -window root "${1:-$WORK/screen.png}"
    echo "wrote ${1:-$WORK/screen.png}"
}

stop() {
    pkill -x pcbnew 2>/dev/null || true
    pkill -x Xvfb 2>/dev/null || true
    echo "stopped"
}

case "${1:-}" in
    start) shift; start "$@" ;;
    shot)  shift; shot "$@" ;;
    stop)  stop ;;
    *) echo "usage: $0 {start [board]|shot [out.png]|stop}" >&2; exit 2 ;;
esac
