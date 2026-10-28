import { useState, type FormEvent } from "react";
import {
  Download,
  FileText,
  ArrowUpRight,
  ShieldCheck,
  Trash2,
  SlidersHorizontal,
  Check,
  AlertTriangle,
  Plus,
  Landmark,
} from "lucide-react";
import {
  api,
  useData,
  send,
  money,
  shortDate,
  query,
  type Filters,
  type Notice,
  type Category,
  type Report,
  type Reconciliation,
  type Transaction,
} from "./api";
import {
  Badge,
  Empty,
  ErrorState,
  Loading,
  Modal,
  PageTitle,
  PanelTitle,
  Submit,
  useAction,
} from "./ui";
import { CategorySelect, TransactionTable } from "./Transactions";
export function LedgerPage({
  review,
  filters,
  notify,
  onOpen,
}: {
  review: boolean;
  filters: Filters;
  notify: Notice;
  onOpen: (r: Transaction) => void;
}) {
  const q = useData<{ rows: Transaction[] }>(
    "/api/" + (review ? "pending" : "ledger") + (review ? "" : query(filters)),
  );
  return (
    <>
      <PageTitle
        eyebrow={review ? "YOUR JUDGEMENT, BUILT IN" : "THE SOURCE OF TRUTH"}
        title={review ? "A moment of review." : "Your ledger, in order."}
        description={
          review
            ? "Check the suggestions. Correct anything that needs context. Post only when you’re ready."
            : "Every posted entry, with a route back to its statement and review history."
        }
        action={
          !review ? (
            <a
              className="button secondary"
              href={"/api/export/csv" + query(filters)}
            >
              <Download size={16} />
              Export CSV
            </a>
          ) : undefined
        }
      />
      {review && (
        <div className="info-strip">
          <ShieldCheck size={17} />
          <span>
            This inbox shows all pending entries. Scores describe suggestions,
            not verified accuracy. Unclassified entries cannot be posted.
          </span>
        </div>
      )}
      {q.isPending ? (
        <Loading />
      ) : q.error ? (
        <ErrorState message={q.error.message} retry={() => q.refetch()} />
      ) : (
        <TransactionTable
          rows={q.data.rows}
          review={review}
          notify={notify}
          onOpen={onOpen}
        />
      )}
    </>
  );
}
type Statement = {
  id: string;
  source_file_name: string;
  account_id: string;
  bank: string;
  parser: string;
  staged_rows: number;
  approved_rows: number;
  duplicate_rows: number;
  created_at: string;
  warnings: string;
  file_size_bytes: number;
};
export function StatementsPage({ onImport }: { onImport: () => void }) {
  const q = useData<{ rows: Statement[] }>("/api/statements");
  return (
    <>
      <PageTitle
        eyebrow="START WITH THE SOURCE"
        title="Your document collection."
        description="Original statements, parsing notes and the progress of each import."
        action={
          <button className="button primary" onClick={onImport}>
            <Plus size={16} />
            Import statement
          </button>
        }
      />
      {q.isPending ? (
        <Loading />
      ) : q.error ? (
        <ErrorState message={q.error.message} />
      ) : q.data.rows.length ? (
        <div className="statement-grid">
          {q.data.rows.map((s) => (
            <article className="panel statement-card" key={s.id}>
              <div className="statement-card-top">
                <span className="file-icon">
                  <FileText size={24} />
                </span>
                <Badge>{s.bank || "Bank statement"}</Badge>
              </div>
              <h2>{s.source_file_name}</h2>
              <p>
                {s.account_id} · {(s.file_size_bytes / 1024).toFixed(0)} KB
              </p>
              <div className="statement-stats">
                <div>
                  <strong>{s.staged_rows}</strong>
                  <span>Staged</span>
                </div>
                <div>
                  <strong>{s.approved_rows}</strong>
                  <span>Approved</span>
                </div>
                <div>
                  <strong>{s.duplicate_rows}</strong>
                  <span>Overlaps</span>
                </div>
              </div>
              <progress
                aria-label="Approval progress"
                value={s.approved_rows}
                max={Math.max(1, s.staged_rows)}
              />
              <div className="statement-footer">
                <span>{shortDate(s.created_at)}</span>
                <a
                  className="text-button"
                  href={"/api/statements/" + s.id + "/source"}
                >
                  Original
                  <Download size={15} />
                </a>
              </div>
              {s.warnings && s.warnings !== "[]" && (
                <details className="parser-notes">
                  <summary>Parsing notes</summary>
                  {JSON.parse(s.warnings).map((w: string, i: number) => (
                    <p key={i}>{w}</p>
                  ))}
                </details>
              )}
            </article>
          ))}
        </div>
      ) : (
        <div className="panel">
          <Empty
            title="A place for every statement."
            action={
              <button className="button primary" onClick={onImport}>
                Import a statement
              </button>
            }
          >
            PDF, XLS, XLSX and CSV. Protected files can be opened with their
            correct password.
          </Empty>
        </div>
      )}
    </>
  );
}
export function ReportsPage({ filters }: { filters: Filters }) {
  const q = useData<Report>("/api/reports" + query(filters));
  const [tab, setTab] = useState("performance");
  return (
    <>
      <PageTitle
        eyebrow="THREE BOOKS. ONE PICTURE."
        title="The numbers, considered."
        description="Period income and expenses, an as-of balance sheet, and an independent accounting check."
        action={
          <div className="button-group">
            {["pdf", "xlsx", "csv"].map((k) => (
              <a
                className="button secondary small"
                key={k}
                href={"/api/export/" + k + query(filters)}
              >
                <Download size={15} />
                {k.toUpperCase()}
              </a>
            ))}
          </div>
        }
      />
      {q.isPending ? (
        <Loading />
      ) : q.error ? (
        <ErrorState message={q.error.message} />
      ) : (
        <>
          <div className="tab-row report-tabs">
            {[
              ["performance", "Income & expenditure"],
              ["position", "Balance sheet"],
              ["check", "Balance verification"],
            ].map(([key, label]) => (
              <button
                key={key}
                className={tab === key ? "active" : ""}
                onClick={() => setTab(key)}
              >
                {label}
              </button>
            ))}
          </div>
          {q.data.pending > 0 && (
            <div className="info-strip amber">
              <AlertTriangle size={17} />
              {q.data.pending} pending entries are excluded. Reports can be
              incomplete until review is finished.
            </div>
          )}
          {tab === "performance" ? (
            <div className="reports-grid">
              <ReportSection
                title="Income"
                rows={q.data.income}
                total={q.data.income_total}
              />
              <ReportSection
                title="Expenditure"
                rows={q.data.expenses}
                total={q.data.expense_total}
              />
              <div className="report-total">
                <span>Period surplus / deficit</span>
                <strong>{money(q.data.surplus)}</strong>
              </div>
            </div>
          ) : tab === "position" ? (
            <>
              <div className="reports-grid">
                <ReportSection
                  title="Assets"
                  rows={q.data.sections.Assets}
                  total={q.data.totals.Assets}
                />
                <ReportSection
                  title="Liabilities"
                  rows={q.data.sections.Liabilities}
                  total={q.data.totals.Liabilities}
                />
                <ReportSection
                  title="Equity"
                  rows={q.data.sections.Equity}
                  total={q.data.totals.Equity}
                />
                <div className="panel networth-card">
                  <Landmark size={26} />
                  <span>Recorded net worth</span>
                  <strong>{money(q.data.net_worth)}</strong>
                  <p>
                    Assets less liabilities. Holdings use recorded cost, with no
                    live price feed.
                  </p>
                </div>
              </div>
              {q.data.sections.Suspense.length > 0 && (
                <ReportSection
                  title="Unmatched transfer clearing"
                  rows={q.data.sections.Suspense}
                  total={q.data.totals.Suspense}
                />
              )}
            </>
          ) : (
            <div className="panel balance-proof">
              <span className="success-seal">
                <ShieldCheck />
              </span>
              <h2>Check the equation.</h2>
              <div className="equation">
                <div>
                  <span>Assets</span>
                  <strong>{money(q.data.totals.Assets)}</strong>
                </div>
                <b>−</b>
                <div>
                  <span>Liabilities</span>
                  <strong>{money(q.data.totals.Liabilities)}</strong>
                </div>
                <b>−</b>
                <div>
                  <span>Equity</span>
                  <strong>{money(q.data.totals.Equity)}</strong>
                </div>
                <b>=</b>
                <div
                  className={q.data.difference === 0 ? "positive" : "negative"}
                >
                  <span>Difference</span>
                  <strong>{money(q.data.difference)}</strong>
                </div>
              </div>
              <Badge
                tone={
                  q.data.journal_balanced && q.data.missing_journals === 0
                    ? "green"
                    : "amber"
                }
              >
                {q.data.journal_balanced
                  ? "Journal debits equal credits"
                  : "Journal imbalance detected"}{" "}
                · {q.data.missing_journals} missing journals
              </Badge>
              <p>
                Balanced books do not prove that every transaction is present or
                correctly categorised. Also check your statement balances and
                opening holdings.
              </p>
            </div>
          )}
          <p className="info-note">
            {q.data.scope} Income uses both date filters; the balance sheet
            includes history through the end date. Account filters apply to
            both.
          </p>
        </>
      )}
    </>
  );
}
function ReportSection({
  title,
  rows,
  total,
}: {
  title: string;
  rows: { label: string; amount: number }[];
  total: number;
}) {
  return (
    <section className="panel report-section">
      <PanelTitle title={title} />
      <div className="report-lines">
        {rows.map((r, i) => (
          <div key={i}>
            <span>{r.label}</span>
            <strong>{money(r.amount)}</strong>
          </div>
        ))}
        {!rows.length && (
          <p className="muted">No recorded values in this section.</p>
        )}
      </div>
      <div className="report-subtotal">
        <strong>Total {title.toLowerCase()}</strong>
        <strong>{money(total)}</strong>
      </div>
    </section>
  );
}
export function ReconciliationPage({ notify }: { notify: Notice }) {
  const q = useData<{ rows: Reconciliation[] }>("/api/reconciliation");
  const [selected, setSelected] = useState<Reconciliation | null>(null);
  const { act, busy } = useAction(notify);
  return (
    <>
      <PageTitle
        eyebrow="TRUST COMES FROM CHECKING"
        title="Make the balances meet."
        description="Opening balance + money in − money out should equal the statement’s closing balance."
      />
      {q.isPending ? (
        <Loading />
      ) : q.error ? (
        <ErrorState message={q.error.message} />
      ) : q.data.rows.length ? (
        <div className="reconciliation-list">
          {q.data.rows.map((r) => (
            <section key={r.id} className="panel recon-card">
              <div className="recon-title">
                <div>
                  <h2>{r.account_id}</h2>
                  <p>
                    {r.file_name} · {r.rows} parsed entries
                  </p>
                </div>
                <Badge tone={r.status === "matched" ? "green" : "amber"}>
                  {r.status === "matched"
                    ? "Balances match"
                    : r.status === "consistent_inferred"
                      ? "Opening needs confirmation"
                      : r.status === "gap"
                        ? "Balance gap"
                        : "More information needed"}
                </Badge>
              </div>
              <div className="recon-numbers">
                <div>
                  <span>Opening balance</span>
                  <strong>{money(r.opening)}</strong>
                  <small>{r.opening_source || "Not recorded"}</small>
                </div>
                <div>
                  <span>Calculated closing</span>
                  <strong>{money(r.computed_closing)}</strong>
                  <small>Opening + parsed cash movements</small>
                </div>
                <div>
                  <span>Statement closing</span>
                  <strong>{money(r.closing)}</strong>
                  <small>{r.closing_source || "Not recorded"}</small>
                </div>
                <div>
                  <span>Difference</span>
                  <strong className={r.difference ? "negative" : "positive"}>
                    {money(r.difference)}
                  </strong>
                  <small>{r.row_breaks.length} running-balance breaks</small>
                </div>
              </div>
              {r.row_breaks.length > 0 && (
                <p className="form-error">
                  Review source rows: {r.row_breaks.join(", ")}
                </p>
              )}
              <div className="recon-foot">
                <span>
                  {r.pending} entries awaiting approval · {r.duplicates} overlap
                  rows retained for the source check
                </span>
                <button className="text-button" onClick={() => setSelected(r)}>
                  Confirm source balances
                  <ArrowUpRight size={16} />
                </button>
              </div>
            </section>
          ))}
        </div>
      ) : (
        <div className="panel">
          <Empty title="No statements to reconcile yet">
            Import a statement with running balances to get started.
          </Empty>
        </div>
      )}
      <p className="info-note">
        An inferred opening checks consistency within the rows. It is not an
        independent confirmation. Copy opening and closing balances from the
        original statement summary to strengthen this check.
      </p>
      {selected && (
        <Modal
          open
          onClose={() => setSelected(null)}
          title="Confirm statement balances"
          description="Read these values from the original statement, not from the calculated total."
        >
          <a
            className="text-button"
            href={"/api/statements/" + selected.id + "/source"}
          >
            Download original statement
            <Download size={15} />
          </a>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const p = Object.fromEntries(new FormData(e.currentTarget));
              act(
                () =>
                  send(
                    "/api/statements/" + selected.id + "/balances",
                    p,
                    "PUT",
                  ),
                "Statement balances confirmed.",
                () => setSelected(null),
              );
            }}
          >
            <label>
              Opening balance
              <input
                name="opening"
                type="number"
                step="0.01"
                required
                defaultValue={selected.opening ?? ""}
              />
            </label>
            <label>
              Closing balance
              <input
                name="closing"
                type="number"
                step="0.01"
                required
                defaultValue={selected.closing ?? ""}
              />
            </label>
            <Submit busy={busy}>Save and check</Submit>
          </form>
        </Modal>
      )}
    </>
  );
}
type Rule = {
  id: string;
  pattern: string;
  match_mode: string;
  ledger_key: string;
  txn_type: string;
};
export function RulesPage({
  categories,
  notify,
}: {
  categories: Category[];
  notify: Notice;
}) {
  const q = useData<{ rows: Rule[] }>("/api/rules");
  const [key, setKey] = useState("");
  const { act, busy } = useAction(notify);
  return (
    <>
      <PageTitle
        eyebrow="TEACH YOUR OWN WORKSPACE"
        title="A little more personal."
        description="Personal rules and reviewed examples apply only to your account."
        action={
          <a className="button secondary" href="/api/learning-examples">
            <Download size={16} />
            Reviewed examples
          </a>
        }
      />
      <div className="settings-grid">
        <section className="panel form-panel">
          <PanelTitle title="Add a classification rule" />
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const form = e.currentTarget;
              const p = {
                ...Object.fromEntries(new FormData(form)),
                ledger_key: key,
              };
              act(
                () => send("/api/rules", p),
                "Rule added. It will apply to future imports.",
                () => {
                  form.reset();
                  setKey("");
                },
              );
            }}
          >
            <label>
              Narration text
              <input
                name="pattern"
                minLength={2}
                maxLength={160}
                required
                placeholder="e.g. CEDAR DESIGN SALARY"
              />
            </label>
            <div className="two-cols">
              <label>
                Match method
                <select name="match_mode">
                  <option value="contains">Contains this text</option>
                  <option value="exact">Exactly this text</option>
                </select>
              </label>
              <label>
                Direction
                <select name="txn_type">
                  <option value="">Either direction</option>
                  <option value="credit">Money in</option>
                  <option value="debit">Money out</option>
                </select>
              </label>
            </div>
            <label>
              Ledger category
              <CategorySelect
                value={key}
                onChange={setKey}
                categories={categories}
              />
            </label>
            <Submit busy={busy}>Save rule</Submit>
            <p className="muted">
              Existing rows are unchanged. Review corrections take priority over
              rules for exactly matching narrations and directions.
            </p>
          </form>
        </section>
        <section className="panel">
          <PanelTitle
            title="Your rules"
            action={<SlidersHorizontal size={19} />}
          />
          {q.isPending ? (
            <Loading />
          ) : q.error ? (
            <ErrorState message={q.error.message} />
          ) : q.data.rows.length ? (
            <div className="rules-list">
              {q.data.rows.map((r) => (
                <div key={r.id}>
                  <div>
                    <strong>{r.pattern}</strong>
                    <p>
                      {r.match_mode} · {r.txn_type || "either direction"}
                    </p>
                    <small>
                      {categories.find((c) => c.key === r.ledger_key)?.label ||
                        r.ledger_key}
                    </small>
                  </div>
                  <button
                    className="icon-button"
                    aria-label={"Disable rule " + r.pattern}
                    disabled={busy}
                    onClick={() =>
                      act(
                        () => api("/api/rules/" + r.id, { method: "DELETE" }),
                        "Rule disabled.",
                      )
                    }
                  >
                    <Trash2 size={17} />
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <Empty title="Your patterns, remembered">
              Add a rule when a recurring transaction deserves a consistent
              category.
            </Empty>
          )}
        </section>
      </div>
    </>
  );
}
export function TradingPage({
  filters,
  notify,
  onOpen,
}: {
  filters: Filters;
  notify: Notice;
  onOpen: (t: Transaction) => void;
}) {
  const q = useData<{
    rows: Transaction[];
    deposits: number;
    payouts: number;
    net: number;
    scope: string;
  }>("/api/trading" + query(filters));
  return (
    <>
      <PageTitle
        eyebrow="FOLLOW THE CAPITAL"
        title="Your broker cash movements."
        description="Money moved to and from trading accounts, kept separate from investment performance."
      />
      {q.isPending ? (
        <Loading />
      ) : q.error ? (
        <ErrorState message={q.error.message} />
      ) : (
        <>
          <div className="metrics three">
            {[
              ["Sent to brokers", q.data.deposits],
              ["Received from brokers", q.data.payouts],
              ["Net funds transferred", q.data.net],
            ].map(([title, n]) => (
              <div className="metric" key={title}>
                <span className="metric-label">{title}</span>
                <strong>{money(Number(n))}</strong>
                <p className="metric-note">Selected period and account</p>
              </div>
            ))}
          </div>
          <TransactionTable
            rows={q.data.rows}
            notify={notify}
            onOpen={onOpen}
          />
          <p className="info-note">
            {q.data.scope} Negative recorded broker balances appear as payables
            in the balance sheet. Add opening holdings where needed.
          </p>
        </>
      )}
    </>
  );
}
