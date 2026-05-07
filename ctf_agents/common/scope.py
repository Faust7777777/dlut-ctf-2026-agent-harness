from __future__ import annotations

from urllib.parse import urlparse
import ipaddress, socket

class ScopeError(RuntimeError):
    pass


def _host_allowed_by_domain(host: str, allowed_domains: list[str]) -> bool:
    host = host.lower().strip(".")
    return any(host == d.lower().strip(".") or host.endswith("." + d.lower().strip(".")) for d in allowed_domains)


def _host_allowed_by_cidr(host: str, allowed_cidrs: list[str]) -> bool:
    if not allowed_cidrs: return False
    nets = [ipaddress.ip_network(c, strict=False) for c in allowed_cidrs]
    try:
        ips = [host] if ipaddress.ip_address(host) else []
    except ValueError:
        try:
            ips = [x[4][0] for x in socket.getaddrinfo(host, None)]
        except socket.gaierror:
            return False
    return any(ipaddress.ip_address(ip_s) in n for ip_s in ips for n in nets)


def assert_url_in_scope(url: str, scope_cfg: dict) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ScopeError(f"只允许 http/https URL: {url}")
    host = parsed.hostname or ""
    allowed_domains = scope_cfg.get("allowed_domains") or []
    allowed_cidrs = scope_cfg.get("allowed_cidrs") or []
    if not allowed_domains and not allowed_cidrs:
        raise ScopeError("未配置 allowed_domains/allowed_cidrs，拒绝联网操作")
    if _host_allowed_by_domain(host, allowed_domains) or _host_allowed_by_cidr(host, allowed_cidrs): return
    raise ScopeError(f"目标不在比赛 scope 白名单内: {host}")
