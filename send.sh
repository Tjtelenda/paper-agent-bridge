#!/bin/sh
# Device-side development client; credentials never enter QML or argv.
set -eu
config=${REMARKABLE_CONFIG_DIR:-/home/root/.config/goodbot2-remarkable}
url=$(cat "$config/url")
token=$(cat "$config/token")
action=${1-}
case "$url" in
    https://*) protocols='=https' ;;
    http://127.0.0.1:*)
        [ "${REMARKABLE_LOCAL_TEST:-}" = 1 ] || exit 2
        protocols='=http' ;;
    *) echo 'HTTPS endpoint required' >&2; exit 2 ;;
esac
case "$token" in ''|*[!A-Za-z0-9_-]*) exit 2 ;; esac
[ "${#token}" -ge 32 ] && [ "${#token}" -le 128 ]
url=${url%/}
case "$action" in
    capture) endpoint=/v1/captures; payload_file=${2-} ;;
    conversation) endpoint=/v1/conversations; payload_file=${2-} ;;
    reply) endpoint=/v1/replies; payload_file=${2-} ;;
    inbox)
        case "${2-0}" in ''|*[!0-9]*) exit 2 ;; esac
        endpoint="/v1/inbox?after=${2-0}" ;;
    events)
        case "${2-0}" in ''|*[!0-9]*) exit 2 ;; esac
        endpoint="/v1/events?after=${2-0}&wait=20" ;;
    *) echo 'usage: send.sh {capture|conversation|reply} PRIVATE_JSON_FILE | {inbox|events} [cursor]' >&2; exit 2 ;;
esac
# No redirect following, insecure TLS, shell evaluation, or automatic resend.
if [ "$action" = capture ] || [ "$action" = conversation ] || [ "$action" = reply ]; then
    [ -n "$payload_file" ] && [ -f "$payload_file" ] || exit 2
    printf 'Authorization: Bearer %s\n' "$token" |
        curl --silent --show-error --fail-with-body --proto "$protocols" \
            --connect-timeout 10 --max-time 30 --header @- \
            --header 'Content-Type: application/json' --data-binary "@$payload_file" "$url$endpoint"
else
    printf 'Authorization: Bearer %s\n' "$token" |
        curl --silent --show-error --fail-with-body --proto "$protocols" \
            --connect-timeout 10 --max-time 30 --header @- "$url$endpoint"
fi
