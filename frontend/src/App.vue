<script setup lang="ts">
import { computed, onMounted } from "vue"
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

const captureLabel = computed(() => (store.wsState === "open" ? "捕获中" : store.wsLabel))
</script>

<template>
  <div class="app">
    <UrlBar />
    <p v-if="store.error" class="error" data-testid="error">{{ store.error }}</p>
    <p v-if="store.adapterStatus" role="status" class="adapter-status" :class="{ error: store.adapterStatus.state === 'error' }">
      {{ store.adapterStatus.message }}
    </p>
    <section class="browser-card">
      <header class="browser-head">
        <h1>目标浏览器</h1>
        <p class="live" :data-state="store.wsState">
          <i></i>
          <span data-testid="ws">{{ captureLabel }}</span>
          <span data-testid="pid">{{ store.browserPid ?? "—" }}</span>
        </p>
      </header>
      <BrowserViewport />
    </section>
    <section class="decks">
      <MediaDeck kind="image" />
      <MediaDeck kind="video" />
    </section>
    <CaptureLog />
  </div>
  <ResourceModal />
  <DownloadTray />
</template>
