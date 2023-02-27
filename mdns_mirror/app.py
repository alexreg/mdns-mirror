import locale
import logging
import sys
from ipaddress import IPv4Address, IPv6Address
from signal import Signals, signal
from threading import Event
from time import sleep
from types import FrameType
from typing import *

import dns
import dns.message
import dns.name
import dns.query
import dns.rdatatype
import dns.resolver
import dns.tsigkeyring
import dns.update
import dns.xfr
import dns.zone
from typer_cloup import *
from zeroconf import (
    IPVersion,
    ServiceBrowser,
    ServiceInfo,
    ServiceStateChange,
    Zeroconf,
    ZeroconfServiceTypes,
)
from zeroconf.const import _SERVICE_TYPE_ENUMERATION_NAME

from .common import EllipsisType, get_show_default, init_logging

MAX_RETRIES: int = 3
GET_SERVICE_INFO_TIMEOUT: int = 10000

_T = TypeVar("_T")

app = Typer(
    context_settings=Context.settings(
        help_option_names=["--help", "-h"],
    ),
)


class ServerInfo:
    def __init__(
        self,
        ipv4_address: Optional[IPv4Address] = None,
        ipv6_address: Optional[IPv6Address] = None,
        ttl: Optional[int] = None,
    ) -> None:
        self.ipv4_address = ipv4_address
        self.ipv6_address = ipv6_address
        self.ttl = ttl


def retry(callable: Callable[[], Optional[_T]], max_retries: int = MAX_RETRIES) -> _T:
    for n in range(MAX_RETRIES):
        result = callable()
        if result is not None:
            return result

    raise RuntimeError(f"no result returned after {max_retries} retries")


def fatal_error() -> None:
    logging.exception("Fatal error")
    secho("A fatal error occurred.", fg=colors.RED, err=True)


def mirror_mdns(dns_server: str, dns_zone: str) -> None:
    exit_event = Event()
    error_event = Event()

    zc = Zeroconf()

    def signal_handler(signum: int, frame: Optional[FrameType]):
        echo()
        echo(f"Exiting...")

        exit_event.set()

    signal(Signals.SIGINT, signal_handler)
    signal(Signals.SIGTERM, signal_handler)

    def remove_all_a_records() -> None:
        record_names: List[dns.name.Name] = []
        query, _ = dns.xfr.make_query(dns.zone.Zone(dns_zone))
        response = dns.query.tcp(query, dns_server)
        for rrset in response.answer:
            if (
                rrset.rdtype == dns.rdatatype.RdataType.A
                or rrset.rdtype == dns.rdatatype.RdataType.AAAA
            ):
                record_names.append(rrset.name)

                record_type = dns.rdatatype.RdataType.to_text(rrset.rdtype)
                logging.info(
                    f"Found DNS {record_type} record for server '{rrset.name}'"
                )

        for name in record_names:
            update = dns.update.UpdateMessage(dns_zone)
            update.delete(name)

            response = dns.query.tcp(update, dns_server)
            logging.info(f"Removed DNS records for server '{name}'")

    remove_all_a_records()

    service_instance_browsers: List[ServiceBrowser] = []
    services: Dict[str, ServiceInfo] = {}
    servers: Dict[str, ServerInfo] = {}

    def on_service_type_state_change(
        zeroconf: Zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        try:
            if state_change == ServiceStateChange.Added:
                logging.info(f"Service type '{service_type}' found")

                browser = ServiceBrowser(
                    zeroconf, name, [on_service_instance_state_change]
                )
                service_instance_browsers.append(browser)
            else:
                raise RuntimeError("unexpected state change from service type listener")
        except Exception:
            fatal_error()
            error_event.set()

    def server_updated(server: str, server_info: ServerInfo) -> None:
        server_parts = server.split(".")
        server_name = server_parts[0]

        if server_info.ipv4_address is not None:
            update = dns.update.UpdateMessage(dns_zone)
            update.replace(
                server_name, server_info.ttl, "A", str(server_info.ipv4_address)
            )

            response = dns.query.tcp(update, dns_server)
            logging.info(
                f"Updated DNS A record for server '{server_name}' to {server_info.ipv4_address}"
            )

        if server_info.ipv6_address is not None:
            update = dns.update.UpdateMessage(dns_zone)
            update.replace(
                server_name, server_info.ttl, "AAAA", str(server_info.ipv6_address)
            )

            response = dns.query.tcp(update, dns_server)
            logging.info(
                f"Updated DNS AAAA record for server '{server_name}' to {server_info.ipv6_address}"
            )

    def server_removed(server: str, server_info: ServerInfo) -> None:
        server_parts = server.split(".")
        server_name = server_parts[0]

        update = dns.update.UpdateMessage(dns_zone)
        update.delete(server_name)

        response = dns.query.tcp(update, dns_server)
        logging.info(f"Removed DNS records for server '{server_name}'")

    def service_updated(name: str, service_info: ServiceInfo) -> None:
        services[name] = service_info

        server = service_info.server

        ipv4_addresses = service_info._ipv4_addresses
        ipv4_address = ipv4_addresses[0] if ipv4_addresses else None

        ipv6_addresses = service_info._ipv6_addresses
        ipv6_address = ipv6_addresses[0] if ipv6_addresses else None

        ttl = service_info.host_ttl

        server_info = ServerInfo(ipv4_address, ipv6_address, ttl)
        if servers.get(server, None) != server_info:
            servers[server] = server_info

            server_updated(server, server_info)

    def service_removed(name: str) -> None:
        service_info = services[name]
        del services[name]

        server = service_info.server

        if server in servers:
            server_info = servers[server]
            del servers[server]

            server_removed(server, server_info)

    def on_service_instance_state_change(
        zeroconf: Zeroconf,
        service_type: str,
        name: str,
        state_change: ServiceStateChange,
    ) -> None:
        try:
            if state_change == ServiceStateChange.Added:
                logging.info(f"Service '{name}' added")

                service_info = retry(
                    lambda: zc.get_service_info(
                        service_type, name, timeout=GET_SERVICE_INFO_TIMEOUT
                    )
                )
                logging.info(f"Service '{name}' got initial info")

                service_updated(name, service_info)
            elif state_change == ServiceStateChange.Removed:
                logging.info(f"Service '{name}' removed")

                service_removed(name)
            elif state_change == ServiceStateChange.Updated:
                logging.info(f"Service '{name}' updated")

                service_info = retry(
                    lambda: zc.get_service_info(
                        service_type, name, timeout=GET_SERVICE_INFO_TIMEOUT
                    )
                )
                logging.info(f"Service '{name}' got updated info")

                service_updated(name, service_info)
        except Exception:
            fatal_error()
            error_event.set()

    service_type_browser = ServiceBrowser(
        zc,
        _SERVICE_TYPE_ENUMERATION_NAME,
        handlers=[on_service_type_state_change],
    )

    while not (exit_event.is_set() or error_event.is_set()):
        sleep(0.1)

    if exit_event.is_set():
        try:
            remove_all_a_records()
        except Exception:
            logging.exception("Error on shutdown")

    service_type_browser.cancel()
    for browser in service_instance_browsers:
        browser.cancel()

    zc.close()


def get_default_dns_server() -> Union[str, EllipsisType]:
    resolver = dns.resolver.Resolver()
    return resolver.nameservers[0] if resolver.nameservers else ...


@app.command()
def command(
    dns_server: str = Option(
        get_default_dns_server,
        "--dns-server",
        "-s",
        show_default=get_show_default(get_default_dns_server),
        help="The address of the DNS server to update.",
    ),
    dns_zone: str = Argument(
        "mdns.lan",
        help="The DNS zone to update with mDNS hostnames.",
    ),
) -> None:
    echo(f"Watching for mDNS services...")

    try:
        mirror_mdns(dns_server, dns_zone)
    except Exception:
        fatal_error()


def main() -> None:
    locale.setlocale(locale.LC_ALL, "")
    init_logging()

    app()
