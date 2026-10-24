<script setup lang="ts">
import { ref, watch } from "vue"
import { useSessionStore } from "../stores/session"

const store = useSessionStore()
const draft = ref(store.currentUrl)
const settingsOpen = ref(false)

watch(
  () => store.currentUrl,
  (value) => {
    draft.value = value
  },
)

function go() {
  const value = draft.value.trim()
  if (value) void store.navigate(value)
}

function openDemo() {
  settingsOpen.value = false
  void store.navigate("http://127.0.0.1:8080/demo/")
}
</script>

<template>
  <form class="urlbar" @submit.prevent="go">
    <label class="url-pill">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="8" />
        <path d="M4 12h16M12 4c2.4 2.6 2.4 13.4 0 16M12 4c-2.4 2.6-2.4 13.4 0 16" />
      </svg>
      <input
        v-model="draft"
        data-testid="url-input"
        type="text"
        spellcheck="false"
        placeholder="https://example.com/gallery"
      />
      <button type="submit" data-testid="go" aria-label="打开">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M7 17 17 7M9 7h8v8" />
        </svg>
      </button>
    </label>
    <button type="button" class="icon-btn" data-testid="reload" aria-label="刷新" @click="store.history('reload')">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M20 12a8 8 0 1 1-2.2-5.5" />
        <path d="M20 4v5h-5" />
      </svg>
    </button>
    <button type="button" class="icon-btn" aria-label="设置" @click="settingsOpen = !settingsOpen">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="3" />
        <path d="M12 3v2.2M12 18.8V21M3 12h2.2M18.8 12H21M5.6 5.6l1.6 1.6M16.8 16.8l1.6 1.6M18.4 5.6l-1.6 1.6M7.2 16.8l-1.6 1.6" />
      </svg>
    </button>
    <div v-if="settingsOpen" class="settings">
      <button type="button" data-testid="back" @click="store.history('back')">后退</button>
      <button type="button" data-testid="forward" @click="store.history('forward')">前进</button>
      <button type="button" data-testid="open-demo" @click="openDemo">测试页</button>
      <button type="button" @click="store.trayOpen = !store.trayOpen">下载任务</button>
    </div>
  </form>
</template>
