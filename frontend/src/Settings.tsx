import { useState } from "react";
import { ShieldCheck, Cpu, Database, KeyRound } from "lucide-react";
import {
  useData,
  send,
  money,
  type Category,
  type Notice,
  type Meta,
} from "./api";
import { CategorySelect } from "./Transactions";
import {
  Badge,
  ErrorState,
  Loading,
  PageTitle,
  PanelTitle,
  Submit,
  useAction,
} from "./ui";
type Profile = {
  entity_type: string;
  legal_name: string;
  huf_name: string;
  is_salaried: number;
  employer_name: string;
};
type Openings = {
  bank: { account_id: string; amount: number; as_of: string }[];
  holdings: {
    account_id: string;
    ledger_key: string;
    amount: number;
    as_of: string;
  }[];
};
export default function Settings({
  categories,
  meta,
  notify,
}: {
  categories: Category[];
  meta?: Meta;
  notify: Notice;
}) {
  const p = useData<{ profile: Profile }>("/api/profile");
  const o = useData<Openings>("/api/openings");
  const h = useData<{
    ocr_available: boolean;
    persistent_storage_configured: boolean;
    version: string;
  }>("/api/health");
  const [kind, setKind] = useState("bank");
  const [key, setKey] = useState("");
  const { act, busy } = useAction(notify);
  return (
    <>
      <PageTitle
        eyebrow="MAKE THE WORKSPACE YOURS"
        title="The details behind the books."
        description="Set your profile and opening positions so the ledger has the context it needs."
      />
      <div className="settings-grid">
        <section className="panel form-panel">
          <PanelTitle title="Your accounting profile" />
          {p.isPending ? (
            <Loading />
          ) : p.error ? (
            <ErrorState message={p.error.message} />
          ) : (
            <form
              key={p.data.profile.legal_name}
              onSubmit={(e) => {
                e.preventDefault();
                const f = new FormData(e.currentTarget);
                const data = {
                  ...Object.fromEntries(f),
                  is_salaried: f.has("is_salaried"),
                };
                act(() => send("/api/profile", data, "PUT"), "Profile saved.");
              }}
            >
              <label>
                Legal name
                <input
                  name="legal_name"
                  defaultValue={p.data.profile.legal_name}
                  required
                  maxLength={100}
                />
              </label>
              <label>
                Entity type
                <select
                  name="entity_type"
                  defaultValue={p.data.profile.entity_type}
                >
                  <option value="INDIVIDUAL">Individual</option>
                  <option value="NRI">NRI</option>
                  <option value="HUF">HUF</option>
                </select>
              </label>
              <label>
                HUF name <span className="optional">if applicable</span>
                <input name="huf_name" defaultValue={p.data.profile.huf_name} />
              </label>
              <label className="check-label">
                <input
                  type="checkbox"
                  name="is_salaried"
                  defaultChecked={Boolean(p.data.profile.is_salaried)}
                />
                I receive a salary
              </label>
              <label>
                Employer name
                <input
                  name="employer_name"
                  defaultValue={p.data.profile.employer_name}
                  placeholder="Helps identify recurring salary credits"
                />
              </label>
              <Submit busy={busy}>Save profile</Submit>
            </form>
          )}
        </section>
        <section className="panel form-panel">
          <PanelTitle title="Opening positions" />
          <p className="muted">
            Record assets and debt held before your first transaction. Associate
            each position with the same account label used in imports.
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const f = Object.fromEntries(new FormData(e.currentTarget));
              act(
                () =>
                  send(
                    "/api/openings",
                    { ...f, key: kind === "bank" ? "bank" : key },
                    "PUT",
                  ),
                "Opening position saved.",
              );
            }}
          >
            <label>
              Account label
              <input
                name="account_id"
                required
                maxLength={60}
                placeholder="e.g. HDFC - Savings"
              />
            </label>
            <label>
              Position type
              <select value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="bank">Bank opening balance</option>
                <option value="holding">Other asset or outstanding debt</option>
              </select>
            </label>
            {kind === "holding" && (
              <label>
                Category
                <CategorySelect
                  categories={categories.filter(
                    (c) =>
                      c.section === "Assets" || c.section === "Liabilities",
                  )}
                  value={key}
                  onChange={setKey}
                />
              </label>
            )}
            <div className="two-cols">
              <label>
                Opening amount
                <input
                  name="amount"
                  type="number"
                  step="0.01"
                  required
                  placeholder="0.00"
                />
              </label>
              <label>
                As of date
                <input name="as_of" type="date" required />
              </label>
            </div>
            <Submit busy={busy}>Save opening position</Submit>
          </form>
          <div className="opening-list">
            {o.data?.bank.map((b) => (
              <div key={b.account_id}>
                <span>
                  {b.account_id}
                  <small>Bank · {b.as_of}</small>
                </span>
                <strong>{money(b.amount / 100)}</strong>
              </div>
            ))}
            {o.data?.holdings.map((b) => (
              <div key={b.account_id + b.ledger_key}>
                <span>
                  {categories.find((c) => c.key === b.ledger_key)?.label}
                  <small>
                    {b.account_id} · {b.as_of}
                  </small>
                </span>
                <strong>{money(b.amount / 100)}</strong>
              </div>
            ))}
          </div>
          <p className="info-note">
            Saving the same account/category updates its opening, rather than
            adding it twice. Set an unwanted holding to zero. Bank openings can
            initially be inferred from statement rows.
          </p>
        </section>
      </div>
      <div className="panel system-panel">
        <PanelTitle
          title="Inside your workspace"
          action={<Badge>v{h.data?.version || "2"}</Badge>}
        />
        <div className="system-grid">
          <div>
            <ShieldCheck size={22} />
            <strong>Account isolation</strong>
            <p>
              Revocable sessions, HttpOnly cookies and ownership checks on
              financial records.
            </p>
          </div>
          <div>
            <Cpu size={22} />
            <strong>
              {meta?.model.ready
                ? "Classifier available"
                : "Classifier loading"}
            </strong>
            <p>
              {meta?.model.classes || 0} synthetic training categories. Your
              corrections remain personal.
            </p>
          </div>
          <div>
            <Database size={22} />
            <strong>
              {h.data?.persistent_storage_configured
                ? "Data directory configured"
                : "Storage needs host verification"}
            </strong>
            <p>
              A configured directory is durable only if the host attaches a
              persistent disk. OCR{" "}
              {h.data?.ocr_available ? "is available." : "is not installed."}
            </p>
          </div>
          <div>
            <KeyRound size={22} />
            <strong>No external model API</strong>
            <p>
              Documents are processed on this server. Hosted records are visible
              to its authorised administrators.
            </p>
          </div>
        </div>
      </div>
      <p className="info-note">
        Your session expires after 30 minutes of inactivity. Financial records
        are not end-to-end encrypted. The host must protect the disk and
        backups; this is not a certified accounting or security audit.
      </p>
    </>
  );
}
