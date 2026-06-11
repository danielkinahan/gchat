export function isMeaningfulPrevious(
    previous: string | null | undefined,
    next: string,
): boolean {
    const trimmedPrev = (previous ?? "").trim();
    if (!trimmedPrev) return false;
    if (trimmedPrev.toLowerCase() === next.trim().toLowerCase()) return false;
    if (trimmedPrev === "(cleared)") return false;
    return true;
}

export type GroupedChange<T> = {
    author_name: string | null;
    ts: string | null;
    changes: T[];
};

export function groupChangesByAuthor<
    T extends {
        author_name?: string | null;
        ts: string | null;
    },
>(changes: T[]): GroupedChange<T>[] {
    const groups: GroupedChange<T>[] = [];
    for (const change of changes) {
        const author = change.author_name ?? null;
        const ts = change.ts ?? null;
        const last = groups[groups.length - 1];
        const sameAuthor =
            last && (last.author_name ?? null) === author;
        const closeInTime =
            last &&
            last.ts &&
            ts &&
            Math.abs(
                new Date(ts).getTime() - new Date(last.ts).getTime(),
            ) <= 5 * 60 * 1000;
        if (sameAuthor && closeInTime) {
            last.changes.push(change);
            last.ts = ts ?? last.ts;
        } else {
            groups.push({
                author_name: author,
                ts,
                changes: [change],
            });
        }
    }
    return groups;
}
