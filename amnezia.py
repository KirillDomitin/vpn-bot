import asyncio
import base64
import ipaddress
import json
import re
import struct
import zlib
from typing import Set, Tuple
from config import CONTAINER_NAME, WG_INTERFACE, SERVER_IP, DNS, SUBNET


async def _run(cmd: str) -> str:
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\n{stderr.decode().strip()}")
    return stdout.decode().strip()


async def _docker_exec(command: str, server_ip: str = SERVER_IP) -> str:
    return await _run(
        f"ssh -o StrictHostKeyChecking=no root@{server_ip} "
        f"docker exec {CONTAINER_NAME} {command}"
    )


async def get_server_config(server_ip: str = SERVER_IP) -> str:
    return await _docker_exec(f"cat /opt/amnezia/awg/{WG_INTERFACE}.conf", server_ip)


async def get_psk(server_ip: str = SERVER_IP) -> str:
    return await _docker_exec("cat /opt/amnezia/awg/wireguard_psk.key", server_ip)


async def get_server_public_key_file(server_ip: str = SERVER_IP) -> str:
    return await _docker_exec("cat /opt/amnezia/awg/wireguard_server_public_key.key", server_ip)


def parse_server_info(config_text: str) -> dict:
    info = {}
    interface_section = config_text.split("[Peer]")[0]

    for key in ("PrivateKey", "ListenPort", "Address",
                "Jc", "Jmin", "Jmax",
                "S1", "S2", "S3", "S4",
                "H1", "H2", "H3", "H4"):
        m = re.search(rf"^{key}\s*=\s*(.+)$", interface_section, re.MULTILINE)
        if m:
            info[key] = m.group(1).strip()

    for key in ("I1", "I2", "I3", "I4", "I5"):
        m = re.search(rf"^#\s*{key}\s*=\s*(.*)$", interface_section, re.MULTILINE)
        info[key] = m.group(1).strip() if m else ""

    return info


def find_used_ips(config_text: str) -> Set[str]:
    ips = set()
    for m in re.finditer(r"AllowedIPs\s*=\s*([\d.]+)", config_text):
        ips.add(m.group(1))
    addr_m = re.search(r"Address\s*=\s*([\d.]+)", config_text)
    if addr_m:
        ips.add(addr_m.group(1))
    return ips


def allocate_ip(config_text: str) -> str:
    used = find_used_ips(config_text)
    network = ipaddress.IPv4Network(SUBNET, strict=False)
    for host in network.hosts():
        ip = str(host)
        if ip not in used and not ip.endswith(".0") and not ip.endswith(".1"):
            return ip
    raise RuntimeError("No free IPs available in subnet")


async def generate_keypair(server_ip: str = SERVER_IP) -> Tuple[str, str]:
    private_key = await _docker_exec("awg genkey", server_ip)
    proc = await asyncio.create_subprocess_shell(
        f"echo '{private_key}' | ssh -o StrictHostKeyChecking=no root@{server_ip} "
        f"docker exec -i {CONTAINER_NAME} awg pubkey",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    public_key = stdout.decode().strip()
    return private_key, public_key


async def get_server_public_key(server_private_key: str, server_ip: str = SERVER_IP) -> str:
    proc = await asyncio.create_subprocess_shell(
        f"echo '{server_private_key}' | ssh -o StrictHostKeyChecking=no root@{server_ip} "
        f"docker exec -i {CONTAINER_NAME} awg pubkey",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode().strip()


async def add_peer(public_key: str, client_ip: str, psk: str, server_ip: str = SERVER_IP):
    config_path = f"/opt/amnezia/awg/{WG_INTERFACE}.conf"
    peer_block = (
        f"\\n[Peer]\\n"
        f"PublicKey = {public_key}\\n"
        f"PresharedKey = {psk}\\n"
        f"AllowedIPs = {client_ip}/32\\n"
    )
    await _docker_exec(f"bash -c 'printf \"{peer_block}\" >> {config_path}'", server_ip)
    await _docker_exec(f"bash -c 'awg syncconf {WG_INTERFACE} <(awg-quick strip /opt/amnezia/awg/{WG_INTERFACE}.conf)'", server_ip)


async def remove_peer(public_key: str, server_ip: str = SERVER_IP):
    config_path = f"/opt/amnezia/awg/{WG_INTERFACE}.conf"
    config_text = await _docker_exec(f"cat {config_path}", server_ip)

    lines = config_text.split("\n")
    new_lines = []
    skip = False
    for line in lines:
        if line.strip() == "[Peer]":
            skip = False
            new_lines.append(line)
            continue
        if skip:
            if line.strip().startswith("["):
                skip = False
                new_lines.append(line)
            continue
        if line.strip().startswith("PublicKey") and public_key in line:
            skip = True
            while new_lines and new_lines[-1].strip() in ("[Peer]", ""):
                new_lines.pop()
            continue
        new_lines.append(line)

    new_config = "\n".join(new_lines)
    escaped = new_config.replace("'", "'\\''")
    await _docker_exec(f"bash -c 'echo '\"'\"'{escaped}'\"'\"' > {config_path}'", server_ip)
    await _docker_exec(f"bash -c 'awg syncconf {WG_INTERFACE} <(awg-quick strip /opt/amnezia/awg/{WG_INTERFACE}.conf)'", server_ip)


def build_vpn_url(
    private_key: str,
    public_key: str,
    client_ip: str,
    server_public_key: str,
    psk: str,
    listen_port: str,
    server_info: dict,
) -> str:
    dns_parts = DNS.replace(" ", "").split(",")
    dns1 = dns_parts[0] if len(dns_parts) > 0 else "1.1.1.1"
    dns2 = dns_parts[1] if len(dns_parts) > 1 else "1.0.0.1"

    awg_keys = ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4",
                "H1", "H2", "H3", "H4", "I1", "I2", "I3", "I4", "I5")

    conf_lines = [
        "[Interface]",
        f"Address = {client_ip}/32",
        f"DNS = {dns1}, {dns2}",
        f"PrivateKey = {private_key}",
    ]
    for key in awg_keys:
        if key in server_info:
            conf_lines.append(f"{key} = {server_info[key]}")
    conf_lines.extend([
        "",
        "[Peer]",
        f"PublicKey = {server_public_key}",
        f"PresharedKey = {psk}",
        "AllowedIPs = 0.0.0.0/0, ::/0",
        f"Endpoint = {SERVER_IP}:{listen_port}",
        "PersistentKeepalive = 25",
        "",
    ])
    config_str = "\n".join(conf_lines)

    last_config = {}
    for key in awg_keys:
        if key in server_info:
            last_config[key] = server_info[key]

    last_config.update({
        "allowed_ips": ["0.0.0.0/0", "::/0"],
        "clientId": public_key,
        "client_ip": client_ip,
        "client_priv_key": private_key,
        "client_pub_key": public_key,
        "config": config_str,
        "hostName": SERVER_IP,
        "mtu": "1280",
        "persistent_keep_alive": "25",
        "port": int(listen_port),
        "psk_key": psk,
        "server_pub_key": server_public_key,
    })

    awg_section = {}
    for key in awg_keys:
        if key in server_info:
            awg_section[key] = server_info[key]

    awg_section["last_config"] = json.dumps(last_config, indent=4) + "\n"
    awg_section["port"] = listen_port
    awg_section["protocol_version"] = "2"
    awg_section["subnet_address"] = SUBNET.split("/")[0]
    awg_section["transport_proto"] = "udp"

    payload = {
        "containers": [
            {
                "awg": awg_section,
                "container": "amnezia-awg2",
            }
        ],
        "defaultContainer": "amnezia-awg2",
        "description": "🇫🇮 Finland VPN",
        "dns1": dns1,
        "dns2": dns2,
        "hostName": SERVER_IP,
    }

    payload_json = json.dumps(payload)
    compressed = zlib.compress(payload_json.encode("utf-8"))
    header = struct.pack(">I", len(compressed))
    encoded = base64.urlsafe_b64encode(header + compressed).decode().rstrip("=")
    return f"vpn://{encoded}"


async def create_client(name: str) -> Tuple[str, str, str, str]:
    config_text = await get_server_config()
    server_info = parse_server_info(config_text)
    psk = await get_psk()

    client_ip = allocate_ip(config_text)
    private_key, public_key = await generate_keypair()
    server_public_key = await get_server_public_key(server_info["PrivateKey"])

    await add_peer(public_key, client_ip, psk)

    vpn_url = build_vpn_url(
        private_key=private_key,
        public_key=public_key,
        client_ip=client_ip,
        server_public_key=server_public_key,
        psk=psk,
        listen_port=server_info["ListenPort"],
        server_info=server_info,
    )
    return vpn_url, client_ip, private_key, public_key
