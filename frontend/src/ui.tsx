import type { ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  X,
  LoaderCircle,
  AlertCircle,
  ArrowUpRight,
  Inbox,
  Check,
} from "lucide-react";
import type { Notice } from "./api";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
export function Logo({ small = false }: { small?: boolean }) {
  return (
    <div className={"brand " + (small ? "small" : "")}>
      <span className="brand-mark" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <span>
        goldilocks<span className="brand-dot">.</span>
      </span>
    </div>
  );
}
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: string;
}) {
  return <span className={"badge " + tone}>{children}</span>;
}
export function Loading() {
  return (
    <div className="loading" role="status">
      <LoaderCircle size={22} className="spin" /> Opening your workspace…
    </div>
  );
}
export function ErrorState({
  message,
  retry,
}: {
  message: string;
  retry?: () => void;
}) {
  return (
    <div className="error-state" role="alert">
      <AlertCircle size={22} />
      <div>
        <strong>We couldn’t finish that request.</strong>
        <p>{message}</p>
        {retry && (
          <button className="button secondary" onClick={retry}>
            Try again
          </button>
        )}
      </div>
    </div>
  );
}
export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <Inbox size={25} />
      </span>
      <h3>{title}</h3>
      <p>{children}</p>
      {action}
    </div>
  );
}
export function PanelTitle({
  eyebrow,
  title,
  action,
}: {
  eyebrow?: string;
  title: string;
  action?: ReactNode;
}) {
  return (
    <div className="panel-title">
      <div>
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h2>{title}</h2>
      </div>
      {action}
    </div>
  );
}
export function PageTitle({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="page-title">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action}
    </div>
  );
}
export function ArrowButton({
  children,
  onClick,
}: {
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <button className="text-button" onClick={onClick}>
      {children}
      <ArrowUpRight size={16} />
    </button>
  );
}
export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  wide = false,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description: string;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(v) => {
        if (!v) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="overlay" />
        <Dialog.Content className={"dialog " + (wide ? "wide" : "")}>
          <div className="dialog-top">
            <div>
              <Dialog.Title>{title}</Dialog.Title>
              <Dialog.Description>{description}</Dialog.Description>
            </div>
            <Dialog.Close className="icon-button" aria-label="Close dialog">
              <X size={20} />
            </Dialog.Close>
          </div>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
export function useAction(notify: Notice) {
  const [busy, setBusy] = useState(false);
  const client = useQueryClient();
  async function act(
    fn: () => Promise<unknown>,
    message: string,
    done?: () => void,
  ) {
    if (busy) return;
    setBusy(true);
    try {
      await fn();
      await client.invalidateQueries();
      notify(message);
      done?.();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Please try again.", true);
    } finally {
      setBusy(false);
    }
  }
  return { busy, act };
}
export function Submit({
  busy,
  children,
}: {
  busy: boolean;
  children: ReactNode;
}) {
  return (
    <button className="button primary" type="submit" disabled={busy}>
      {busy ? <LoaderCircle size={16} className="spin" /> : <Check size={16} />}{" "}
      {busy ? "Working…" : children}
    </button>
  );
}
