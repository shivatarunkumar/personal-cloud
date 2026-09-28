"use client";

import { useMemo, useState } from "react";
import { formatBytes, formatDate } from "@/lib/format";
import { FileGlyph, FolderGlyph } from "./icons";
import { Button, Modal } from "./ui";

export type Decision = "replace" | "skip" | "rename";

export type Clash = {
  key: string;
  name: string;
  /** where it's going, e.g. "Trip/Day 1", or "" for the folder you're in */
  where: string;
  newSize: number;
  /** "file": can be replaced; "folder": a folder has that name, so skip or keep both only */
  type: "file" | "folder";
  oldSize: number | null;
  oldDate: string;
};

const LABEL: Record<Decision, string> = { replace: "Replace", skip: "Skip", rename: "Keep both" };
// a long list only draws this many rows; the buttons above still apply to every file
const SHOWN = 300;

const sameSize = (c: Clash) => c.type === "file" && c.oldSize === c.newSize;

/** A sensible starting point: probably unchanged (same size) → skip, changed → replace. */
function suggested(c: Clash): Decision {
  if (c.type === "folder") return "rename";
  return sameSize(c) ? "skip" : "replace";
}

export function ConflictDialog({
  clashes,
  total,
  onDone,
}: {
  clashes: Clash[];
  /** files in the whole upload, clashes or not */
  total: number;
  onDone: (decisions: Map<string, Decision> | null) => void;
}) {
  const [choice, setChoice] = useState<Map<string, Decision>>(() => new Map(clashes.map((c) => [c.key, suggested(c)])));
  const counts = useMemo(() => {
    const n = { replace: 0, skip: 0, rename: 0 };
    for (const d of choice.values()) n[d]++;
    return n;
  }, [choice]);
  const unchanged = clashes.filter(sameSize).length;

  function all(decision: Decision) {
    // a folder can't be replaced by a file: it keeps both instead
    setChoice(new Map(clashes.map((c) => [c.key, c.type === "folder" && decision === "replace" ? "rename" : decision])));
  }

  const others = total - clashes.length;
  return (
    <Modal
      wide
      title={`${clashes.length} ${clashes.length === 1 ? "item already exists" : "items already exist"}`}
      onClose={() => onDone(null)}
      footer={
        <>
          <span className="mr-auto self-center text-xs text-muted">
            {[counts.replace && `Replace ${counts.replace}`, counts.skip && `Skip ${counts.skip}`, counts.rename && `Keep both ${counts.rename}`]
              .filter(Boolean)
              .join(" · ")}
          </span>
          <Button onClick={() => onDone(null)}>Cancel upload</Button>
          <Button variant="primary" data-autofocus onClick={() => onDone(choice)}>
            Continue
          </Button>
        </>
      }
    >
      <p className="text-sm text-muted">
        {others > 0 && `${others} other file${others === 1 ? "" : "s"} will upload as usual. `}
        Choose what to do with the ones already here. Replaced files go to the trash, so you can undo for 30 days.
      </p>

      <div className="mt-3 flex flex-wrap gap-2">
        <Button onClick={() => all("replace")}>Replace all</Button>
        <Button onClick={() => all("skip")}>Skip all</Button>
        <Button onClick={() => all("rename")}>Keep both for all</Button>
        {unchanged > 0 && unchanged < clashes.length && (
          <Button onClick={() => setChoice(new Map(clashes.map((c) => [c.key, suggested(c)])))}>
            Skip same size, replace the rest
          </Button>
        )}
      </div>

      <ul className="mt-3 max-h-[45vh] divide-y divide-line overflow-y-auto rounded-lg border border-line">
        {clashes.slice(0, SHOWN).map((c) => (
          <li key={c.key} className="flex items-center gap-3 px-3 py-2">
            {c.type === "folder" ? <FolderGlyph size={22} /> : <FileGlyph kind="other" size={22} />}
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm" title={c.where ? `${c.where}/${c.name}` : c.name}>
                {c.name}
                {c.where && <span className="ml-1.5 text-xs text-muted">in {c.where}</span>}
              </div>
              <div className="text-xs text-muted tabular">
                {c.type === "folder" ? (
                  "A folder has this name"
                ) : (
                  <>
                    New {formatBytes(c.newSize)} · existing {formatBytes(c.oldSize)}, {formatDate(c.oldDate)}
                    {sameSize(c) && <span className="ml-1.5 rounded bg-surface px-1.5 py-0.5 text-[11px]">same size</span>}
                  </>
                )}
              </div>
            </div>
            <select
              aria-label={`What to do with ${c.name}`}
              value={choice.get(c.key)}
              onChange={(e) => setChoice((m) => new Map(m).set(c.key, e.target.value as Decision))}
              className="rounded-lg border border-line bg-bg px-2 py-1 text-sm"
            >
              {(c.type === "folder" ? (["skip", "rename"] as Decision[]) : (["replace", "skip", "rename"] as Decision[])).map((d) => (
                <option key={d} value={d}>
                  {LABEL[d]}
                </option>
              ))}
            </select>
          </li>
        ))}
        {clashes.length > SHOWN && (
          <li className="px-3 py-2 text-xs text-muted">
            … and {clashes.length - SHOWN} more. The buttons above apply to all of them.
          </li>
        )}
      </ul>
    </Modal>
  );
}
