#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Produce a credential-free Mongo cluster/database identity hash.

mongodb_database_fingerprint() {
  [ "$#" -eq 2 ] || return 2
  local uri=$1 database=$2 scheme remainder locator authority query option
  local option_key option_value seedlist seed host port port_number
  local normalized_seeds srv_service_name=mongodb srv_service_name_seen=0
  local replica_set='' replica_set_seen=0 identity query_delimiter='?'
  local -a options seeds normalized=()

  # WHATWG URL parsing strips literal tabs/newlines before option lookup.
  # Reject raw whitespace so it cannot hide an endpoint-selecting option from
  # this stricter parser. Legitimate credential/option data must be encoded.
  case "$uri" in
    *[[:space:]]*) return 1 ;;
  esac
  case "$uri" in
    mongodb://*) scheme=mongodb; remainder=${uri#mongodb://} ;;
    mongodb+srv://*) scheme=mongodb+srv; remainder=${uri#mongodb+srv://} ;;
    *) return 1 ;;
  esac
  [ -n "$database" ] || return 1
  case "$database" in
    *$'\n'*|*$'\r'*) return 1 ;;
  esac
  case "$remainder" in
    *\#*) return 1 ;;
  esac

  # The application selects its database through MONGODB_DB. URI path,
  # credentials, and ordinary retry/transport options do not define the
  # durable cluster/database identity this deployment gate compares.
  locator=$remainder
  query=
  if [[ "$locator" == *\?* ]]; then
    query=${locator#*\?}
    locator=${locator%%\?*}
  fi
  authority=${locator%%/*}
  seedlist=${authority##*@}
  [ -n "$seedlist" ] || return 1
  case "$seedlist" in
    *@*|*/*|*\?*|*\#*|*%*|*[[:space:]]*) return 1 ;;
  esac
  IFS=',' read -r -a seeds <<<"$seedlist"
  [ "${#seeds[@]}" -gt 0 ] || return 1
  if [ "$scheme" = mongodb+srv ] && [ "${#seeds[@]}" -ne 1 ]; then
    return 1
  fi

  # srvServiceName is not transport policy: it selects the DNS SRV record and
  # can therefore point the same hostname at a different cluster. Preserve it
  # in the identity, canonicalizing the documented default and DNS casing.
  if [ -n "$query" ]; then
    # MongoDB also accepts semicolons as option separators. Reject that legacy
    # spelling so an identity-bearing option cannot bypass the parser below.
    case "$query" in
      *';'*) return 1 ;;
    esac
    IFS='&' read -r -a options <<<"$query"
    for option in "${options[@]}"; do
      [ -n "$option" ] || return 1
      option_key=${option%%=*}
      case "$option_key" in
        *%*) return 1 ;;
      esac
      option_value=${option#*=}
      case "${option_key,,}" in
        srvservicename)
          [ "$scheme" = mongodb+srv ] || return 1
          [ "$srv_service_name_seen" -eq 0 ] || return 1
          [ "$option" != "$option_key" ] || return 1
          [[ "$option_value" =~ ^[A-Za-z0-9-]+$ ]] || return 1
          srv_service_name=${option_value,,}
          srv_service_name_seen=1
          ;;
        replicaset)
          [ "$replica_set_seen" -eq 0 ] || return 1
          [ "$option" != "$option_key" ] || return 1
          [[ "$option_value" =~ ^[A-Za-z0-9_-]+$ ]] || return 1
          replica_set=$option_value
          replica_set_seen=1
          ;;
      esac
    done
  fi

  for seed in "${seeds[@]}"; do
    [ -n "$seed" ] || return 1
    # The Node driver treats raw *.sock authorities as case-sensitive Unix
    # socket paths. They cannot share DNS case normalization safely.
    case "${seed,,}" in
      *.sock|*.sock:[0-9]*) return 1 ;;
    esac
    seed=${seed,,}
    host=
    port=
    if [[ "$seed" =~ ^(\[[^]]+\])(:([0-9]+))?$ ]]; then
      host=${BASH_REMATCH[1]}
      port=${BASH_REMATCH[3]:-}
    elif [[ "$seed" =~ ^([^:]+)(:([0-9]+))?$ ]]; then
      host=${BASH_REMATCH[1]}
      port=${BASH_REMATCH[3]:-}
    else
      return 1
    fi
    [ -n "$host" ] || return 1
    case "$host" in
      *@*|*/*|*\?*|*\#*|*[[:space:]]*) return 1 ;;
    esac
    if [ "$scheme" = mongodb+srv ] && [ -n "$port" ]; then
      return 1
    fi
    if [ -n "$port" ]; then
      [ "${#port}" -le 5 ] || return 1
      port_number=$((10#$port))
      [ "$port_number" -ge 1 ] && [ "$port_number" -le 65535 ] || return 1
      if [ "$port_number" -eq 27017 ]; then
        port=
      else
        port=:$port_number
      fi
    fi
    normalized+=("${host}${port}")
  done

  normalized_seeds=$(printf '%s\n' "${normalized[@]}" | LC_ALL=C sort -u | paste -sd, -)
  [ -n "$normalized_seeds" ] || return 1
  identity="${scheme}://${normalized_seeds}"
  if [ "$scheme" = mongodb+srv ]; then
    identity="${identity}?srvServiceName=${srv_service_name}"
    query_delimiter='&'
  fi
  if [ "$replica_set_seen" -eq 1 ]; then
    identity="${identity}${query_delimiter}replicaSet=${replica_set}"
  fi
  printf '%s\0%s' "$identity" "$database" \
    | sha256sum | awk '{print $1}'
}
