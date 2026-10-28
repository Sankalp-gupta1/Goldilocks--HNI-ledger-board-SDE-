import { useQuery } from "@tanstack/react-query";
export type User = {
  id: string;
  name: string;
  email: string;
  user_type: string;
};
export type Category = {
  key: string;
  book: string;
  section: string;
  group: string;
  label: string;
};
export type Transaction = {
  id: string;
  txn_date: string;
  account_id: string;
  narration: string;
  amount: number;
  txn_type: string;
  account: string;
  section: string;
  book: string;
  counterparty: string;
  confidence: number;
  source: string;
  note: string;
  status?: string;
  ledger_key?: string;
  predicted_ledger_key?: string;
  reclassified_key?: string;
};
export type Line = { label: string; amount: number; code?: string };
export type Report = {
  sections: Record<string, Line[]>;
  totals: Record<string, number>;
  income: Line[];
  expenses: Line[];
  income_total: number;
  expense_total: number;
  surplus: number;
  net_worth: number;
  difference: number;
  journal_balanced: boolean;
  missing_journals: number;
  pending: number;
  scope: string;
};
export type Reconciliation = {
  id: string;
  file_name: string;
  account_id: string;
  opening: number | null;
  closing: number | null;
  computed_closing: number | null;
  difference: number | null;
  row_breaks: number[];
  status: string;
  rows: number;
  pending: number;
  duplicates: number;
  opening_source: string;
  closing_source: string;
};
export type Overview = {
  report: Report;
  months: {
    month: string;
    income: number;
    expense: number;
    inflow: number;
    outflow: number;
  }[];
  recent: Transaction[];
  transactions: number;
  statements: number;
  accounts: { account_id: string; transactions: number }[];
  events: {
    id: string;
    action: string;
    record_id: string;
    created_at: string;
  }[];
  reconciliation: Reconciliation[];
};
export type Meta = {
  categories: Category[];
  banks: string[];
  model: {
    ready: boolean;
    classes: number;
    training: string;
    score_note: string;
  };
};
export type Filters = { from: string; to: string; account: string };
export type Notice = (message: string, error?: boolean) => void;
export async function api<T>(url: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-Goldilocks", "1");
  if (init.body && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const r = await fetch(url, { ...init, headers, credentials: "same-origin" });
  const value = await r
    .json()
    .catch(() => ({ error: "The server returned an unreadable response." }));
  if (!r.ok) {
    if (r.status === 401 && !url.endsWith("/login"))
      window.dispatchEvent(new Event("session-expired"));
    throw new Error(value.error || "The request could not finish.");
  }
  return value as T;
}
export const send = <T>(url: string, data: unknown, method = "POST") =>
  api<T>(url, { method, body: JSON.stringify(data) });
export const useData = <T>(url: string) =>
  useQuery<T, Error>({
    queryKey: [url],
    queryFn: ({ signal }) => api<T>(url, { signal }),
  });
export function query(filters: Filters) {
  const p = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v) p.set(k, v);
  });
  return p.size ? "?" + p.toString() : "";
}
export const money = (n: number | null | undefined, compact = false) =>
  n == null
    ? "—"
    : new Intl.NumberFormat("en-IN", {
        style: "currency",
        currency: "INR",
        maximumFractionDigits: compact ? 1 : 2,
        minimumFractionDigits: compact ? 0 : 2,
        ...(compact ? { notation: "compact" as const } : {}),
      }).format(n);
export const shortDate = (s: string) =>
  new Date(s.slice(0, 10) + "T12:00:00").toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
