#!/bin/bash
set -euo pipefail
# Stop both review user services BEFORE invoking this root script.
[ "$(id -u)" -eq 0 ] || { echo 'root required' >&2; exit 1; }
binary=/usr/local/libexec/antigravity-review/bwrap-0.9.0
policy=/etc/apparmor.d/usr.local.libexec.antigravity-review.bwrap-0.9.0
expected=e318903862396f96de3df57264e0158682b952fd3fb53ac23d876413e7b30f71
expected_policy=1900091fd0844af11d072fe8f16b54cd486ae0fea22c4421bfca7186d8bc5221
[ -f "$binary" ] && [ ! -L "$binary" ] && [ "$(sha256sum -- "$binary" | cut -d ' ' -f 1)" = "$expected" ]
[ -f "$policy" ] && [ ! -L "$policy" ] && [ "$(sha256sum -- "$policy" | cut -d ' ' -f 1)" = "$expected_policy" ]
if ! apparmor_parser --remove --skip-cache "$policy"; then
  printf '%s\n' 'Profile unload did not succeed; removing the verified files. Check kernel policy state before reinstalling.' >&2
fi
rm -- "$policy" "$binary"
rmdir -- /usr/local/libexec/antigravity-review
printf '%s\n' 'Dedicated profile file/copy removed; keep workers stopped or disabled until a qualified replacement exists.'
