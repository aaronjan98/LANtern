import { useEffect, useState, useCallback } from "react"
import { useParams, useNavigate } from "react-router-dom"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

// Who is hitting this vhost, and with what -- the drill-down the DNS tab's
// "top domains" list links into. http_requests.host (added 2026-10-08, via
// nginx's $host) is what makes this possible; rows ingested before that are
// NULL and won't appear here.

interface Summary {
  domain: string
  hours: number
  requests: number
  clients: number
  first_seen: number | null
  last_seen: number | null
  statuses: { status: number; n: number }[]
}

interface ClientRow {
  client_ip: string
  label: string | null
  hostname: string | null
  vendor: string | null
  requests: number
  distinct_user_agents: number
  last_seen: number
  is_lan: boolean
}

interface PathRow {
  path_base: string
  requests: number
  clients: number
  last_seen: number
}

interface RecentRow {
  ts_unix: number
  client_ip: string
  method: string
  path: string
  status: number
  user_agent: string | null
}

const WINDOWS = [
  { label: "Last 24h", value: "24" },
  { label: "Last 7d", value: "168" },
  { label: "Last 30d", value: "720" },
]

function fmtTime(unix: number) {
  return new Date(unix * 1000).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  })
}

function StatCard({ title, value, sub }: { title: string; value: string | number; sub?: string }) {
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

function StatusBadge({ status }: { status: number }) {
  const cls = status < 300 ? "bg-green-500/20 text-green-400"
    : status < 400 ? "bg-blue-500/20 text-blue-400"
    : status < 500 ? "bg-amber-500/20 text-amber-400"
    : "bg-red-500/20 text-red-400"
  return <Badge className={`border-0 ${cls}`}>{status}</Badge>
}

export default function DomainDetail() {
  const { domain } = useParams<{ domain: string }>()
  const navigate = useNavigate()
  const decoded = decodeURIComponent(domain ?? "")

  const [hours, setHours] = useState("168")
  const [summary, setSummary] = useState<Summary | null>(null)
  const [clients, setClients] = useState<ClientRow[]>([])
  const [paths, setPaths] = useState<PathRow[]>([])
  const [recent, setRecent] = useState<RecentRow[]>([])

  const load = useCallback(() => {
    if (!decoded) return
    const q = `hours=${hours}`
    const base = `/api/domains/${encodeURIComponent(decoded)}`
    Promise.all([
      fetch(`${base}/summary?${q}`).then((r) => r.json()),
      fetch(`${base}/clients?${q}&limit=30`).then((r) => r.json()),
      fetch(`${base}/paths?${q}&limit=30`).then((r) => r.json()),
      fetch(`${base}/recent?${q}&limit=100`).then((r) => r.json()),
    ]).then(([s, c, p, r]) => { setSummary(s); setClients(c); setPaths(p); setRecent(r) }).catch(() => {})
  }, [decoded, hours])

  useEffect(() => {
    load()
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [load])

  const topExternal = clients.filter((c) => !c.is_lan)[0] ?? null

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <button
            onClick={() => navigate("/dns")}
            className="text-sm text-muted-foreground hover:text-foreground mb-2 flex items-center gap-1"
          >
            ← DNS
          </button>
          <h1 className="text-2xl font-semibold font-mono">{decoded}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Who's hitting this vhost, and with what.
          </p>
        </div>
        <Select value={hours} onValueChange={(v) => v && setHours(v)}>
          <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
          <SelectContent>
            {WINDOWS.map((o) => (
              <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard title="Requests" value={summary?.requests?.toLocaleString() ?? "—"} />
        <StatCard title="Unique clients" value={summary?.clients ?? "—"} />
        <StatCard title="First seen" value={summary?.first_seen ? fmtTime(summary.first_seen) : "—"} />
        <StatCard title="Top non-LAN visitor"
          value={topExternal?.client_ip ?? "none"}
          sub={topExternal ? `${topExternal.requests} requests` : "no outside traffic in this window"} />
      </div>

      {summary && summary.requests === 0 && (
        <Card>
          <CardContent className="py-6">
            <p className="text-muted-foreground text-sm">
              No requests tagged with this domain in this window. If this domain was
              active before 2026-10-08, that traffic predates nginx logging $host and
              won't show here -- only requests since then are taggable.
            </p>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Clients */}
        <Card>
          <CardHeader><CardTitle className="text-sm font-medium">Clients</CardTitle></CardHeader>
          <CardContent className="p-0">
            <div className="divide-y divide-border text-sm max-h-80 overflow-auto">
              {clients.length === 0 && (
                <p className="text-muted-foreground text-sm px-6 py-4">No activity in this window.</p>
              )}
              {clients.map((c) => (
                <div key={c.client_ip} className="flex items-center gap-3 px-4 py-2.5">
                  <span className={`inline-block w-2 h-2 rounded-full shrink-0 ${c.is_lan ? "bg-zinc-500" : "bg-amber-400"}`}
                    title={c.is_lan ? "LAN device" : "outside your network"} />
                  <span className="font-medium w-36 shrink-0 truncate">
                    {c.label || c.hostname || c.client_ip}
                  </span>
                  <span className="text-xs text-muted-foreground w-24 shrink-0 font-mono truncate">
                    {c.client_ip}
                  </span>
                  <span className="text-xs text-muted-foreground w-16 shrink-0 tabular-nums text-right">
                    {c.requests.toLocaleString()}
                  </span>
                  <span className="text-xs text-muted-foreground truncate">
                    last {fmtTime(c.last_seen)}
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Paths */}
        <Card>
          <CardHeader><CardTitle className="text-sm font-medium">Top paths</CardTitle></CardHeader>
          <CardContent className="p-0">
            <div className="divide-y divide-border text-sm max-h-80 overflow-auto">
              {paths.length === 0 && (
                <p className="text-muted-foreground text-sm px-6 py-4">No activity in this window.</p>
              )}
              {paths.map((p) => (
                <div key={p.path_base} className="flex items-center gap-3 px-4 py-2.5">
                  <span className="flex-1 min-w-0 font-mono text-xs truncate" title={p.path_base}>
                    {p.path_base}
                  </span>
                  <span className="text-xs text-muted-foreground shrink-0 tabular-nums">
                    {p.requests.toLocaleString()}
                  </span>
                  <span className="text-xs text-muted-foreground shrink-0 tabular-nums w-16 text-right">
                    {p.clients} client{p.clients === 1 ? "" : "s"}
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Status codes */}
      {summary && summary.statuses.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap">
          {summary.statuses.map((s) => (
            <span key={s.status} className="flex items-center gap-1.5 text-sm">
              <StatusBadge status={s.status} />
              <span className="text-muted-foreground tabular-nums">{s.n.toLocaleString()}</span>
            </span>
          ))}
        </div>
      )}

      {/* Recent requests */}
      <Card>
        <CardHeader><CardTitle className="text-sm font-medium">Recent requests</CardTitle></CardHeader>
        <CardContent className="p-0">
          <div className="overflow-auto max-h-[32rem]">
            <table className="w-full text-sm">
              <thead className="border-b border-border sticky top-0 bg-card">
                <tr>
                  <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">When</th>
                  <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">Client</th>
                  <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">Path</th>
                  <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">Status</th>
                  <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">User agent</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {recent.length === 0 && (
                  <tr><td colSpan={5} className="text-center text-muted-foreground py-8">No requests in this window.</td></tr>
                )}
                {recent.map((r, i) => (
                  <tr key={i} className="hover:bg-accent/30">
                    <td className="px-4 py-2 text-xs text-muted-foreground whitespace-nowrap">{fmtTime(r.ts_unix)}</td>
                    <td className="px-4 py-2 font-mono text-xs">{r.client_ip}</td>
                    <td className="px-4 py-2 font-mono text-xs max-w-sm truncate" title={r.path}>
                      {r.method} {r.path}
                    </td>
                    <td className="px-4 py-2"><StatusBadge status={r.status} /></td>
                    <td className="px-4 py-2 text-xs text-muted-foreground max-w-xs truncate" title={r.user_agent ?? undefined}>
                      {r.user_agent ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
