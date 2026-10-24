<script setup lang="ts">
import { ref } from "vue"
import { useSessionStore } from "../stores/session"

const store = useSessionStore()
const open = ref(false)
</script>

<template>
  <section class="log" :class="{ open }" data-testid="capture-log">
    <button type="button" class="log-toggle" @click="open = !open">
      捕获
      <span>{{ store.log.length }}</span>
    </button>
    <p v-if="open && !store.log.length">等待页面中的图片和视频。</p>
    <ul v-show="open">
      <li v-for="(item, index) in store.log.slice(0, 8)" :key="`${item.at}-${index}`">
        <em>{{ item.via }}</em>
        <span>{{ item.url }}</span>
      </li>
    </ul>
  </section>
</template>
