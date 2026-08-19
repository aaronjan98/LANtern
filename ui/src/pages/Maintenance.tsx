import { useEffect, useState, useCallback } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

// The Deco WiFi reboot control surface. The main Deco (10.0.50.216) has a
// recurring 5 GHz uptime-degradation fault that only a power-cycle clears, so
// LANtern schedules an idle-gated backstop reboot and logs every run here. The
// brain (lantern.deco_reboot) decides + idle-gates; a host runner does the
// actual power-cycle (it owns the Kasa plug) and records an external-reachability
// check before/after — proof the Cloudflare tunnel / hosted services never blip
// when only the WiFi AP is cycled.

interface RebootEvent {
  id: number
  created_unix: number
  trigger: string
  status: string
  requested_by: string | null
  approved_by: string | null
  ready_unix: number | null
  executed_unix: number | null
  finished_unix: number | null
  result: string | null
  idle_evidence: { internet?: { headline?: string }; homelab?: { headline?: string } } | null
  tunnel_before: string | null
  tunnel_after: string | null
  detail: string | null
}

interface RebootStatus {
  last_success_unix: number | null
  open_request: RebootEvent | null
}

interface IdleState {
  internet_idle: boolean
  homelab_idle: boolean
  safe_for_deco_reboot: boolean
}


function fmtTime(unix: number) {
  return new Date(unix * 1000).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  })
}

function fmtAge(unix: number | null): string {
  if (!unix) return "—"
  const s = Math.max(0, Math.floor(Date.now() / 1000) - unix)
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${(s / 86400).toFixed(1)}d ago`
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    succeeded: "bg-green-500/20 text-green-400",
    failed: "bg-red-500/20 text-red-400",
    executing: "bg-amber-500/20 text-amber-400",
    ready: "bg-blue-500/20 text-blue-400",
    approved: "bg-indigo-500/20 text-indigo-400",
    pending: "bg-amber-500/20 text-amber-400",
    canceled: "bg-zinc-600/20 text-zinc-400",
    superseded: "bg-zinc-600/20 text-zinc-400",
  }
  return <Badge className={`border-0 ${map[status] ?? "bg-zinc-600/20 text-zinc-400"}`}>{status}</Badge>
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

export default function Maintenance() {
  const [events, setEvents] = useState<RebootEvent[]>([])
  const [status, setStatus] = useState<RebootStatus | null>(null)
  const [idle, setIdle] = useState<IdleState | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    Promise.all([
      fetch("/api/reboot/events?limit=100").then((r) => r.json()),
      fetch("/api/reboot/status").then((r) => r.json()),
      fetch("/api/activity/idle").then((r) => r.json()),
    ]).then(([e, s, i]) => { setEvents(e); setStatus(s); setIdle(i) }).catch(() => {})
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 20000)
    return () => clearInterval(id)
  }, [load])

  async function post(url: string, confirmMsg?: string) {
    if (confirmMsg && !confirm(confirmMsg)) return
    setBusy(true)
    try {
      const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" } })
      if (!r.ok) {
        const body = await r.json().catch(() => ({}))
        alert(body.detail || `Request failed (${r.status})`)
      }
    } finally {
      setBusy(false)
      load()
    }
  }

  const open = status?.open_request ?? null
  const safe = idle?.safe_for_deco_reboot

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Maintenance</h1>
          <p className="text-sm text-muted-foreground">
            Deco WiFi reboot — idle-gated backstop every 3 days, clears the recurring 5 GHz degradation.
          </p>
        </div>
        <button
          onClick={() => post("/api/reboot/request",
            "Queue a Deco reboot? It won't fire until WiFi is idle; you can cancel it before then.")}
          disabled={busy || !!open}
          title={open ? "A reboot request is already in flight" : "Queue an idle-gated reboot now"}
          className="text-sm px-4 py-2 rounded-md bg-primary text-primary-foreground disabled:opacity-40"
        >
          Reboot now
        </button>
      </div>

      {/* Headline status */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard title="Last reboot"
          value={fmtAge(status?.last_success_unix ?? null)}
          sub={status?.last_success_unix ? fmtTime(status.last_success_unix) : "no reboot recorded yet"} />
        <StatCard title="Deco uptime"
          value={status?.last_success_unix ? fmtAge(status.last_success_unix).replace(" ago", "") : "—"}
          sub="since last power-cycle" />
        <StatCard title="Safe to reboot now?"
          value={safe == null ? "—" : safe ? "Yes — idle" : "No — in use"}
          sub={idle ? `internet ${idle.internet_idle ? "idle" : "busy"} · homelab ${idle.homelab_idle ? "idle" : "busy"}` : "oracle unreachable"} />
        <StatCard title="Pending"
          value={open ? open.status : "none"}
          sub={open ? `${open.trigger} · ${open.requested_by ?? "—"}` : "no request in flight"} />
      </div>

      {/* In-flight request with controls */}
      {open && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">In-flight request #{open.id}</CardTitle>
          </CardHeader>
          <CardContent className="flex items-center gap-4 flex-wrap">
            <StatusBadge status={open.status} />
            <span className="text-sm text-muted-foreground">
              {open.trigger === "scheduled_backstop" ? "scheduled backstop" : open.trigger}
              {open.status === "approved" && !safe && " — waiting for an idle window"}
              {open.status === "ready" && " — idle; the runner will execute within ~10 min"}
              {open.status === "executing" && " — power-cycling now (~2 min)"}
            </span>
            <div className="flex-1" />
            {open.status === "pending" && (
              <button onClick={() => post(`/api/reboot/${open.id}/approve`)} disabled={busy}
                className="text-sm px-3 py-1.5 rounded-md bg-primary text-primary-foreground disabled:opacity-40">
                Approve
              </button>
            )}
            {open.status !== "executing" && (
              <button onClick={() => post(`/api/reboot/${open.id}/cancel`, "Cancel this reboot request?")} disabled={busy}
                className="text-sm px-3 py-1.5 rounded-md border border-border hover:bg-accent disabled:opacity-40">
                Cancel
              </button>
            )}
          </CardContent>
        </Card>
      )}

      {/* History */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Reboot history</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <div className="divide-y divide-border text-sm max-h-[32rem] overflow-auto">
            {events.length === 0 && (
              <p className="text-muted-foreground text-sm px-6 py-4">
                No reboot events yet. The 3-day backstop is counting; use “Reboot now” to run one on demand.
              </p>
            )}
            {events.map((e) => {
              const idleSummary = e.idle_evidence
                ? [e.idle_evidence.internet?.headline, e.idle_evidence.homelab?.headline]
                    .filter(Boolean).join(" · ")
                : null
              return (
                <div key={e.id} className="flex items-start gap-4 px-4 py-2.5">
                  <span className="text-muted-foreground text-xs w-28 shrink-0 tabular-nums pt-0.5">
                    {fmtTime(e.created_unix)}
                  </span>
                  <span className="w-24 shrink-0"><StatusBadge status={e.status} /></span>
                  <span className="text-xs text-muted-foreground w-32 shrink-0">
                    {e.trigger === "scheduled_backstop" ? "backstop" : e.trigger}
                    {e.approved_by && e.approved_by !== "scheduler" && (
                      <span className="block text-[11px]">by {e.approved_by}</span>
                    )}
                  </span>
                  <div className="flex-1 min-w-0 text-xs text-muted-foreground space-y-0.5">
                    {idleSummary && <div>idle at go: {idleSummary}</div>}
                    {(e.tunnel_before || e.tunnel_after) && (
                      <div>
                        external: <span className="text-foreground/80">{e.tunnel_before ?? "—"}</span>
                        {" → "}
                        <span className="text-foreground/80">{e.tunnel_after ?? "—"}</span>
                      </div>
                    )}
                    {e.detail && <div className="truncate" title={e.detail}>{e.detail}</div>}
                  </div>
                </div>
              )
            })}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
