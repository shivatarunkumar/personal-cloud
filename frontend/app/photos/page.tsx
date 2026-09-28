"use client";

import { useCallback } from "react";
import { Browser } from "@/components/Browser";
import { api } from "@/lib/api";
import type { FileItem, Listing } from "@/lib/types";

// Names, sizes and dates only: nothing is downloaded until you open one.
export default function Photos() {
  const load = useCallback(async (): Promise<Listing> => {
    const files = await api<FileItem[]>("/drive/kind/image?limit=1000");
    return { folder: null, breadcrumbs: [], folders: [], files };
  }, []);
  return (
    <Browser
      mode="flat"
      title="Photos"
      load={load}
      groupByMonth
      defaultView="grid"
      empty={{ title: "No photos yet", hint: "Every image you upload, from any folder, shows up here." }}
    />
  );
}
