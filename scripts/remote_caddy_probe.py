import getpass, paramiko
host='103.214.172.30'; user='root'
pw=getpass.getpass('SSH password: ')
client=paramiko.SSHClient(); client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(host, username=user, password=pw, timeout=20, look_for_keys=False, allow_agent=False)
cmds={
 'caddy_inspect': "docker inspect momo-link-caddy --format '{{json .Mounts}}' 2>/dev/null || true",
 'caddy_files': "find /opt/momo-link -maxdepth 3 -type d -iname '*caddy*' -o -path '*caddy*' 2>/dev/null | sed -n '1,80p'",
 'dns_tools': "command -v dig || true; command -v nslookup || true; getent hosts mail.i7wap.xyz || true",
 'mail_ports_public': "ss -lntup '( sport = :25 or sport = :465 or sport = :587 or sport = :993 or sport = :143 or sport = :110 or sport = :995 or sport = :4190 )' 2>/dev/null || true"
}
for name,cmd in cmds.items():
    print('\n===== '+name+' =====')
    _,out,err=client.exec_command(cmd, timeout=60)
    print(out.read().decode(errors='replace').strip())
    e=err.read().decode(errors='replace').strip()
    if e: print('[stderr]\n'+e)
client.close()
