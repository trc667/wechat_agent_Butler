# -*- coding: utf-8 -*-
"""UPnP 端口映射工具（纯标准库，零依赖）：不登录路由器后台，自动把本地端口暴露到公网。

用法：
    python upnp_map.py           # 添加映射（默认 9000）
    python upnp_map.py --port 9000
    python upnp_map.py --remove  # 删除映射
"""
import argparse
import socket
import sys
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

PORT = 9000
SSDP_ADDR = ("239.255.255.250", 1900)
SSDP_MSG = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    "MAN: \"ssdp:discover\"\r\n"
    "MX: 2\r\n"
    "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n"
    "\r\n"
)
NS = {"s": "http://schemas.xmlsoap.org/soap/envelope/"}
SERVICE_TYPES = [
    "urn:schemas-upnp-org:service:WANIPConnection:2",
    "urn:schemas-upnp-org:service:WANIPConnection:1",
    "urn:schemas-upnp-org:service:WANPPPConnection:1",
]


def get_local_ip():
    """拿到局域网 IP（默认路由所在网卡的地址）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 只建立 UDP 握手、不真正发包，让系统告诉我们出口网卡 IP
        s.connect(("223.5.5.5", 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        s.close()


def ssdp_discover(timeout=3):
    """SSDP 发现网关上 UPnP 设备，返回设备描述 XML 的 Location 列表。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", 0))
    sock.settimeout(timeout)
    locations = set()
    try:
        sock.sendto(SSDP_MSG.encode("utf-8"), SSDP_ADDR)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                break
            text = data.decode("utf-8", "ignore")
            for line in text.splitlines():
                if line.lower().startswith("location:"):
                    loc = line.split(":", 1)[1].strip()
                    if loc.lower().startswith("http"):
                        locations.add(loc)
    finally:
        sock.close()
    return locations


def fetch_xml(url, timeout=5):
    req = urllib.request.Request(url, headers={"User-Agent": "upnp-map/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def find_service(desc_url):
    """从设备描述 XML 里找到 WANIPConnection/WANPPPConnection 服务。"""
    root = ET.fromstring(fetch_xml(desc_url))
    base = desc_url.rsplit("/", 1)[0] + "/"
    for elem in root.iter():
        if elem.tag.endswith("serviceType") and elem.text in SERVICE_TYPES:
            service_type = elem.text
            control_url = None
            # 兄弟节点里找 controlURL
            parent = None
            for p in root.iter():
                if elem in list(p):
                    parent = p
                    break
            for child in parent:
                if child.tag.endswith("controlURL"):
                    control_url = child.text
                    break
            if control_url:
                return service_type, urllib.parse.urljoin(base, control_url)
    return None, None


def soap_call(control_url, service_type, action, body):
    """执行 SOAP 动作。成功返回响应 XML 根节点；失败抛出带错误码的异常。"""
    payload = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body><u:{action} xmlns:u="{service_type}">{body}'
        "</u:{action}></s:Body></s:Envelope>".format(
            action=action, service_type=service_type, body=body
        )
    )
    req = urllib.request.Request(
        control_url,
        data=payload.encode("utf-8"),
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": '"%s#%s"' % (service_type, action),
            "User-Agent": "upnp-map/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=6) as resp:
        xml_text = resp.read().decode("utf-8", "ignore")
    root = ET.fromstring(xml_text)
    err = root.find(".//s:Body//*[local-name()='errorCode']", NS)
    if err is not None:
        desc = root.find(".//*[local-name()='errorDescription']")
        raise RuntimeError("UPnP 错误 %s: %s" % (err.text, desc.text if desc is not None else "?"))
    return root


def get_external_ip(control_url, service_type):
    try:
        root = soap_call(control_url, service_type, "GetExternalIPAddress", "")
        ip = root.find(".//*[local-name()='NewExternalIPAddress']")
        return ip.text if ip is not None else "?"
    except Exception:
        return "?"


def add_mapping(control_url, service_type, port, local_ip):
    """添加映射，自动处理 725/726 两种常见路由器怪癖。"""
    body = (
        "<NewRemoteHost></NewRemoteHost>"
        "<NewExternalPort>{port}</NewExternalPort>"
        "<NewProtocol>TCP</NewProtocol>"
        "<NewInternalPort>{port}</NewInternalPort>"
        "<NewInternalClient>{ip}</NewInternalClient>"
        "<NewEnabled>1</NewEnabled>"
        "<NewPortMappingDescription>xiaoqi-callback</NewPortMappingDescription>"
        "<NewLeaseDuration>{lease}</NewLeaseDuration>"
    )
    try:
        # 先按永久租约(0)添加
        soap_call(control_url, service_type, "AddPortMapping",
                  body.format(port=port, ip=local_ip, lease=0))
    except RuntimeError as e:
        if "725" in str(e):
            # 该路由器只支持非永久租约
            soap_call(control_url, service_type, "AddPortMapping",
                      body.format(port=port, ip=local_ip, lease=86400))
        elif "718" in str(e):
            raise RuntimeError("端口 %d 已有映射（可能是之前添加的残留，可先 --remove）" % port)
        else:
            raise


def remove_mapping(control_url, service_type, port):
    body = (
        "<NewRemoteHost></NewRemoteHost>"
        "<NewExternalPort>{port}</NewExternalPort>"
        "<NewProtocol>TCP</NewProtocol>"
    ).format(port=port)
    try:
        soap_call(control_url, service_type, "DeletePortMapping", body)
    except RuntimeError as e:
        if "714" in str(e):
            print("[*] 本来就没有这个映射")
        else:
            raise


def main():
    parser = argparse.ArgumentParser(description="UPnP 端口映射工具（不登录路由器后台，纯标准库）")
    parser.add_argument("--port", type=int, default=PORT, help="要映射的端口（默认 9000）")
    parser.add_argument("--remove", action="store_true", help="删除已有映射而不是添加")
    args = parser.parse_args()

    local_ip = get_local_ip()
    if not local_ip:
        print("[-] 获取本机局域网 IP 失败")
        sys.exit(1)
    print("[*] 本机局域网 IP:", local_ip)

    print("[*] 正在扫描路由器（等几秒）...")
    locations = ssdp_discover()
    if not locations:
        print("[-] 没发现支持 UPnP 的路由器/光猫。")
        print("    可能原因：路由器没开 UPnP 功能，或不支持 UPnP。")
        print("    这种情况只能手动进路由器后台做端口转发（或 DMZ 指向本机），告诉我我们转手动方案。")
        sys.exit(1)

    service_type, control_url = None, None
    for loc in locations:
        try:
            service_type, control_url = find_service(loc)
        except Exception:
            continue
        if control_url:
            break
    if not control_url:
        print("[-] 找到了 UPnP 设备，但没找到 WAN 连接服务，无法映射。")
        sys.exit(1)
    print("[+] 找到网关服务，正在查询公网 IP...")

    pub_ip = get_external_ip(control_url, service_type)
    print("[*] 公网 IP:", pub_ip)

    if args.remove:
        remove_mapping(control_url, service_type, args.port)
        print("[+] 已删除 TCP", args.port, "的映射")
        sys.exit(0)

    try:
        add_mapping(control_url, service_type, args.port, local_ip)
    except RuntimeError as e:
        print("[-] 添加映射失败:", e)
        sys.exit(1)
    print("[+] 映射成功: 公网", pub_ip + ":" + str(args.port), "-> 本机", local_ip + ":" + str(args.port))
    print("    企微回调 URL 填: http://" + pub_ip + ":" + str(args.port) + "/wechat/callback")


if __name__ == "__main__":
    main()
