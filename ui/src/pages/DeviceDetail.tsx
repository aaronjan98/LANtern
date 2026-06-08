import { useEffect, useState } from "react"
import { useParams, useNavigate } from "react-router-dom"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
} from "recharts"

interface Device {
  mac: string
  hostname: string | null
  last_ip: string | null
  vendor: string | null
  first_seen_unix: number | null
  last_seen_unix: number | null
  label: string | null
  active: boolean
}

interface IpEntry {
  ip: string
  hostname: string | null
  observed_unix: number
  lease_expires_unix: number | null
}

interface DnsQuery {
  ts_unix: number
  client_ip: string
  qtype: string
  domain: string
}

interface HistoryBucket {
  bucket: number
  queries: number
}

interface Flow {
  received_unix: number
  src_addr: string | null
  src_port: number | null
  dst_addr: string | null
  dst_port: number | null
  protocol_name: string
  packets: number | null
  bytes: number | null
}

function fmt(unix: number | null) {
  if (!unix) return "—"
  return new Date(unix * 1000).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  })
}

function fmtTime(unix: number) {
  return new Date(unix * 1000).toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  })
}

export default function DeviceDetail() {
  const { mac } = useParams<{ mac: string }>()
  const navigate = useNavigate()
  const decoded = decodeURIComponent(mac ?? "")

  const [device, setDevice] = useState<Device | null>(null)
  const [ipHistory, setIpHistory] = useState<IpEntry[]>([])
  const [dnsRecent, setDnsRecent] = useState<DnsQuery[]>([])
  const [dnsHistory, setDnsHistory] = useState<HistoryBucket[]>([])
  const [flows, setFlows] = useState<Flow[]>([])

  useEffect(() => {
    if (!decoded) return
    Promise.all([
      fetch(`/api/devices/${encodeURIComponent(decoded)}`).then((r) => r.json()),
      fetch(`/api/dns/recent?limit=30&client_ip=${decoded}`).then((r) => r.json()),
      fetch(`/api/dns/history?hours=24&bucket_minutes=60`).then((r) => r.json()),
      fetch(`/api/flows/recent?limit=20&host=${decoded}`).then((r) => r.json()),
    ]).then(([dev, dns, hist, fl]) => {
      setDevice(dev.device)
      setIpHistory(dev.ip_history)
      setDnsRecent(dns)
      setDnsHistory(hist)
      setFlows(fl)
    })
  }, [decoded])

  if (!device) return <p className="text-muted-foreground">Loading…</p>

  const displayName = device.label || device.hostname || device.mac
  const [editingLabel, setEditingLabel] = useState(false)
  const [labelInput, setLabelInput] = useState(device.label ?? "")

  function saveLabel() {
    fetch(`/api/devices/${encodeURIComponent(decoded)}/label`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label: labelInput.trim() || null }),
    })
      .then((r) => r.json())
      .then((data) => {
        setDevice((d) => d ? { ...d, label: data.label } : d)
        setEditingLabel(false)
      })
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <button
          onClick={() => navigate("/devices")}
          className="text-sm text-muted-foreground hover:text-foreground mb-2 flex items-center gap-1"
        >
          ← Devices
        </button>
        <div className="flex items-center gap-3">
          <span
            className={`inline-block w-2.5 h-2.5 rounded-full shrink-0 ${
              device.active ? "bg-green-500" : "bg-zinc-600"
            }`}
          />
          {editingLabel ? (
            <div className="flex items-center gap-2">
              <input
                autoFocus
                className="text-xl font-semibold bg-transparent border-b border-border focus:outline-none focus:border-primary"
                value={labelInput}
                onChange={(e) => setLabelInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") saveLabel(); if (e.key === "Escape") setEditingLabel(false) }}
              />
              <button onClick={saveLabel} className="text-xs px-2 py-1 rounded bg-primary text-primary-foreground">Save</button>
              <button onClick={() => setEditingLabel(false)} className="text-xs text-muted-foreground">Cancel</button>
            </div>
          ) : (
            <div className="flex items-center gap-2 group">
              <h1 className="text-2xl font-semibold">{displayName}</h1>
              <button
                onClick={() => { setLabelInput(device.label ?? ""); setEditingLabel(true) }}
                className="text-xs text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded hover:bg-accent"
              >
                {device.label ? "edit" : "add label"}
              </button>
            </div>
          )}
          {device.active && <Badge className="bg-green-500/20 text-green-400 border-0">Active</Badge>}
        </div>
        {device.label && device.hostname && (
          <p className="text-sm text-muted-foreground mt-1 ml-5">{device.hostname}</p>
        )}
      </div>

      {/* Meta cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card><CardHeader className="pb-1"><CardTitle className="text-xs text-muted-foreground">MAC</CardTitle></CardHeader>
          <CardContent><p className="font-mono text-sm">{device.mac}</p></CardContent></Card>
        <Card><CardHeader className="pb-1"><CardTitle className="text-xs text-muted-foreground">IP</CardTitle></CardHeader>
          <CardContent><p className="font-mono text-sm">{device.last_ip ?? "—"}</p></CardContent></Card>
        <Card><CardHeader className="pb-1"><CardTitle className="text-xs text-muted-foreground">Vendor</CardTitle></CardHeader>
          <CardContent><p className="text-sm">{device.vendor ?? "—"}</p></CardContent></Card>
        <Card><CardHeader className="pb-1"><CardTitle className="text-xs text-muted-foreground">First seen</CardTitle></CardHeader>
          <CardContent><p className="text-sm">{fmt(device.first_seen_unix)}</p></CardContent></Card>
      </div>

      {/* DNS activity chart */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">DNS Activity — last 24h (network-wide)</CardTitle>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={140}>
            <LineChart data={dnsHistory}>
              <XAxis dataKey="bucket"
                tickFormatter={(v) => new Date(v * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                tick={{ fontSize: 10, fill: "#71717a" }} stroke="#3f3f46" />
              <YAxis tick={{ fontSize: 10, fill: "#71717a" }} stroke="#3f3f46" width={36} />
              <Tooltip labelFormatter={(v) => new Date(Number(v) * 1000).toLocaleTimeString()}
                contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: "6px" }}
                labelStyle={{ color: "#a1a1aa" }} itemStyle={{ color: "#e4e4e7" }} />
              <Line type="monotone" dataKey="queries" stroke="#818cf8" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Recent DNS queries */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">Recent DNS Queries</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <div className="divide-y divide-border text-sm max-h-72 overflow-auto">
              {dnsRecent.length === 0 && (
                <p className="text-muted-foreground text-sm px-6 py-4">No queries recorded.</p>
              )}
              {dnsRecent.map((q, i) => (
                <div key={i} className="flex items-center gap-3 px-4 py-2">
                  <span className="text-muted-foreground text-xs w-20 shrink-0 tabular-nums">{fmtTime(q.ts_unix)}</span>
                  <span className="text-xs font-mono bg-accent px-1.5 py-0.5 rounded w-12 text-center shrink-0">{q.qtype}</span>
                  <span className="truncate font-mono text-xs">{q.domain}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* IP history */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium">IP History</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <div className="divide-y divide-border text-sm max-h-72 overflow-auto">
              {ipHistory.length === 0 && (
                <p className="text-muted-foreground text-sm px-6 py-4">No history recorded.</p>
              )}
              {ipHistory.map((entry, i) => (
                <div key={i} className="flex items-center gap-4 px-4 py-2">
                  <span className="font-mono text-xs w-28 shrink-0">{entry.ip}</span>
                  <span className="text-xs text-muted-foreground">{fmt(entry.observed_unix)}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Recent flows */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Recent Traffic Flows</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <div className="divide-y divide-border text-xs max-h-64 overflow-auto">
            {flows.length === 0 && (
              <p className="text-muted-foreground text-sm px-6 py-4">No flows recorded.</p>
            )}
            {flows.map((f, i) => (
              <div key={i} className="flex items-center gap-4 px-4 py-2 font-mono">
                <span className="text-muted-foreground w-20 shrink-0">{fmtTime(f.received_unix)}</span>
                <span className="w-36 shrink-0 truncate">{f.src_addr}:{f.src_port}</span>
                <span className="text-muted-foreground shrink-0">→</span>
                <span className="w-36 shrink-0 truncate">{f.dst_addr}:{f.dst_port}</span>
                <span className="text-muted-foreground w-12 shrink-0">{f.protocol_name}</span>
                <span className="text-muted-foreground">{f.bytes?.toLocaleString() ?? "—"} B</span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
