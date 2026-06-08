import { useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"

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

function formatDate(unix: number | null) {
  if (!unix) return "—"
  return new Date(unix * 1000).toLocaleString([], {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  })
}

function displayName(d: Device) {
  return d.label || d.hostname || d.mac
}

export default function Devices() {
  const [devices, setDevices] = useState<Device[]>([])
  const [filter, setFilter] = useState("")
  const navigate = useNavigate()

  useEffect(() => {
    fetch("/api/devices")
      .then((r) => r.json())
      .then(setDevices)
    const interval = setInterval(() =>
      fetch("/api/devices").then((r) => r.json()).then(setDevices), 30000)
    return () => clearInterval(interval)
  }, [])

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
                  <span
                    className={`inline-block w-2 h-2 rounded-full ${
                      d.active ? "bg-green-500" : "bg-zinc-600"
                    }`}
                  />
                </td>
                <td className="px-4 py-3">
                  <span className="font-medium">{displayName(d)}</span>
                  {d.hostname && (
                    <span className="text-xs text-muted-foreground ml-2 font-mono">{d.mac}</span>
                  )}
                </td>
                <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                  {d.last_ip ?? "—"}
                </td>
                <td className="px-4 py-3 text-muted-foreground">
                  {d.vendor === "Locally administered" ? (
                    <Badge variant="outline" className="text-xs">Randomized MAC</Badge>
                  ) : (
                    d.vendor ?? "—"
                  )}
                </td>
                <td className="px-4 py-3 text-muted-foreground text-xs">
                  {formatDate(d.last_seen_unix)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length === 0 && (
          <p className="text-center text-muted-foreground py-8 text-sm">No devices match your filter.</p>
        )}
      </div>
    </div>
  )
}
