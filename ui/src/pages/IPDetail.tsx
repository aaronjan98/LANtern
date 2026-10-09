import { useEffect, useState, useCallback } from "react"
import { useParams, useNavigate, Link } from "react-router-dom"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

// Enrichment for one external IP: ASN/ISP/org, coarse geo, reverse DNS, and
// which other vhosts on this server it has hit. Hard ceiling on what this
// can ever say, by design of what any IP-based lookup provides: ISP/carrier
// and city-level geo, never a person. A mobile carrier IP especially can
// geolocate to a regional gateway, not the phone's actual location.

interface Intel {
  ip: string
  asn: string | null
  org: string | null
  isp: string | null
  city: string | null
  region: string | null
  country: string | null
  country_code: string | null
  lat: number | null
  lon: number | null
  timezone: string | null
  ptr_hostname: string | null
  is_private: number
  error: string | null
  looked_up_unix: number
}

interface DomainHit {
  host: string
  requests: number
  first_seen: number
  last_seen: number
}

function fmtTime(unix: number) {
  return new Date(unix * 1000).toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  })
}

function Row({ label, value }: { label: string; value: string | null }) {
  if (!value) return null
  return (
    <div className="flex items-center gap-3 py-1.5">
      <span className="text-xs text-muted-foreground w-28 shrink-0">{label}</span>
      <span className="text-sm">{value}</span>
    </div>
  )
}

export default function IPDetail() {
  const { ip } = useParams<{ ip: string }>()
  const navigate = useNavigate()
  const decoded = decodeURIComponent(ip ?? "")

  const [intel, setIntel] = useState<Intel | null>(null)
  const [domains, setDomains] = useState<DomainHit[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback((refresh = false) => {
    if (!decoded) return
    setLoading(true)
    Promise.all([
      fetch(`/api/ip/${encodeURIComponent(decoded)}/intel${refresh ? "?refresh=true" : ""}`).then((r) => r.json()),
      fetch(`/api/ip/${encodeURIComponent(decoded)}/domains`).then((r) => r.json()),
    ]).then(([i, d]) => { setIntel(i); setDomains(d) }).finally(() => setLoading(false))
  }, [decoded])

  useEffect(() => { load() }, [load])

  return (
    <div className="space-y-6">
      <div>
        <button
          onClick={() => navigate(-1)}
          className="text-sm text-muted-foreground hover:text-foreground mb-2 flex items-center gap-1"
        >
          ← Back
        </button>
        <h1 className="text-2xl font-semibold font-mono">{decoded}</h1>
        <p className="text-sm text-muted-foreground mt-1">
          ISP/carrier + coarse location only -- this can never identify a person.
        </p>
      </div>

      {loading && !intel && <p className="text-muted-foreground text-sm">Looking up…</p>}

      {intel?.is_private === 1 && (
        <Card><CardContent className="py-6">
          <p className="text-muted-foreground text-sm">
            This is a private/internal address -- there's nothing to enrich.
          </p>
        </CardContent></Card>
      )}

      {intel && intel.is_private === 0 && intel.error && (
        <Card><CardContent className="py-6 space-y-2">
          <p className="text-sm text-amber-400">Lookup failed: {intel.error}</p>
          <button onClick={() => load(true)} className="text-xs underline text-muted-foreground hover:text-foreground">
            Try again
          </button>
        </CardContent></Card>
      )}

      {intel && intel.is_private === 0 && !intel.error && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader><CardTitle className="text-sm font-medium">Network</CardTitle></CardHeader>
            <CardContent className="divide-y divide-border">
              <Row label="ISP" value={intel.isp} />
              <Row label="Organization" value={intel.org} />
              <Row label="ASN" value={intel.asn} />
              <Row label="Reverse DNS" value={intel.ptr_hostname} />
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle className="text-sm font-medium">Approximate location</CardTitle></CardHeader>
            <CardContent className="divide-y divide-border">
              <Row label="City" value={intel.city} />
              <Row label="Region" value={intel.region} />
              <Row label="Country" value={intel.country} />
              <Row label="Timezone" value={intel.timezone} />
            </CardContent>
          </Card>
        </div>
      )}

      {intel && (
        <p className="text-xs text-muted-foreground">
          Looked up {fmtTime(intel.looked_up_unix)} ·{" "}
          <button onClick={() => load(true)} className="underline hover:text-foreground">refresh</button>
        </p>
      )}

      <Card>
        <CardHeader><CardTitle className="text-sm font-medium">Domains this IP has hit</CardTitle></CardHeader>
        <CardContent className="p-0">
          <div className="divide-y divide-border text-sm">
            {domains.length === 0 && (
              <p className="text-muted-foreground text-sm px-6 py-4">No tagged requests from this IP.</p>
            )}
            {domains.map((d) => (
              <div key={d.host} className="flex items-center gap-3 px-4 py-2.5">
                <Link to={`/domains/${encodeURIComponent(d.host)}`} className="font-mono text-xs flex-1 min-w-0 truncate hover:underline hover:text-primary">
                  {d.host}
                </Link>
                <span className="text-xs text-muted-foreground shrink-0 tabular-nums">{d.requests.toLocaleString()} requests</span>
                <span className="text-xs text-muted-foreground shrink-0">last {fmtTime(d.last_seen)}</span>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
