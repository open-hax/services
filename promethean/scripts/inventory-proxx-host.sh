#!/usr/bin/env bash
set -euo pipefail

: "${PROMETHEAN_SSH_HOST:=proxx.promethean.rest}"
: "${PROMETHEAN_SSH_USER:=error}"
: "${PROMETHEAN_SSH_KEY_PATH:=${HOME}/.ssh/id_ed25519}"

remote="${PROMETHEAN_SSH_USER}@${PROMETHEAN_SSH_HOST}"
out_dir="${1:-docs/reports/inventory}"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$out_dir"
raw="${out_dir}/promethean-host-runtime-inventory-${stamp}.txt"
json="${out_dir}/promethean-host-runtime-inventory-${stamp}.json"
md="${out_dir}/promethean-host-runtime-inventory-${stamp}.md"

ssh -i "${PROMETHEAN_SSH_KEY_PATH}" "$remote" 'bash -s' <<'REMOTE' > "$raw"
set -euo pipefail
printf 'host=%s\n' "$(hostname -f 2>/dev/null || hostname)"
printf '\n[docker]\n'
docker ps --format '{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}' | sort || true
printf '\n[pm2]\n'
export PATH=/usr/local/bin:$HOME/.local/bin:$PATH
~/.local/bin/pm2 jlist 2>/dev/null | node -e '
let s=""; process.stdin.on("data", d => s += d); process.stdin.on("end", () => {
  const rows = JSON.parse(s || "[]");
  for (const p of rows) {
    const e = p.pm2_env || {};
    console.log([p.name, e.status, p.pid, e.pm_cwd || e.cwd || "", e.pm_exec_path || e.script || ""].join("|"));
  }
});' || true
printf '\n[nginx-promethean-conf]\n'
sed -n '1,260p' ~/devel/services/openplanner/cloud/nginx/promethean.conf || true
printf '\n[listeners]\n'
ss -ltnp | grep -E ':(80|443|7777|17777|8000|18000|8789|8890|8891|5174)\\b' || true
REMOTE

python3 - "$raw" "$json" "$md" "$remote" <<'PY'
import json, re, sys
from datetime import datetime, timezone
raw_path, json_path, md_path, remote = sys.argv[1:]
text=open(raw_path).read()
sections={}
cur=None
for line in text.splitlines():
    if line.startswith('[') and line.endswith(']'):
        cur=line[1:-1]; sections[cur]=[]
    elif cur:
        sections[cur].append(line)
host_line=next((l for l in text.splitlines() if l.startswith('host=')), 'host=unknown')
containers=[]
for l in sections.get('docker', []):
    if not l.strip() or '|' not in l: continue
    name,image,status,ports=(l.split('|',3)+['','','',''])[:4]
    containers.append({'name':name,'image':image,'status':status,'ports':ports})
pm2=[]
for l in sections.get('pm2', []):
    if not l.strip() or '|' not in l: continue
    name,status,pid,cwd,exec_path=(l.split('|',4)+['','','','',''])[:5]
    pm2.append({'name':name,'status':status,'pid':pid,'cwd':cwd,'execPath':exec_path})
conf='\n'.join(sections.get('nginx-promethean-conf', []))
routes=[]
for block in re.split(r'\nserver\s*\{', '\n'+conf):
    names=re.findall(r'server_name\s+([^;]+);', block)
    targets=re.findall(r'proxy_pass\s+([^;]+);', block)
    if names:
        routes.append({'serverNames':names[0].split(), 'proxyPassTargets':sorted(set(targets))})
record={
    'generatedAt': datetime.now(timezone.utc).isoformat(),
    'sshTarget': remote,
    'host': host_line.split('=',1)[1],
    'runtime': {'dockerRunningCount': len(containers), 'pm2ProcessCount': len(pm2)},
    'containers': containers,
    'pm2Processes': pm2,
    'routes': routes,
    'notes': ['PM2 environment variables intentionally omitted to avoid leaking secrets.']
}
open(json_path,'w').write(json.dumps(record, indent=2)+'\n')
lines=['# Promethean host runtime inventory', '', f'- generated: {record["generatedAt"]}', f'- ssh: `{remote}`', f'- host: `{record["host"]}`', '', '## Containers', '']
for c in containers:
    lines.append(f'- `{c["name"]}` — {c["status"]} — `{c["ports"]}`')
lines += ['', '## PM2 processes', '']
for p in pm2:
    lines.append(f'- `{p["name"]}` — {p["status"]} — pid `{p["pid"]}` — cwd `{p["cwd"]}`')
lines += ['', '## Public routes', '']
for r in routes:
    lines.append(f'- `{", ".join(r["serverNames"])}` -> `{", ".join(r["proxyPassTargets"])}`')
lines += ['', '## Caveats', '', '- Secret-bearing PM2/container environment values are intentionally not recorded.', '- A route entry is a config declaration; live health must be checked separately.']
open(md_path,'w').write('\n'.join(lines)+'\n')
PY

rm -f "$raw"
echo "$json"
echo "$md"
