import { useEffect, useState, useCallback } from "react"
import { useNavigate } from "react-router-dom"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"

interface Person {
  id: number
  name: string
  note: string | null
  device_count: number
  active_devices: number
  online: boolean
}

interface Device {
  mac: string
  hostname: string | null
  last_ip: string | null
  label: string | null
  person_id: number | null
  active: boolean
}

function deviceName(d: Device): string {
  return d.label || d.hostname || d.last_ip || d.mac
}

export default function People() {
  const navigate = useNavigate()
  const [people, setPeople] = useState<Person[]>([])
  const [devices, setDevices] = useState<Device[]>([])
  const [newName, setNewName] = useState("")

  const load = useCallback(() => {
    Promise.all([
      fetch("/api/people").then((r) => r.json()),
      fetch("/api/devices").then((r) => r.json()),
    ]).then(([p, d]) => { setPeople(p); setDevices(d) })
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [load])

  function addPerson() {
    const name = newName.trim()
    if (!name) return
    fetch("/api/people", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }).then(() => { setNewName(""); load() })
  }

  function removePerson(p: Person) {
    if (!confirm(`Delete "${p.name}"? Their ${p.device_count} device(s) will be unassigned, not deleted.`)) return
    fetch(`/api/people/${p.id}`, { method: "DELETE" }).then(load)
  }

  const unassigned = devices.filter((d) => d.person_id == null).length

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">People</h1>
        <p className="text-sm text-muted-foreground">
          Group devices under the people who own them, so LANtern can answer in terms of who — not just what.
        </p>
      </div>

      {/* Add a person */}
      <div className="flex items-center gap-2">
        <Input
          placeholder="Add a person (e.g. Michelle)…"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") addPerson() }}
          className="max-w-xs"
        />
        <button
          onClick={addPerson}
          disabled={!newName.trim()}
          className="text-sm px-3 py-2 rounded-md bg-primary text-primary-foreground disabled:opacity-40"
        >
          Add
        </button>
      </div>

      {/* People cards */}
      {people.length === 0 ? (
        <p className="text-muted-foreground text-sm">No people yet. Add one above, then assign devices to them from any device's page.</p>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {people.map((p) => {
            const theirs = devices.filter((d) => d.person_id === p.id)
            return (
              <Card key={p.id}>
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-base font-semibold flex items-center gap-2">
                      <span className={`inline-block w-2.5 h-2.5 rounded-full ${p.online ? "bg-green-500" : "bg-zinc-600"}`} />
                      {p.name}
                      {p.online && <Badge className="bg-green-500/20 text-green-400 border-0">online</Badge>}
                    </CardTitle>
                    <button
                      onClick={() => removePerson(p)}
                      className="text-xs text-muted-foreground hover:text-red-400 px-1.5 py-0.5 rounded hover:bg-accent"
                    >
                      delete
                    </button>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {p.device_count} device{p.device_count === 1 ? "" : "s"} · {p.active_devices} active
                  </p>
                </CardHeader>
                <CardContent>
                  {theirs.length === 0 ? (
                    <p className="text-xs text-muted-foreground">No devices assigned yet — open a device and pick this person.</p>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {theirs.map((d) => (
                        <button
                          key={d.mac}
                          onClick={() => navigate(`/devices/${encodeURIComponent(d.mac)}`)}
                          className="flex items-center gap-1.5 text-xs px-2 py-1 rounded bg-accent hover:bg-accent/70 transition-colors"
                        >
                          <span className={`inline-block w-1.5 h-1.5 rounded-full ${d.active ? "bg-green-500" : "bg-zinc-600"}`} />
                          {deviceName(d)}
                        </button>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}

      {unassigned > 0 && (
        <p className="text-xs text-muted-foreground">
          {unassigned} device{unassigned === 1 ? "" : "s"} not assigned to anyone (shared/infrastructure devices can stay that way). Assign one from its page.
        </p>
      )}
    </div>
  )
}
