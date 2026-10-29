#!/bin/sh
# Read-only guard for canonical production writers. Never clear this marker.
# A missing ancestor is normal on a new host; an inaccessible or aliased path
# cannot establish absence. The deployment queue prevents cooperating writers
# from creating a hold between this observation and their first effect.
set -eu
[ "$#" -eq 0 ] || exit 2
refuse() { printf '%s\n' 'Axxium production transition is held or its hold state is unreadable; preserve the operation journal.' >&2; exit 74; }
for directory in /srv /srv/open-hax /srv/open-hax/services /srv/open-hax/services/axxium; do
  [ ! -L "$directory" ] || refuse
  if [ ! -e "$directory" ]; then exit 0; fi
  [ -d "$directory" ] && [ -x "$directory" ] || refuse
done
marker=/srv/open-hax/services/axxium/.axxium-production-pending
if [ -e "$marker" ] || [ -L "$marker" ]; then refuse; fi
