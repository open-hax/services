#!/usr/bin/env bash
# Root-owned files are created only inside this disposable alternate root.
set -euo pipefail
root=$(mktemp -d "${TMPDIR:-/tmp}/ingress-tmpfiles-fixture.XXXXXXXX")
cleanup() { sudo -n rm -rf -- "$root"; }
trap cleanup EXIT
mkdir -p "$root/etc/tmpfiles.d"
cp digitalocean/systemd/open-hax-ingress.conf "$root/etc/tmpfiles.d/open-hax-ingress.conf"
sudo -n chown -R root:root "$root"
sudo -n systemd-tmpfiles --root "$root" --create "$root/etc/tmpfiles.d/open-hax-ingress.conf"
lock="$root/run/open-hax/ingress/transaction.lock"
before=$(sudo -n stat -c '%d:%i:%u:%a' "$lock")
sudo -n systemd-tmpfiles --root "$root" --create "$root/etc/tmpfiles.d/open-hax-ingress.conf"
after=$(sudo -n stat -c '%d:%i:%u:%a' "$lock")
[ "$before" = "$after" ]
test "$(sudo -n stat -c '%u:%a' "$root/var/lib/open-hax/ingress/bootstrap")" = 0:700
echo 'PASS tmpfiles provisions durable control directories without replacing lock inode'
