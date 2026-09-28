"use client";

import { useCallback } from "react";
import { Browser } from "@/components/Browser";
import { api } from "@/lib/api";
import type { FileItem, Listing } from "@/lib/types";

// Names, sizes and dates only: nothing is downloaded until you open one.
export default function Videos() {
  const load = useCallback(async (): Promise<Listing> => {
    const files = await api<FileItem[]>("/drive/kind/video?limit=1000");
    return { folder: null, breadcrumbs: [], folders: [], files };
  }, []);
  return (
    <Browser
      mode="flat"
      title="Videos"
      load={load}
      groupByMonth
      defaultView="grid"
      empty={{ title: "No videos yet", hint: "Every video you upload, from any folder, shows up here." }}
    />
  );
}
