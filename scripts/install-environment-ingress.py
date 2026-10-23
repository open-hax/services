from pathlib import Path
import subprocess
from service_slots import load_slots
# Candidate declarations only; invoking this installer is a parent runtime action.
root=Path('/etc/systemd/system')
slots=[('testing-axxium',19071,18871),('staging-axxium',19072,18872),('testing-knoxx',19082,18881),('staging-knoxx',19083,18882)]
slots += [(env+'-proxx', row['caddy_port'], row['droplet_target']) for env,row in load_slots().items() if row['caddy_port'] is not None]
for name,listen,target in slots:
 unit=f'promethean-{name}'
 (root/f'{unit}.socket').write_text(f'''[Unit]
Description=Caddy-only ingress for {name}
[Socket]
ListenStream=172.31.255.1:{listen}
FreeBind=true
NoDelay=true
IPAddressDeny=any
IPAddressAllow=172.31.255.2/32
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
IPAddressAllow=127.0.0.1/32
''')
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','--now',*[f'promethean-{name}.socket' for name,_,_ in slots]],check=True)

for _,port,_ in slots:
 subprocess.run(['ufw','allow','from','172.31.255.2','to','172.31.255.1','port',str(port),'proto','tcp','comment','Caddy environment ingress'],check=True)
print('Enabled declared private environment ingress relays')
