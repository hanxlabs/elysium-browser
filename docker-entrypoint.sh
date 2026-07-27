#!/bin/sh
set -eu

display="${DISPLAY:-:99}"
display_number="${display#:}"
display_number="${display_number%%.*}"
lock_file="/tmp/.X${display_number}-lock"
socket_file="/tmp/.X11-unix/X${display_number}"

mkdir -p /tmp/.X11-unix
rm -f "${lock_file}" "${socket_file}"

echo "[elysium-browser] starting Xvfb on ${display}"
Xvfb "${display}" -screen 0 1920x1080x24 -nolisten tcp -ac &
xvfb_pid=$!

attempt=0
while [ "${attempt}" -lt 50 ]; do
    if [ -S "${socket_file}" ]; then
        echo "[elysium-browser] Xvfb ready on ${display}, pid=${xvfb_pid}"
        export DISPLAY="${display}"
        echo "[elysium-browser] starting application: $*"
        exec "$@"
    fi
    if ! kill -0 "${xvfb_pid}" 2>/dev/null; then
        echo "[elysium-browser] ERROR: Xvfb exited before becoming ready" >&2
        wait "${xvfb_pid}" || true
        exit 1
    fi
    attempt=$((attempt + 1))
    sleep 0.1
done

echo "[elysium-browser] ERROR: Xvfb startup timed out on ${display}" >&2
kill "${xvfb_pid}" 2>/dev/null || true
wait "${xvfb_pid}" 2>/dev/null || true
exit 1
