import { useState, type FormEvent } from "react";
import {
  ArrowRight,
  Check,
  Fingerprint,
  LockKeyhole,
  ShieldCheck,
} from "lucide-react";
import { send, type User } from "./api";
import { Logo } from "./ui";
export default function Auth({
  onSuccess,
}: {
  onSuccess: (user: User) => void;
}) {
  const [mode, setMode] = useState<"login" | "register" | "recover">("login");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{
    user: User;
    recovery_code?: string;
  } | null>(null);
  const [saved, setSaved] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const p = Object.fromEntries(new FormData(e.currentTarget));
      const r = await send<{ user: User; recovery_code?: string }>(
        "/api/" + mode,
        p,
      );
      if (r.recovery_code) setResult(r);
      else onSuccess(r.user);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="auth-shell">
      <section className="auth-story">
        <Logo />
        <div className="auth-story-main">
          <span className="eyebrow">THE PRIVATE LEDGER WORKSPACE</span>
          <h1>
            Your wealth.
            <br />
            Every detail.
            <br />
            <em>Accounted for.</em>
          </h1>
          <p>
            From a bank statement to a clear financial picture. Know where every
            number comes from.
          </p>
          <div className="story-ledger">
            <div>
              <span>01</span>
              <strong>Bring your statements</strong>
              <Check size={16} />
            </div>
            <div>
              <span>02</span>
              <strong>Review with confidence</strong>
              <Check size={16} />
            </div>
            <div>
              <span>03</span>
              <strong>See the whole picture</strong>
              <ArrowRight size={16} />
            </div>
          </div>
        </div>
        <div className="auth-bottom">
          <ShieldCheck size={18} />
          <span>Processed on this server. No external AI or banking API.</span>
        </div>
      </section>
      <main className="auth-form-panel">
        <div className="auth-topline">
          YOUR FINANCES, WITH CONTEXT <span>GL / 02</span>
        </div>
        <div className="auth-form-wrap">
          {result?.recovery_code ? (
            <>
              <span className="auth-icon">
                <Fingerprint />
              </span>
              <h2>Keep your way back in.</h2>
              <p className="muted">
                Save this recovery code in your password manager. We show it
                only now; no reset email is sent.
              </p>
              <code className="recovery-code">{result.recovery_code}</code>
              <label className="check-label">
                <input
                  type="checkbox"
                  checked={saved}
                  onChange={(e) => setSaved(e.target.checked)}
                />
                I have saved my recovery code.
              </label>
              <button
                className="button primary full"
                disabled={!saved}
                onClick={() => onSuccess(result.user)}
              >
                Open my workspace
                <ArrowRight size={17} />
              </button>
            </>
          ) : (
            <>
              <span className="auth-icon">
                <LockKeyhole size={23} />
              </span>
              <div className="eyebrow">A LITTLE ORDER. A LOT OF CLARITY.</div>
              <h2>
                {mode === "login"
                  ? "Welcome back."
                  : mode === "register"
                    ? "A fresh perspective."
                    : "Let’s get you back in."}
              </h2>
              <p className="muted">
                {mode === "login"
                  ? "Sign in to your personal ledger workspace."
                  : mode === "register"
                    ? "Create your own workspace. Your review, your records."
                    : "Use the recovery code saved when you created your account."}
              </p>
              <form onSubmit={submit}>
                {mode === "register" && (
                  <label>
                    Your name
                    <input
                      name="name"
                      autoComplete="name"
                      minLength={2}
                      maxLength={100}
                      required
                      placeholder="Your full name"
                    />
                  </label>
                )}
                <label>
                  {mode === "login"
                    ? "Email or existing phone number"
                    : "Email address"}
                  <input
                    name="email"
                    type={mode === "login" ? "text" : "email"}
                    autoComplete="username"
                    required
                    placeholder="you@example.com"
                  />
                </label>
                {mode === "recover" && (
                  <label>
                    Saved recovery code
                    <input name="recovery_code" autoComplete="off" required />
                  </label>
                )}
                <label>
                  {mode === "recover" ? "New password" : "Password"}
                  <input
                    name="password"
                    type="password"
                    autoComplete={
                      mode === "login" ? "current-password" : "new-password"
                    }
                    minLength={mode === "login" ? 1 : 12}
                    maxLength={128}
                    required
                    placeholder={
                      mode === "login"
                        ? "Enter your password"
                        : "At least 12 characters"
                    }
                  />
                </label>
                {mode !== "login" && (
                  <label>
                    Confirm password
                    <input
                      name="confirm_password"
                      type="password"
                      autoComplete="new-password"
                      minLength={12}
                      required
                      placeholder="Enter it once more"
                    />
                  </label>
                )}
                {error && (
                  <p className="form-error" role="alert">
                    {error}
                  </p>
                )}
                <button
                  type="submit"
                  className="button primary full"
                  disabled={busy}
                >
                  {busy
                    ? "Please wait…"
                    : mode === "login"
                      ? "Enter workspace"
                      : mode === "register"
                        ? "Create workspace"
                        : "Reset password"}
                  <ArrowRight size={17} />
                </button>
              </form>
              <div className="auth-switch">
                {mode === "login"
                  ? "New to Goldilocks?"
                  : "Already have an account?"}{" "}
                <button
                  onClick={() => {
                    setError("");
                    setMode(mode === "login" ? "register" : "login");
                  }}
                >
                  {mode === "login" ? "Create an account" : "Sign in"}
                </button>
              </div>
              {mode === "login" && (
                <button
                  className="quiet-link"
                  onClick={() => {
                    setError("");
                    setMode("recover");
                  }}
                >
                  Recover your account
                </button>
              )}
            </>
          )}
          <div className="auth-note">
            <ShieldCheck size={16} />
            <p>
              Separate accounts. Reviewed transactions. Traceable records.
              <br />
              Use a unique password and keep your recovery code private.
            </p>
          </div>
        </div>
        <div className="auth-footer">
          GOLDILOCKS LEDGER <span>Clarity is a good investment.</span>
        </div>
      </main>
    </div>
  );
}
