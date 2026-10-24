<script setup lang="ts">
import { onMounted } from "vue"
import BrowserViewport from "./components/BrowserViewport.vue"
import CaptureLog from "./components/CaptureLog.vue"
import DownloadTray from "./components/DownloadTray.vue"
import MediaDeck from "./components/MediaDeck.vue"
import ResourceModal from "./components/ResourceModal.vue"
import UrlBar from "./components/UrlBar.vue"
import { useSessionStore } from "./stores/session"

const store = useSessionStore()
onMounted(() => {
  void store.boot()
})
</script>

<template>
  <div class="app">
    <header class="top">
      <div class="brand">
        <strong>网页媒体资源解析器</strong>
        <span>同一 Chromium 会话</span>
        <span data-testid="pid">进程 {{ store.browserPid ?? "—" }}</span>
        <span data-testid="ws">{{ store.wsLabel }}</span>
        <button type="button" class="ghost" @click="store.trayOpen = !store.trayOpen">下载</button>
      </div>
      <UrlBar />
      <p v-if="store.error" class="error" data-testid="error">{{ store.error }}</p>
    </header>
    <BrowserViewport />
    <section class="decks">
      <MediaDeck kind="image" />
      <MediaDeck kind="video" />
    </section>
    <CaptureLog />
  </div>
  <ResourceModal />
  <DownloadTray />
</template>
