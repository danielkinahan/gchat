<script lang="ts">
    /** Table header with a native tooltip and shared hover styling. */
    export let title: string;
    export let align: "left" | "center" = "center";
</script>

<th
    class="tooltip-header"
    class:tooltip-header-left={align === "left"}
    {title}
>
    <slot />
</th>
