import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as client from "../api/client";
import Approvals from "../pages/Approvals";
import IncidentDetail from "../pages/IncidentDetail";
import Incidents from "../pages/Incidents";
import Overview from "../pages/Overview";
import { action, detail, incident, proposal } from "./fixtures";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, get: vi.fn(), post: vi.fn() };
});
const get = vi.mocked(client.get);
const post = vi.mocked(client.post);
const page = <T,>(items: T[]) => ({ items, total: items.length, page: 1, page_size: 20 });

const renderAt = (path: string, ui: React.ReactElement, route = path) =>
  render(<MemoryRouter initialEntries={[path]}><Routes><Route path={route} element={ui} /></Routes></MemoryRouter>);

beforeEach(() => { get.mockReset(); post.mockReset(); localStorage.clear(); });

describe("Incidents page", () => {
  it("lists incidents from the API and filters by status/severity", async () => {
    get.mockResolvedValue(page([incident(), incident({ id: "inc-2", title: "HighLatency: slow", severity: "medium", status: "open" })]));
    renderAt("/incidents", <Incidents />);
    expect(await screen.findByText(/HighErrorRate/)).toBeInTheDocument();
    expect(screen.getByText(/HighLatency/)).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Status filter"), "open");
    await userEvent.selectOptions(screen.getByLabelText("Severity filter"), "medium");
    await waitFor(() => expect(get).toHaveBeenLastCalledWith("/incidents", expect.objectContaining({ status: "open", severity: "medium", page: 1 })));
  });

  it("shows an empty state", async () => {
    get.mockResolvedValue(page([]));
    renderAt("/incidents", <Incidents />);
    expect(await screen.findByText("Nothing to show yet")).toBeInTheDocument();
  });

  it("shows an error state with retry", async () => {
    get.mockRejectedValueOnce(new Error("boom")).mockResolvedValue(page([incident()]));
    renderAt("/incidents", <Incidents />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    await userEvent.click(screen.getByText("Retry"));
    expect(await screen.findByText(/HighErrorRate/)).toBeInTheDocument();
  });

  it("shows a loading state first", () => {
    get.mockReturnValue(new Promise(() => {}));
    renderAt("/incidents", <Incidents />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
  });
});

describe("Incident detail workflow", () => {
  function mockDetail(proposals = [proposal()], actions = [action()]) {
    get.mockImplementation(async (url: string) => {
      if (url.startsWith("/incidents/")) return detail();
      if (url === "/agent/executions") return page([{ id: "ex1", incident_id: "inc-1111-aaaa", mode: "deterministic", status: "completed", current_node: "propose", diagnosis: null, error: null, fallback_reason: null, started_at: "2026-01-01T10:00:05Z", finished_at: null }]);
      if (url === "/recovery/proposals") return page(proposals);
      if (url === "/recovery/actions") return page(actions);
      throw new Error("unexpected " + url);
    });
  }

  it("shows timeline, alerts, investigation history and rollback verification results", async () => {
    mockDetail();
    renderAt("/incidents/inc-1111-aaaa", <IncidentDetail />, "/incidents/:id");
    const tl = await screen.findByLabelText("Incident timeline");
    expect(within(tl).getByText(/Incident created from alert/)).toBeInTheDocument();
    expect(within(tl).getByText(/Recovery proposed/)).toBeInTheDocument();
    expect(await screen.findByText("awaiting approval", { selector: "span" })).toBeInTheDocument();
    const v = await screen.findByTestId("verification");
    expect(within(v).getByText(/Verification passed/)).toBeInTheDocument();
    expect(within(v).getByText("pass")).toBeInTheDocument();
    expect(within(v).getByText("fail")).toBeInTheDocument();
  });

  it("approves a pending proposal with the operator's name and reason", async () => {
    mockDetail();
    post.mockResolvedValue({});
    renderAt("/incidents/inc-1111-aaaa", <IncidentDetail />, "/incidents/:id");
    const card = await screen.findByTestId("proposal-card");
    await userEvent.clear(within(card).getByLabelText("Your name"));
    await userEvent.type(within(card).getByLabelText("Your name"), "alice");
    await userEvent.type(within(card).getByLabelText("Reason"), "verified in staging");
    await userEvent.click(within(card).getByText("Approve & execute"));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/recovery/proposals/prop-1/approve", { decided_by: "alice", reason: "verified in staging" }));
    expect(localStorage.getItem("agentops.user")).toBe("alice");
  });

  it("shows the backend error when approval is refused", async () => {
    mockDetail();
    post.mockRejectedValue(new Error("Proposal is 'approved'"));
    renderAt("/incidents/inc-1111-aaaa", <IncidentDetail />, "/incidents/:id");
    await userEvent.click(await screen.findByText("Reject"));
    expect(await screen.findByText(/Proposal is 'approved'/)).toBeInTheDocument();
  });

  it("offers re-investigation only for open/failed incidents", async () => {
    get.mockImplementation(async (url: string) => (url.startsWith("/incidents/") ? detail({ status: "failed" }) : page([])));
    post.mockResolvedValue({ queued: true });
    renderAt("/incidents/inc-1111-aaaa", <IncidentDetail />, "/incidents/:id");
    await userEvent.click(await screen.findByText("Re-investigate"));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/incidents/inc-1111-aaaa/investigate"));
  });
});

describe("Approvals page", () => {
  it("shows pending proposals and the empty state", async () => {
    get.mockImplementation(async (_u: string, params?: any) => (params?.status === "pending_approval" ? page([proposal()]) : page([proposal({ id: "p2", status: "executed" })])));
    renderAt("/approvals", <Approvals />);
    expect(await screen.findAllByTestId("proposal-card")).toHaveLength(2);
    expect(screen.getAllByText("rollback_deployment").length).toBeGreaterThan(0);
  });

  it("shows an empty state when nothing is pending", async () => {
    get.mockResolvedValue(page([]));
    renderAt("/approvals", <Approvals />);
    expect(await screen.findByText("No proposals awaiting approval")).toBeInTheDocument();
  });
});

describe("Overview page", () => {
  const overview = (over = {}) => ({
    total_incidents: 4, active_incidents: 1, resolved_incidents: 2, failed_incidents: 1, by_status: { resolved: 2, failed: 1, open: 1 },
    by_severity: { high: 3, low: 1 }, recovery_success_rate: 0.5, avg_investigation_seconds: 3.2, avg_recovery_seconds: 40, avg_time_to_resolve_seconds: 60,
    tool_calls_total: 30, tool_failure_rate: 0.1, agent_runs_total: 4, recent_executions: [], ...over,
  });

  it("renders statistics returned by the backend (nothing hardcoded)", async () => {
    get.mockImplementation(async (url: string) => (url === "/stats/overview" ? overview() : page([incident({ status: "open" })])));
    renderAt("/", <Overview />);
    await waitFor(() => expect(within(screen.getByTestId("stat-Total incidents")).getByText("4")).toBeInTheDocument());
    expect(within(screen.getByTestId("stat-Recovery success rate")).getByText("50.0%")).toBeInTheDocument();
    expect(within(screen.getByTestId("stat-Tool failure rate")).getByText("10.0%")).toBeInTheDocument();
  });

  it("shows n/a rather than zeros when there is no data", async () => {
    get.mockImplementation(async (url: string) => (url === "/stats/overview" ? overview({ total_incidents: 0, by_status: {}, by_severity: {}, recovery_success_rate: null, avg_investigation_seconds: null, avg_recovery_seconds: null, tool_failure_rate: null }) : page([])));
    renderAt("/", <Overview />);
    await waitFor(() => expect(within(screen.getByTestId("stat-Recovery success rate")).getByText("n/a")).toBeInTheDocument());
    expect(screen.getAllByText("No incidents yet").length).toBeGreaterThan(0);
  });

  it("surfaces backend errors", async () => {
    get.mockRejectedValue(new Error("Cannot reach the AgentOps backend"));
    renderAt("/", <Overview />);
    expect((await screen.findAllByRole("alert"))[0]).toHaveTextContent("Cannot reach");
  });
});
