"use client";

import { type ReactNode, useEffect, useRef } from "react";
import { XIcon } from "./icons";

export function Button({
  children,
  variant = "secondary",
  className = "",
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger" }) {
  const styles = {
    primary: "bg-brand text-brand-contrast hover:bg-brand-hover",
    secondary: "border border-line bg-bg hover:bg-surface-hover",
    ghost: "hover:bg-surface-hover",
    danger: "bg-bad text-white hover:opacity-90",
  }[variant];
  return (
    <button
      type="button"
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Field({
  label,
  error,
  hint,
  ...rest
}: React.InputHTMLAttributes<HTMLInputElement> & { label: string; error?: string; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-sm font-medium">{label}</span>
      <input
        className={`w-full rounded-lg border bg-bg px-3 py-2 text-sm outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20 ${
          error ? "border-bad" : "border-line"
        }`}
        {...rest}
      />
      {error ? (
        <span className="mt-1 block text-xs text-bad">{error}</span>
      ) : hint ? (
        <span className="mt-1 block text-xs text-muted">{hint}</span>
      ) : null}
    </label>
  );
}

export function Modal({
  title,
  onClose,
  children,
  footer,
  wide = false,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    panel.current?.querySelector<HTMLElement>("input, button[data-autofocus]")?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal aria-label={title}>
      <button type="button" aria-label="Close" className="absolute inset-0 bg-scrim" onClick={onClose} />
      <div
        ref={panel}
        className={`relative w-full ${wide ? "max-w-xl" : "max-w-md"} rounded-2xl border border-line bg-bg shadow-2xl`}
      >
        <div className="flex items-center justify-between px-5 pb-2 pt-4">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button type="button" onClick={onClose} className="rounded-md p-1 text-muted hover:bg-surface-hover" aria-label="Close">
            <XIcon size={18} />
          </button>
        </div>
        <div className="px-5 pb-4">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}

export function Spinner({ size = 18 }: { size?: number }) {
  return (
    <span
      className="inline-block animate-spin rounded-full border-2 border-current border-t-transparent text-muted"
      style={{ width: size, height: size }}
      aria-label="Loading"
    />
  );
}

export function ErrorBanner({ message, onClose }: { message: string; onClose?: () => void }) {
  return (
    <div className="mb-3 flex items-start gap-3 rounded-lg border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad">
      <span className="flex-1">{message}</span>
      {onClose && (
        <button type="button" onClick={onClose} aria-label="Dismiss">
          <XIcon size={16} />
        </button>
      )}
    </div>
  );
}

/** A short-lived message at the bottom of the screen. */
export function Toast({ message, onDone }: { message: string; onDone: () => void }) {
  useEffect(() => {
    const t = setTimeout(onDone, 4000);
    return () => clearTimeout(t);
  }, [message, onDone]);
  return (
    <div className="fixed bottom-5 left-1/2 z-[60] -translate-x-1/2 rounded-lg bg-fg px-4 py-2.5 text-sm text-bg shadow-lg">
      {message}
    </div>
  );
}
