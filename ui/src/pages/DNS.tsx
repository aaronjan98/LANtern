import { useEffect, useState, useCallback } from "react"
import { useNavigate } from "react-router-dom"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
} from "recharts"

interface DnsQuery {
  ts_unix: number
  client_ip: string
  qtype: string
  domain: string
}

interface TopDomain {
  domain: string
  queries: number
  clients: number
}

interface Client {
  client_ip: string
  hostname: string | null
  queries: number
}

const TIME_OPTIONS = [
  { label: "Last hour", value: "60" },
  { label: "Last 6h", value: "360" },
  { label: "Last 24h", value: "1440" },
  { label: "Last 7d", value: "10080" },
]

const QTYPE_OPTIONS = [
  { label: "All types", value: "all" },
  { label: "A (IPv4)", value: "A" },
  { label: "AAAA (IPv6)", value: "AAAA" },
]

function fmtTime(ts_unix: number) {
  return new Date(ts_unix * 1000).toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  })
}

export default function DNS() {
  const navigate = useNavigate()
  const [feed, setFeed] = useState<DnsQuery[]>([])
  const [topDomains, setTopDomains] = useState<TopDomain[]>([])
  const [clients, setClients] = useState<Client[]>([])
  const [minutes, setMinutes] = useState("60")
  const [qtype, setQtype] = useState("all")
  const [clientFilter, setClientFilter] = useState("")

  const loadFeed = useCallback(() => {
    const params = new URLSearchParams({ limit: "100" })
    if (clientFilter) params.set("client_ip", clientFilter)
    fetch(`/api/dns/recent?${params}`).then((r) => r.json()).then(setFeed)
  }, [clientFilter])

  const loadTop = useCallback(() => {
    fetch(`/api/dns/top?minutes=${minutes}&limit=20`).then((r) => r.json()).then(setTopDomains)
  }, [minutes])

  const loadClients = useCallback(() => {
    fetch(`/api/dns/clients?minutes=${minutes}`).then((r) => r.json()).then(setClients)
  }, [minutes])

  useEffect(() => {
    loadFeed(); loadTop(); loadClients()
    const id = setInterval(() => { loadFeed(); loadTop(); loadClients() }, 10000)
    return () => clearInterval(id)
  }, [loadFeed, loadTop, loadClients])

  const filteredFeed = qtype === "all"
    ? feed
    : feed.filter((q) => q.qtype === qtype)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-semibold">DNS</h1>
        <div className="flex items-center gap-2">
          <Select value={minutes} onValueChange={(v) => v && setMinutes(v)}>
            <SelectTrigger className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {TIME_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <Tabs defaultValue="feed">
        <TabsList>
          <TabsTrigger value="feed">Feed</TabsTrigger>
          <TabsTrigger value="top">Top Domains</TabsTrigger>
          <TabsTrigger value="clients">Clients</TabsTrigger>
        </TabsList>

        {/* ── Feed tab ── */}
        <TabsContent value="feed" className="space-y-3 mt-4">
          <div className="flex gap-2">
            <Input
              placeholder="Filter by client IP…"
              value={clientFilter}
              onChange={(e) => setClientFilter(e.target.value)}
              className="max-w-xs"
            />
            <Select value={qtype} onValueChange={(v) => v && setQtype(v)}>
              <SelectTrigger className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {QTYPE_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Card>
            <CardContent className="p-0">
              <div className="divide-y divide-border text-sm max-h-[60vh] overflow-auto">
                {filteredFeed.length === 0 && (
                  <p className="text-muted-foreground text-sm px-6 py-8 text-center">No queries match.</p>
                )}
                {filteredFeed.map((q, i) => (
                  <div key={i} className="flex items-center gap-4 px-4 py-2.5">
                    <span className="text-muted-foreground w-20 shrink-0 tabular-nums text-xs">
                      {fmtTime(q.ts_unix)}
                    </span>
                    <span
                      className="text-xs font-mono text-muted-foreground w-28 shrink-0 truncate cursor-pointer hover:text-foreground"
                      onClick={() => navigate(`/devices/${encodeURIComponent(q.client_ip)}`)}
                      title="View device"
                    >
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
        </TabsContent>

        {/* ── Top Domains tab ── */}
        <TabsContent value="top" className="space-y-4 mt-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-medium">Top domains by query count</CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={320}>
                <BarChart
                  data={topDomains.slice(0, 15)}
                  layout="vertical"
                  margin={{ left: 8, right: 32, top: 4, bottom: 4 }}
                >
                  <XAxis type="number" tick={{ fontSize: 11, fill: "#71717a" }} stroke="#3f3f46" />
                  <YAxis
                    type="category"
                    dataKey="domain"
                    width={200}
                    tick={{ fontSize: 11, fill: "#71717a" }}
                    stroke="#3f3f46"
                    tickFormatter={(v: string) => v.length > 30 ? "…" + v.slice(-28) : v}
                  />
                  <Tooltip
                    contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: "6px" }}
                    labelStyle={{ color: "#e4e4e7" }}
                    itemStyle={{ color: "#a1a1aa" }}
                    formatter={(v: unknown) => [(v as number).toLocaleString(), "queries"]}
                  />
                  <Bar dataKey="queries" radius={[0, 3, 3, 0]}>
                    {topDomains.slice(0, 15).map((_, i) => (
                      <Cell key={i} fill={i === 0 ? "#818cf8" : "#3f3f46"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-0">
              <table className="w-full text-sm">
                <thead className="border-b border-border">
                  <tr>
                    <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">#</th>
                    <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">Domain</th>
                    <th className="text-right px-4 py-2.5 text-muted-foreground font-medium">Queries</th>
                    <th className="text-right px-4 py-2.5 text-muted-foreground font-medium">Clients</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {topDomains.map((d, i) => (
                    <tr key={d.domain} className="hover:bg-accent/30">
                      <td className="px-4 py-2 text-muted-foreground text-xs">{i + 1}</td>
                      <td className="px-4 py-2 font-mono text-xs">{d.domain}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{d.queries.toLocaleString()}</td>
                      <td className="px-4 py-2 text-right text-muted-foreground tabular-nums">{d.clients}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── Clients tab ── */}
        <TabsContent value="clients" className="mt-4">
          <Card>
            <CardContent className="p-0">
              <table className="w-full text-sm">
                <thead className="border-b border-border">
                  <tr>
                    <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">Device</th>
                    <th className="text-right px-4 py-2.5 text-muted-foreground font-medium">Queries</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {clients.map((c) => (
                    <tr
                      key={c.client_ip}
                      className="hover:bg-accent/30 cursor-pointer"
                      onClick={() => navigate(`/devices/${encodeURIComponent(c.client_ip)}`)}
                    >
                      <td className="px-4 py-2.5">
                        <p className="font-medium">{c.hostname ?? c.client_ip}</p>
                        {c.hostname && (
                          <p className="text-xs text-muted-foreground font-mono">{c.client_ip}</p>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-right tabular-nums">{c.queries.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}
