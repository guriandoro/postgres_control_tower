import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { Activity, BellRing, Database, ScrollText, LogOut, Wrench } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { useAuth } from "@/auth/AuthContext";
import { cn } from "@/lib/cn";

const NAV = [
  { to: "/", label: "Dashboard", icon: Activity, end: true },
  { to: "/clusters", label: "Clusters", icon: Database, end: false },
  { to: "/logs", label: "Logs", icon: ScrollText, end: false },
  { to: "/jobs", label: "Jobs", icon: Wrench, end: false },
  { to: "/alerts", label: "Alerts", icon: BellRing, end: false },
];

export function Layout() {
  const { user, logout } = useAuth();
  const loc = useLocation();

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-border bg-card/40 backdrop-blur">
        <div className="container flex h-14 items-center justify-between gap-4">
          <Link to="/" className="flex items-center gap-2 font-semibold">
            <span className="grid h-7 w-7 place-items-center rounded-md bg-primary text-primary-foreground text-xs font-bold">
              PCT
            </span>
            <span className="hidden sm:inline">Postgres Control Tower</span>
          </Link>
          <nav className="flex items-center gap-1">
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  cn(
                    "inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium",
                    "transition-colors hover:bg-muted",
                    isActive || (to !== "/" && loc.pathname.startsWith(to))
                      ? "bg-muted text-foreground"
                      : "text-muted-foreground",
                  )
                }
              >
                <Icon className="h-4 w-4" />
                <span className="hidden md:inline">{label}</span>
              </NavLink>
            ))}
          </nav>
          <div className="flex items-center gap-2">
            <span className="hidden text-xs text-muted-foreground sm:inline">
              {user?.email}
            </span>
            <Button variant="ghost" size="icon" onClick={logout} aria-label="Sign out">
              <LogOut className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </header>
      <main className="container flex-1 py-6">
        <Outlet />
      </main>
      <footer className="border-t border-border py-3 text-center text-xs text-muted-foreground">
        v1 prototype · all timestamps UTC
      </footer>
    </div>
  );
}
