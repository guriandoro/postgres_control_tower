import clsx, { type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Standard shadcn helper: clsx + tailwind-merge for conflict resolution. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
