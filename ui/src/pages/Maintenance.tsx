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
  scheduled_unix: number | null
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

// History should show when a run actually happened, not created_unix (the
// moment the request/schedule was made). finished_unix is set when the
// runner completes (succeeded/failed); fall back to executed_unix (power-cycle
// start) for an in-flight row, and only to created_unix for one that never ran.
function historyTime(e: RebootEvent): number {
  return e.finished_unix ?? e.executed_unix ?? e.created_unix
}

function fmtAge(unix: number | null): string {
  if (!unix) return "—"
  const s = Math.max(0, Math.floor(Date.now() / 1000) - unix)
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${(s / 86400).toFixed(1)}d ago`
}

// <input type="datetime-local"> works in the viewer's local time, not UTC --
// these convert at the boundary so everything else keeps working in epoch seconds.
function toLocalInputValue(unix: number): string {
  const d = new Date(unix * 1000)
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
function fromLocalInputValue(v: string): number | null {
  const ms = new Date(v).getTime()
  return Number.isNaN(ms) ? null : Math.floor(ms / 1000)
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
  const [authUser, setAuthUser] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [customTime, setCustomTime] = useState("")  // 'Schedule for...' next to Reboot now
  const [pendingTime, setPendingTime] = useState("") // editable override on a pending backstop's suggestion

  const load = useCallback(() => {
    Promise.all([
      fetch("/api/reboot/events?limit=100").then((r) => r.json()),
      fetch("/api/reboot/status").then((r) => r.json()),
      fetch("/api/activity/idle").then((r) => r.json()),
    ]).then(([e, s, i]) => { setEvents(e); setStatus(s); setIdle(i) }).catch(() => {})
    // oauth2-proxy tells us who's signed in (401 when nobody is).
    fetch("/oauth2/userinfo")
      .then((r) => (r.ok ? r.json() : null))
      .then((u) => setAuthUser(u ? (u.preferredUsername || u.user || u.email || "signed in") : null))
      .catch(() => setAuthUser(null))
  }, [])

  function signIn() {
    window.location.href = `/oauth2/sign_in?rd=${encodeURIComponent(location.pathname)}`
  }

  useEffect(() => {
    load()
    const id = setInterval(load, 20000)
    return () => clearInterval(id)
  }, [load])

  async function post(url: string, opts?: { confirmMsg?: string; body?: unknown }) {
    if (opts?.confirmMsg && !confirm(opts.confirmMsg)) return
    setBusy(true)
    try {
      const r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: opts?.body !== undefined ? JSON.stringify(opts.body) : undefined,
      })
      if (r.status === 401 || r.status === 403) {
        // Not signed in (or not you) — send to GitHub login, then come back.
        signIn()
        return
      }
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
  // Only trust scheduled_unix as a future promise. An approved request's slot
  // can pass without executing (the oracle just never went idle) -- the brain
  // deliberately leaves that timestamp alone once approved (approval is a
  // commitment, not a rolling suggestion), so a stale display value has to be
  // filtered out here rather than mutated server-side. Past that point the
  // request is really just 'waiting for the next idle window', unscheduled.
  const now = Math.floor(Date.now() / 1000)
  const scheduledUpcoming =
    open?.scheduled_unix && open.scheduled_unix > now ? open.scheduled_unix : null
  const pendingTimeUnix = fromLocalInputValue(pendingTime)

  // Keep the editable suggestion in sync with the server's row -- resets when a
  // different request opens, and picks up a rolled-forward suggestion too (the
  // brain advances a still-pending backstop's slot once it passes unapproved).
  useEffect(() => {
    if (open?.status === "pending" && open.scheduled_unix) {
      setPendingTime(toLocalInputValue(open.scheduled_unix))
    } else {
      setPendingTime("")
    }
  }, [open?.id, open?.status, open?.scheduled_unix])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Maintenance</h1>
          <p className="text-sm text-muted-foreground">
            Deco WiFi reboot — idle-gated backstop every 3 days, clears the recurring 5 GHz degradation.
          </p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <div className="text-xs text-muted-foreground">
            {authUser ? (
              <>signed in as <span className="text-foreground">{authUser}</span> ·{" "}
                <a href={`/oauth2/sign_out?rd=${encodeURIComponent(location.pathname)}`} className="underline hover:text-foreground">sign out</a>
              </>
            ) : (
              <>🔒 <button onClick={signIn} className="underline hover:text-foreground">sign in to make changes</button></>
            )}
          </div>
          <div className="flex items-center gap-2">
            <input
              type="datetime-local"
              value={customTime}
              onChange={(e) => setCustomTime(e.target.value)}
              min={toLocalInputValue(now + 60)}
              disabled={busy || !!open}
              title="Pick a time, then Schedule"
              className="text-sm px-2 py-2 rounded-md border border-border bg-background disabled:opacity-40"
            />
            <button
              onClick={() => {
                const t = fromLocalInputValue(customTime)
                if (t == null) return
                post("/api/reboot/request", {
                  confirmMsg: `Schedule a Deco reboot for ${fmtTime(t)}? It'll wait for an idle window at or after that time.`,
                  body: { scheduled_unix: t },
                })
              }}
              disabled={busy || !!open || fromLocalInputValue(customTime) == null}
              title={open ? "A reboot request is already in flight" : "Schedule an idle-gated reboot for a specific time"}
              className="text-sm px-3 py-2 rounded-md border border-border hover:bg-accent disabled:opacity-40"
            >
              Schedule
            </button>
            <button
              onClick={() => post("/api/reboot/request", {
                confirmMsg: "Queue a Deco reboot? It won't fire until WiFi is idle; you can cancel it before then.",
              })}
              disabled={busy || !!open}
              title={open ? "A reboot request is already in flight" : "Queue an idle-gated reboot now"}
              className="text-sm px-4 py-2 rounded-md bg-primary text-primary-foreground disabled:opacity-40"
            >
              Reboot now
            </button>
          </div>
        </div>
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
          sub={open ? (scheduledUpcoming ? `suggested ${fmtTime(scheduledUpcoming)}` : open.trigger) : "no request in flight"} />
      </div>

      {/* In-flight request with controls */}
      {open && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">
              {open.trigger === "scheduled_backstop" ? "Backstop reboot recommended" : `Reboot request #${open.id}`}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center gap-3 flex-wrap">
              <StatusBadge status={open.status} />
              {open.status === "pending" && open.scheduled_unix ? (
                <span className="text-sm flex items-center gap-2">
                  Suggested time:
                  <input
                    type="datetime-local"
                    value={pendingTime}
                    onChange={(e) => setPendingTime(e.target.value)}
                    min={toLocalInputValue(now + 60)}
                    className="text-sm px-2 py-1 rounded-md border border-border bg-background"
                  />
                </span>
              ) : scheduledUpcoming && (
                <span className="text-sm">
                  Scheduled: <span className="font-semibold">{fmtTime(scheduledUpcoming)}</span>
                  <span className="text-muted-foreground"> (overnight)</span>
                </span>
              )}
            </div>
            <p className="text-xs text-muted-foreground">
              {open.status === "pending" && open.trigger === "scheduled_backstop" &&
                "The Deco is overdue for its mitigation reboot. Click OK to schedule it for the suggested overnight time — it’ll only power-cycle then, and only if WiFi is idle."}
              {open.status === "pending" && open.trigger !== "scheduled_backstop" && "Awaiting approval."}
              {open.status === "approved" && scheduledUpcoming &&
                "Confirmed — it’ll power-cycle at the scheduled time if WiFi is idle. WiFi clients briefly roam to the other Deco units."}
              {open.status === "approved" && !scheduledUpcoming &&
                (safe ? "Approved and idle — will run shortly." : "Approved — its slot passed without an idle window; waiting for the next one.")}
              {open.status === "ready" && "Idle window open — the runner will execute shortly."}
              {open.status === "executing" && "Power-cycling now (~2 min); WiFi clients briefly roam to the other units."}
            </p>
            <div className="flex items-center gap-2">
              {open.status === "pending" && (
                <button
                  onClick={() => post(`/api/reboot/${open.id}/approve`,
                    pendingTimeUnix != null ? { body: { scheduled_unix: pendingTimeUnix } } : undefined)}
                  disabled={busy || (!!open.scheduled_unix && pendingTimeUnix == null)}
                  className="text-sm px-4 py-1.5 rounded-md bg-primary text-primary-foreground disabled:opacity-40">
                  {pendingTimeUnix != null ? `OK — schedule ${fmtTime(pendingTimeUnix)}` : "Approve"}
                </button>
              )}
              {open.status !== "executing" && (
                <button onClick={() => post(`/api/reboot/${open.id}/cancel`, { confirmMsg: "Cancel this reboot request?" })} disabled={busy}
                  className="text-sm px-3 py-1.5 rounded-md border border-border hover:bg-accent disabled:opacity-40">
                  {open.status === "pending" ? "Dismiss" : "Cancel"}
                </button>
              )}
            </div>
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
                    {fmtTime(historyTime(e))}
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
