"use client";

import { useCallback } from "react";
import { Browser } from "@/components/Browser";
import { api } from "@/lib/api";
import type { Listing } from "@/lib/types";

export default function MyFiles() {
  const load = useCallback(() => api<Listing>("/drive/folders/root"), []);
  return (
    <Browser
      mode="folder"
      folderId={null}
      load={load}
      empty={{ title: "Your cloud is empty", hint: "Drag files or folders here, or use New → Upload." }}
    />
  );
}
