"use client";

import { useCallback, useEffect, useState } from "react";
import { contentUrl, download, ensureSession, refreshSession } from "@/lib/api";
import { formatBytes, formatDate } from "@/lib/format";
import type { FileItem } from "@/lib/types";
import { ChevronLeft, ChevronRight, DownloadIcon, FileGlyph, PlayIcon, XIcon } from "./icons";
import { Spinner } from "./ui";

// Only the file you open is fetched. A video fetches nothing until you press play, and then
// only the parts the player asks for (range requests), so seeking never downloads the gap.

const TEXT_PREVIEW_BYTES = 1024 * 1024;
// file types browsers can show; anything else gets a Download card
const BROWSER_IMAGES = /^image\/(jpeg|png|gif|webp|avif|bmp|x-icon|vnd\.microsoft\.icon|heic|heif)$/;

function Unsupported({ file, reason }: { file: FileItem; reason: string }) {
  return (
    <div className="flex flex-col items-center gap-4 rounded-2xl bg-bg/95 p-10 text-center text-fg">
      <FileGlyph kind={file.kind} size={72} />
      <div className="max-w-sm text-sm text-muted">{reason}</div>
      <button
        type="button"
        onClick={() => download(file.id)}
        className="inline-flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-contrast"
      >
        <DownloadIcon size={18} /> Download ({formatBytes(file.size_bytes)})
      </button>
    </div>
  );
}

function ImageView({ file }: { file: FileItem }) {
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    setReady(false);
    setFailed(false);
    setLoaded(false);
    ensureSession().then(() => setReady(true));
  }, [file.id]);
  if (failed)
    return <Unsupported file={file} reason="This browser can't show this image format (HEIC photos open in Safari). You can download it." />;
  return (
    <div className="relative flex h-full w-full items-center justify-center">
      {!loaded && <Spinner size={32} />}
      {ready && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={contentUrl(file.id)}
          alt={file.name}
          onLoad={() => setLoaded(true)}
          onError={() => setFailed(true)}
          className={`max-h-full max-w-full object-contain ${loaded ? "" : "absolute opacity-0"}`}
        />
      )}
    </div>
  );
}

function MediaView({ file }: { file: FileItem }) {
  const [playing, setPlaying] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setPlaying(false);
    setFailed(false);
  }, [file.id]);
  // a long film outlives the 15-minute access cookie: keep the session fresh while it plays
  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => refreshSession(), 10 * 60 * 1000);
    return () => clearInterval(t);
  }, [playing]);

  if (failed) return <Unsupported file={file} reason="This browser can't play this format (MP4 and WebM play everywhere). You can download it." />;
  if (!playing)
    return (
      <button
        type="button"
        onClick={async () => {
          await ensureSession();
          setPlaying(true);
        }}
        className="group flex flex-col items-center gap-4 rounded-2xl bg-bg/95 px-14 py-10 text-fg"
      >
        <span className="flex h-20 w-20 items-center justify-center rounded-full bg-brand text-brand-contrast transition group-hover:scale-105">
          <PlayIcon size={36} />
        </span>
        <span className="text-sm text-muted">
          Play · {formatBytes(file.size_bytes)} · streams as it plays
        </span>
      </button>
    );
  return file.kind === "audio" ? (
    // biome-ignore lint/a11y/useMediaCaption: personal recordings have no captions
    <audio src={contentUrl(file.id)} controls autoPlay preload="none" onError={() => setFailed(true)} className="w-full max-w-xl" />
  ) : (
    // biome-ignore lint/a11y/useMediaCaption: personal videos have no captions
    <video
      src={contentUrl(file.id)}
      controls
      autoPlay
      preload="none"
      playsInline
      onError={() => setFailed(true)}
      className="max-h-full max-w-full rounded-lg bg-black"
    />
  );
}

function TextView({ file }: { file: FileItem }) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    setText(null);
    (async () => {
      await ensureSession();
      const res = await fetch(contentUrl(file.id), { headers: { Range: `bytes=0-${TEXT_PREVIEW_BYTES - 1}` } });
      const body = res.ok ? await res.text() : "Could not load this file.";
      if (alive) setText(body);
    })();
    return () => {
      alive = false;
    };
  }, [file.id]);
  if (text === null) return <Spinner size={32} />;
  return (
    <pre className="h-full w-full max-w-5xl overflow-auto whitespace-pre-wrap break-words rounded-xl bg-bg p-5 font-mono text-[13px] leading-relaxed text-fg">
      {text}
      {file.size_bytes > TEXT_PREVIEW_BYTES && `\n\n… showing the first 1 MB of ${formatBytes(file.size_bytes)}. Download for the rest.`}
    </pre>
  );
}

function PdfView({ file }: { file: FileItem }) {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setReady(false);
    ensureSession().then(() => setReady(true));
  }, [file.id]);
  if (!ready) return <Spinner size={32} />;
  return <iframe src={contentUrl(file.id)} title={file.name} className="h-full w-full max-w-5xl rounded-xl bg-white" />;
}

function Body({ file }: { file: FileItem }) {
  switch (file.kind) {
    case "image":
      return BROWSER_IMAGES.test(file.content_type) ? (
        <ImageView file={file} />
      ) : (
        <Unsupported file={file} reason="Browsers can't show this image format. You can download it." />
      );
    case "video":
    case "audio":
      return <MediaView file={file} />;
    case "pdf":
      return <PdfView file={file} />;
    case "text":
      return <TextView file={file} />;
    default:
      return <Unsupported file={file} reason="There's no preview for this kind of file." />;
  }
}

export function Viewer({ files, index, onClose }: { files: FileItem[]; index: number; onClose: () => void }) {
  const [i, setI] = useState(index);
  const file = files[i];
  const prev = useCallback(() => setI((n) => Math.max(0, n - 1)), []);
  const next = useCallback(() => setI((n) => Math.min(files.length - 1, n + 1)), [files.length]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if ((e.target as HTMLElement)?.tagName === "VIDEO") return;
      if (e.key === "ArrowLeft") prev();
      if (e.key === "ArrowRight") next();
    };
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose, prev, next]);

  if (!file) return null;
  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[rgb(8_12_16/0.94)] text-white" role="dialog" aria-modal aria-label={file.name}>
      <header className="flex items-center gap-3 px-4 py-3">
        <button type="button" onClick={onClose} className="rounded-lg p-2 hover:bg-white/10" aria-label="Close">
          <XIcon />
        </button>
        <FileGlyph kind={file.kind} size={22} />
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">{file.name}</div>
          <div className="text-xs text-white/60">
            {formatBytes(file.size_bytes)} · {formatDate(file.updated_at)}
            {files.length > 1 && ` · ${i + 1} of ${files.length}`}
          </div>
        </div>
        <button
          type="button"
          onClick={() => download(file.id)}
          className="inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm hover:bg-white/10"
        >
          <DownloadIcon size={18} /> <span className="hidden sm:inline">Download</span>
        </button>
      </header>
      <div className="relative flex min-h-0 flex-1 items-center justify-center px-4 pb-4 sm:px-16">
        <Body key={file.id} file={file} />
        {i > 0 && (
          <button type="button" onClick={prev} className="absolute left-2 top-1/2 -translate-y-1/2 rounded-full bg-white/10 p-2 hover:bg-white/20" aria-label="Previous">
            <ChevronLeft size={26} />
          </button>
        )}
        {i < files.length - 1 && (
          <button type="button" onClick={next} className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full bg-white/10 p-2 hover:bg-white/20" aria-label="Next">
            <ChevronRight size={26} />
          </button>
        )}
      </div>
    </div>
  );
}
