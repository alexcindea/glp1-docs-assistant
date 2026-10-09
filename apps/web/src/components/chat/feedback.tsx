"use client";

import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { Schemas } from "@/lib/api/client";
import { cn } from "@/lib/utils";

type Rating = Schemas["FeedbackRequest"]["rating"];

/**
 * Thumbs up or down on a saved answer, with an optional comment on "down".
 * Feedback is stored against the answer (POST /answers/{id}/feedback); giving
 * it again replaces it.
 */
export function Feedback({ answerId }: { answerId: string }) {
  const [rating, setRating] = useState<Rating | null>(null);
  const [commenting, setCommenting] = useState(false);
  const [comment, setComment] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "saved" | "failed">(
    "idle",
  );

  async function send(next: Rating, text: string | null) {
    setRating(next);
    setState("saving");
    try {
      const response = await fetch(`/api/answers/${answerId}/feedback`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          rating: next,
          comment: text,
        } satisfies Schemas["FeedbackRequest"]),
      });
      setState(response.ok ? "saved" : "failed");
    } catch {
      setState("failed");
    }
  }

  return (
    <div className="space-y-2">
      <div className="text-muted-foreground flex items-center gap-1 text-xs">
        <span className="mr-1">Was this right?</span>
        <RatingButton
          label="Good answer"
          active={rating === "up"}
          onClick={() => {
            setCommenting(false);
            void send("up", null);
          }}
        >
          <ThumbsUp />
        </RatingButton>
        <RatingButton
          label="Bad answer"
          active={rating === "down"}
          onClick={() => {
            setCommenting(true);
            void send("down", null);
          }}
        >
          <ThumbsDown />
        </RatingButton>
        <span role="status" className="ml-1">
          {state === "saving" && "Saving…"}
          {state === "saved" && "Thanks, saved."}
          {state === "failed" && (
            <span className="text-destructive">
              Couldn&apos;t save. Try again.
            </span>
          )}
        </span>
      </div>
      {commenting && (
        <form
          className="flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void send("down", comment.trim() || null);
            setCommenting(false);
          }}
        >
          <label htmlFor={`comment-${answerId}`} className="sr-only">
            What was wrong?
          </label>
          <Textarea
            id={`comment-${answerId}`}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="What was wrong? (optional)"
            maxLength={2000}
            rows={2}
            className="min-h-0 text-sm"
          />
          <Button type="submit" size="sm" variant="outline">
            Send
          </Button>
        </form>
      )}
    </div>
  );
}

function RatingButton({
  label,
  active,
  onClick,
  children,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-xs"
      onClick={onClick}
      aria-label={label}
      aria-pressed={active}
      className={cn(active && "bg-muted text-foreground")}
    >
      {children}
    </Button>
  );
}
