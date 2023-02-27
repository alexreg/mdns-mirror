# mdns-mirror

A program for continuously mirroring mDNS (a.k.a. Bonjour) hostnames under a particular zone on a regular DNS server.

This is useful, for example, when connecting to a LAN over VPN from iOS devices, since iOS does not currently support sending mDNS packets through a VPN tunnel.

## Installation

To install mdns-mirror, run

```console
$ pip3 install -U git+https://github.com/alexreg/mdns-mirror
```

A *systemd* service file is provided at `mdns-mirror.service`.

## Usage

Run `mdns-mirror --help` for details on how to use the program.

Set the `LOGLEVEL` environment variable to configure the verbosity of the log.
