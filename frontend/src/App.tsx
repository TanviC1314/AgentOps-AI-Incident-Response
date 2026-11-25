import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Approvals from "./pages/Approvals";
import AuditLogs from "./pages/AuditLogs";
import DeploymentHistory from "./pages/DeploymentHistory";
import IncidentDetail from "./pages/IncidentDetail";
import Incidents from "./pages/Incidents";
import InvestigationDetail, { Investigations } from "./pages/Investigations";
import Overview from "./pages/Overview";
import ServiceHealth from "./pages/ServiceHealth";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Overview />} />
        <Route path="incidents" element={<Incidents />} />
        <Route path="incidents/:id" element={<IncidentDetail />} />
        <Route path="investigations" element={<Investigations />} />
        <Route path="investigations/:id" element={<InvestigationDetail />} />
        <Route path="approvals" element={<Approvals />} />
        <Route path="service-health" element={<ServiceHealth />} />
        <Route path="deployments" element={<DeploymentHistory />} />
        <Route path="audit" element={<AuditLogs />} />
        <Route path="*" element={<div className="text-slate-400">Page not found</div>} />
      </Route>
    </Routes>
  );
}
