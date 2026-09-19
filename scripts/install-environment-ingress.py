from pathlib import Path
import subprocess
root=Path('/etc/systemd/system')
for name,listen,target in [('testing-axxium',19071,18871),('staging-axxium',19072,18872),('testing-knoxx',19082,18881),('staging-knoxx',19083,18882)]:
 unit=f'promethean-{name}'
 (root/f'{unit}.socket').write_text(f'''[Unit]
Description=Caddy-only ingress for {name}
[Socket]
ListenStream=172.31.255.1:{listen}
NoDelay=true
[Install]
WantedBy=sockets.target
''')
 (root/f'{unit}.service').write_text(f'''[Unit]
Description=Relay Caddy to the private {name} SSH forward
Requires={unit}.socket
After=network.target
[Service]
ExecStart=/usr/lib/systemd/systemd-socket-proxyd 127.0.0.1:{target}
DynamicUser=yes
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
IPAddressDeny=any
IPAddressAllow=172.31.255.2/32
IPAddressAllow=127.0.0.1/32
''')
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','--now','promethean-testing-axxium.socket','promethean-staging-axxium.socket','promethean-testing-knoxx.socket','promethean-staging-knoxx.socket'],check=True)

for port in [19071,19072,19082,19083]:
 subprocess.run(['ufw','allow','from','172.31.255.2','to','172.31.255.1','port',str(port),'proto','tcp','comment','Caddy environment ingress'],check=True)
print('Enabled four private environment ingress relays')
