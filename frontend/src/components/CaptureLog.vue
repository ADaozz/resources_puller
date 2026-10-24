<script setup lang="ts">
import { useSessionStore } from "../stores/session"

const store = useSessionStore()
</script>

<template>
  <section class="log" data-testid="capture-log">
    <h2>捕获</h2>
    <p v-if="!store.log.length">等待页面中的图片和视频。</p>
    <ul v-else>
      <li v-for="(item, index) in store.log.slice(0, 6)" :key="`${item.at}-${index}`">
        <em>{{ item.via }}</em>
        <span>{{ item.url }}</span>
      </li>
    </ul>
  </section>
</template>
