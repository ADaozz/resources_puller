<script setup lang="ts">
import { useSessionStore } from "../stores/session"

const store = useSessionStore()
</script>

<template>
  <section class="log" data-testid="capture-log">
    <h2>捕获</h2>
    <p v-if="!store.log.length" class="muted">导航或在画面里点击后，图片和视频地址会出现在这里。</p>
    <ul v-else>
      <li v-for="(item, index) in store.log" :key="`${item.at}-${index}`">
        <em>{{ item.via }}</em>
        <span>{{ item.url }}</span>
      </li>
    </ul>
  </section>
</template>
