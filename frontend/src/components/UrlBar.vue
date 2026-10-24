<script setup lang="ts">
import { ref, watch } from "vue"
import { useSessionStore } from "../stores/session"

const store = useSessionStore()
const draft = ref(store.currentUrl)

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
</script>

<template>
  <form class="urlbar" @submit.prevent="go">
    <button type="button" data-testid="back" @click="store.history('back')">后退</button>
    <button type="button" data-testid="forward" @click="store.history('forward')">前进</button>
    <button type="button" data-testid="reload" @click="store.history('reload')">刷新</button>
    <input
      v-model="draft"
      data-testid="url-input"
      type="text"
      spellcheck="false"
      placeholder="容器内可访问的地址，例如 http://127.0.0.1:8080/demo/"
    />
    <button type="submit" data-testid="go">打开</button>
    <button type="button" data-testid="open-demo" @click="store.navigate('http://127.0.0.1:8080/demo/')">
      测试页
    </button>
  </form>
</template>
