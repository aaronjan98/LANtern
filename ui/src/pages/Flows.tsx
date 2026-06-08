import { useEffect, useState, useCallback } from "react"
import { useNavigate } from "react-router-dom"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
} from "recharts"

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

interface Talker {
  addr: string
  bytes: number
  flows: number
}

interface TopTalkers {
  top_sources: Talker[]
  top_destinations: Talker[]
}

const TIME_OPTIONS = [
  { label: "Last hour", value: "60" },
  { label: "Last 6h", value: "360" },
  { label: "Last 24h", value: "1440" },
]

const PROTO_COLORS: Record<string, string> = {
  TCP: "#818cf8",
  UDP: "#34d399",
  ICMP: "#fbbf24",
  ICMPv6: "#fbbf24",
}

function fmtBytes(b: number) {
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
  return `${(b / 1024 / 1024).toFixed(1)} MB`
}

function fmtTime(unix: number) {
  return new Date(unix * 1000).toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  })
}

function addrLabel(addr: string | null, port: number | null) {
  if (!addr) return "—"
  return port ? `${addr}:${port}` : addr
}

export default function Flows() {
  const navigate = useNavigate()
  const [flows, setFlows] = useState<Flow[]>([])
  const [talkers, setTalkers] = useState<TopTalkers>({ top_sources: [], top_destinations: [] })
  const [hostFilter, setHostFilter] = useState("")
  const [minutes, setMinutes] = useState("60")

  const load = useCallback(() => {
    const params = new URLSearchParams({ limit: "100" })
    if (hostFilter) params.set("host", hostFilter)
    Promise.all([
      fetch(`/api/flows/recent?${params}`).then((r) => r.json()),
      fetch(`/api/flows/top-talkers?minutes=${minutes}`).then((r) => r.json()),
    ]).then(([f, t]) => { setFlows(f); setTalkers(t) })
  }, [hostFilter, minutes])

  useEffect(() => {
    load()
    const id = setInterval(load, 15000)
    return () => clearInterval(id)
  }, [load])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-semibold">Flows</h1>
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

      {/* Top talkers */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {([
          { label: "Top Sources", data: talkers.top_sources },
          { label: "Top Destinations", data: talkers.top_destinations },
        ] as const).map(({ label, data }) => (
          <Card key={label}>
            <CardHeader>
              <CardTitle className="text-sm font-medium">{label} by bytes</CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={data} layout="vertical" margin={{ left: 8, right: 48, top: 4, bottom: 4 }}>
                  <XAxis type="number" tickFormatter={(v) => fmtBytes(v)}
                    tick={{ fontSize: 10, fill: "#71717a" }} stroke="#3f3f46" />
                  <YAxis type="category" dataKey="addr" width={120}
                    tick={{ fontSize: 10, fill: "#71717a" }} stroke="#3f3f46"
                    tickFormatter={(v: string) => v.length > 15 ? v.slice(0, 14) + "…" : v} />
                  <Tooltip
                    contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", borderRadius: "6px" }}
                    labelStyle={{ color: "#e4e4e7" }} itemStyle={{ color: "#a1a1aa" }}
                    formatter={(v: unknown) => [fmtBytes(v as number), "bytes"]}
                  />
                  <Bar dataKey="bytes" radius={[0, 3, 3, 0]}>
                    {data.map((_, i) => (
                      <Cell key={i} fill={i === 0 ? "#818cf8" : "#3f3f46"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Flow table */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-sm font-medium">Recent Flows</CardTitle>
          <Input
            placeholder="Filter by IP…"
            value={hostFilter}
            onChange={(e) => setHostFilter(e.target.value)}
            className="max-w-48 h-8 text-xs"
          />
        </CardHeader>
        <CardContent className="p-0">
          <div className="overflow-auto max-h-[50vh]">
            <table className="w-full text-xs">
              <thead className="border-b border-border sticky top-0 bg-card">
                <tr>
                  <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">Time</th>
                  <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">Source</th>
                  <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">Destination</th>
                  <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">Proto</th>
                  <th className="text-right px-4 py-2.5 text-muted-foreground font-medium">Bytes</th>
                  <th className="text-right px-4 py-2.5 text-muted-foreground font-medium">Pkts</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border font-mono">
                {flows.length === 0 && (
                  <tr><td colSpan={6} className="text-center text-muted-foreground py-8">No flows.</td></tr>
                )}
                {flows.map((f, i) => (
                  <tr key={i} className="hover:bg-accent/30">
                    <td className="px-4 py-2 text-muted-foreground">{fmtTime(f.received_unix)}</td>
                    <td className="px-4 py-2">
                      <span
                        className="cursor-pointer hover:text-primary"
                        onClick={() => f.src_addr && navigate(`/devices/${encodeURIComponent(f.src_addr)}`)}
                      >
                        {addrLabel(f.src_addr, f.src_port)}
                      </span>
                    </td>
                    <td className="px-4 py-2">
                      <span
                        className="cursor-pointer hover:text-primary"
                        onClick={() => f.dst_addr && navigate(`/devices/${encodeURIComponent(f.dst_addr)}`)}
                      >
                        {addrLabel(f.dst_addr, f.dst_port)}
                      </span>
                    </td>
                    <td className="px-4 py-2">
                      <span
                        className="px-1.5 py-0.5 rounded text-xs"
                        style={{
                          background: (PROTO_COLORS[f.protocol_name] ?? "#3f3f46") + "33",
                          color: PROTO_COLORS[f.protocol_name] ?? "#71717a",
                        }}
                      >
                        {f.protocol_name}
                      </span>
                    </td>
                    <td className="px-4 py-2 text-right text-muted-foreground">
                      {f.bytes != null ? fmtBytes(f.bytes) : "—"}
                    </td>
                    <td className="px-4 py-2 text-right text-muted-foreground">
                      {f.packets ?? "—"}
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
