import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom"
import Overview from "@/pages/Overview"
import Devices from "@/pages/Devices"
import DNS from "@/pages/DNS"
import Flows from "@/pages/Flows"

const navItems = [
  { to: "/", label: "Overview", exact: true },
  { to: "/devices", label: "Devices" },
  { to: "/dns", label: "DNS" },
  { to: "/flows", label: "Flows" },
]

function Sidebar() {
  return (
    <aside className="w-56 shrink-0 border-r border-border bg-card flex flex-col">
      <div className="px-6 py-5 border-b border-border">
        <span className="text-lg font-semibold tracking-tight">
          <span className="text-primary">LAN</span>tern
        </span>
      </div>
      <nav className="flex flex-col gap-1 px-3 py-4">
        {navItems.map(({ to, label, exact }) => (
          <NavLink
            key={to}
            to={to}
            end={exact}
            className={({ isActive }) =>
              `px-3 py-2 rounded-md text-sm transition-colors ${
                isActive
                  ? "bg-accent text-accent-foreground font-medium"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent/50"
              }`
            }
          >
            {label}
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex h-screen bg-background text-foreground">
        <Sidebar />
        <main className="flex-1 overflow-auto p-6">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/devices" element={<Devices />} />
            <Route path="/dns" element={<DNS />} />
            <Route path="/flows" element={<Flows />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
