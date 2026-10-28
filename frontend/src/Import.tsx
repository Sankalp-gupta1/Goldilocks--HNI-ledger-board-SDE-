import { useState, type FormEvent } from "react";
import {
  CloudUpload,
  FileText,
  LockKeyhole,
  ArrowRight,
  Check,
} from "lucide-react";
import { api, type Notice } from "./api";
import { Modal, useAction, Submit } from "./ui";
export default function Import({
  onClose,
  notify,
}: {
  onClose: () => void;
  notify: Notice;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [drag, setDrag] = useState(false);
  const [result, setResult] = useState<{
    staged: number;
    duplicates?: number;
    duplicate: boolean;
    warnings?: string[];
  } | null>(null);
  const { act, busy } = useAction(notify);
  function choose(f: File | undefined) {
    if (!f) return;
    if (f.size > 15_000_000) {
      notify("Choose a file smaller than 15 MB.", true);
      return;
    }
    setFile(f);
  }
  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!file) {
      notify("Choose a statement first.", true);
      return;
    }
    const data = new FormData(e.currentTarget);
    data.set("file", file);
    act(async () => {
      setResult(await api("/api/import", { method: "POST", body: data }));
    }, "Statement processed. Review before posting.");
  }
  return (
    <Modal
      open
      onClose={onClose}
      title={result ? "Ready for your review." : "Bring a little clarity."}
      description={
        result
          ? "Nothing is posted until you approve it."
          : "Import a bank statement. We’ll suggest categories; you stay in control."
      }
    >
      {result ? (
        <>
          <div className="import-result">
            <span className="success-seal">
              <Check />
            </span>
            <h2>
              {result.duplicate
                ? "Already in your workspace."
                : `${result.staged} entries ready to review.`}
            </h2>
            <p>
              {result.duplicate
                ? "This exact file was already imported. Your existing records are unchanged."
                : `${result.duplicates || 0} overlapping rows kept out of the review queue.`}
            </p>
          </div>
          {result.warnings?.map((w, i) => (
            <p className="info-note" key={i}>
              {w}
            </p>
          ))}
          <button className="button primary full" onClick={onClose}>
            Back to workspace
            <ArrowRight size={16} />
          </button>
        </>
      ) : (
        <form onSubmit={submit}>
          <label
            className={"upload-zone " + (drag ? "dragging" : "")}
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              choose(e.dataTransfer.files[0]);
            }}
          >
            <input
              type="file"
              accept=".pdf,.xlsx,.xls,.csv"
              onChange={(e) => choose(e.target.files?.[0])}
              aria-label="Statement file"
            />
            {file ? <FileText size={30} /> : <CloudUpload size={32} />}
            <strong>{file ? file.name : "Drop a statement here"}</strong>
            <span>
              {file
                ? `${(file.size / 1024).toFixed(0)} KB · Click to choose another`
                : "or click to choose a file"}
            </span>
            <small>PDF, XLSX, XLS or CSV · up to 15 MB</small>
          </label>
          <label>
            Account label
            <input
              name="account_id"
              required
              pattern="[A-Za-z0-9 _.-]{1,60}"
              maxLength={60}
              placeholder="e.g. HDFC - Savings"
            />
            <small>
              Use the same label for future statements from this account.
            </small>
          </label>
          <label>
            Statement password <span className="optional">if protected</span>
            <input
              name="password"
              type="password"
              autoComplete="off"
              placeholder="Used only to unlock this file"
            />
          </label>
          <details className="balance-options">
            <summary>Add opening and closing balances</summary>
            <p>
              Copy these from the statement summary to enable an independent
              balance check.
            </p>
            <div className="two-cols">
              <label>
                Opening balance
                <input
                  name="opening_balance"
                  type="number"
                  step="0.01"
                  placeholder="Optional"
                />
              </label>
              <label>
                Closing balance
                <input
                  name="closing_balance"
                  type="number"
                  step="0.01"
                  placeholder="Optional"
                />
              </label>
            </div>
          </details>
          <div className="privacy-note">
            <LockKeyhole size={16} />
            <span>
              Your file stays in your account on this server. The password is
              not saved. No external AI API is called.
            </span>
          </div>
          <div className="form-actions">
            <a href="/api/statement-template" className="quiet-link">
              Download CSV template
            </a>
            <Submit busy={busy}>Import statement</Submit>
          </div>
        </form>
      )}
    </Modal>
  );
}
