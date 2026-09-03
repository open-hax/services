#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=/dev/null
. "$script_dir/mongodb-database-identity.sh"

fingerprint() {
  mongodb_database_fingerprint "$1" "$2"
}

expect_same() {
  local left right database
  left=$1
  right=$2
  database=$3
  [ "$(fingerprint "$left" "$database")" = "$(fingerprint "$right" "$database")" ] || {
    echo "equivalent Mongo database identities produced different fingerprints" >&2
    exit 1
  }
}

expect_different() {
  local left left_database right right_database
  left=$1
  left_database=$2
  right=$3
  right_database=$4
  [ "$(fingerprint "$left" "$left_database")" != \
    "$(fingerprint "$right" "$right_database")" ] || {
    echo "different Mongo database identities produced the same fingerprint" >&2
    exit 1
  }
}

expect_rejected() {
  if fingerprint "$1" openplanner >/dev/null 2>&1; then
    echo "invalid Mongo identity was accepted: $1" >&2
    exit 1
  fi
}

expect_same \
  'mongodb+srv://alice:old%40secret@Cluster.Example.NET/openplanner?retryWrites=true&w=majority' \
  'mongodb+srv://bob:new-secret@cluster.example.net/other?tls=false' \
  openplanner
expect_same \
  'mongodb+srv://cluster.example.net/db' \
  'mongodb+srv://cluster.example.net/db?srvServiceName=MongoDB&tls=false' \
  openplanner
expect_same \
  'mongodb://old:secret@DB-B:27017,db-a:27018/openplanner?retryWrites=true' \
  'mongodb://new:rotated@db-a:27018,db-b/ignored?tls=true' \
  openplanner
expect_same \
  'mongodb://old:secret@db-a/openplanner?replicaSet=production&tls=true' \
  'mongodb://new:rotated@DB-A:27017/ignored?retryWrites=false&replicaSet=production' \
  openplanner
expect_different \
  'mongodb+srv://user:secret@cluster-a.example.net/db' openplanner \
  'mongodb+srv://user:secret@cluster-b.example.net/db' openplanner
expect_different \
  'mongodb+srv://user:secret@cluster.example.net/db' openplanner \
  'mongodb+srv://user:secret@cluster.example.net/db' another_database
expect_different \
  'mongodb://cluster.example.net/db' openplanner \
  'mongodb+srv://cluster.example.net/db' openplanner
expect_different \
  'mongodb+srv://cluster.example.net/db?srvServiceName=mongodb' openplanner \
  'mongodb+srv://cluster.example.net/db?srvServiceName=alternate' openplanner
expect_different \
  'mongodb://db-a,db-b/db?replicaSet=one' openplanner \
  'mongodb://db-a,db-b/db?replicaSet=two' openplanner
expect_different \
  'mongodb://cluster/db' openplanner \
  'mongodb://cluster./db' openplanner

for invalid in \
  'https://cluster.example.net/db' \
  'mongodb://' \
  'mongodb://host,,other/db' \
  'mongodb://host:99999/db' \
  'mongodb://%2Ftmp%2Fproduction.sock/db' \
  'mongodb://Prod.sock/db' \
  'mongodb://prod.sock/db' \
  'mongodb://prod.sock:27017/db' \
  'mongodb://host/db?srvServiceName=alternate' \
  'mongodb+srv://host:27017/db' \
  'mongodb+srv://one,two/db' \
  'mongodb+srv://host/db?srvServiceName=one&srvServiceName=two' \
  'mongodb://host/db?replicaSet=one&replicaSet=two' \
  'mongodb+srv://host/db?tls=true;srvServiceName=alternate' \
  'mongodb+srv://host/db?srvServiceName=%61lternate' \
  'mongodb+srv://host/db?%73rvServiceName=alternate' \
  'mongodb://host/db?replicaSet=%72s0' \
  $'mongodb+srv://host/db?srvServiceNa\tme=alternate' \
  $'mongodb://host/db?replica\tSet=alternate'; do
  expect_rejected "$invalid"
done

result=$(fingerprint 'mongodb://cluster.example.net/openplanner' openplanner)
[[ "$result" =~ ^[0-9a-f]{64}$ ]]
echo "Mongo database identity fingerprint self-test: ok"
