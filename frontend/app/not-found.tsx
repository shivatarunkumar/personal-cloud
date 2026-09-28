import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex flex-col items-center px-6 py-24 text-center">
      <h1 className="text-2xl font-semibold">Nothing here</h1>
      <p className="mt-2 text-muted">That page doesn't exist, or the folder was moved or deleted.</p>
      <Link href="/drive" className="mt-6 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-contrast">
        Go to My files
      </Link>
    </div>
  );
}
