import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "@/components/Layout";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { Spinner } from "@/components/ui/Spinner";

// Code-split each top-level page so first paint stays small.
const LoginPage = lazy(() => import("@/pages/Login").then((m) => ({ default: m.LoginPage })));
const DashboardPage = lazy(() =>
  import("@/pages/Dashboard").then((m) => ({ default: m.DashboardPage })),
);
const ClusterPage = lazy(() =>
  import("@/pages/Cluster").then((m) => ({ default: m.ClusterPage })),
);
const LogsPage = lazy(() => import("@/pages/Logs").then((m) => ({ default: m.LogsPage })));
const JobsPage = lazy(() => import("@/pages/Jobs").then((m) => ({ default: m.JobsPage })));
const AlertsPage = lazy(() => import("@/pages/Alerts").then((m) => ({ default: m.AlertsPage })));

export function App() {
  return (
    <Suspense
      fallback={
        <div className="grid min-h-screen place-items-center">
          <Spinner className="h-6 w-6" />
        </div>
      }
    >
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<ProtectedRoute />}>
          <Route element={<Layout />}>
            <Route index element={<DashboardPage />} />
            <Route path="clusters" element={<DashboardPage />} />
            <Route path="clusters/:id" element={<ClusterPage />} />
            <Route path="logs" element={<LogsPage />} />
            <Route path="jobs" element={<JobsPage />} />
            <Route path="alerts" element={<AlertsPage />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
