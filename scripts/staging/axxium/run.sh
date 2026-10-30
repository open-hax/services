#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Disposable, synthetic candidate-image OIDC integration; no host bind mounts.
# Usage: ./run.sh CANDIDATE_IMAGE_SHA POSTGRES_IMAGE_SHA
set -Eeuo pipefail
umask 077

# This fixture is destructive to its own temporary containers. Refuse shared
# deployments even if someone accidentally invokes it outside the CI job.
if [[ "${GITHUB_ACTIONS:-}" != true || "${RUNNER_ENVIRONMENT:-}" != github-hosted || -e /srv/open-hax ]]; then
  printf '%s\n' 'Refused: the synthetic integration requires an empty GitHub-hosted VM.' >&2
  exit 2
fi

candidate_expected='sha256:6aed20a3439dc29c6bcb43cf3f1e0bf41f65a72734975eb2db922898c45e4652'
postgres_expected='sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685'
if [[ $# != 2 || "$1" != "$candidate_expected" || "$2" != "$postgres_expected" ]]; then
  printf '%s\n' 'Refused: require the fixed candidate and PostgreSQL image IDs.' >&2
  exit 2
fi
candidate=$1
postgres=$2
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
test -f "$script_dir/probe.py"
command -v docker >/dev/null
python3 -c 'from cryptography.hazmat.primitives.asymmetric import rsa, padding' >/dev/null
[[ $(timeout --kill-after=3s 20s docker image inspect --format '{{.Id}}' "$candidate") == "$candidate" ]]
[[ $(timeout --kill-after=3s 20s docker image inspect --format '{{.Id}}' "$postgres") == "$postgres" ]]

# Randomized names and an unguessable private label bind cleanup to this run.
run_id=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
[[ "$run_id" =~ ^[0-9a-f]{32}$ ]]
printf 'Synthetic staging test run ID: %s\n' "$run_id"
label_key='codex.staging-test.id'
network_name="axxium-oidc-test-$run_id"
app_name="axxium-oidc-app-$run_id"
db_name="axxium-oidc-db-$run_id"
private_dir=$(mktemp -d /tmp/axxium-oidc-test.XXXXXXXX)
chmod 700 "$private_dir"
network_id=''
app_id=''
db_id=''
network_attempted=0
app_attempted=0
db_attempted=0
uncertain=0

# A failed or interrupted daemon mutation may have happened even if the client
# did not receive its reply. Retain all run-owned resources for investigation.
mutate() {
  local result
  uncertain=1
  if timeout --kill-after=3s 60s docker "$@"; then
    uncertain=0
    return 0
  else
    result=$?
    printf '%s\n' 'Docker mutation outcome uncertain; automatic cleanup is disabled.' >&2
    return "$result"
  fi
}

remove_container() {
  local id=$1 expected_name=$2 observed
  [[ -n "$id" && "$id" =~ ^[0-9a-f]{64}$ ]] || return 0
  observed=$(timeout --kill-after=3s 20s docker inspect --format '{{.Id}} {{.Name}} {{index .Config.Labels "codex.staging-test.id"}}' "$id" 2>/dev/null) || { printf '%s\n' 'Owned container missing or uninspectable; manual inspection may be needed.' >&2; return 1; }
  if [[ "$observed" != "$id /$expected_name $run_id" ]]; then
    printf '%s\n' 'Refused cleanup: container identity or ownership label changed.' >&2
    return 1
  fi
  mutate rm --force "$id" >/dev/null
}

remove_network() {
  local observed
  [[ -n "$network_id" && "$network_id" =~ ^[0-9a-f]{64}$ ]] || return 0
  observed=$(timeout --kill-after=3s 20s docker network inspect --format '{{.Id}} {{.Name}} {{index .Labels "codex.staging-test.id"}}' "$network_id" 2>/dev/null) || { printf '%s\n' 'Owned network missing or uninspectable; manual inspection may be needed.' >&2; return 1; }
  if [[ "$observed" != "$network_id $network_name $run_id" ]]; then
    printf '%s\n' 'Refused cleanup: network identity or ownership label changed.' >&2
    return 1
  fi
  mutate network rm "$network_id" >/dev/null
}

cleanup() {
  local original_status=$? cleanup_status=0
  trap - EXIT INT TERM
  if [[ "$uncertain" == 1 ]]; then
    printf 'Automatic cleanup refused after an uncertain Docker outcome; run ID %s. Private synthetic state retained in %s.\n' "$run_id" "$private_dir" >&2
    exit 1
  fi
  # A failed docker start may still have written its own exact cidfile.
  if [[ -z "$app_id" && -f "$private_dir/app.cid" ]]; then IFS= read -r app_id < "$private_dir/app.cid" || true; fi
  if [[ -z "$db_id" && -f "$private_dir/db.cid" ]]; then IFS= read -r db_id < "$private_dir/db.cid" || true; fi
  if [[ "$app_attempted" == 1 && -z "$app_id" ]]; then printf '%s\n' 'Candidate creation outcome unknown; inspect the exact run ID before recovery.' >&2; cleanup_status=1; fi
  if [[ "$db_attempted" == 1 && -z "$db_id" ]]; then printf '%s\n' 'Database creation outcome unknown; inspect the exact run ID before recovery.' >&2; cleanup_status=1; fi
  if [[ "$network_attempted" == 1 && -z "$network_id" ]]; then printf '%s\n' 'Network creation outcome unknown; inspect the exact run ID before recovery.' >&2; cleanup_status=1; fi
  remove_container "$app_id" "$app_name" || cleanup_status=1
  if [[ "$uncertain" == 1 ]]; then printf 'Cleanup outcome uncertain; run ID %s requires inspection. Private synthetic state retained in %s.\n' "$run_id" "$private_dir" >&2; exit 1; fi
  remove_container "$db_id" "$db_name" || cleanup_status=1
  if [[ "$uncertain" == 1 ]]; then printf 'Cleanup outcome uncertain; run ID %s requires inspection. Private synthetic state retained in %s.\n' "$run_id" "$private_dir" >&2; exit 1; fi
  remove_network || cleanup_status=1
  if [[ "$uncertain" == 1 ]]; then printf 'Cleanup outcome uncertain; run ID %s requires inspection. Private synthetic state retained in %s.\n' "$run_id" "$private_dir" >&2; exit 1; fi
  # Only files created by this run, in its fresh 0700 directory, are removed.
  rm -f -- "$private_dir/client-secret" "$private_dir/app.cid" "$private_dir/db.cid" "$private_dir/network.id" \
    "$private_dir/app-state" "$private_dir/port-bindings" "$private_dir/publish-all.json" "$private_dir/network-ports.json" "$private_dir/network-state" \
    "$private_dir/attachment" "$private_dir/ipam.json" "$private_dir/peers.json"
  rmdir -- "$private_dir" 2>/dev/null || { printf '%s\n' 'Private temporary directory retained for inspection.' >&2; cleanup_status=1; }
  if [[ $cleanup_status != 0 ]]; then
    printf 'Cleanup incomplete; run ID %s. Inspect exact labeled resources before recovery.\n' "$run_id" >&2
    exit 1
  fi
  exit "$original_status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

network_attempted=1
mutate network create --internal --label "$label_key=$run_id" "$network_name" > "$private_dir/network.id"
network_id=$(<"$private_dir/network.id")
[[ "$network_id" =~ ^[0-9a-f]{64}$ ]]
db_attempted=1
mutate run -d --pull never --cidfile "$private_dir/db.cid" --name "$db_name" \
  --label "$label_key=$run_id" --network "$network_id" --network-alias db \
  --user 70:70 --read-only --tmpfs /var/lib/postgresql/data:rw,uid=70,gid=70 \
  --tmpfs /var/run/postgresql:rw,uid=70,gid=70 --tmpfs /tmp:rw \
  --env POSTGRES_USER=fixture --env POSTGRES_DB=fixture --env POSTGRES_PASSWORD=fixture-only \
  "$postgres" >/dev/null
db_id=$(<"$private_dir/db.cid")
[[ "$db_id" =~ ^[0-9a-f]{64}$ ]]
ready=0
for _ in $(seq 1 45); do
  if timeout --kill-after=3s 10s docker exec --user 70:70 "$db_id" pg_isready -U fixture -d fixture >/dev/null 2>&1; then ready=1; break; else result=$?; fi
  if [[ "$result" == 124 || "$result" == 137 ]]; then uncertain=1; printf '%s\n' 'Database readiness observation timed out.' >&2; exit 1; fi
  sleep 1
done
[[ "$ready" == 1 ]] || { printf '%s\n' 'Synthetic PostgreSQL did not become ready.' >&2; exit 1; }

# All identity material is generated inside the candidate's private tmpfs.
# The generated values are test-only, never inherited from production.
read -r -d '' bootstrap <<'BOOTSTRAP' || true
set -eu
node <<'NODE'
const fs = require('node:fs');
const crypto = require('node:crypto');
const {privateKey} = crypto.generateKeyPairSync('rsa', {modulusLength: 2048});
const key = {...privateKey.export({format: 'jwk'}), alg: 'RS256', use: 'sig', kid: crypto.randomBytes(12).toString('hex')};
const random = () => crypto.randomBytes(48).toString('base64url');
const authority = {issuer: process.env.AXXIUM_PUBLIC_BASE_URL + '/oidc',
  clients: [{id: 'synthetic-staging-probe', secret: random(),
    'redirect-uris': ['http://127.0.0.1:8765/callback'],
    'refresh-enabled?': true, 'device-enabled?': false}],
  'cookie-keys': [random(), random()], jwks: {keys: [key]}, 'allow-loopback?': true};
fs.writeFileSync('/run/axxium/authority.json', JSON.stringify(authority), {mode: 0o600, flag: 'wx'});
NODE
exec node dist/server.js
BOOTSTRAP

app_attempted=1
mutate run -d --pull never --cidfile "$private_dir/app.cid" --name "$app_name" \
  --label "$label_key=$run_id" --network "$network_id" --user 1000:1000 \
  --read-only --tmpfs /tmp:rw --tmpfs /run/axxium:rw,noexec,nosuid,nodev,uid=1000,gid=1000,mode=0700 \
  --env DB_HOST=db --env DB_USER=fixture --env DB_NAME=fixture --env DB_PASSWORD=fixture-only \
  --env JWT_SECRET=fixture-only-identity-secret-never-production \
  --env AXXIUM_PUBLIC_BASE_URL=http://127.0.0.1:8787 \
  --env AXXIUM_OIDC_CONFIG_FILE=/run/axxium/authority.json \
  --entrypoint /bin/sh "$candidate" -c "$bootstrap" >/dev/null
app_id=$(<"$private_dir/app.cid")
[[ "$app_id" =~ ^[0-9a-f]{64}$ ]]
# No host port is published. Inspect the exact candidate and its unique,
# internal network before issuing requests to its container IP.
network_diagnostics() {
  local details result
  # Deliberately project only state and networking; never print environment,
  # application logs, private configuration, or identity material.
  if details=$(timeout --kill-after=3s 20s docker inspect --format '{{json .State.Status}} {{json .State.Running}} {{json .State.ExitCode}} {{json .NetworkSettings.Ports}} {{json .HostConfig.PortBindings}}' "$app_id" 2>/dev/null); then
    printf 'Candidate network diagnostics (status running exit ports bindings): %s\n' "$details" >&2
  else
    result=$?
    printf 'Candidate network diagnostics unavailable (status %s).\n' "$result" >&2
    if [[ "$result" == 124 || "$result" == 137 ]]; then uncertain=1; fi
  fi
}
inspect_value() {
  local result
  if timeout --kill-after=3s 20s docker "$@"; then return 0; else
    result=$?
    if [[ "$result" == 124 || "$result" == 137 ]]; then uncertain=1; fi
    return "$result"
  fi
}
if ! inspect_value inspect --format '{{.Id}} {{.Name}} {{index .Config.Labels "codex.staging-test.id"}} {{.Image}} {{len .NetworkSettings.Networks}}' "$app_id" > "$private_dir/app-state"; then network_diagnostics; exit 1; fi
app_state=$(<"$private_dir/app-state")
if [[ "$app_state" != "$app_id /$app_name $run_id $candidate 1" ]]; then printf '%s\n' 'Candidate identity or network count refused.' >&2; network_diagnostics; exit 1; fi
if ! inspect_value inspect --format '{{json .HostConfig.PortBindings}}' "$app_id" > "$private_dir/port-bindings"; then network_diagnostics; exit 1; fi
bindings=$(<"$private_dir/port-bindings")
if [[ "$bindings" != null && "$bindings" != '{}' ]]; then printf '%s\n' 'Candidate unexpectedly publishes host ports.' >&2; network_diagnostics; exit 1; fi
if ! inspect_value inspect --format '{{json .HostConfig.PublishAllPorts}}' "$app_id" > "$private_dir/publish-all.json"; then network_diagnostics; exit 1; fi
if ! inspect_value inspect --format '{{json .NetworkSettings.Ports}}' "$app_id" > "$private_dir/network-ports.json"; then network_diagnostics; exit 1; fi
if ! python3 - "$private_dir/publish-all.json" "$private_dir/network-ports.json" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding='utf-8') as f: publish_all = json.load(f)
    with open(sys.argv[2], encoding='utf-8') as f: ports = json.load(f)
    if publish_all is not False or not isinstance(ports, dict) or any(mapping is not None for mapping in ports.values()):
        raise ValueError('unsafe port publication')
except (OSError, ValueError, TypeError):
    sys.exit(1)
PY
then printf '%s\n' 'Candidate unexpectedly publishes host ports.' >&2; network_diagnostics; exit 1; fi
if ! inspect_value network inspect --format '{{.Id}} {{.Name}} {{index .Labels "codex.staging-test.id"}} {{.Internal}}' "$network_id" > "$private_dir/network-state"; then network_diagnostics; exit 1; fi
network_state=$(<"$private_dir/network-state")
if [[ "$network_state" != "$network_id $network_name $run_id true" ]]; then printf '%s\n' 'Network identity or isolation refused.' >&2; network_diagnostics; exit 1; fi
if ! inspect_value inspect --format "{{with index .NetworkSettings.Networks \"$network_name\"}}{{.NetworkID}} {{.IPAddress}}{{end}}" "$app_id" > "$private_dir/attachment"; then network_diagnostics; exit 1; fi
IFS=' ' read -r attachment_id transport_ip < "$private_dir/attachment" || true
if [[ "$attachment_id" != "$network_id" || -z "$transport_ip" ]]; then printf '%s\n' 'Candidate network attachment refused.' >&2; network_diagnostics; exit 1; fi
if ! inspect_value network inspect --format '{{json .IPAM.Config}}' "$network_id" > "$private_dir/ipam.json"; then network_diagnostics; exit 1; fi
if ! inspect_value network inspect --format '{{json .Containers}}' "$network_id" > "$private_dir/peers.json"; then network_diagnostics; exit 1; fi
if ! python3 - "$transport_ip" "$app_id" "$db_id" "$private_dir/ipam.json" "$private_dir/peers.json" <<'PY'
import ipaddress, json, sys
try:
    ip = ipaddress.ip_address(sys.argv[1])
    with open(sys.argv[4], encoding='utf-8') as f: ipam = json.load(f)
    with open(sys.argv[5], encoding='utf-8') as f: peers = json.load(f)
    if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_link_local:
        raise ValueError('unsafe candidate address')
    if not isinstance(ipam, list) or len(ipam) != 1:
        raise ValueError('unexpected network allocation')
    subnet = ipaddress.ip_network(ipam[0]['Subnet'], strict=True)
    if subnet.version != 4 or ip not in subnet or ip in (subnet.network_address, subnet.broadcast_address):
        raise ValueError('candidate address outside usable subnet')
    if ipam[0].get('Gateway') and ip == ipaddress.ip_address(ipam[0]['Gateway']):
        raise ValueError('candidate address is network gateway')
    if not isinstance(peers, dict) or set(peers) != {sys.argv[2], sys.argv[3]}:
        raise ValueError('unexpected network peers')
    if ipaddress.ip_interface(peers[sys.argv[2]]['IPv4Address']).ip != ip:
        raise ValueError('candidate address mismatch')
except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
    sys.exit(1)
PY
then printf '%s\n' 'Candidate IP, subnet, or network peers refused.' >&2; network_diagnostics; exit 1; fi
base='http://127.0.0.1:8787'
ready=0
for _ in $(seq 1 60); do
  if TRANSPORT_IP="$transport_ip" python3 -c 'import http.client,os,sys; c=http.client.HTTPConnection(os.environ["TRANSPORT_IP"],8787,timeout=1); c.request("GET","/health",headers={"Host":"127.0.0.1:8787"}); r=c.getresponse(); r.read(); sys.exit(0 if r.status==200 else 1)' >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[[ "$ready" == 1 ]] || { printf '%s\n' 'Synthetic candidate did not become ready.' >&2; exit 1; }

# Transfer only the generated synthetic client secret to a private host file;
# it is never included in process arguments or terminal output.
if timeout --kill-after=3s 20s docker exec "$app_id" node -e 'const fs=require("node:fs");process.stdout.write(JSON.parse(fs.readFileSync("/run/axxium/authority.json","utf8")).clients[0].secret)' > "$private_dir/client-secret"; then :; else
  result=$?
  if [[ "$result" == 124 || "$result" == 137 ]]; then uncertain=1; fi
  printf '%s\n' 'Synthetic client material could not be retrieved.' >&2
  exit 1
fi
chmod 600 "$private_dir/client-secret"
python3 "$script_dir/probe.py" --base "$base" --transport-ip "$transport_ip" --client-id synthetic-staging-probe --client-secret-file "$private_dir/client-secret"
python3 "$script_dir/handoff_probe.py" --db-id "$db_id" --app-id "$app_id" --network-id "$network_id" --run-id "$run_id"
printf 'PASS synthetic OIDC integration against fixed candidate image; run ID %s\n' "$run_id"
