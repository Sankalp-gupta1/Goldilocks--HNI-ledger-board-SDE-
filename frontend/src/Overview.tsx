import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  ArrowUpRight,
  ArrowDownLeft,
  CircleCheck,
  Layers3,
  Landmark,
  FileCheck2,
  Plus,
  ArrowRight,
  ShieldCheck,
  ListChecks,
} from "lucide-react";
import {
  money,
  shortDate,
  type Overview as Data,
  type Transaction,
} from "./api";
import { ArrowButton, Badge, Empty, PanelTitle } from "./ui";
export default function Overview({
  data,
  onNavigate,
  onImport,
  onSample,
  onOpen,
  sampleBusy,
}: {
  data: Data;
  onNavigate: (s: string) => void;
  onImport: () => void;
  onSample: () => void;
  onOpen: (r: Transaction) => void;
  sampleBusy: boolean;
}) {
  const r = data.report;
  const bank = r.sections.Assets.filter((x) =>
    x.code?.startsWith("bank:"),
  ).reduce((s, x) => s + x.amount, 0);
  const expenses = [...r.expenses]
    .sort((a, b) => b.amount - a.amount)
    .slice(0, 5);
  const issues = data.reconciliation.filter(
    (x) => x.status !== "matched",
  ).length;
  return (
    <>
      <div className="overview-heading">
        <div>
          <div className="eyebrow">
            <span className="little-line" /> YOUR FINANCIAL PICTURE
          </div>
          <h1>
            A little clarity.
            <br />
            <em>A better perspective.</em>
          </h1>
          <p>Your money, brought together. Every number backed by a record.</p>
        </div>
        <div className="heading-note">
          <span className="outline-seal">
            <Layers3 size={26} />
          </span>
          <p>
            Considered. Connected.
            <br />
            <strong>Accounted for.</strong>
          </p>
        </div>
      </div>
      <div className="metrics">
        <div className="metric featured">
          <div className="metric-label">
            Recorded net worth <ArrowUpRight size={17} />
          </div>
          <strong>{money(r.net_worth)}</strong>
          <div className="metric-note">
            <span className="metric-dot" /> Assets less liabilities at recorded
            cost
          </div>
        </div>
        <div className="metric">
          <div className="metric-label">
            Income <ArrowDownLeft size={17} />
          </div>
          <strong>{money(r.income_total)}</strong>
          <div className="metric-note">
            Approved income in the selected period
          </div>
        </div>
        <div className="metric">
          <div className="metric-label">
            Expenditure <ArrowUpRight size={17} />
          </div>
          <strong>{money(r.expense_total)}</strong>
          <div className="metric-note">
            Excludes transfers and asset purchases
          </div>
        </div>
        <div className="metric">
          <div className="metric-label">
            Surplus / deficit <Layers3 size={17} />
          </div>
          <strong>{money(r.surplus)}</strong>
          <div className="metric-note">Income minus expenditure</div>
        </div>
      </div>
      <div className="overview-main">
        <section className="panel cashflow-panel">
          <PanelTitle
            eyebrow="THE LONGER VIEW"
            title="Money in motion"
            action={
              <div className="chart-legend">
                <span>
                  <i />
                  Inflow
                </span>
                <span>
                  <i />
                  Outflow
                </span>
              </div>
            }
          />
          {data.months.length ? (
            <div
              className="chart-wrap"
              role="img"
              aria-label="Monthly incoming and outgoing approved cash transactions"
            >
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={data.months}
                  margin={{ top: 18, right: 12, left: 2, bottom: 0 }}
                >
                  <defs>
                    <linearGradient id="flowFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#75987d" stopOpacity={0.4} />
                      <stop
                        offset="100%"
                        stopColor="#75987d"
                        stopOpacity={0.01}
                      />
                    </linearGradient>
                  </defs>
                  <CartesianGrid
                    strokeDasharray="3 5"
                    vertical={false}
                    stroke="#e8eae5"
                  />
                  <XAxis
                    dataKey="month"
                    tickFormatter={(v) =>
                      new Date(v + "-01T12:00:00").toLocaleString("en", {
                        month: "short",
                      })
                    }
                    tickLine={false}
                    axisLine={false}
                    tick={{ fontSize: 11, fill: "#7c847e" }}
                    dy={10}
                  />
                  <YAxis
                    tickFormatter={(v) => money(v, true)}
                    tickLine={false}
                    axisLine={false}
                    tick={{ fontSize: 10, fill: "#7c847e" }}
                    width={58}
                  />
                  <Tooltip
                    formatter={(value) => money(Number(value))}
                    contentStyle={{
                      border: "1px solid #e3e6df",
                      borderRadius: 12,
                      fontSize: 12,
                    }}
                  />
                  <Area
                    isAnimationActive={false}
                    name="Inflow"
                    type="monotone"
                    dataKey="inflow"
                    stroke="#315e49"
                    strokeWidth={2.5}
                    fill="url(#flowFill)"
                    activeDot={{ r: 5, strokeWidth: 3, stroke: "#fff" }}
                  />
                  <Area
                    isAnimationActive={false}
                    name="Outflow"
                    type="monotone"
                    dataKey="outflow"
                    stroke="#bb9869"
                    strokeWidth={2}
                    strokeDasharray="5 4"
                    fill="transparent"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <Empty
              title="Your story is taking shape."
              action={
                <button className="button secondary" onClick={onImport}>
                  <Plus size={16} />
                  Import your first statement
                </button>
              }
            >
              Review and post a statement to see real cash-flow trends here.
            </Empty>
          )}
          <div className="panel-foot">
            <span>{data.transactions} approved transactions</span>
            <span>Cash movements include transfers and investments</span>
          </div>
        </section>
        <aside className="review-card">
          <div className="review-card-top">
            <span className="eyebrow">A LITTLE ATTENTION</span>
            <ListChecks size={22} />
          </div>
          <div className="review-number">
            {r.pending.toString().padStart(2, "0")}
            <span>entries to review</span>
          </div>
          <h2>
            You make
            <br />
            the final call.
          </h2>
          <p>
            Suggestions are a starting point. Review categories before they
            become part of your books.
          </p>
          <button onClick={() => onNavigate("review")}>
            Open review inbox
            <ArrowRight size={18} />
          </button>
          <div className="review-card-foot">
            <ShieldCheck size={14} /> Nothing posts without your approval
          </div>
        </aside>
      </div>
      <div className="overview-secondary">
        <section className="panel">
          <PanelTitle
            eyebrow="WHERE IT GOES"
            title="Expenditure, understood"
            action={
              <ArrowButton onClick={() => onNavigate("reports")}>
                Full report
              </ArrowButton>
            }
          />
          {expenses.length ? (
            <>
              <div className="expense-total">
                {money(r.expense_total)}
                <span>total expenditure</span>
              </div>
              <div className="expense-bars">
                {expenses.map((x, i) => (
                  <div key={x.label}>
                    <div>
                      <span>
                        <i
                          style={{
                            background: [
                              "#315e49",
                              "#79957e",
                              "#a4b69b",
                              "#bdad87",
                              "#d6c9ac",
                            ][i],
                          }}
                        />
                        {x.label}
                      </span>
                      <strong>{money(x.amount)}</strong>
                    </div>
                    <div className="bar-track">
                      <i
                        style={{
                          width:
                            Math.max(
                              1,
                              (x.amount / Math.max(r.expense_total, 1)) * 100,
                            ) + "%",
                          background: [
                            "#315e49",
                            "#79957e",
                            "#a4b69b",
                            "#bdad87",
                            "#d6c9ac",
                          ][i],
                        }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <Empty title="No expenses posted yet">
              Your largest expense categories will appear here.
            </Empty>
          )}
        </section>
        <section className="panel">
          <PanelTitle
            eyebrow="CONNECTED RECORDS"
            title="Your accounts"
            action={<Landmark size={19} className="muted" />}
          />
          <div className="account-balance">
            <span>Recorded bank assets</span>
            <strong>{money(bank)}</strong>
          </div>
          <div className="account-list">
            {data.accounts.map((a, i) => (
              <div key={a.account_id}>
                <span className="account-letter">
                  {a.account_id[0]?.toUpperCase()}
                </span>
                <div>
                  <strong>{a.account_id}</strong>
                  <small>{a.transactions} posted entries</small>
                </div>
                <Badge>{String(i + 1).padStart(2, "0")}</Badge>
              </div>
            ))}
            {!data.accounts.length && (
              <p className="muted">No statements imported yet.</p>
            )}
          </div>
          <button className="account-add" onClick={onImport}>
            <Plus size={16} />
            Add a statement
          </button>
        </section>
        <section className="panel integrity-panel">
          <PanelTitle eyebrow="BUILT ON EVIDENCE" title="A clearer trail" />
          <div className="integrity-item">
            <CircleCheck size={19} />
            <div>
              <strong>
                {r.journal_balanced && r.missing_journals === 0
                  ? "Journal entries balance"
                  : "Journal check needs attention"}
              </strong>
              <p>Debits and credits checked independently.</p>
            </div>
          </div>
          <div className="integrity-item">
            <FileCheck2 size={19} />
            <div>
              <strong>{data.statements} source statements</strong>
              <p>Original files stay attached to your records.</p>
            </div>
          </div>
          <div className="integrity-item">
            <Layers3 size={19} />
            <div>
              <strong>
                {issues
                  ? `${issues} balance checks need review`
                  : "Statement checks up to date"}
              </strong>
              <p>
                {issues
                  ? "Confirm inferred openings or investigate gaps."
                  : "Open the detail to see each balance calculation."}
              </p>
            </div>
          </div>
          <ArrowButton onClick={() => onNavigate("reconciliation")}>
            View reconciliation
          </ArrowButton>
        </section>
      </div>
      <section className="panel recent-panel">
        <PanelTitle
          eyebrow="THE LATEST MOVEMENTS"
          title="Recent activity"
          action={
            <ArrowButton onClick={() => onNavigate("ledger")}>
              View ledger
            </ArrowButton>
          }
        />
        {data.recent.length ? (
          <div className="recent-list">
            {data.recent.map((t) => (
              <button key={t.id} onClick={() => onOpen(t)}>
                <span className={"transaction-icon " + t.txn_type}>
                  {t.txn_type === "credit" ? (
                    <ArrowDownLeft size={18} />
                  ) : (
                    <ArrowUpRight size={18} />
                  )}
                </span>
                <span className="recent-description">
                  <strong>{t.counterparty || t.narration}</strong>
                  <small>
                    {t.account} · {t.account_id}
                  </small>
                </span>
                <span className="recent-date">{shortDate(t.txn_date)}</span>
                <span
                  className={
                    "amount " + (t.txn_type === "credit" ? "positive" : "")
                  }
                >
                  {t.txn_type === "credit" ? "+" : "−"}
                  {money(t.amount)}
                </span>
                <ArrowUpRight size={15} />
              </button>
            ))}
          </div>
        ) : (
          <Empty
            title="A clean slate."
            action={
              <button
                className="text-button"
                disabled={sampleBusy}
                onClick={onSample}
              >
                {sampleBusy ? "Loading…" : "Explore with fictional sample data"}
                <ArrowRight size={15} />
              </button>
            }
          >
            Start with your own statement, or try the review flow using an
            explicitly fictional sample.
          </Empty>
        )}
      </section>
      <div className="workspace-footnote">
        <ShieldCheck size={14} />
        <span>{r.scope}</span>
        <span>MADE FOR THE DETAILS.</span>
      </div>
    </>
  );
}
