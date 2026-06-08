<div align="center">

# 🏮 LANtern

**A self-hosted SIEM for your home network.**

*Your LAN, illuminated.*

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Docker](https://img.shields.io/badge/Docker-required-blue)
![Python](https://img.shields.io/badge/Python-3.11+-green)

</div>

---

Your router sees everything — every DNS query, every connection, every device that comes and goes. Most of that data disappears the moment it's generated. LANtern catches it, stores it locally, and gives you a real-time window into what's actually happening on your LAN.

Think of it as the observability stack your home network never had: device inventory with vendor lookup, per-device DNS history, NetFlow traffic analysis, and (coming soon) a local LLM layer that can answer questions about your network in plain English.

Everything runs on your hardware. No data leaves your network.

---

## What it collects

| Source | Data |
|--------|------|
| dnsmasq logs | DNS queries per device, DHCP events, resolver activity |
| dnsmasq lease file | Device inventory with MAC vendor lookup |
| NetFlow v9 (via router) | Per-flow traffic records (src/dst/port/protocol/bytes) |

---

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │  Your router (OpenWrt + softflowd)       │
                    │  NetFlow v9 → UDP 2055                   │
                    └────────────────┬────────────────────────┘
                                     │
         ┌───────────────────────────▼───────────────────────────┐
         │  Host running dnsmasq (DNS/DHCP server)                │
         │                                                        │
         │  /var/log/dnsmasq.log ──► dnsmasq-ingest collector    │
         │  /var/lib/misc/dnsmasq.leases ──► inventory collector  │
         │  UDP :2055 ──────────────► netflow collector           │
         │                                 │                      │
         │                    /var/lib/lantern/lantern.db         │
         │                                 │                      │
         │                    FastAPI  ◄───┘                      │
         │                    React UI (lantern.home)             │
         └───────────────────────────────────────────────────────┘
```

LANtern runs as lightweight Docker containers on the same host as your DNS/DHCP server. The collectors use Python's standard library only — no third-party dependencies.

---

## Quick start

**Prerequisites:**
- Docker + Docker Compose
- dnsmasq running as your DNS/DHCP server, with `log-queries` and `log-dhcp` enabled
- An OUI database at `/usr/share/nmap/nmap-mac-prefixes` (from `nmap` package) or equivalent
- *(Optional)* A router running softflowd exporting NetFlow v9 to this host on UDP 2055

**1. Create the data directory:**

```sh
sudo install -d -o root -g adm -m 2775 /var/lib/lantern
```

**2. Start the stack:**

```sh
docker compose up -d
```

The UI will be available at `http://<your-host>:3000`. The API runs on port 8000.

**3. Query your data from the CLI:**

```sh
# Row counts across all tables
docker compose exec dnsmasq-ingest python3 -m lantern.query stats

# Device inventory with vendor names
docker compose exec dnsmasq-ingest python3 -m lantern.query devices

# Recent DNS queries
docker compose exec dnsmasq-ingest python3 -m lantern.query dns-recent

# Most-queried domains in the last hour
docker compose exec dnsmasq-ingest python3 -m lantern.query top-dns --minutes 60

# Recent traffic flows
docker compose exec netflow python3 -m lantern.query flows-recent
```

---

## dnsmasq configuration

Add to `/etc/dnsmasq.conf` or a drop-in under `/etc/dnsmasq.d/`:

```
log-queries
log-dhcp
log-facility=/var/log/dnsmasq.log
```

Then restart dnsmasq. The `dnsmasq-ingest` container must be able to read the
log file — it runs as root so standard Unix permissions apply.

---

## NetFlow setup (OpenWrt + softflowd)

Install softflowd on your OpenWrt router:

```sh
apk add softflowd       # OpenWrt 25.x
# or: opkg install softflowd  # older OpenWrt
```

Configure via UCI:

```sh
uci set softflowd.@softflowd[0].enabled='1'
uci set softflowd.@softflowd[0].interface='br-lan'
uci set softflowd.@softflowd[0].host_port='<lantern-host-ip>:2055'
uci set softflowd.@softflowd[0].export_version='9'
uci commit softflowd
/etc/init.d/softflowd enable && /etc/init.d/softflowd start
```

If your host has a firewall, allow the NetFlow port:

```sh
sudo ufw allow proto udp from <router-ip> to any port 2055
```

---

## Volume mounts

| Mount | Purpose |
|-------|---------|
| `/var/log/dnsmasq.log` | dnsmasq log (read-only) |
| `/var/lib/misc/dnsmasq.leases` | DHCP lease file (read-only) |
| `/usr/share/nmap/nmap-mac-prefixes` | OUI vendor database (read-only) |
| `/var/lib/lantern` | SQLite database (read-write) |

---

## Roadmap

- [ ] Per-device behavioral baseline — learn normal query volume, active hours, typical destinations
- [ ] Anomaly detection with LLM explanation via local Ollama ("this device made 300 DNS queries in 60 seconds — pattern resembles beaconing")
- [ ] Natural-language query interface ("which device woke up at 3 AM?")
- [ ] Weekly network health report generated by LLM
- [ ] Suricata IDS integration with LLM-interpreted alerts

---

## License

MIT
