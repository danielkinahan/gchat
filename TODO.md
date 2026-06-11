1. Replace custom YouTube/SoundCloud iframe embeds with a general link-preview
   library (OpenGraph). Would require an `/api/link-preview?url=` endpoint that
   fetches and parses OG metadata, plus a Svelte component to render it. Left
   for a future change.
2. Platform usage over time should be a line graph
3. Clicking on links in link tab should show example messages like the language tab.
4. Place links tab next to language
5. Make conversations a filter like we have for the messages and word buttons