#!/usr/bin/env bash
# Run the eeschema GUI on a *real* X display so an agent's IPC edits are visible
# as they happen.
#
#   documentation/tools/live-gui.sh start [file.kicad_sch]
#   documentation/tools/live-gui.sh shot   out.png     # KiCad window only
#   documentation/tools/live-gui.sh record out.mp4     # until `stoprec`
#   documentation/tools/live-gui.sh stoprec
#   documentation/tools/live-gui.sh stop
#
# Differences from xvfb-gui.sh, which are the whole reason this is separate:
#
#   * It targets the developer's live session (default :1), so `stop` kills
#     ONLY eeschema. Never pkill Xvfb or the X server here -- that would take
#     down the user's desktop.
#
#   * Drive the editor over the IPC API, not xdotool. On a real display there is
#     a window manager and other applications, so a blind `mousemove X Y click`
#     lands on whatever happens to be under the pointer. Screenshots are taken
#     against the KiCad window id rather than root for the same reason.
#
#   * Library setup is seeded up front (see below), which also stops KiCad
#     showing its first-run "configure global libraries" wizard -- worth having
#     because dismissing that wizard is exactly the kind of synthetic input we
#     are trying to avoid on a live desktop.
#
# Two library traps, both of which fail *silently* (you get no symbols rather
# than an error) -- see documentation/03-gaps.md gap 4:
#
#   * A fresh settings profile has no global library tables. KiCad seeds them
#     from the stock template on first GUI run; nothing else does.
#   * ${KICAD10_SYMBOL_DIR} follows the build's install prefix, so a /usr/local
#     build resolves stock libraries to a path that does not exist.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DISPLAY_NUM="${AUTOKICAD_DISPLAY:-:1}"
# NOT /tmp: that is a tmpfs here (4.9G, backed by RAM) and this box has already
# been OOM-killed twice during this project. Video goes to real disk.
WORK="${AUTOKICAD_LIVE_WORK:-$HOME/autokicad-live}"
HEARTBEAT="$WORK/heartbeat"
REC_MAX_SECS="${AUTOKICAD_REC_SECS:-1200}"   # 20 min hard cap
REC_MAX_SIZE="${AUTOKICAD_REC_SIZE:-400M}"   # and a size cap, whichever comes first
STALE_SECS="${AUTOKICAD_STALE_SECS:-600}"    # watchdog: stop if the driver goes quiet
CFG="$WORK/config"
STOCK="${KICAD_STOCK_DATA:-/usr/share/kicad}"
# Build-tree KiCad is pinned to the SONAMEs it was linked against. Arch has
# moved on (protobuf 36, abseil 20260817, boost 1.92, poppler 26.08); keep the
# matching archived .so files under .deps/lib so we do not need a full rebuild
# just to open the editor.
DEPS_LIB="$REPO/.deps/lib"

seed_config() {
    mkdir -p "$CFG/10.99"

    if [[ ! -f "$CFG/10.99/kicad_common.json" ]]; then
        cat > "$CFG/10.99/kicad_common.json" <<'JSON'
{
  "api": { "enable_server": true, "interpreter_path": "" },
  "environment": { "show_warning_dialog": false },
  "do_not_show_again": {
    "update_check_prompt": true,
    "data_collection_prompt": true
  }
}
JSON
    fi

    # Without these the symbol chooser and any LIB_ID lookup come up empty.
    for t in sym-lib-table fp-lib-table design-block-lib-table; do
        if [[ -f "$STOCK/template/$t" && ! -f "$CFG/10.99/$t" ]]; then
            cp "$STOCK/template/$t" "$CFG/10.99/$t"
        fi
    done

    # LIBRARY_MANAGER::InvalidGlobalTables() checks SYMBOL, FOOTPRINT *and* DESIGN_BLOCK.
    # KiCad ships no design-block template, so that table is missing however carefully the
    # other two are seeded -- and one invalid table is enough to make the start wizard run.
    # The wizard is modal and sits inside OnPgmInit *before* SetReadyToReply(), so the IPC
    # server accepts connections and answers AS_NOT_READY to everything, forever. An empty
    # table is valid and keeps startup unattended.
    if [[ ! -f "$CFG/10.99/design-block-lib-table" ]]; then
        printf '(design_block_lib_table\n\t(version 7)\n)\n' > "$CFG/10.99/design-block-lib-table"
    fi
}

kicad_env() {
    local ld="$DEPS_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    env -u APPDIR -u APPIMAGE -u OWD -u ARGV0 -u WAYLAND_DISPLAY \
        DISPLAY="$DISPLAY_NUM" \
        GDK_BACKEND=x11 \
        QT_QPA_PLATFORM=xcb \
        LD_LIBRARY_PATH="$ld" \
        KICAD_RUN_FROM_BUILD_DIR=1 \
        KICAD_CONFIG_HOME="$CFG" \
        KICAD10_SYMBOL_DIR="$STOCK/symbols" \
        KICAD10_FOOTPRINT_DIR="$STOCK/footprints" \
        KICAD10_3DMODEL_DIR="$STOCK/3dmodels" \
        KICAD10_TEMPLATE_DIR="$STOCK/template" \
        "$@"
}

# eeschema owns several windows, including 10x10 and 200x200 utility ones. Match the
# editor by title; `search --class eeschema | tail -1` picks a dummy and screenshots
# come out as a 200x200 square.
winid() {
    DISPLAY="$DISPLAY_NUM" xdotool search --name "Schematic Editor" 2>/dev/null | head -1
}

# Any modal the wizard-suppression missed, so a blocked startup is diagnosable.
dialogs() {
    DISPLAY="$DISPLAY_NUM" xdotool search --name "KiCad Setup" 2>/dev/null | head -1
}

start() {
    seed_config
    mkdir -p "$WORK"

    DISPLAY="$DISPLAY_NUM" xdotool getdisplaygeometry >/dev/null

    if [[ ! -d "$DEPS_LIB" ]]; then
        echo "missing $DEPS_LIB — build-tree eeschema needs archived SONAMEs there" >&2
        return 1
    fi

    # Force X11. Plasma exports WAYLAND_DISPLAY; wx/GTK then prefer Wayland and
    # xdotool/import/ffmpeg-x11grab (this script's observe path) see no window.
    setsid nohup env -u APPDIR -u APPIMAGE -u OWD -u ARGV0 -u WAYLAND_DISPLAY \
        DISPLAY="$DISPLAY_NUM" \
        GDK_BACKEND=x11 \
        QT_QPA_PLATFORM=xcb \
        LD_LIBRARY_PATH="$DEPS_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
        KICAD_RUN_FROM_BUILD_DIR=1 \
        KICAD_CONFIG_HOME="$CFG" \
        KICAD10_SYMBOL_DIR="$STOCK/symbols" \
        KICAD10_FOOTPRINT_DIR="$STOCK/footprints" \
        KICAD10_3DMODEL_DIR="$STOCK/3dmodels" \
        KICAD10_TEMPLATE_DIR="$STOCK/template" \
        "$REPO/build/eeschema/eeschema" "$@" \
        > "$WORK/eeschema.log" 2>&1 < /dev/null &
    disown

    for _ in $(seq 1 60); do
        [[ -S /tmp/kicad/api.sock || -e /tmp/kicad/api.sock ]] && break
        sleep 1
    done

    echo "display  : $DISPLAY_NUM"
    echo "config   : $CFG"
    echo "window   : $(winid || echo none)"
    echo "modal    : $(dialogs || echo none)"
    echo "api sock : $(ls /tmp/kicad/api.sock 2>/dev/null || echo 'not listening')"
}

shot() {
    local out="${1:-$WORK/screen.png}" id
    id="$(winid || true)"

    mkdir -p "$(dirname "$out")"

    if [[ -z "$id" ]]; then
        # Fall back to whatever eeschema window exists, so a blocked startup is still
        # visible in the artefact rather than producing no file at all.
        id="$(DISPLAY="$DISPLAY_NUM" xdotool search --class eeschema 2>/dev/null | head -1)"
    fi

    if [[ -z "$id" ]]; then
        echo "no eeschema window found" >&2
        return 1
    fi

    # ImageMagick 7's import rejects `-window root` ("missing an image filename");
    # a numeric window id works.
    DISPLAY="$DISPLAY_NUM" import -window "$id" "$out"

    echo "wrote $out"
}

# Recording is capped three independent ways, because an overnight run that dies
# quietly must not fill a 94%-full disk:
#
#   -t   wall-clock cap, enforced by ffmpeg itself even if nothing else survives
#   -fs  file-size cap, same
#   watchdog  stops early when the driver stops touching the heartbeat, which is
#             what happens if the agent hits a usage limit mid-run
#
# 1280x720 at 8fps is ample for watching a schematic get drawn and keeps a
# 20-minute capture in the tens of megabytes.
record() {
    local out="${1:-$WORK/session.mp4}"
    mkdir -p "$WORK"
    touch "$HEARTBEAT"

    setsid nohup ffmpeg -y -f x11grab -framerate 8 -video_size 1920x1080 \
        -i "$DISPLAY_NUM" -t "$REC_MAX_SECS" -fs "$REC_MAX_SIZE" \
        -vf scale=1280:-2 -codec:v libx264 -preset veryfast -crf 30 \
        -pix_fmt yuv420p "$out" > "$WORK/ffmpeg.log" 2>&1 < /dev/null &
    disown

    watchdog &
    disown

    echo "recording $DISPLAY_NUM -> $out (max ${REC_MAX_SECS}s / $REC_MAX_SIZE)"
}

# Stops the recording if the heartbeat goes stale. Deliberately leaves eeschema
# running: if the agent stops, the user should still find the editor showing
# whatever state it reached.
watchdog() {
    while pgrep -x ffmpeg >/dev/null; do
        sleep 30
        [[ -f "$HEARTBEAT" ]] || continue
        local age=$(( $(date +%s) - $(stat -c %Y "$HEARTBEAT") ))
        if (( age > STALE_SECS )); then
            echo "watchdog: heartbeat ${age}s stale, stopping recording" >> "$WORK/watchdog.log"
            pkill -x ffmpeg 2>/dev/null || true
            break
        fi
    done
}

beat() { mkdir -p "$WORK"; touch "$HEARTBEAT"; }

stoprec() {
    pkill -x ffmpeg 2>/dev/null || true
    echo "recording stopped"
}

stop() {
    # ONLY eeschema. This runs against the user's live session: never touch the
    # X server, the window manager, or any other application.
    pkill -x eeschema 2>/dev/null || true
    echo "stopped eeschema (display left alone)"
}

case "${1:-}" in
    start)   shift; start "$@" ;;
    beat)    beat ;;
    shot)    shift; shot "$@" ;;
    record)  shift; record "$@" ;;
    stoprec) stoprec ;;
    stop)    stop ;;
    *) echo "usage: $0 {start|shot|record|stoprec|stop}" >&2; exit 2 ;;
esac
