<script setup lang="ts">
import { useSessionStore } from "../stores/session"

const store = useSessionStore()
</script>

<template>
  <div class="viewport">
    <iframe
      data-testid="viewport"
      :src="store.novncUrl"
      title="远程 Chromium"
      allow="clipboard-read; clipboard-write"
    />
  </div>
</template>
