<script setup lang="ts">
import { useSessionStore } from "../stores/session"

const store = useSessionStore()
const terminal = new Set(["COMPLETED", "FAILED", "CANCELLED"])
</script>

<template>
  <aside v-if="store.trayOpen" class="tray" data-testid="tray">
    <header>
      <strong>下载</strong>
      <button type="button" @click="store.trayOpen = false">关闭</button>
    </header>
    <p v-if="!store.downloads.length" class="muted">还没有下载任务。</p>
    <article v-for="job in store.downloads" :key="job.id" :data-state="job.state" data-testid="job">
      <div class="job-line">
        <b>{{ job.filename }}</b>
        <span>{{ job.state }} {{ Math.round(job.progress) }}%</span>
      </div>
      <div class="bar"><i :style="{ width: `${Math.max(0, Math.min(100, job.progress))}%` }" /></div>
      <p v-if="job.error" class="error">{{ job.error }}</p>
      <div class="job-actions">
        <button v-if="!terminal.has(job.state)" type="button" data-testid="cancel-job" @click="store.cancel(job.id)">
          取消
        </button>
        <a v-if="job.state === 'COMPLETED'" :href="store.fileUrl(job.id)">保存文件</a>
      </div>
    </article>
  </aside>
</template>
