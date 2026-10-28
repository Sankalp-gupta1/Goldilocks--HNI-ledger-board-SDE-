import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  LayoutDashboard,
  Files,
  ListChecks,
  BookOpen,
  Scale,
  PieChart,
  ArrowLeftRight,
  SlidersHorizontal,
  Settings as SettingsIcon,
  Plus,
  Search,
  Command,
  ChevronDown,
  LogOut,
  X,
  Menu,
  ShieldCheck,
  ArrowRight,
  CalendarDays,
} from "lucide-react";
import {
  api,
  send,
  useData,
  query,
  type User,
  type Meta,
  type Overview as OverviewData,
  type Filters,
  type Transaction,
} from "./api";
import Auth from "./Auth";
import { Badge, ErrorState, Loading, Logo, Modal, useAction } from "./ui";
import { TransactionDetail } from "./Transactions";
const Overview = lazy(() => import("./Overview"));
const Import = lazy(() => import("./Import"));
const Settings = lazy(() => import("./Settings"));
const LedgerPage = lazy(() =>
  import("./Pages").then((m) => ({ default: m.LedgerPage })),
);
const StatementsPage = lazy(() =>
  import("./Pages").then((m) => ({ default: m.StatementsPage })),
);
const ReportsPage = lazy(() =>
  import("./Pages").then((m) => ({ default: m.ReportsPage })),
);
const ReconciliationPage = lazy(() =>
  import("./Pages").then((m) => ({ default: m.ReconciliationPage })),
);
const RulesPage = lazy(() =>
  import("./Pages").then((m) => ({ default: m.RulesPage })),
);
const TradingPage = lazy(() =>
  import("./Pages").then((m) => ({ default: m.TradingPage })),
);
const navigation = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "statements", label: "Statements", icon: Files },
  { id: "review", label: "Review inbox", icon: ListChecks },
  { id: "ledger", label: "Ledger", icon: BookOpen },
  { id: "reconciliation", label: "Reconciliation", icon: Scale },
  { id: "reports", label: "Reports", icon: PieChart },
  { id: "trading", label: "Broker cash flows", icon: ArrowLeftRight },
  { id: "rules", label: "Personal rules", icon: SlidersHorizontal },
];
export default function App() {
  const client = useQueryClient();
  const host = useData<{ storage_warning: string | null }>("/api/health");
  const session = useData<{ user: User | null }>("/api/session");
  const [notice, setNotice] = useState<{
    message: string;
    error: boolean;
  } | null>(null);
  const notify = useCallback(
    (message: string, error = false) => setNotice({ message, error }),
    [],
  );
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 6500);
    return () => clearTimeout(t);
  }, [notice]);
  useEffect(() => {
    const expire = () => {
      void client.cancelQueries();
      client.removeQueries({
        predicate: (q) => q.queryKey[0] !== "/api/session",
      });
      client.setQueryData(["/api/session"], { user: null });
      notify("Your session expired. Sign in again.", true);
    };
    window.addEventListener("session-expired", expire);
    return () => window.removeEventListener("session-expired", expire);
  }, [client, notify]);
  const onSuccess = (user: User) => {
    void client.cancelQueries();
    client.removeQueries({
      predicate: (q) => q.queryKey[0] !== "/api/session",
    });
    client.setQueryData(["/api/session"], { user });
  };
  return (
    <>
      {host.data?.storage_warning && (
        <div className="host-warning" role="status">
          {host.data.storage_warning}
        </div>
      )}
      {session.isPending ? (
        <div className="initial-loading">
          <Logo />
          <Loading />
        </div>
      ) : session.error ? (
        <div className="initial-loading">
          <Logo />
          <ErrorState
            message={session.error.message}
            retry={() => session.refetch()}
          />
        </div>
      ) : session.data.user ? (
        <Workspace
          user={session.data.user}
          notify={notify}
          onLogout={async () => {
            await send("/api/logout", {});
            void client.cancelQueries();
            client.removeQueries({
              predicate: (q) => q.queryKey[0] !== "/api/session",
            });
            client.setQueryData(["/api/session"], { user: null });
          }}
        />
      ) : (
        <Auth onSuccess={onSuccess} />
      )}{" "}
      {notice && (
        <div
          className={"toast " + (notice.error ? "error" : "")}
          role={notice.error ? "alert" : "status"}
        >
          <ShieldCheck size={18} />
          <span>{notice.message}</span>
          <button
            className="icon-button"
            aria-label="Dismiss notification"
            onClick={() => setNotice(null)}
          >
            <X size={16} />
          </button>
        </div>
      )}
    </>
  );
}
function Workspace({
  user,
  notify,
  onLogout,
}: {
  user: User;
  notify: (s: string, e?: boolean) => void;
  onLogout: () => Promise<void>;
}) {
  const [page, setPage] = useState("overview");
  const [importing, setImporting] = useState(false);
  const [mobile, setMobile] = useState(false);
  const [command, setCommand] = useState(false);
  const [commandText, setCommandText] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [filters, setFilters] = useState<Filters>({
    from: "",
    to: "",
    account: "",
  });
  const [selected, setSelected] = useState<Transaction | null>(null);
  const data = useData<OverviewData>("/api/overview" + query(filters));
  const meta = useData<Meta>("/api/meta");
  const { act, busy } = useAction(notify);
  const onOpen = useCallback((r: Transaction) => setSelected(r), []);
  function navigate(s: string) {
    setPage(s);
    setMobile(false);
    setCommand(false);
    window.scrollTo(0, 0);
  }
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCommand((v) => !v);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  const title =
    page === "settings"
      ? "Settings"
      : navigation.find((n) => n.id === page)?.label || "Overview";
  const filtered = Boolean(filters.from || filters.to || filters.account);
  const sample = () =>
    act(
      () => send("/api/sample", {}),
      "Fictional sample staged. Review its entries before posting.",
      () => navigate("review"),
    );
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to workspace
      </a>
      {mobile && (
        <button
          className="mobile-scrim"
          aria-label="Close navigation"
          onClick={() => setMobile(false)}
        />
      )}
      <aside className={"sidebar " + (mobile ? "open" : "")}>
        <Logo />
        <div className="workspace-identity">
          <span className="workspace-avatar">
            {user.name[0]?.toUpperCase()}
          </span>
          <div>
            <strong>Personal workspace</strong>
            <small>{user.name}</small>
          </div>
          <ChevronDown size={14} />
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav aria-label="Main navigation">
          {navigation.map((n, i) => (
            <button
              key={n.id}
              className={
                (page === n.id ? "active " : "") + (i === 6 ? "nav-break" : "")
              }
              onClick={() => navigate(n.id)}
              aria-current={page === n.id ? "page" : undefined}
            >
              <n.icon size={18} />
              <span>{n.label}</span>
              {n.id === "review" && Boolean(data.data?.report.pending) && (
                <span className="nav-count">{data.data?.report.pending}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-note">
            <ShieldCheck size={20} />
            <p>
              Clarity starts
              <br />
              with <em>good records.</em>
            </p>
            <span>Yours to review. Yours to understand.</span>
          </div>
          <button
            className={"settings-nav " + (page === "settings" ? "active" : "")}
            onClick={() => navigate("settings")}
          >
            <SettingsIcon size={18} />
            Workspace settings
          </button>
          <div className="sidebar-user">
            <span className="user-avatar">
              {user.name
                .split(" ")
                .map((s) => s[0])
                .slice(0, 2)
                .join("")}
            </span>
            <div>
              <strong>{user.name}</strong>
              <small>Personal account</small>
            </div>
            <button
              className="icon-button"
              aria-label="Sign out"
              onClick={() => act(onLogout, "Signed out.")}
            >
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button menu-toggle"
              aria-label="Open navigation"
              onClick={() => setMobile(true)}
            >
              <Menu size={20} />
            </button>
            <span>Workspace</span>
            <span>/</span>
            <strong>{title}</strong>
          </div>
          <div className="topbar-actions">
            <button className="command-button" onClick={() => setCommand(true)}>
              <Search size={16} />
              <span>Quick navigation</span>
              <kbd>⌘ K</kbd>
            </button>
            <span className="topbar-divider" />
            <span className="private-label">
              <span />
              Private workspace
            </span>
            <span className="user-avatar light">{user.name[0]}</span>
          </div>
        </header>
        <main id="main-content" className="workspace-content">
          <div className="workspace-tools">
            <span className="workspace-date">
              {new Date().toLocaleDateString("en-IN", {
                weekday: "long",
                day: "numeric",
                month: "long",
                year: "numeric",
              })}
            </span>
            <div>
              <button
                className={
                  "button secondary small " + (filtered ? "filter-active" : "")
                }
                onClick={() => setFiltersOpen(true)}
              >
                <CalendarDays size={15} />
                {filtered ? "Filters applied" : "All dates · All accounts"}
                <ChevronDown size={13} />
              </button>
              <button
                className="button primary small"
                onClick={() => setImporting(true)}
              >
                <Plus size={16} />
                Import statement
              </button>
            </div>
          </div>
          <Suspense fallback={<Loading />}>
            {page === "overview" ? (
              data.isPending ? (
                <Loading />
              ) : data.error ? (
                <ErrorState
                  message={data.error.message}
                  retry={() => data.refetch()}
                />
              ) : (
                <Overview
                  data={data.data}
                  onNavigate={navigate}
                  onImport={() => setImporting(true)}
                  onSample={sample}
                  onOpen={onOpen}
                  sampleBusy={busy}
                />
              )
            ) : page === "statements" ? (
              <StatementsPage onImport={() => setImporting(true)} />
            ) : page === "review" || page === "ledger" ? (
              <LedgerPage
                review={page === "review"}
                filters={filters}
                notify={notify}
                onOpen={onOpen}
              />
            ) : page === "reports" ? (
              <ReportsPage filters={filters} />
            ) : page === "reconciliation" ? (
              <ReconciliationPage notify={notify} />
            ) : page === "rules" ? (
              <RulesPage
                categories={meta.data?.categories || []}
                notify={notify}
              />
            ) : page === "trading" ? (
              <TradingPage filters={filters} notify={notify} onOpen={onOpen} />
            ) : (
              <Settings
                categories={meta.data?.categories || []}
                meta={meta.data}
                notify={notify}
              />
            )}
          </Suspense>
          <footer className="app-footer">
            <Logo small />
            <span>YOUR WEALTH, ACCOUNTED FOR.</span>
            <span>INR · Goldilocks 2.0</span>
          </footer>
        </main>
      </div>
      {importing && (
        <Suspense fallback={<Loading />}>
          <Import
            onClose={() => {
              setImporting(false);
              navigate("review");
            }}
            notify={notify}
          />
        </Suspense>
      )}
      {selected && (
        <TransactionDetail
          key={selected.id}
          row={selected}
          onClose={() => setSelected(null)}
          categories={meta.data?.categories || []}
          notify={notify}
        />
      )}
      <Modal
        open={filtersOpen}
        onClose={() => setFiltersOpen(false)}
        title="A closer look."
        description="Filter overview, posted ledger, broker cash flows and reports. The review inbox always shows all pending entries."
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            setFilters({
              from: String(f.get("from") || ""),
              to: String(f.get("to") || ""),
              account: String(f.get("account") || ""),
            });
            setFiltersOpen(false);
          }}
        >
          <div className="two-cols">
            <label>
              From date
              <input type="date" name="from" defaultValue={filters.from} />
            </label>
            <label>
              To date
              <input type="date" name="to" defaultValue={filters.to} />
            </label>
          </div>
          <label>
            Bank account
            <select name="account" defaultValue={filters.account}>
              <option value="">All accounts</option>
              {data.data?.accounts.map((a) => (
                <option value={a.account_id} key={a.account_id}>
                  {a.account_id}
                </option>
              ))}
            </select>
          </label>
          <div className="form-actions">
            <button
              type="button"
              className="button secondary"
              onClick={() => {
                setFilters({ from: "", to: "", account: "" });
                setFiltersOpen(false);
              }}
            >
              Clear filters
            </button>
            <button className="button primary" type="submit">
              Apply filters
              <ArrowRight size={16} />
            </button>
          </div>
        </form>
      </Modal>
      <Modal
        open={command}
        onClose={() => setCommand(false)}
        title="Where would you like to go?"
        description="Jump to a page. Search transactions inside the ledger or review inbox."
      >
        <label className="search-box command-search">
          <Search size={18} />
          <input
            autoFocus
            value={commandText}
            onChange={(e) => setCommandText(e.target.value)}
            placeholder="Find a page…"
            aria-label="Find a page"
          />
        </label>
        <div className="command-list">
          {[
            ...navigation,
            { id: "settings", label: "Workspace settings", icon: SettingsIcon },
          ]
            .filter((n) =>
              n.label.toLowerCase().includes(commandText.toLowerCase()),
            )
            .map((n) => (
              <button
                onClick={() => {
                  navigate(n.id);
                  setCommandText("");
                }}
                key={n.id}
              >
                <n.icon size={18} />
                {n.label}
                <ArrowRight size={16} />
              </button>
            ))}
        </div>
      </Modal>
    </div>
  );
}
