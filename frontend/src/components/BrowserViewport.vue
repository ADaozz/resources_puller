<script setup lang="ts">
import { computed } from "vue"
import { useSessionStore } from "../stores/session"

const store = useSessionStore()
const hostLabel = computed(() => {
  try {
    const host = new URL(store.currentUrl).hostname
    if (!host || host === "about:blank") return ""
    return host.replace(/^www\./, "")
  } catch {
    return ""
  }
})
</script>

<template>
  <div class="viewport">
    <iframe
      data-testid="viewport"
      :src="store.novncUrl"
      title="远程 Chromium"
      allow="clipboard-read; clipboard-write"
    />
    <span v-if="hostLabel" class="site-badge">{{ hostLabel }}</span>
  </div>
</template>
