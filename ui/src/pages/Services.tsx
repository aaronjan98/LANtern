import { useEffect, useState, useCallback, useMemo } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from "recharts"

// What people are actually using on the network, service by service. Jellyfin
// (via its Playback Reporting plugin, pushed in from qwerty -- see
// api/routes/jellyfin.py) is the first source; more services join this page
// later without a rename.

interface PlaybackEvent {
  id: number
  ts_unix: number
  username: string | null
  item_id: string | null
  item_type: string | null
  item_name: string | null
  playback_method: string | null
  client_name: string | null
  device_name: string | null
  play_duration: number | null
}

interface UserSummary {
  username: string | null
  plays: number
  seconds: number
  last_seen: number
}

interface TitleSummary {
  item_name: string | null
  item_type: string | null
  plays: number
  seconds: number
}

interface Summary {
  hours: number
  plays: number
  seconds: number
  active_users: number
  by_user: UserSummary[]
  top_titles: TitleSummary[]
}

const WINDOWS = [
  { label: "Last 24h", value: "24" },
  { label: "Last 7d", value: "168" },
  { label: "Last 30d", value: "720" },
]

// Validated categorical palette (dark-mode steps), assigned by a series' fixed
// alphabetical slot -- never by its rank in the data -- so a user's color
// never changes as who-watched-most shifts day to day. This app is dark-only
// (index.html hardcodes <html class="dark">), so only the dark steps are
// needed. See the dataviz skill's references/palette.md for the validation.
const CATEGORICAL = [
  "#3987e5", "#d95926", "#199e70", "#c98500",
  "#d55181", "#008300", "#9085e9", "#e66767",
]
const CHART_SURFACE = "#1a1a19"   // the validated dark chart surface
const CHART_GRID = "#2c2c2a"      // hairline gridline, dark
const CHART_MUTED = "#898781"     // axis/tick labels, dark

function fmtTime(unix: number) {
  return new Date(unix * 1000).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  })
}

function fmtDuration(seconds: number): string {
  if (!seconds) return "0m"
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  if (h === 0) return `${m}m`
  return `${h}h ${m}m`
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

function ItemTypeBadge({ type }: { type: string | null }) {
  const map: Record<string, string> = {
    Movie: "bg-indigo-500/20 text-indigo-400",
    Episode: "bg-blue-500/20 text-blue-400",
    Audio: "bg-green-500/20 text-green-400",
  }
  return (
    <Badge className={`border-0 ${type && map[type] ? map[type] : "bg-zinc-600/20 text-zinc-400"}`}>
      {type ?? "?"}
    </Badge>
  )
}

// -- Hours-per-user-over-time chart -----------------------------------------

// Local calendar bucket (hour or day, in the *viewer's* timezone -- never a
// server-side assumption) truncated to that unit's start.
function bucketStart(date: Date, unit: "hour" | "day"): number {
  const d = new Date(date)
  d.setMinutes(0, 0, 0)
  if (unit === "day") d.setHours(0)
  return d.getTime()
}

function buildBuckets(hoursWindow: number, unit: "hour" | "day"): number[] {
  const now = new Date()
  const start = new Date(now.getTime() - hoursWindow * 3600 * 1000)
  const cursor = new Date(bucketStart(start, unit))
  const endKey = bucketStart(now, unit)
  const keys: number[] = []
  // setDate/setHours (not raw ms arithmetic) so this steps correctly across a
  // DST transition instead of drifting by an hour.
  while (cursor.getTime() <= endKey) {
    keys.push(cursor.getTime())
    if (unit === "day") cursor.setDate(cursor.getDate() + 1)
    else cursor.setHours(cursor.getHours() + 1)
  }
  return keys
}

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload || payload.length === 0) return null
  const rows = [...payload].filter((p: any) => p.value > 0).sort((a: any, b: any) => b.value - a.value)
  if (rows.length === 0) return null
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-md">
      <div className="text-muted-foreground mb-1.5">{label}</div>
      {rows.map((p: any) => (
        <div key={p.dataKey} className="flex items-center gap-2 py-0.5">
          <span className="inline-block w-3 h-0.5 rounded-full shrink-0" style={{ background: p.color }} />
          <span className="font-semibold tabular-nums">{Number(p.value).toFixed(1)}h</span>
          <span className="text-muted-foreground">{p.dataKey}</span>
        </div>
      ))}
    </div>
  )
}

function ChartLegend({ payload }: any) {
  if (!payload || payload.length === 0) return null
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1.5 justify-center text-xs text-muted-foreground pt-3">
      {payload.map((entry: any) => (
        <span key={entry.value} className="flex items-center gap-1.5">
          <span className="inline-block w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: entry.color }} />
          {entry.value}
        </span>
      ))}
    </div>
  )
}

function HoursPerUserChart({ events, hours }: { events: PlaybackEvent[]; hours: string }) {
  const hoursNum = Number(hours)
  const unit: "hour" | "day" = hoursNum <= 24 ? "hour" : "day"

  // Fixed identity order (alphabetical), not data rank -- a user's color and
  // stack position must never repaint because someone else watched more this
  // week. This order also IS the stack draw order, bottom to top.
  const allUsers = useMemo(() => {
    const set = new Set<string>()
    for (const e of events) if (e.username) set.add(e.username)
    return Array.from(set).sort()
  }, [events])

  const bucketKeys = useMemo(() => buildBuckets(hoursNum, unit), [hoursNum, unit])

  const chartData = useMemo(() => {
    const sums = new Map<number, Record<string, number>>()
    for (const k of bucketKeys) sums.set(k, {})
    for (const e of events) {
      if (!e.username || e.play_duration == null) continue
      const k = bucketStart(new Date(e.ts_unix * 1000), unit)
      const row = sums.get(k)
      if (!row) continue // outside the generated range (clock skew at the edge)
      row[e.username] = (row[e.username] ?? 0) + e.play_duration / 3600
    }
    return bucketKeys.map((k) => {
      const d = new Date(k)
      const label = unit === "day"
        ? d.toLocaleDateString([], { month: "short", day: "numeric" })
        : d.toLocaleTimeString([], { hour: "numeric" })
      const row = sums.get(k) ?? {}
      const out: Record<string, number | string> = { label }
      for (const u of allUsers) out[u] = Math.round((row[u] ?? 0) * 100) / 100
      return out
    })
  }, [bucketKeys, events, allUsers, unit])

  if (allUsers.length === 0) {
    return <p className="text-muted-foreground text-sm text-center py-12">No activity in this window.</p>
  }

  return (
    <div style={{ width: "100%", height: 280 }}>
      <ResponsiveContainer>
        <BarChart data={chartData} barCategoryGap={unit === "day" ? "20%" : "10%"}>
          <CartesianGrid vertical={false} stroke={CHART_GRID} />
          <XAxis dataKey="label" tick={{ fill: CHART_MUTED, fontSize: 12 }}
            axisLine={{ stroke: CHART_GRID }} tickLine={false} />
          <YAxis tick={{ fill: CHART_MUTED, fontSize: 12 }} axisLine={false} tickLine={false}
            width={36} tickFormatter={(v) => `${v}h`} allowDecimals={false} />
          <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgba(255,255,255,0.04)" }} />
          <Legend content={<ChartLegend />} />
          {allUsers.map((u, i) => (
            <Bar
              key={u}
              dataKey={u}
              stackId="hours"
              fill={CATEGORICAL[i % CATEGORICAL.length]}
              stroke={CHART_SURFACE}
              strokeWidth={2}
              maxBarSize={24}
              radius={i === allUsers.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

export default function Services() {
  const [hours, setHours] = useState("168")
  const [summary, setSummary] = useState<Summary | null>(null)
  const [events, setEvents] = useState<PlaybackEvent[]>([])

  const load = useCallback(() => {
    Promise.all([
      fetch(`/api/jellyfin/summary?hours=${hours}`).then((r) => r.json()),
      // Fetched once at a size that comfortably covers the chart's full window
      // (not just the "recent activity" table's page) -- the table below
      // slices to the newest 100 of these for display.
      fetch(`/api/jellyfin/playback?hours=${hours}&limit=1000`).then((r) => r.json()),
    ]).then(([s, e]) => { setSummary(s); setEvents(e) }).catch(() => {})
  }, [hours])

  useEffect(() => {
    load()
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [load])

  const topUser = summary?.by_user[0] ?? null
  const recentEvents = events.slice(0, 100)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Services</h1>
          <p className="text-sm text-muted-foreground">
            What's actually being watched/used, service by service. Jellyfin first.
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

      {/* Jellyfin */}
      <div className="space-y-3">
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">Jellyfin</h2>

        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard title="Plays" value={summary?.plays ?? "—"} />
          <StatCard title="Watch time" value={summary ? fmtDuration(summary.seconds) : "—"} />
          <StatCard title="Active users" value={summary?.active_users ?? "—"} />
          <StatCard title="Top watcher"
            value={topUser?.username ?? "—"}
            sub={topUser ? `${fmtDuration(topUser.seconds)} · ${topUser.plays} plays` : undefined} />
        </div>

        <Card>
          <CardHeader><CardTitle className="text-sm font-medium">Hours watched per user</CardTitle></CardHeader>
          <CardContent>
            <HoursPerUserChart events={events} hours={hours} />
          </CardContent>
        </Card>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          {/* By user */}
          <Card>
            <CardHeader><CardTitle className="text-sm font-medium">By user</CardTitle></CardHeader>
            <CardContent className="p-0">
              <div className="divide-y divide-border text-sm max-h-72 overflow-auto">
                {(!summary || summary.by_user.length === 0) && (
                  <p className="text-muted-foreground text-sm px-6 py-4">No activity in this window.</p>
                )}
                {summary?.by_user.map((u) => (
                  <div key={u.username ?? "unknown"} className="flex items-center gap-3 px-4 py-2.5">
                    <span className="font-medium w-24 shrink-0 truncate">{u.username ?? "unknown"}</span>
                    <span className="text-xs text-muted-foreground w-20 shrink-0 tabular-nums">
                      {fmtDuration(u.seconds)}
                    </span>
                    <span className="text-xs text-muted-foreground w-16 shrink-0 tabular-nums">
                      {u.plays} plays
                    </span>
                    <span className="text-xs text-muted-foreground truncate">
                      last {fmtTime(u.last_seen)}
                    </span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* Top titles */}
          <Card>
            <CardHeader><CardTitle className="text-sm font-medium">Top titles</CardTitle></CardHeader>
            <CardContent className="p-0">
              <div className="divide-y divide-border text-sm max-h-72 overflow-auto">
                {(!summary || summary.top_titles.length === 0) && (
                  <p className="text-muted-foreground text-sm px-6 py-4">No activity in this window.</p>
                )}
                {summary?.top_titles.map((t, i) => (
                  <div key={i} className="flex items-center gap-3 px-4 py-2.5">
                    <span className="w-16 shrink-0"><ItemTypeBadge type={t.item_type} /></span>
                    <span className="flex-1 min-w-0 truncate" title={t.item_name ?? undefined}>
                      {t.item_name ?? "—"}
                    </span>
                    <span className="text-xs text-muted-foreground shrink-0 tabular-nums">
                      {fmtDuration(t.seconds)}
                    </span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Recent activity */}
        <Card>
          <CardHeader><CardTitle className="text-sm font-medium">Recent activity</CardTitle></CardHeader>
          <CardContent className="p-0">
            <div className="overflow-auto max-h-[32rem]">
              <table className="w-full text-sm">
                <thead className="border-b border-border sticky top-0 bg-card">
                  <tr>
                    <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">When</th>
                    <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">User</th>
                    <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">Title</th>
                    <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">Client</th>
                    <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">Method</th>
                    <th className="text-right px-4 py-2.5 text-muted-foreground font-medium">Duration</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {recentEvents.length === 0 && (
                    <tr><td colSpan={6} className="text-center text-muted-foreground py-8">No plays in this window.</td></tr>
                  )}
                  {recentEvents.map((e) => (
                    <tr key={e.id} className="hover:bg-accent/30">
                      <td className="px-4 py-2 text-xs text-muted-foreground whitespace-nowrap">{fmtTime(e.ts_unix)}</td>
                      <td className="px-4 py-2 font-medium">{e.username ?? "—"}</td>
                      <td className="px-4 py-2 max-w-sm truncate" title={e.item_name ?? undefined}>
                        <span className="mr-2 inline-block align-middle"><ItemTypeBadge type={e.item_type} /></span>
                        {e.item_name ?? "—"}
                      </td>
                      <td className="px-4 py-2 text-xs text-muted-foreground">
                        {e.client_name ?? "—"}
                        {e.device_name && <span className="block text-[11px]">{e.device_name}</span>}
                      </td>
                      <td className="px-4 py-2 text-xs text-muted-foreground">{e.playback_method ?? "—"}</td>
                      <td className="px-4 py-2 text-right tabular-nums">
                        {e.play_duration != null ? fmtDuration(e.play_duration) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
