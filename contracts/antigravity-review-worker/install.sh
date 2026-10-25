#!/bin/bash
set -euo pipefail
# Run only after review. Does not change user services, credentials, or sysctls.
[ "$(id -u)" -eq 0 ] || { echo 'root required' >&2; exit 1; }
proposal_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
profile_name=usr.local.libexec.antigravity-review.bwrap-0.9.0
source_binary=/usr/bin/bwrap
binary=/usr/local/libexec/antigravity-review/bwrap-0.9.0
policy=/etc/apparmor.d/$profile_name
expected=e318903862396f96de3df57264e0158682b952fd3fb53ac23d876413e7b30f71
[ ! -e "$binary" ] && [ ! -L "$binary" ] && [ ! -e "$policy" ] && [ ! -L "$policy" ] || { echo 'dedicated target already exists; inspect instead of overwriting' >&2; exit 1; }
[ "$(sha256sum -- "$source_binary" | cut -d ' ' -f 1)" = "$expected" ]
[ "$("$source_binary" --version)" = 'bubblewrap 0.9.0' ]
apparmor_parser --skip-kernel-load --skip-cache --Werror "$proposal_dir/$profile_name"
# Recheck the reviewed proposal before privileged installation.
[ "$(sha256sum -- "$proposal_dir/$profile_name" | cut -d ' ' -f 1)" = 1900091fd0844af11d072fe8f16b54cd486ae0fea22c4421bfca7186d8bc5221 ]
for directory in /usr/local /usr/local/libexec /usr/local/libexec/antigravity-review; do
  [ ! -L "$directory" ] || { echo "refusing symlink: $directory" >&2; exit 1; }
  if [ -e "$directory" ]; then
    [ -d "$directory" ] && [ "$(stat -c %u "$directory")" = 0 ]
    permissions=$(stat -c %a "$directory")
    (( (8#$permissions & 8#022) == 0 )) || { echo "directory writable by non-root: $directory" >&2; exit 1; }
  else
    install -d -o root -g root -m 0755 "$directory"
  fi
done
install -o root -g root -m 0755 "$source_binary" "$binary"
[ "$(sha256sum -- "$binary" | cut -d ' ' -f 1)" = "$expected" ]
install -o root -g root -m 0644 "$proposal_dir/$profile_name" "$policy"
[ "$(sha256sum -- "$policy" | cut -d ' ' -f 1)" = 1900091fd0844af11d072fe8f16b54cd486ae0fea22c4421bfca7186d8bc5221 ]
apparmor_parser --replace --skip-cache --Werror "$policy"
stat -c '%U:%G %a %n' "$binary" "$policy"
sha256sum -- "$binary" "$policy"
printf '%s\n' 'Installed dedicated profile; workers remain stopped until independent qualification.'
