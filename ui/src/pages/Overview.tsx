import { useEffect, useState } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts"

interface Stats {
  devices: number
  active_devices: number
  dns_queries_today: number
  bytes_today: number
  top_domain_last_hour: { domain: string; cnt: number } | null
}

interface DnsHistoryBucket {
  bucket: number
  queries: number
}

interface DnsQuery {
  ts_unix: number
  ts: string
  client_ip: string
  qtype: string
  domain: string
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function formatTime(ts_unix: number) {
  return new Date(ts_unix * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  })
}

function StatCard({
  title,
  value,
  sub,
}: {
  title: string
  value: string | number
  sub?: string
}) {
  return (
    <Card>
      <CardHeader className="pb-1">
        <CardTitle className="text-sm font-medium text-muted-foreground">
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-bold">{value}</p>
        {sub && <p className="text-xs text-muted-foreground mt-1 truncate">{sub}</p>}
      </CardContent>
    </Card>
  )
}

export default function Overview() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [history, setHistory] = useState<DnsHistoryBucket[]>([])
  const [feed, setFeed] = useState<DnsQuery[]>([])

  useEffect(() => {
    const load = async () => {
      const [s, h, f] = await Promise.all([
        fetch("/api/stats").then((r) => r.json()),
        fetch("/api/dns/history?hours=24&bucket_minutes=60").then((r) => r.json()),
        fetch("/api/dns/recent?limit=20").then((r) => r.json()),
      ])
      setStats(s)
      setHistory(h)
      setFeed(f)
    }
    load()
    const interval = setInterval(load, 10000)
    return () => clearInterval(interval)
  }, [])

  if (!stats) return <p className="text-muted-foreground">Loading…</p>

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Overview</h1>

      {/* Stat cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard title="Active Devices" value={stats.active_devices} sub={`${stats.devices} total`} />
        <StatCard title="DNS Queries Today" value={stats.dns_queries_today.toLocaleString()} />
        <StatCard
          title="Top Domain (1h)"
          value={stats.top_domain_last_hour?.cnt.toLocaleString() ?? "—"}
          sub={stats.top_domain_last_hour?.domain}
        />
        <StatCard title="Traffic Today" value={formatBytes(stats.bytes_today)} />
      </div>

      {/* DNS sparkline */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">DNS Queries — last 24h</CardTitle>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={160}>
            <LineChart data={history}>
              <XAxis
                dataKey="bucket"
                tickFormatter={(v) =>
                  new Date(v * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
                }
                tick={{ fontSize: 11 }}
                stroke="hsl(var(--muted-foreground))"
              />
              <YAxis tick={{ fontSize: 11 }} stroke="hsl(var(--muted-foreground))" width={40} />
              <Tooltip
                labelFormatter={(v) => new Date(Number(v) * 1000).toLocaleTimeString()}
                contentStyle={{ background: "hsl(var(--card))", border: "1px solid hsl(var(--border))" }}
              />
              <Line
                type="monotone"
                dataKey="queries"
                stroke="hsl(var(--primary))"
                dot={false}
                strokeWidth={2}
              />
            </LineChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      {/* Activity feed */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Recent DNS Activity</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <div className="divide-y divide-border text-sm">
            {feed.map((q, i) => (
              <div key={i} className="flex items-center gap-4 px-6 py-2.5">
                <span className="text-muted-foreground w-20 shrink-0 tabular-nums">
                  {formatTime(q.ts_unix)}
                </span>
                <span className="text-muted-foreground w-28 shrink-0 tabular-nums truncate">
                  {q.client_ip}
                </span>
                <span className="text-xs font-mono bg-accent px-1.5 py-0.5 rounded w-12 text-center shrink-0">
                  {q.qtype}
                </span>
                <span className="truncate font-mono text-xs">{q.domain}</span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
