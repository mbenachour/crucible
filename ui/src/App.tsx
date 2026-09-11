import { Component, type ReactNode } from "react";
import { createBrowserRouter, Navigate, RouterProvider } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Shell } from "./components/Shell";
import { Runs } from "./pages/Runs";
import { RunLayout } from "./pages/RunLayout";
import { RunOverview } from "./pages/RunOverview";
import { Findings } from "./pages/Findings";
import { FindingDetail } from "./pages/FindingDetail";
import { Report } from "./pages/Report";
import { Recon } from "./pages/Recon";
import { Coverage } from "./pages/Coverage";
import { RunStatePage } from "./pages/RunStatePage";
import { Wishlist } from "./pages/Wishlist";
import { Artifacts } from "./pages/Artifacts";

const qc = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 10_000 } },
});

class ErrorBoundary extends Component<{ children: ReactNode }, { err: Error | null }> {
  state = { err: null as Error | null };
  static getDerivedStateFromError(err: Error) {
    return { err };
  }
  render() {
    if (this.state.err) {
      return (
        <div style={{ padding: 40 }}>
          <div className="banner bad" style={{ display: "inline-block" }}>
            UI error: {this.state.err.message}
          </div>
          <div style={{ marginTop: 12 }}>
            <button onClick={() => this.setState({ err: null })}>dismiss</button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

const router = createBrowserRouter([
  {
    element: <Shell />,
    children: [
      { index: true, element: <Navigate to="/runs" replace /> },
      { path: "runs", element: <Runs /> },
      {
        path: "runs/:runId",
        element: <RunLayout />,
        children: [
          { index: true, element: <RunOverview /> },
          { path: "findings", element: <Findings /> },
          { path: "report", element: <Report /> },
          { path: "recon", element: <Recon /> },
          { path: "coverage", element: <Coverage /> },
          { path: "state", element: <RunStatePage /> },
          { path: "artifacts", element: <Artifacts /> },
          { path: "wishes", element: <Wishlist /> },
        ],
      },
      { path: "findings/:findingId", element: <FindingDetail /> },
      { path: "wishes", element: <Wishlist /> },
      { path: "*", element: <div className="state-msg">Not found.</div> },
    ],
  },
]);

export function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={qc}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
