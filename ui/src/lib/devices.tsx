import { createContext, useContext, useEffect, useState, type ReactNode } from "react"
import { useNavigate } from "react-router-dom"

// Shared device identity for the whole UI. One fetch of /api/devices, kept fresh,
// so every page can turn a raw IP into the same friendly name the Devices tab
// shows (label > hostname > the IP itself). This is the "device-first" principle:
// nothing should surface a bare 10.0.50.x when we know whose it is.

export interface Device {
  mac: string
  hostname: string | null
  last_ip: string | null
  vendor: string | null
  label: string | null
  active: boolean
}

interface DeviceLookup {
  deviceForIp: (ip: string | null | undefined) => Device | undefined
  nameForIp: (ip: string | null | undefined) => string
}

const DevicesContext = createContext<DeviceLookup>({
  deviceForIp: () => undefined,
  nameForIp: (ip) => ip ?? "—",
})

export function displayName(d: Device): string {
  return d.label || d.hostname || d.mac
}

export function DevicesProvider({ children }: { children: ReactNode }) {
  const [byIp, setByIp] = useState<Map<string, Device>>(new Map())

  useEffect(() => {
    const load = () =>
      fetch("/api/devices")
        .then((r) => r.json())
        .then((devices: Device[]) => {
          const m = new Map<string, Device>()
          // Newest-last-seen wins if two devices ever shared an IP; /api/devices
          // is already sorted last_seen desc, so set() in reverse keeps the first.
          for (const d of [...devices].reverse()) if (d.last_ip) m.set(d.last_ip, d)
          setByIp(m)
        })
        .catch(() => {})
    load()
    const id = setInterval(load, 30000)
    return () => clearInterval(id)
  }, [])

  const deviceForIp = (ip: string | null | undefined) => (ip ? byIp.get(ip) : undefined)
  const nameForIp = (ip: string | null | undefined) => {
    if (!ip) return "—"
    const d = byIp.get(ip)
    return d ? d.label || d.hostname || ip : ip
  }

  return (
    <DevicesContext.Provider value={{ deviceForIp, nameForIp }}>
      {children}
    </DevicesContext.Provider>
  )
}

export const useDevices = () => useContext(DevicesContext)

// Renders an IP as its device name (falling back to the raw IP for anything we
// don't recognise — external hosts, un-leased addresses) and links to the
// device detail view. `mono` keeps the monospace look where the surrounding
// column is monospace (flows, feeds).
export function DeviceLabel({
  ip,
  className = "",
  mono = false,
}: {
  ip: string | null | undefined
  className?: string
  mono?: boolean
}) {
  const { deviceForIp } = useDevices()
  const navigate = useNavigate()
  if (!ip) return <span className={className}>—</span>
  const d = deviceForIp(ip)
  const label = d ? d.label || d.hostname : null
  const named = !!label
  const name = label ?? ip
  return (
    <span
      className={`cursor-pointer hover:text-primary transition-colors ${
        named ? "" : "font-mono"
      } ${mono && !named ? "font-mono" : ""} ${className}`}
      title={named ? `${ip} — view device` : "view device"}
      onClick={() => navigate(`/devices/${encodeURIComponent(ip)}`)}
    >
      {name}
    </span>
  )
}
