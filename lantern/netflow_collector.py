#!/usr/bin/env python3
"""Collect NetFlow v9 packets and store decoded records in SQLite."""

from __future__ import annotations

import argparse
import ipaddress
import json
import socket
import struct
import sys
import time
from dataclasses import dataclass

from lantern.common import DEFAULT_DB, connect_db


FIELD_NAMES = {
    1: "in_bytes",
    2: "in_pkts",
    4: "protocol",
    5: "src_tos",
    6: "tcp_flags",
    7: "l4_src_port",
    8: "ipv4_src_addr",
    9: "src_mask",
    10: "input_snmp",
    11: "l4_dst_port",
    12: "ipv4_dst_addr",
    13: "dst_mask",
    14: "output_snmp",
    15: "ipv4_next_hop",
    16: "src_as",
    17: "dst_as",
    18: "bgp_ipv4_next_hop",
    21: "last_switched",
    22: "first_switched",
    23: "out_bytes",
    24: "out_pkts",
    27: "ipv6_src_addr",
    28: "ipv6_dst_addr",
    29: "ipv6_src_mask",
    30: "ipv6_dst_mask",
    31: "ipv6_flow_label",
    32: "icmp_type",
    34: "sampling_interval",
    35: "sampling_algorithm",
    48: "flow_sampler_id",
    61: "direction",
    62: "ipv6_next_hop",
    85: "flow_bytes",
    86: "flow_pkts",
}

IPV4_FIELDS = {8, 12, 15, 18}
IPV6_FIELDS = {27, 28, 62}


@dataclass(frozen=True)
class Template:
    template_id: int
    fields: tuple[tuple[int, int], ...]

    @property
    def record_len(self) -> int:
        return sum(length for _, length in self.fields)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", default="0.0.0.0", help="listen address, default 0.0.0.0")
    parser.add_argument("--port", type=int, default=2055, help="UDP listen port, default 2055")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database path, default {DEFAULT_DB}")
    parser.add_argument("--commit-every", type=int, default=1, help="commit after this many records")
    return parser.parse_args()


def decode_value(field_type: int, raw: bytes):
    if field_type in IPV4_FIELDS and len(raw) == 4:
        return str(ipaddress.ip_address(raw))
    if field_type in IPV6_FIELDS and len(raw) == 16:
        return str(ipaddress.ip_address(raw))
    if len(raw) in (1, 2, 3, 4, 8):
        return int.from_bytes(raw, "big")
    return raw.hex()


def add_decoded(decoded: dict[str, object], field_type: int, value) -> None:
    key = FIELD_NAMES.get(field_type, f"field_{field_type}")
    existing = decoded.get(key)
    if existing is None:
        decoded[key] = value
    elif isinstance(existing, list):
        existing.append(value)
    else:
        decoded[key] = [existing, value]


def first_value(decoded: dict[str, object], *keys: str):
    for key in keys:
        value = decoded.get(key)
        if isinstance(value, list):
            return value[0] if value else None
        if value is not None:
            return value
    return None


def switched_to_unix(unix_secs: int, sys_uptime_ms: int, switched_ms) -> int | None:
    if switched_ms is None:
        return None
    try:
        return int(unix_secs - ((sys_uptime_ms - int(switched_ms)) / 1000.0))
    except (TypeError, ValueError):
        return None


class NetflowCollector:
    def __init__(self, db_path: str, commit_every: int) -> None:
        self.conn = connect_db(db_path)
        self.templates: dict[tuple[str, int, int], Template] = {}
        self.missing_templates_seen: set[tuple[str, int, int]] = set()
        self.pending_records = 0
        self.commit_every = max(1, commit_every)

    def process_packet(self, data: bytes, exporter_ip: str) -> int:
        if len(data) < 20:
            return 0

        version, _count, sys_uptime_ms, unix_secs, _sequence, source_id = struct.unpack("!HHIIII", data[:20])
        if version != 9:
            return 0

        offset = 20
        inserted = 0
        templates_seen = 0
        while offset + 4 <= len(data):
            flowset_id, length = struct.unpack("!HH", data[offset : offset + 4])
            if length < 4 or offset + length > len(data):
                print(
                    f"invalid flowset from {exporter_ip}: id={flowset_id} length={length} packet_len={len(data)}",
                    file=sys.stderr,
                    flush=True,
                )
                break

            payload = data[offset + 4 : offset + length]
            if flowset_id == 0:
                templates_seen += self._parse_templates(exporter_ip, source_id, payload)
            elif flowset_id >= 256:
                inserted += self._parse_data_flowset(
                    exporter_ip,
                    source_id,
                    flowset_id,
                    payload,
                    sys_uptime_ms,
                    unix_secs,
                )

            offset += length

        if inserted or templates_seen:
            self.pending_records += inserted
            if templates_seen or self.pending_records >= self.commit_every:
                self.conn.commit()
                self.pending_records = 0
        return inserted

    def _parse_templates(self, exporter_ip: str, source_id: int, payload: bytes) -> int:
        offset = 0
        now = int(time.time())
        count = 0
        while offset + 4 <= len(payload):
            template_id, field_count = struct.unpack("!HH", payload[offset : offset + 4])
            offset += 4
            fields: list[tuple[int, int]] = []

            for _ in range(field_count):
                if offset + 4 > len(payload):
                    return count
                field_type, field_length = struct.unpack("!HH", payload[offset : offset + 4])
                offset += 4
                fields.append((field_type, field_length))

            template = Template(template_id=template_id, fields=tuple(fields))
            self.templates[(exporter_ip, source_id, template_id)] = template
            count += 1
            print(
                f"learned template exporter={exporter_ip} source_id={source_id} "
                f"template_id={template_id} fields={len(fields)} record_len={template.record_len}",
                flush=True,
            )
            self.conn.execute(
                """
                INSERT INTO netflow_templates(exporter_ip, source_id, template_id, fields_json, updated_unix)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(exporter_ip, source_id, template_id) DO UPDATE SET
                    fields_json = excluded.fields_json,
                    updated_unix = excluded.updated_unix
                """,
                (exporter_ip, source_id, template_id, json.dumps(fields), now),
            )
        return count

    def _parse_data_flowset(
        self,
        exporter_ip: str,
        source_id: int,
        template_id: int,
        payload: bytes,
        sys_uptime_ms: int,
        unix_secs: int,
    ) -> int:
        template = self.templates.get((exporter_ip, source_id, template_id))
        if template is None or template.record_len <= 0:
            key = (exporter_ip, source_id, template_id)
            if key not in self.missing_templates_seen:
                self.missing_templates_seen.add(key)
                print(
                    f"skipping data flowset without template exporter={exporter_ip} "
                    f"source_id={source_id} template_id={template_id} payload_len={len(payload)}",
                    file=sys.stderr,
                    flush=True,
                )
            return 0

        offset = 0
        inserted = 0
        while offset + template.record_len <= len(payload):
            record = payload[offset : offset + template.record_len]
            offset += template.record_len
            decoded = self._decode_record(template, record)
            self._insert_record(exporter_ip, source_id, decoded, sys_uptime_ms, unix_secs)
            inserted += 1
        return inserted

    def _decode_record(self, template: Template, record: bytes) -> dict[str, object]:
        decoded: dict[str, object] = {}
        offset = 0
        for field_type, field_length in template.fields:
            raw = record[offset : offset + field_length]
            offset += field_length
            add_decoded(decoded, field_type, decode_value(field_type, raw))
        return decoded

    def _insert_record(
        self,
        exporter_ip: str,
        source_id: int,
        decoded: dict[str, object],
        sys_uptime_ms: int,
        unix_secs: int,
    ) -> None:
        first_switched = first_value(decoded, "first_switched")
        last_switched = first_value(decoded, "last_switched")
        first_unix = switched_to_unix(unix_secs, sys_uptime_ms, first_switched)
        last_unix = switched_to_unix(unix_secs, sys_uptime_ms, last_switched)

        self.conn.execute(
            """
            INSERT INTO netflow_records(
                received_unix, exporter_ip, source_id, src_addr, dst_addr, next_hop,
                input_snmp, output_snmp, packets, bytes, first_switched_ms,
                last_switched_ms, first_unix, last_unix, src_port, dst_port,
                tcp_flags, protocol, tos, src_as, dst_as, src_mask, dst_mask,
                direction, sampler_id, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(time.time()),
                exporter_ip,
                source_id,
                first_value(decoded, "ipv4_src_addr", "ipv6_src_addr"),
                first_value(decoded, "ipv4_dst_addr", "ipv6_dst_addr"),
                first_value(decoded, "ipv4_next_hop", "ipv6_next_hop", "bgp_ipv4_next_hop"),
                first_value(decoded, "input_snmp"),
                first_value(decoded, "output_snmp"),
                first_value(decoded, "in_pkts", "out_pkts", "flow_pkts"),
                first_value(decoded, "in_bytes", "out_bytes", "flow_bytes"),
                first_switched,
                last_switched,
                first_unix,
                last_unix,
                first_value(decoded, "l4_src_port"),
                first_value(decoded, "l4_dst_port"),
                first_value(decoded, "tcp_flags"),
                first_value(decoded, "protocol"),
                first_value(decoded, "src_tos"),
                first_value(decoded, "src_as"),
                first_value(decoded, "dst_as"),
                first_value(decoded, "src_mask", "ipv6_src_mask"),
                first_value(decoded, "dst_mask", "ipv6_dst_mask"),
                first_value(decoded, "direction"),
                first_value(decoded, "flow_sampler_id"),
                json.dumps(decoded, sort_keys=True),
            ),
        )

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()


def main() -> int:
    args = parse_args()
    collector = NetflowCollector(args.db, args.commit_every)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.listen, args.port))
    print(f"listening for NetFlow v9 on {args.listen}:{args.port}", flush=True)

    try:
        while True:
            data, addr = sock.recvfrom(65535)
            try:
                inserted = collector.process_packet(data, addr[0])
                if inserted:
                    print(f"inserted {inserted} NetFlow records from {addr[0]}", flush=True)
            except Exception as exc:  # keep the collector alive on malformed packets
                print(f"error processing packet from {addr[0]}: {exc}", file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        collector.close()
        sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
