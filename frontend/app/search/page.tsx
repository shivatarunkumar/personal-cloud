"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useCallback } from "react";
import { Browser } from "@/components/Browser";
import { api } from "@/lib/api";
import type { Listing } from "@/lib/types";

function Results() {
  const q = useSearchParams().get("q") ?? "";
  const load = useCallback(() => api<Listing>(`/drive/search?q=${encodeURIComponent(q)}`), [q]);
  if (!q) return <p className="p-6 text-muted">Type in the search box to find files and folders by name.</p>;
  return (
    <Browser
      mode="flat"
      title={`Results for “${q}”`}
      load={load}
      empty={{ title: "No matches", hint: "Search looks at file and folder names in every folder." }}
    />
  );
}

export default function Search() {
  return (
    <Suspense>
      <Results />
    </Suspense>
  );
}
