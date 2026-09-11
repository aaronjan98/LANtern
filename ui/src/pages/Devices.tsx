import { useEffect, useState, useCallback } from "react"
import { useNavigate } from "react-router-dom"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

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

interface NewDevice extends Device {
  guess: string
  top_domains: string[]
  is_new: boolean
}

interface Person {
  id: number
  name: string
}

function formatDate(unix: number | null) {
  if (!unix) return "—"
  return new Date(unix * 1000).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  })
}

function displayName(d: Device) {
  return d.label || d.hostname || d.mac
}

// One card in the New-devices inbox: shows the auto-guess pre-filled, plus
// Name it / Assign / Dismiss. Any action resolves the device and removes it.
function NewDeviceCard({ d, people, onDone }: { d: NewDevice; people: Person[]; onDone: () => void }) {
  const [name, setName] = useState(d.guess)
  const put = (path: string, body: unknown) =>
    fetch(`/api/devices/${encodeURIComponent(d.mac)}/${path}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(onDone)

  return (
    <Card>
      <CardContent className="p-4 space-y-3">
        <div className="flex items-center gap-2 flex-wrap text-xs">
          {d.is_new && <Badge className="bg-blue-500/20 text-blue-400 border-0">new</Badge>}
          {d.active && <span className="inline-block w-2 h-2 rounded-full bg-green-500" />}
          <span className="font-mono text-muted-foreground">{d.last_ip ?? "—"} · {d.mac}</span>
          <span className="text-muted-foreground">· first seen {formatDate(d.first_seen_unix)}</span>
          {d.vendor && d.vendor !== "Locally administered" && (
            <span className="text-muted-foreground">· {d.vendor}</span>
          )}
        </div>
        {d.top_domains.length > 0 && (
          <p className="text-xs text-muted-foreground truncate">
            <span className="text-foreground/70">fingerprint:</span> {d.top_domains.join(", ")}
          </p>
        )}
        <div className="flex items-center gap-2 flex-wrap">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Name this device…"
            className="max-w-xs h-8 text-sm"
          />
          <button
            onClick={() => name.trim() && put("label", { label: name.trim() })}
            disabled={!name.trim()}
            className="text-sm px-3 py-1.5 rounded-md bg-primary text-primary-foreground disabled:opacity-40"
          >
            Name it
          </button>
          <select
            defaultValue=""
            onChange={(e) => e.target.value && put("person", { person_id: Number(e.target.value) })}
            className="text-sm px-2 py-1.5 rounded-md border border-border bg-background"
          >
            <option value="" disabled>Assign to…</option>
            {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <button
            onClick={() => put("dismiss", { dismissed: true })}
            className="text-sm px-3 py-1.5 rounded-md border border-border hover:bg-accent"
            title="Mark as known (e.g. a guest) — clears it and protects it from pruning"
          >
            Dismiss
          </button>
        </div>
      </CardContent>
    </Card>
  )
}

export default function Devices() {
  const [devices, setDevices] = useState<Device[]>([])
  const [newDevices, setNewDevices] = useState<NewDevice[]>([])
  const [people, setPeople] = useState<Person[]>([])
  const [filter, setFilter] = useState("")
  const navigate = useNavigate()

  const loadAll = useCallback(() => {
    fetch("/api/devices").then((r) => r.json()).then(setDevices)
  }, [])
  const loadNew = useCallback(() => {
    fetch("/api/devices/new").then((r) => r.json()).then(setNewDevices)
  }, [])

  useEffect(() => {
    loadAll(); loadNew()
    fetch("/api/people").then((r) => r.json()).then(setPeople).catch(() => {})
    const id = setInterval(() => { loadAll(); loadNew() }, 30000)
    return () => clearInterval(id)
  }, [loadAll, loadNew])

  const filtered = devices.filter((d) => {
    const q = filter.toLowerCase()
    return (
      !q ||
      (d.label ?? "").toLowerCase().includes(q) ||
      (d.hostname ?? "").toLowerCase().includes(q) ||
      (d.last_ip ?? "").includes(q) ||
      (d.vendor ?? "").toLowerCase().includes(q) ||
      d.mac.toLowerCase().includes(q)
    )
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Devices</h1>
        <span className="text-sm text-muted-foreground">
          {devices.filter((d) => d.active).length} active / {devices.length} total
        </span>
      </div>

      <Tabs defaultValue="all">
        <TabsList>
          <TabsTrigger value="all">All devices</TabsTrigger>
          <TabsTrigger value="new">
            New / unidentified
            {newDevices.length > 0 && (
              <span className="ml-1.5 text-xs rounded-full bg-blue-500/20 text-blue-400 px-1.5">
                {newDevices.length}
              </span>
            )}
          </TabsTrigger>
        </TabsList>

        {/* ── All devices ── */}
        <TabsContent value="all" className="space-y-4 mt-4">
          <Input
            placeholder="Filter by hostname, IP, vendor, or MAC…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="max-w-sm"
          />
          <div className="rounded-lg border border-border overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-card border-b border-border">
                <tr>
                  <th className="text-left px-4 py-3 font-medium text-muted-foreground w-8"></th>
                  <th className="text-left px-4 py-3 font-medium text-muted-foreground">Hostname / MAC</th>
                  <th className="text-left px-4 py-3 font-medium text-muted-foreground">IP</th>
                  <th className="text-left px-4 py-3 font-medium text-muted-foreground">Vendor</th>
                  <th className="text-left px-4 py-3 font-medium text-muted-foreground">Last seen</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {filtered.map((d) => (
                  <tr
                    key={d.mac}
                    className="hover:bg-accent/30 cursor-pointer transition-colors"
                    onClick={() => navigate(`/devices/${encodeURIComponent(d.mac)}`)}
                  >
                    <td className="px-4 py-3">
                      <span className={`inline-block w-2 h-2 rounded-full ${d.active ? "bg-green-500" : "bg-zinc-600"}`} />
                    </td>
                    <td className="px-4 py-3">
                      <span className="font-medium">{displayName(d)}</span>
                      {d.hostname && <span className="text-xs text-muted-foreground ml-2 font-mono">{d.mac}</span>}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{d.last_ip ?? "—"}</td>
                    <td className="px-4 py-3 text-muted-foreground">
                      {d.vendor === "Locally administered"
                        ? <Badge variant="outline" className="text-xs">Randomized MAC</Badge>
                        : (d.vendor ?? "—")}
                    </td>
                    <td className="px-4 py-3 text-muted-foreground text-xs">{formatDate(d.last_seen_unix)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {filtered.length === 0 && (
              <p className="text-center text-muted-foreground py-8 text-sm">No devices match your filter.</p>
            )}
          </div>
        </TabsContent>

        {/* ── New / unidentified inbox ── */}
        <TabsContent value="new" className="space-y-3 mt-4">
          <p className="text-sm text-muted-foreground">
            Devices seen recently that aren't yet a labeled device, a person's device, or a dismissed guest.
            Each has a best-guess from its vendor + DNS traffic — confirm the name, assign it to someone, or dismiss it.
          </p>
          {newDevices.length === 0 ? (
            <p className="text-muted-foreground text-sm py-8 text-center">
              🎉 Nothing to review — every recent device is accounted for.
            </p>
          ) : (
            <div className="space-y-3">
              {newDevices.map((d) => (
                <NewDeviceCard key={d.mac} d={d} people={people} onDone={() => { loadNew(); loadAll() }} />
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}
