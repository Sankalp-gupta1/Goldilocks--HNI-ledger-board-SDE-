import { useMemo, useState } from "react";
import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type RowSelectionState,
  type SortingState,
  type HeaderContext,
  type CellContext,
} from "@tanstack/react-table";
import {
  ArrowDownLeft,
  ArrowUpRight,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  Search,
  CheckCheck,
  FileText,
  Split,
  Plus,
  Trash2,
  Download,
} from "lucide-react";
import {
  useData,
  send,
  money,
  shortDate,
  type Transaction,
  type Category,
  type Notice,
} from "./api";
import {
  Badge,
  Empty,
  ErrorState,
  Loading,
  Modal,
  Submit,
  useAction,
} from "./ui";
export function CategorySelect({
  categories,
  value,
  onChange,
}: {
  categories: Category[];
  value: string;
  onChange: (s: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      required
      aria-label="Ledger category"
    >
      <option value="">Choose a category</option>
      {[
        "Income",
        "Expenditure",
        "Assets",
        "Liabilities",
        "Equity",
        "Suspense",
      ].map((section) => (
        <optgroup key={section} label={section}>
          {categories
            .filter((c) => c.section === section)
            .map((c) => (
              <option value={c.key} key={c.key}>
                {c.label}
              </option>
            ))}
        </optgroup>
      ))}
    </select>
  );
}
export function TransactionTable({
  rows,
  review = false,
  onOpen,
  notify,
}: {
  rows: Transaction[];
  review?: boolean;
  onOpen: (r: Transaction) => void;
  notify: Notice;
}) {
  const [search, setSearch] = useState("");
  const [sorting, setSorting] = useState<SortingState>([
    { id: "txn_date", desc: true },
  ]);
  const [selection, setSelection] = useState<RowSelectionState>({});
  const { act, busy } = useAction(notify);
  const columns = useMemo<ColumnDef<Transaction>[]>(
    () => [
      ...(review
        ? [
            {
              id: "select",
              header: ({ table }: HeaderContext<Transaction, unknown>) => (
                <input
                  aria-label="Select this page"
                  type="checkbox"
                  checked={table.getIsAllPageRowsSelected()}
                  onChange={table.getToggleAllPageRowsSelectedHandler()}
                />
              ),
              cell: ({ row }: CellContext<Transaction, unknown>) => (
                <input
                  aria-label={"Select " + row.original.narration}
                  type="checkbox"
                  checked={row.getIsSelected()}
                  onChange={row.getToggleSelectedHandler()}
                />
              ),
              enableSorting: false,
            },
          ]
        : []),
      {
        accessorKey: "txn_date",
        header: "Date",
        cell: ({ getValue }) => (
          <span className="table-date">{shortDate(getValue<string>())}</span>
        ),
      },
      {
        accessorKey: "narration",
        header: "Transaction",
        cell: ({ row }) => (
          <button
            className="transaction-name"
            onClick={() => onOpen(row.original)}
          >
            <span className={"transaction-icon " + row.original.txn_type}>
              {row.original.txn_type === "credit" ? (
                <ArrowDownLeft size={17} />
              ) : (
                <ArrowUpRight size={17} />
              )}
            </span>
            <span>
              <strong>
                {row.original.counterparty || row.original.narration}
              </strong>
              <small>{row.original.narration}</small>
            </span>
          </button>
        ),
      },
      {
        accessorKey: "account",
        header: "Category",
        cell: ({ row }) => (
          <span className="category-cell">
            {row.original.section === "Suspense" ? (
              <Badge tone="amber">Needs a category</Badge>
            ) : (
              row.original.account
            )}
            <small>{row.original.account_id}</small>
          </span>
        ),
      },
      ...(review
        ? [
            {
              accessorKey: "confidence",
              header: "Suggestion",
              cell: ({ row }: CellContext<Transaction, unknown>) => (
                <span className="suggestion">
                  <span className="score-track">
                    <i
                      style={{
                        width: Math.round(row.original.confidence * 100) + "%",
                      }}
                    />
                  </span>
                  <span>
                    {Math.round(row.original.confidence * 100)}%{" "}
                    <small>{row.original.source}</small>
                  </span>
                </span>
              ),
            },
          ]
        : []),
      {
        accessorKey: "amount",
        header: "Amount",
        cell: ({ row }) => (
          <span
            className={
              "amount " + (row.original.txn_type === "credit" ? "positive" : "")
            }
          >
            {row.original.txn_type === "credit" ? "+" : "−"}
            {money(row.original.amount)}
          </span>
        ),
      },
      {
        id: "open",
        header: "",
        cell: ({ row }) => (
          <button
            className="icon-button"
            aria-label={"Inspect " + row.original.narration}
            onClick={() => onOpen(row.original)}
          >
            <ArrowUpRight size={17} />
          </button>
        ),
      },
    ],
    [review, onOpen],
  );
  const table = useReactTable({
    data: rows,
    columns,
    state: { globalFilter: search, sorting, rowSelection: selection },
    onGlobalFilterChange: setSearch,
    onSortingChange: setSorting,
    onRowSelectionChange: setSelection,
    getRowId: (r) => r.id,
    enableRowSelection: true,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 10 } },
  });
  const ids = Object.keys(selection).filter(
    (k) => selection[k] && rows.some((r) => r.id === k),
  );
  return (
    <div className="panel table-panel">
      <div className="table-toolbar">
        <label className="search-box">
          <Search size={17} />
          <input
            aria-label="Search transactions"
            placeholder="Search a transaction, category or account…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <div className="table-actions">
          <span className="muted">
            {table.getFilteredRowModel().rows.length} records
          </span>
          {review && (
            <button
              disabled={!ids.length || busy}
              className="button primary small"
              onClick={() =>
                act(
                  () => send("/api/approve", { ids }),
                  `${ids.length} entries posted to your ledger.`,
                  () => setSelection({}),
                )
              }
            >
              <CheckCheck size={16} />
              {busy
                ? "Posting…"
                : `Post selected${ids.length ? " · " + ids.length : ""}`}
            </button>
          )}
        </div>
      </div>
      {rows.length ? (
        <>
          <div className="table-scroll">
            <table>
              <thead>
                {table.getHeaderGroups().map((g) => (
                  <tr key={g.id}>
                    {g.headers.map((h) => (
                      <th key={h.id}>
                        {h.isPlaceholder ? null : h.column.getCanSort() ? (
                          <button
                            className="table-sort"
                            onClick={h.column.getToggleSortingHandler()}
                          >
                            {flexRender(
                              h.column.columnDef.header,
                              h.getContext(),
                            )}
                            <ArrowUpDown size={12} />
                          </button>
                        ) : (
                          flexRender(h.column.columnDef.header, h.getContext())
                        )}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map((r) => (
                  <tr
                    key={r.id}
                    className={r.getIsSelected() ? "selected" : ""}
                  >
                    {r.getVisibleCells().map((c) => (
                      <td key={c.id}>
                        {flexRender(c.column.columnDef.cell, c.getContext())}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {!table.getRowModel().rows.length && (
              <Empty title="No matching records">
                Try a shorter search or another category.
              </Empty>
            )}
          </div>
          <div className="table-footer">
            <span>
              Showing {table.getRowModel().rows.length} of{" "}
              {table.getFilteredRowModel().rows.length} entries
              {ids.length > 0 && ` · ${ids.length} selected`}
            </span>
            <div>
              <button
                className="icon-button"
                disabled={!table.getCanPreviousPage()}
                aria-label="Previous page"
                onClick={() => table.previousPage()}
              >
                <ChevronLeft size={17} />
              </button>
              <span>
                {table.getState().pagination.pageIndex + 1} /{" "}
                {Math.max(1, table.getPageCount())}
              </span>
              <button
                className="icon-button"
                disabled={!table.getCanNextPage()}
                aria-label="Next page"
                onClick={() => table.nextPage()}
              >
                <ChevronRight size={17} />
              </button>
            </div>
          </div>
        </>
      ) : (
        <Empty
          title={
            review ? "Your review inbox is clear." : "Your ledger starts here."
          }
        >
          {review
            ? "Import a statement to review suggested categories."
            : "Approved transactions will appear here, with their original source attached."}
        </Empty>
      )}
    </div>
  );
}
type Trace = {
  transaction: Transaction;
  posted: boolean;
  source: {
    source_file_name: string;
    batch_id: string;
    raw_json: string;
    row_number: number;
    normalized_balance: number | null;
  } | null;
  journal: { code: string; label: string; debit: number; credit: number }[];
  splits: { ledger_key: string; amount: number }[];
  events: {
    id: string;
    action: string;
    created_at: string;
    after_json: string;
  }[];
};
export function TransactionDetail({
  row,
  onClose,
  categories,
  notify,
}: {
  row: Transaction;
  onClose: () => void;
  categories: Category[];
  notify: Notice;
}) {
  const q = useData<Trace>("/api/trace/" + row.id);
  const [key, setKey] = useState(
    row.reclassified_key || row.ledger_key || row.predicted_ledger_key || "",
  );
  const [note, setNote] = useState("");
  const [split, setSplit] = useState(false);
  const [allocations, setAllocations] = useState([
    { key: "", amount: "" },
    { key: "", amount: "" },
  ]);
  const { act, busy } = useAction(notify);
  let sourcePage: unknown;
  try {
    sourcePage = q.data?.source
      ? JSON.parse(q.data.source.raw_json).source_page
      : null;
  } catch {
    /* Original JSON remains downloadable. */
  }
  return (
    <Modal
      open
      onClose={onClose}
      title="Every entry has a story."
      description="Review its category, follow the evidence and see what changed."
      wide
    >
      {q.isPending ? (
        <Loading />
      ) : q.error ? (
        <ErrorState message={q.error.message} />
      ) : (
        q.data && (
          <>
            <div className="detail-head">
              <span className={"transaction-icon large " + row.txn_type}>
                {row.txn_type === "credit" ? (
                  <ArrowDownLeft />
                ) : (
                  <ArrowUpRight />
                )}
              </span>
              <div>
                <span className="eyebrow">
                  {row.txn_type === "credit" ? "MONEY IN" : "MONEY OUT"} ·{" "}
                  {shortDate(row.txn_date)}
                </span>
                <h2 className="detail-amount">{money(row.amount)}</h2>
              </div>
              <Badge tone={q.data.posted ? "green" : "amber"}>
                {q.data.posted ? "Posted" : "Awaiting review"}
              </Badge>
            </div>
            <div className="narration">
              <span className="eyebrow">ORIGINAL NARRATION</span>
              <p>{row.narration}</p>
            </div>
            <div className="detail-grid">
              <div>
                <span>Bank account</span>
                <strong>{row.account_id}</strong>
              </div>
              <div>
                <span>Counterparty</span>
                <strong>{row.counterparty || "Not identified"}</strong>
              </div>
              <div>
                <span>Classification source</span>
                <strong>{row.source}</strong>
              </div>
              <div>
                <span>Model / rule score</span>
                <strong>
                  {Math.round(row.confidence * 100)}%{" "}
                  <small>Not verified accuracy</small>
                </strong>
              </div>
            </div>
            {row.note && <p className="info-note">{row.note}</p>}
            <div className="section-divider" />
            <div className="tab-row">
              <button
                className={!split ? "active" : ""}
                onClick={() => setSplit(false)}
              >
                Change category
              </button>
              <button
                className={split ? "active" : ""}
                onClick={() => {
                  setSplit(true);
                  if (q.data?.splits.length)
                    setAllocations(
                      q.data.splits.map((s) => ({
                        key: s.ledger_key,
                        amount: String(s.amount / 100),
                      })),
                    );
                }}
              >
                <Split size={15} />
                Split allocation
              </button>
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                act(
                  () =>
                    send(
                      "/api/transactions/" +
                        row.id +
                        (split ? "/split" : "/classification"),
                      split
                        ? {
                            allocations: allocations.map((a) => ({
                              ...a,
                              amount: Number(a.amount),
                            })),
                            note,
                            posted: q.data!.posted,
                          }
                        : { key, note, posted: q.data!.posted },
                    ),
                  "Review saved.",
                );
              }}
            >
              {split ? (
                <>
                  <p className="muted">
                    Separate principal, interest or gains. All parts must total{" "}
                    {money(row.amount)}.
                  </p>
                  {allocations.map((a, i) => (
                    <div className="split-row" key={i}>
                      <CategorySelect
                        categories={categories.filter(
                          (c) => c.section !== "Suspense",
                        )}
                        value={a.key}
                        onChange={(key) =>
                          setAllocations((v) =>
                            v.map((x, j) => (i === j ? { ...x, key } : x)),
                          )
                        }
                      />
                      <input
                        type="number"
                        step="0.01"
                        min="0.01"
                        required
                        placeholder="Amount"
                        aria-label={"Split amount " + (i + 1)}
                        value={a.amount}
                        onChange={(e) =>
                          setAllocations((v) =>
                            v.map((x, j) =>
                              i === j ? { ...x, amount: e.target.value } : x,
                            ),
                          )
                        }
                      />
                      {allocations.length > 2 && (
                        <button
                          type="button"
                          className="icon-button"
                          aria-label="Remove allocation"
                          onClick={() =>
                            setAllocations((v) => v.filter((_, j) => i !== j))
                          }
                        >
                          <Trash2 size={15} />
                        </button>
                      )}
                    </div>
                  ))}
                  <div className="split-footer">
                    <button
                      type="button"
                      className="text-button"
                      disabled={allocations.length >= 12}
                      onClick={() =>
                        setAllocations((v) => [...v, { key: "", amount: "" }])
                      }
                    >
                      <Plus size={15} />
                      Add allocation
                    </button>
                    <span>
                      Remaining:{" "}
                      {money(
                        row.amount -
                          allocations.reduce((s, a) => s + Number(a.amount), 0),
                      )}
                    </span>
                  </div>
                </>
              ) : (
                <label>
                  Ledger category
                  <CategorySelect
                    categories={categories}
                    value={key}
                    onChange={setKey}
                  />
                </label>
              )}
              <label>
                Review note
                <input
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  maxLength={1000}
                  placeholder="Why this category? Add context for your future self."
                />
              </label>
              <div className="form-actions">
                <Submit busy={busy}>
                  Save {split ? "allocation" : "review"}
                </Submit>
                {!q.data.posted && (
                  <button
                    type="button"
                    className="button secondary"
                    disabled={busy}
                    onClick={() =>
                      act(
                        () => send("/api/approve", { ids: [row.id] }),
                        "Entry posted.",
                        onClose,
                      )
                    }
                  >
                    Post this entry
                    <CheckCheck size={16} />
                  </button>
                )}
              </div>
            </form>
            <div className="section-divider" />
            <h3>
              <FileText size={17} /> Source evidence
            </h3>
            {q.data.source ? (
              <div className="source-card">
                <div>
                  <strong>{q.data.source.source_file_name}</strong>
                  <small>
                    Source row {q.data.source.row_number}
                    {sourcePage ? " · Page " + String(sourcePage) : ""} ·
                    Running balance {money(q.data.source.normalized_balance)}
                  </small>
                </div>
                <a
                  className="icon-button"
                  aria-label="Download original statement"
                  href={"/api/statements/" + q.data.source.batch_id + "/source"}
                >
                  <Download size={18} />
                </a>
              </div>
            ) : (
              <p className="muted">
                This older entry has no retained source file. Its saved
                narration is still available.
              </p>
            )}
            {q.data.journal.length > 0 && (
              <>
                <h3>Double-entry journal</h3>
                <div className="journal-lines">
                  {q.data.journal.map((j, i) => (
                    <div key={i}>
                      <span>{j.label}</span>
                      <span>
                        {j.debit
                          ? "Dr " + money(j.debit / 100)
                          : "Cr " + money(j.credit / 100)}
                      </span>
                    </div>
                  ))}
                </div>
              </>
            )}
            <h3>Activity trail</h3>
            <div className="timeline">
              {q.data.events.map((e) => (
                <div key={e.id}>
                  <i />
                  <span>
                    {e.action.replaceAll("_", " ")}
                    <small>{e.created_at.replace("T", " ").slice(0, 19)}</small>
                  </span>
                </div>
              ))}
              {!q.data.events.length && (
                <p className="muted">No review events recorded yet.</p>
              )}
            </div>
          </>
        )
      )}
    </Modal>
  );
}
