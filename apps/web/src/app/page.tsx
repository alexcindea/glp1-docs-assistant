import Link from "next/link";

import { buttonVariants } from "@/components/ui/button";

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-xl flex-col gap-4 p-8">
      <h1 className="text-2xl font-semibold">Docs Assistant</h1>
      <p className="text-muted-foreground">
        Ask questions across a collection of regulatory documents and get
        answers that cite the exact passage they came from. The chat arrives in
        a later phase.
      </p>
      <Link href="/health" className={buttonVariants({ className: "w-fit" })}>
        System health
      </Link>
    </main>
  );
}
