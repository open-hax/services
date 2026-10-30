#!/bin/sh
# Read-only guard for canonical writers. Check both fixed Axxium and Colab
# production holds; never clear either marker.
# A missing ancestor is normal on a new host; an inaccessible or aliased path
# cannot establish absence. The deployment queue prevents cooperating writers
# from creating a hold between this observation and their first effect.
set -eu
mode=ordinary
if [ "$#" -eq 1 ] && [ "$1" = --require-axxium-hold ]; then
  mode=recovery
elif [ "$#" -ne 0 ]; then
  exit 2
fi
refuse() { printf '%s\n' 'Axxium or Colab production transition is held or its hold state is unreadable; preserve the operation journal.' >&2; exit 74; }
for directory in /srv /srv/open-hax /srv/open-hax/services; do
  [ ! -L "$directory" ] || refuse
  if [ ! -e "$directory" ]; then
    [ "$mode" = ordinary ] && exit 0
    refuse
  fi
  if [ ! -d "$directory" ] || [ ! -x "$directory" ]; then refuse; fi
done
for service in axxium colab-mcp; do
  directory=/srv/open-hax/services/$service
  [ ! -L "$directory" ] || refuse
  if [ ! -e "$directory" ]; then
    [ "$service" != axxium ] || [ "$mode" != recovery ] || refuse
    continue
  fi
  if [ ! -d "$directory" ] || [ ! -x "$directory" ]; then refuse; fi
  case "$service" in
    axxium) marker=$directory/.axxium-production-pending ;;
    colab-mcp) marker=$directory/.colab-production-pending ;;
    *) refuse ;;
  esac
  if [ "$service" = axxium ] && [ "$mode" = recovery ]; then
    if [ ! -f "$marker" ] || [ -L "$marker" ]; then refuse; fi
  elif [ -e "$marker" ] || [ -L "$marker" ]; then
    refuse
  fi
done
