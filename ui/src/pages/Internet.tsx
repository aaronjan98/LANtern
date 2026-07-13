import { useEffect, useState, useCallback } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend,
} from "recharts"

interface SpeedRow {
  ts_unix: number
  ok: number
  download_bps: number | null
  upload_bps: number | null
  ping_ms: number | null
  jitter_ms: number | null
  packet_loss: number | null
  server_name: string | null
  isp: string | null
  result_url: string | null
  error: string | null
}

interface ConnSummary {
  hours: number
  checks: number
  up_checks: number
  uptime_pct: number | null
  outages: number
  downtime_s: number
  current_up: boolean | null
  current_gateway_up: boolean | null
  last_check_ts: number | null
}

interface Outage {
  start_ts: number
  end_ts: number | null
  duration_s: number
  ongoing: boolean
  wan_only: boolean
}

interface KumaMonitor {
  id: number
  name: string
  type: string | null
  status: number | null
  ping_ms: number | null
  last_ts: number | null
  uptime_pct: number | null
}

interface KumaEvent {
  ts_unix: number
  status: number
  ping_ms: number | null
  msg: string | null
  name: string
}

function statusColor(status: number | null): string {
  return status === 1 ? "bg-green-500" : status === 0 ? "bg-red-500" : "bg-zinc-600"
}

interface LatencyBucket {
  bucket: number
  avg_rtt: number | null
  max_rtt: number | null
  loss_pct: number | null
  samples: number
}

const WINDOWS = [
  { label: "Last 24h", value: "24" },
  { label: "Last 7d", value: "168" },
  { label: "Last 30d", value: "720" },
]

function mbps(bps: number | null): number | null {
  return bps == null ? null : bps / 1e6
}

function fmtMbps(bps: number | null): string {
  const m = mbps(bps)
  return m == null ? "—" : `${m.toFixed(1)} Mbps`
}

function fmtTime(unix: number) {
  return new Date(unix * 1000).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  })
}

function fmtDuration(s: number): string {
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  const h = Math.floor(s / 3600)
  const m = Math.round((s % 3600) / 60)
  return m ? `${h}h ${m}m` : `${h}h`
}

function StatCard({ title, value, sub }: { title: string; value: string; sub?: string }) {
  return (
    <Card>
      <CardHeader className="pb-1">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-bold">{value}</p>
        {sub && <p className="text-xs text-muted-foreground mt-1 truncate">{sub}</p>}
      </CardContent>
    </Card>
  )
}

export default function Internet() {
  const [hours, setHours] = useState("168")
  const [rows, setRows] = useState<SpeedRow[]>([])
  const [latest, setLatest] = useState<SpeedRow | null>(null)
  const [conn, setConn] = useState<ConnSummary | null>(null)
  const [outages, setOutages] = useState<Outage[]>([])
  const [monitors, setMonitors] = useState<KumaMonitor[]>([])
  const [events, setEvents] = useState<KumaEvent[]>([])
  const [latency, setLatency] = useState<LatencyBucket[]>([])

  const load = useCallback(() => {
    // keep ~100 points regardless of window
    const bucketMinutes = Math.max(30, Math.round((Number(hours) * 60) / 100))
    Promise.all([
      fetch(`/api/internet/speedtest?hours=${hours}&limit=500`).then((r) => r.json()),
      fetch(`/api/internet/speedtest/latest`).then((r) => r.json()),
      fetch(`/api/internet/connectivity/summary?hours=${hours}`).then((r) => r.json()),
      fetch(`/api/internet/outages?hours=${hours}&limit=100`).then((r) => r.json()),
      fetch(`/api/internet/kuma/monitors?hours=${hours}`).then((r) => r.json()),
      fetch(`/api/internet/kuma/events?hours=${hours}&limit=60`).then((r) => r.json()),
      fetch(`/api/internet/latency?hours=${hours}&bucket_minutes=${bucketMinutes}`).then((r) => r.json()),
    ]).then(([h, l, c, o, m, e, la]) => {
      setRows(h); setLatest(l); setConn(c); setOutages(o); setMonitors(m); setEvents(e); setLatency(la)
    })
  }, [hours])

  useEffect(() => {
    load()
    const id = setInterval(load, 60000)
    return () => clearInterval(id)
  }, [load])

  // recharts wants oldest→newest; the API returns newest first.
  const chartData = [...rows]
    .filter((r) => r.ok)
    .reverse()
    .map((r) => ({
      ts: r.ts_unix,
      download: mbps(r.download_bps),
      upload: mbps(r.upload_bps),
      ping: r.ping_ms,
    }))

  const failures = rows.filter((r) => !r.ok).length

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Internet</h1>
          <p className="text-sm text-muted-foreground">
            ISP speed history{latest?.isp ? ` — ${latest.isp}` : ""}
          </p>
        </div>
        <Select value={hours} onValueChange={(v) => v && setHours(v)}>
          <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
          <SelectContent>
            {WINDOWS.map((w) => <SelectItem key={w.value} value={w.value}>{w.label}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>

      {/* Headline cards — latest successful test */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard title="Download" value={fmtMbps(latest?.download_bps ?? null)}
          sub={latest ? `as of ${fmtTime(latest.ts_unix)}` : "no tests yet"} />
        <StatCard title="Upload" value={fmtMbps(latest?.upload_bps ?? null)}
          sub={latest?.server_name ?? undefined} />
        <StatCard title="Ping" value={latest?.ping_ms != null ? `${latest.ping_ms.toFixed(1)} ms` : "—"}
          sub={latest?.jitter_ms != null ? `${latest.jitter_ms.toFixed(1)} ms jitter` : undefined} />
        <StatCard title="Packet loss" value={latest?.packet_loss != null ? `${latest.packet_loss}%` : "—"}
          sub={failures > 0 ? `${failures} failed test(s) in window` : "no failed tests"} />
      </div>

      {/* Connectivity summary */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard title="Internet now"
          value={conn?.current_up == null ? "—" : conn.current_up ? "Online" : "Offline"}
          sub={conn?.current_up === false && conn?.current_gateway_up
            ? "LAN up — no internet"
            : conn?.last_check_ts ? `checked ${fmtTime(conn.last_check_ts)}` : "no checks yet"} />
        <StatCard title="Uptime" value={conn?.uptime_pct != null ? `${conn.uptime_pct}%` : "—"}
          sub={conn ? `${conn.checks} checks in window` : undefined} />
        <StatCard title="Outages" value={conn ? String(conn.outages) : "—"} sub="in window" />
        <StatCard title="Downtime" value={conn ? fmtDuration(conn.downtime_s) : "—"} sub="total in window" />
      </div>

      {/* Speed-over-time chart */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Speed over time — download / upload (Mbps)</CardTitle>
        </CardHeader>
        <CardContent>
          {chartData.length === 0 ? (
            <p className="text-muted-foreground text-sm py-8 text-center">
              No successful speedtests recorded in this window yet.
            </p>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <LineChart data={chartData}>
                <XAxis dataKey="ts"
                  tickFormatter={(v) => new Date(v * 1000).toLocaleDateString([], { month: "short", day: "numeric" })}
                  tick={{ fontSize: 10, fill: "#71717a" }} stroke="#3f3f46" />
                <YAxis tick={{ fontSize: 10, fill: "#71717a" }} stroke="#3f3f46" width={40}
                  label={{ value: "Mbps", angle: -90, position: "insideLeft", fill: "#71717a", fontSize: 10 }} />
                <Tooltip
                  labelFormatter={(v) => fmtTime(Number(v))}
                  formatter={(v: unknown, name: unknown) => [v == null ? "—" : `${Number(v).toFixed(1)} Mbps`, name as string]}
                  contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: "6px" }}
                  labelStyle={{ color: "#a1a1aa" }} itemStyle={{ color: "#e4e4e7" }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line type="monotone" dataKey="download" name="Download" stroke="#818cf8" dot={false} strokeWidth={2} />
                <Line type="monotone" dataKey="upload" name="Upload" stroke="#34d399" dot={false} strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      {/* Recent tests table */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Recent tests</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <div className="divide-y divide-border text-sm max-h-96 overflow-auto">
            {rows.length === 0 && (
              <p className="text-muted-foreground text-sm px-6 py-4">No tests recorded.</p>
            )}
            {rows.map((r, i) => (
              <div key={i} className="flex items-center gap-4 px-4 py-2">
                <span className="text-muted-foreground text-xs w-32 shrink-0 tabular-nums">{fmtTime(r.ts_unix)}</span>
                {r.ok ? (
                  <>
                    <span className="w-24 shrink-0 tabular-nums">↓ {fmtMbps(r.download_bps)}</span>
                    <span className="w-24 shrink-0 tabular-nums">↑ {fmtMbps(r.upload_bps)}</span>
                    <span className="w-20 shrink-0 tabular-nums text-muted-foreground">
                      {r.ping_ms != null ? `${r.ping_ms.toFixed(0)} ms` : "—"}
                    </span>
                    {r.result_url ? (
                      <a href={r.result_url} target="_blank" rel="noreferrer"
                        className="text-xs text-primary hover:underline truncate">result ↗</a>
                    ) : <span />}
                  </>
                ) : (
                  <>
                    <Badge className="bg-red-500/20 text-red-400 border-0 shrink-0">failed</Badge>
                    <span className="text-xs text-red-400/80 truncate">{r.error ?? "unknown error"}</span>
                  </>
                )}
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Outage log */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Outages</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <div className="divide-y divide-border text-sm max-h-72 overflow-auto">
            {outages.length === 0 && (
              <p className="text-muted-foreground text-sm px-6 py-4">
                No internet outages recorded in this window.
              </p>
            )}
            {outages.map((o, i) => (
              <div key={i} className="flex items-center gap-4 px-4 py-2">
                <span className="text-muted-foreground text-xs w-32 shrink-0 tabular-nums">{fmtTime(o.start_ts)}</span>
                <span className="w-20 shrink-0 tabular-nums">{fmtDuration(o.duration_s)}</span>
                {o.ongoing && <Badge className="bg-red-500/20 text-red-400 border-0 shrink-0">ongoing</Badge>}
                <span className="text-xs text-muted-foreground">
                  {o.wan_only ? "no internet (LAN up)" : "link down"}
                </span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* WAN latency & packet loss */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">WAN latency &amp; packet loss</CardTitle>
        </CardHeader>
        <CardContent>
          {latency.length === 0 ? (
            <p className="text-muted-foreground text-sm py-8 text-center">
              No connectivity samples yet — the probe runs every 30s.
            </p>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={latency}>
                <XAxis dataKey="bucket"
                  tickFormatter={(v) => new Date(v * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  tick={{ fontSize: 10, fill: "#71717a" }} stroke="#3f3f46" />
                <YAxis yAxisId="rtt" tick={{ fontSize: 10, fill: "#71717a" }} stroke="#3f3f46" width={40}
                  label={{ value: "ms", angle: -90, position: "insideLeft", fill: "#71717a", fontSize: 10 }} />
                <YAxis yAxisId="loss" orientation="right" domain={[0, 100]} width={40}
                  tick={{ fontSize: 10, fill: "#71717a" }} stroke="#3f3f46" />
                <Tooltip
                  labelFormatter={(v) => fmtTime(Number(v))}
                  contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: "6px" }}
                  labelStyle={{ color: "#a1a1aa" }} itemStyle={{ color: "#e4e4e7" }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line yAxisId="rtt" type="monotone" dataKey="avg_rtt" name="Latency (ms)" stroke="#818cf8" dot={false} strokeWidth={2} />
                <Line yAxisId="loss" type="monotone" dataKey="loss_pct" name="Loss (%)" stroke="#f87171" dot={false} strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      {/* Service & WAN monitors (uptime-kuma) */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">
            Service &amp; WAN monitors <span className="text-muted-foreground font-normal">— uptime-kuma</span>
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <div className="grid grid-cols-1 sm:grid-cols-2">
            {monitors.length === 0 && (
              <p className="text-muted-foreground text-sm px-6 py-4">No monitors ingested yet.</p>
            )}
            {monitors.map((m) => (
              <div key={m.id} className="flex items-center gap-3 px-4 py-2 border-b border-border">
                <span className={`inline-block w-2.5 h-2.5 rounded-full shrink-0 ${statusColor(m.status)}`} />
                <span className="text-sm flex-1 truncate">{m.name}</span>
                <span className="text-xs text-muted-foreground tabular-nums w-16 text-right">
                  {m.uptime_pct != null ? `${m.uptime_pct}%` : "—"}
                </span>
                <span className="text-xs text-muted-foreground tabular-nums w-16 text-right">
                  {m.ping_ms != null ? `${Math.round(m.ping_ms)} ms` : "—"}
                </span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Recent up/down events */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Recent up/down events</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <div className="divide-y divide-border text-sm max-h-72 overflow-auto">
            {events.length === 0 && (
              <p className="text-muted-foreground text-sm px-6 py-4">No status changes in this window.</p>
            )}
            {events.map((e, i) => (
              <div key={i} className="flex items-center gap-3 px-4 py-2">
                <span className="text-muted-foreground text-xs w-32 shrink-0 tabular-nums">{fmtTime(e.ts_unix)}</span>
                <Badge className={`border-0 shrink-0 ${
                  e.status === 1 ? "bg-green-500/20 text-green-400"
                    : e.status === 0 ? "bg-red-500/20 text-red-400"
                    : "bg-zinc-600/20 text-zinc-400"}`}>
                  {e.status === 1 ? "up" : e.status === 0 ? "down" : "other"}
                </Badge>
                <span className="text-sm shrink-0 w-32 truncate">{e.name}</span>
                <span className="text-xs text-muted-foreground truncate">{e.msg}</span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
