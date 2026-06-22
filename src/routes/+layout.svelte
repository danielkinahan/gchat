<script lang="ts">
    import { onMount } from "svelte";

    let authRequired = false;
    let authenticated = false;
    let checking = true;
    let password = "";
    let error = "";
    let submitting = false;

    onMount(async () => {
        try {
            const res = await fetch("/api/auth/status");
            const data = await res.json();
            authRequired = data.required;
            authenticated = data.authenticated;
        } catch {
            // If the status check itself fails (e.g. 401 from another middleware),
            // assume auth is required and not yet satisfied.
            authRequired = true;
            authenticated = false;
        }
        checking = false;
    });

    async function login() {
        if (!password) return;
        submitting = true;
        error = "";
        try {
            const res = await fetch("/api/auth/login", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ password }),
            });
            if (res.ok) {
                authenticated = true;
                // Reload so all page data is fetched with the new auth cookie
                window.location.reload();
            } else if (res.status === 401) {
                error = "Incorrect password";
                password = "";
            } else {
                error = "Cannot connect to backend. Contact the admin.";
            }
        } catch {
            error = "Login failed — please try again";
        } finally {
            submitting = false;
        }
    }

    function handleKeydown(e: KeyboardEvent) {
        if (e.key === "Enter") login();
    }
</script>

{#if checking}
    <!-- blank while we check auth status -->
{:else if authRequired && !authenticated}
    <div class="auth-gate">
        <div class="auth-card">
            <h1 class="auth-title">gChat</h1>
            <p class="auth-subtitle">Enter the password to continue</p>
            <div class="auth-field">
                <!-- svelte-ignore a11y_autofocus -->
                <input
                    type="password"
                    class="auth-input"
                    placeholder="Password"
                    bind:value={password}
                    on:keydown={handleKeydown}
                    autofocus
                    autocomplete="current-password"
                />
                <button
                    type="button"
                    class="auth-button"
                    on:click={login}
                    disabled={submitting || !password}
                >
                    {submitting ? "…" : "Unlock"}
                </button>
            </div>
            {#if error}
                <p class="auth-error">{error}</p>
            {/if}
        </div>
    </div>
{:else}
    <slot />
{/if}

<style>
    .auth-gate {
        position: fixed;
        inset: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        background: #020617;
    }

    .auth-card {
        width: min(380px, 92vw);
        background: #0b1220;
        border: 1px solid #1f2937;
        border-radius: 16px;
        padding: 36px 32px;
        display: flex;
        flex-direction: column;
        gap: 12px;
        box-shadow: 0 24px 60px rgba(2, 6, 23, 0.6);
    }

    .auth-title {
        font-size: 1.6rem;
        font-weight: 700;
        color: #f1f5f9;
        margin: 0;
        letter-spacing: -0.02em;
    }

    .auth-subtitle {
        font-size: 0.9rem;
        color: #64748b;
        margin: 0 0 8px;
    }

    .auth-field {
        display: flex;
        gap: 8px;
    }

    .auth-input {
        flex: 1 1 auto;
        background: #111827;
        color: #e2e8f0;
        border: 1px solid #1f2937;
        border-radius: 8px;
        padding: 10px 14px;
        font: inherit;
        font-size: 1rem;
        outline: none;
        transition: border-color 120ms ease;
    }

    .auth-input:focus {
        border-color: #2563eb;
    }

    .auth-button {
        flex-shrink: 0;
        background: #1d4ed8;
        color: #fff;
        border: none;
        border-radius: 8px;
        padding: 10px 18px;
        font: inherit;
        font-size: 0.95rem;
        font-weight: 600;
        cursor: pointer;
        transition: background 120ms ease;
    }

    .auth-button:hover:not(:disabled) {
        background: #2563eb;
    }

    .auth-button:disabled {
        opacity: 0.5;
        cursor: not-allowed;
    }

    .auth-error {
        font-size: 0.85rem;
        color: #f87171;
        margin: 4px 0 0;
    }
</style>
