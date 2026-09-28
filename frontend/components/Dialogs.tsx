"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Folder } from "@/lib/types";
import { ChevronRight, FolderGlyph, HomeFolderIcon } from "./icons";
import { Button, Field, Modal, Spinner } from "./ui";

export function NameDialog({
  title,
  label,
  initial = "",
  action,
  onSubmit,
  onClose,
}: {
  title: string;
  label: string;
  initial?: string;
  action: string;
  onSubmit: (name: string) => Promise<void>;
  onClose: () => void;
}) {
  const [name, setName] = useState(initial);
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  async function submit(e?: React.FormEvent) {
    e?.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    setError(undefined);
    try {
      await onSubmit(name.trim());
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
      setBusy(false);
    }
  }

  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={() => submit()} disabled={busy || !name.trim()}>
            {busy ? <Spinner size={14} /> : action}
          </Button>
        </>
      }
    >
      <form onSubmit={submit}>
        <Field
          label={label}
          value={name}
          onChange={(e) => setName(e.target.value)}
          error={error}
          maxLength={255}
          onFocus={(e) => {
            // select the name without its extension, like Finder
            const dot = e.target.value.lastIndexOf(".");
            e.target.setSelectionRange(0, dot > 0 ? dot : e.target.value.length);
          }}
        />
      </form>
    </Modal>
  );
}

export function ConfirmDialog({
  title,
  message,
  action,
  danger = false,
  onConfirm,
  onClose,
}: {
  title: string;
  message: React.ReactNode;
  action: string;
  danger?: boolean;
  onConfirm: () => Promise<void>;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant={danger ? "danger" : "primary"}
            data-autofocus
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onConfirm();
                onClose();
              } catch (err) {
                setError(err instanceof ApiError ? err.message : "Something went wrong");
                setBusy(false);
              }
            }}
          >
            {busy ? <Spinner size={14} /> : action}
          </Button>
        </>
      }
    >
      <div className="text-sm text-muted">{message}</div>
      {error && <p className="mt-2 text-sm text-bad">{error}</p>}
    </Modal>
  );
}

// ---------------------------------------------------------------- folder picker

function TreeLevel({
  parent,
  depth,
  selected,
  onSelect,
  disabled,
}: {
  parent: string;
  depth: number;
  selected: string | null;
  onSelect: (id: string | null, path: string) => void;
  disabled: Set<string>;
}) {
  const [folders, setFolders] = useState<Folder[] | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  useEffect(() => {
    api<Folder[]>(`/drive/tree?parent=${parent}`).then(setFolders).catch(() => setFolders([]));
  }, [parent]);
  if (folders === null)
    return (
      <div className="py-1" style={{ paddingLeft: 12 + depth * 18 }}>
        <Spinner size={14} />
      </div>
    );
  return (
    <ul>
      {folders.map((f) => {
        const blocked = disabled.has(f.id);
        return (
          <li key={f.id}>
            <div
              className={`flex items-center gap-1 rounded-md py-1 pr-2 text-sm ${
                selected === f.id ? "bg-selected font-medium" : blocked ? "opacity-40" : "hover:bg-surface-hover"
              }`}
              style={{ paddingLeft: 4 + depth * 18 }}
            >
              <button
                type="button"
                className={`rounded p-0.5 text-muted ${f.item_count ? "" : "invisible"}`}
                onClick={() => setOpen((o) => ({ ...o, [f.id]: !o[f.id] }))}
                aria-label={open[f.id] ? "Collapse" : "Expand"}
              >
                <ChevronRight size={14} className={`transition ${open[f.id] ? "rotate-90" : ""}`} />
              </button>
              <button
                type="button"
                disabled={blocked}
                className="flex min-w-0 flex-1 items-center gap-2 text-left disabled:cursor-not-allowed"
                onClick={() => onSelect(f.id, f.path)}
                onDoubleClick={() => setOpen((o) => ({ ...o, [f.id]: true }))}
              >
                <FolderGlyph size={18} />
                <span className="truncate">{f.name}</span>
              </button>
            </div>
            {open[f.id] && !blocked && (
              <TreeLevel parent={f.id} depth={depth + 1} selected={selected} onSelect={onSelect} disabled={disabled} />
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function MoveCopyDialog({
  mode,
  count,
  disabled,
  onSubmit,
  onClose,
}: {
  mode: "move" | "copy";
  count: number;
  /** folders being moved: they (and what's inside) can't be the destination */
  disabled: Set<string>;
  onSubmit: (targetId: string | null) => Promise<void>;
  onClose: () => void;
}) {
  const [target, setTarget] = useState<{ id: string | null; path: string }>({ id: null, path: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const verb = mode === "move" ? "Move" : "Copy";
  return (
    <Modal
      wide
      title={`${verb} ${count} item${count === 1 ? "" : "s"}`}
      onClose={onClose}
      footer={
        <>
          <span className="mr-auto self-center truncate text-sm text-muted">To: My files{target.path ? ` / ${target.path}` : ""}</span>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError(undefined);
              try {
                await onSubmit(target.id);
                onClose();
              } catch (err) {
                setError(err instanceof ApiError ? err.message : "Something went wrong");
                setBusy(false);
              }
            }}
          >
            {busy ? <Spinner size={14} /> : `${verb} here`}
          </Button>
        </>
      }
    >
      <div className="max-h-80 overflow-y-auto rounded-lg border border-line p-1">
        <button
          type="button"
          onClick={() => setTarget({ id: null, path: "" })}
          className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm ${
            target.id === null ? "bg-selected font-medium" : "hover:bg-surface-hover"
          }`}
        >
          <HomeFolderIcon size={18} /> My files
        </button>
        <TreeLevel parent="root" depth={0} selected={target.id} onSelect={(id, path) => setTarget({ id, path })} disabled={disabled} />
      </div>
      {error && <p className="mt-2 text-sm text-bad">{error}</p>}
    </Modal>
  );
}
