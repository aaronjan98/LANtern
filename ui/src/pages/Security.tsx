import { useEffect, useState, useCallback } from "react"
import { DeviceLabel } from "@/lib/devices"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"

interface Summary {
  hours: number
  attempts: number
  attackers: number
  bans: number
  banned_ips: number
  logins: number
  caught: number
  coverage_pct: number | null
  uncaught: string[]
}
interface Attacker {
  src_ip: string
  attempts: number
  users_tried: number
  usernames: string[]
  first_seen: number
  last_seen: number
  banned: boolean
}
interface Login {
  ts_unix: number
  username: string | null
  src_ip: string | null
  port: number | null
  method: string | null
}
interface Ban {
  ts_unix: number
  jail: string
  action: string
  ip: string | null
}

const WINDOWS = [
  { label: "Last 24h", value: "24" },
  { label: "Last 7d", value: "168" },
  { label: "Last 30d", value: "720" },
]

// An unbanned attacker with real volume is the thing worth flagging — it's the
// low-and-slow brute-forcer that paces under fail2ban's findtime.
const EVADER_MIN_ATTEMPTS = 20

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

export default function Security() {
  const [hours, setHours] = useState("168")
  const [summary, setSummary] = useState<Summary | null>(null)
  const [attackers, setAttackers] = useState<Attacker[]>([])
  const [logins, setLogins] = useState<Login[]>([])
  const [bans, setBans] = useState<Ban[]>([])

  const load = useCallback(() => {
    Promise.all([
      fetch(`/api/security/summary?hours=${hours}`).then((r) => r.json()),
      fetch(`/api/security/ssh/attackers?hours=${hours}&limit=50`).then((r) => r.json()),
      fetch(`/api/security/ssh/logins?hours=${hours}&limit=25`).then((r) => r.json()),
      fetch(`/api/security/bans?hours=${hours}&limit=50`).then((r) => r.json()),
    ]).then(([s, a, l, b]) => { setSummary(s); setAttackers(a); setLogins(l); setBans(b) })
  }, [hours])

  useEffect(() => {
    load()
    const id = setInterval(load, 15000)
    return () => clearInterval(id)
  }, [load])

  const evaders = attackers.filter((a) => !a.banned && a.attempts >= EVADER_MIN_ATTEMPTS)
  const coverage = summary?.coverage_pct

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-semibold">Security</h1>
        <Select value={hours} onValueChange={(v) => v && setHours(v)}>
          <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
          <SelectContent>
            {WINDOWS.map((o) => (
              <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* "Is fail2ban working?" verdict banner */}
      {summary && (
        <Card
          className={
            evaders.length > 0
              ? "border-amber-500/40 bg-amber-500/5"
              : "border-green-500/40 bg-green-500/5"
          }
        >
          <CardContent className="py-4">
            {evaders.length > 0 ? (
              <div>
                <p className="font-medium text-amber-400">
                  ⚠ {evaders.length} high-volume attacker{evaders.length > 1 ? "s" : ""} evading fail2ban
                </p>
                <p className="text-sm text-muted-foreground mt-1">
                  fail2ban caught {summary.caught}/{summary.attackers} attacker IPs ({coverage}% coverage),
                  but {evaders.length} made ≥{EVADER_MIN_ATTEMPTS} attempts without being banned — likely
                  pacing under the 10-min findtime. Worst: <span className="font-mono">{evaders[0].src_ip}</span> with{" "}
                  {evaders[0].attempts} attempts.
                </p>
              </div>
            ) : (
              <p className="font-medium text-green-400">
                ✓ fail2ban is catching attackers — {summary.caught}/{summary.attackers} IPs banned ({coverage}% coverage),
                no high-volume evaders.
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* Summary stats */}
      {summary && (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatCard title="Attack Attempts" value={summary.attempts.toLocaleString()} sub={`${summary.attackers} unique IPs`} />
          <StatCard title="fail2ban Bans" value={summary.bans.toLocaleString()} sub={`${summary.banned_ips} IPs banned`} />
          <StatCard title="Coverage" value={coverage != null ? `${coverage}%` : "—"} sub={`${summary.caught} of ${summary.attackers} caught`} />
          <StatCard title="Successful Logins" value={summary.logins.toLocaleString()} sub="accepted SSH sessions" />
        </div>
      )}

      {/* Top attackers */}
      <Card>
        <CardHeader><CardTitle className="text-sm font-medium">Top Attackers</CardTitle></CardHeader>
        <CardContent className="p-0">
          <div className="overflow-auto max-h-[50vh]">
            <table className="w-full text-sm">
              <thead className="border-b border-border sticky top-0 bg-card">
                <tr>
                  <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">Source IP</th>
                  <th className="text-right px-4 py-2.5 text-muted-foreground font-medium">Attempts</th>
                  <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">Usernames tried</th>
                  <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">Last seen</th>
                  <th className="text-left px-4 py-2.5 text-muted-foreground font-medium">fail2ban</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {attackers.length === 0 && (
                  <tr><td colSpan={5} className="text-center text-muted-foreground py-8">No attempts in window.</td></tr>
                )}
                {attackers.map((a) => (
                  <tr key={a.src_ip} className="hover:bg-accent/30">
                    <td className="px-4 py-2 font-mono text-xs">{a.src_ip}</td>
                    <td className="px-4 py-2 text-right tabular-nums">{a.attempts.toLocaleString()}</td>
                    <td className="px-4 py-2 text-xs text-muted-foreground max-w-xs truncate">
                      {a.usernames.join(", ")}{a.users_tried > a.usernames.length ? " …" : ""}
                    </td>
                    <td className="px-4 py-2 text-xs text-muted-foreground">{fmtTime(a.last_seen)}</td>
                    <td className="px-4 py-2">
                      {a.banned ? (
                        <Badge className="bg-green-500/20 text-green-400 border-0">banned</Badge>
                      ) : a.attempts >= EVADER_MIN_ATTEMPTS ? (
                        <Badge className="bg-red-500/20 text-red-400 border-0">evading</Badge>
                      ) : (
                        <Badge variant="outline" className="text-muted-foreground">not banned</Badge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Successful logins */}
        <Card>
          <CardHeader><CardTitle className="text-sm font-medium">Successful Logins</CardTitle></CardHeader>
          <CardContent className="p-0">
            <div className="divide-y divide-border text-sm max-h-72 overflow-auto">
              {logins.length === 0 && (
                <p className="text-muted-foreground text-sm px-6 py-4">No logins recorded.</p>
              )}
              {logins.map((l, i) => (
                <div key={i} className="flex items-center gap-3 px-4 py-2">
                  <span className="text-muted-foreground text-xs w-24 shrink-0 tabular-nums">{fmtTime(l.ts_unix)}</span>
                  <span className="font-medium w-16 shrink-0 truncate">{l.username ?? "—"}</span>
                  <span className="text-xs text-muted-foreground shrink-0">from</span>
                  <span className="text-xs w-28 shrink-0 truncate"><DeviceLabel ip={l.src_ip} /></span>
                  <span className="text-xs font-mono bg-accent px-1.5 py-0.5 rounded shrink-0">{l.method ?? "?"}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Recent fail2ban actions */}
        <Card>
          <CardHeader><CardTitle className="text-sm font-medium">Recent fail2ban Actions</CardTitle></CardHeader>
          <CardContent className="p-0">
            <div className="divide-y divide-border text-sm max-h-72 overflow-auto">
              {bans.length === 0 && (
                <p className="text-muted-foreground text-sm px-6 py-4">No actions recorded.</p>
              )}
              {bans.map((b, i) => (
                <div key={i} className="flex items-center gap-3 px-4 py-2">
                  <span className="text-muted-foreground text-xs w-24 shrink-0 tabular-nums">{fmtTime(b.ts_unix)}</span>
                  <span
                    className={`text-xs px-1.5 py-0.5 rounded shrink-0 w-16 text-center ${
                      b.action.includes("Ban")
                        ? "bg-red-500/20 text-red-400"
                        : b.action === "Unban"
                        ? "bg-zinc-500/20 text-zinc-400"
                        : "bg-amber-500/20 text-amber-400"
                    }`}
                  >
                    {b.action}
                  </span>
                  <span className="font-mono text-xs">{b.ip}</span>
                  <span className="text-xs text-muted-foreground">{b.jail}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
