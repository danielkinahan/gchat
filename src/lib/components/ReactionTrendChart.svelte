<script lang="ts">
    import type { ReactionsOverTime } from "$lib/api";

    export let data: ReactionsOverTime;

    const WIDTH = 760;
    const HEIGHT = 260;
    const PADDING = { top: 16, right: 16, bottom: 36, left: 52 };
    const innerWidth = WIDTH - PADDING.left - PADDING.right;
    const innerHeight = HEIGHT - PADDING.top - PADDING.bottom;

    let metric: "rate" | "total" = "rate";

    function chartX(index: number, count: number): number {
        if (count <= 1) return PADDING.left + innerWidth / 2;
        return PADDING.left + (index / (count - 1)) * innerWidth;
    }

    $: points = data.points;
    $: values = points.map((point) =>
        metric === "rate"
            ? point.reactions_per_message
            : point.reaction_count,
    );
    $: max = Math.max(...values, 0.01);
    $: labelStep = Math.max(1, Math.ceil(points.length / 8));
    $: linePoints = points.map((point, index) => {
        const value =
            metric === "rate"
                ? point.reactions_per_message
                : point.reaction_count;
        return {
            x: chartX(index, points.length),
            y:
                PADDING.top +
                (1 - Math.min(value / max, 1)) * innerHeight,
            point,
        };
    });
    $: gridLines = [0, 0.25, 0.5, 0.75, 1].map((ratio) => ({
        y: PADDING.top + (1 - ratio) * innerHeight,
        label:
            metric === "rate"
                ? (max * ratio).toFixed(2)
                : Math.round(max * ratio).toLocaleString(),
    }));
</script>

<div class="panel timeline-panel platform-panel">
    <div class="panel-head">
        <h2>Reactions over time</h2>
        <div class="metric-switch" role="group" aria-label="Reaction metric">
            <button
                type="button"
                class:active={metric === "rate"}
                on:click={() => (metric = "rate")}>Per message</button
            >
            <button
                type="button"
                class:active={metric === "total"}
                on:click={() => (metric = "total")}>Total</button
            >
        </div>
    </div>
    {#if points.length === 0}
        <p class="muted">No reaction data available.</p>
    {:else}
        <div class="platform-line-chart">
            <svg
                viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
                preserveAspectRatio="none"
                role="img"
                aria-label="Reactions over time"
            >
                {#each gridLines as gridLine}
                    <line
                        class="grid-line"
                        x1={PADDING.left}
                        x2={WIDTH - PADDING.right}
                        y1={gridLine.y}
                        y2={gridLine.y}
                    />
                    <text
                        class="axis-label-y"
                        x={PADDING.left - 8}
                        y={gridLine.y + 4}
                        text-anchor="end">{gridLine.label}</text
                    >
                {/each}
                {#each points as point, i}
                    {#if i % labelStep === 0}
                        <text
                            class="axis-label-x"
                            x={chartX(i, points.length)}
                            y={HEIGHT - PADDING.bottom + 18}
                            text-anchor="middle"
                        >
                            {new Date(point.bucket).toLocaleDateString("en-US", {
                                month: "short",
                                year: "2-digit",
                            })}
                        </text>
                    {/if}
                {/each}
                <polyline
                    class="platform-line"
                    fill="none"
                    stroke="#a855f7"
                    stroke-width="2"
                    stroke-linejoin="round"
                    stroke-linecap="round"
                    points={linePoints
                        .map((point) => `${point.x},${point.y}`)
                        .join(" ")}
                />
                {#each linePoints as linePoint}
                    <circle
                        class="platform-line-dot"
                        cx={linePoint.x}
                        cy={linePoint.y}
                        r="3"
                        fill="#a855f7"
                    >
                        <title
                            >{new Date(
                                linePoint.point.bucket,
                            ).toLocaleDateString("en-US", {
                                month: "long",
                                year: "numeric",
                            })}: {linePoint.point.reaction_count.toLocaleString()}
                            reactions across {linePoint.point.message_count.toLocaleString()}
                            messages ({linePoint.point.reactions_per_message.toFixed(
                                3,
                            )} per message;
                            {linePoint.point.reacted_message_count.toLocaleString()}
                            messages reacted to)</title
                        >
                    </circle>
                {/each}
            </svg>
        </div>
    {/if}
</div>
