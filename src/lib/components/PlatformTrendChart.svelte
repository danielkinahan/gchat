<script lang="ts">
    import type { PlatformOverTime } from "$lib/api";

    export let data: PlatformOverTime;

    const WIDTH = 960;
    const HEIGHT = 280;
    const PADDING = { top: 16, right: 16, bottom: 28, left: 56 };
    const innerWidth = WIDTH - PADDING.left - PADDING.right;
    const innerHeight = HEIGHT - PADDING.top - PADDING.bottom;
    const COLORS: Record<string, string> = {
        discord: "#5865f2",
        facebook: "#f43f5e",
        signal: "#22c55e",
    };

    function color(platform: string): string {
        return COLORS[platform.toLowerCase()] ?? "#a855f7";
    }

    function chartX(index: number, total: number): number {
        if (total <= 1) return PADDING.left + innerWidth / 2;
        return PADDING.left + (index / (total - 1)) * innerWidth;
    }

    $: points = data.points;
    $: max = Math.max(
        ...points.flatMap((point) =>
            Object.values(point.counts).map((value) => value || 0),
        ),
        1,
    );
    $: labelStep = Math.max(1, Math.ceil(points.length / 8));
    $: lines = data.platforms.map((platform) => ({
        platform,
        color: color(platform),
        points: points.map((point, index) => {
            const value = point.counts[platform] || 0;
            return {
                x: chartX(index, points.length),
                y:
                    PADDING.top +
                    (1 - Math.min(Math.max(0, value) / max, 1)) *
                        innerHeight,
                value,
                bucket: point.bucket,
            };
        }),
    }));
    $: gridLines = [0, 0.25, 0.5, 0.75, 1].map((ratio) => ({
        y: PADDING.top + (1 - ratio) * innerHeight,
        label: Math.round(max * ratio).toLocaleString(),
    }));
</script>

<div class="panel timeline-panel platform-panel">
    <div class="panel-head">
        <h2>Platform usage over time</h2>
        <div class="platform-legend">
            {#each data.platforms as platform}
                <span class="legend-item">
                    <span
                        class="legend-swatch"
                        style={`background:${color(platform)}`}
                    ></span>
                    {platform}
                </span>
            {/each}
        </div>
    </div>
    {#if points.length === 0}
        <p class="muted">No platform data available.</p>
    {:else}
        <div class="platform-line-chart">
            <svg
                viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
                preserveAspectRatio="none"
                role="img"
                aria-label="Platform usage over time"
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
                {#each lines as line}
                    {#if line.points.length > 0}
                        <polyline
                            class="platform-line"
                            fill="none"
                            stroke={line.color}
                            stroke-width="2"
                            stroke-linejoin="round"
                            stroke-linecap="round"
                            points={line.points
                                .map((point) => `${point.x},${point.y}`)
                                .join(" ")}
                        />
                        {#each line.points as point}
                            <circle
                                class="platform-line-dot"
                                cx={point.x}
                                cy={point.y}
                                r="3"
                                fill={line.color}
                            >
                                <title
                                    >{new Date(
                                        point.bucket,
                                    ).toLocaleDateString("en-US", {
                                        month: "long",
                                        year: "numeric",
                                    })} ·
                                    {line.platform}:
                                    {point.value.toLocaleString()}</title
                                >
                            </circle>
                        {/each}
                    {/if}
                {/each}
            </svg>
        </div>
    {/if}
</div>
