<script setup lang="ts">
import { useSessionStore } from "../stores/session"
import type { DownloadJob } from "../types"

const store = useSessionStore()
const terminal = new Set(["COMPLETED", "FAILED", "CANCELLED"])

function statusLabel(state: string) {
  switch (state) {
    case "COMPLETED":
      return "已完成"
    case "FAILED":
      return "失败"
    case "CANCELLED":
      return "已取消"
    case "MERGING":
      return "合并中"
    case "DOWNLOADING":
      return "下载中"
    case "PROBING":
      return "探测中"
    default:
      return "等待中"
  }
}

function statusDetail(job: DownloadJob) {
  if (job.state === "FAILED") return job.error || "下载失败"
  if (job.state === "CANCELLED") return "已取消"
  if (job.state === "COMPLETED") return "下载和合并完成"
  if (job.state === "MERGING") return "正在合并"
  if (job.state === "DOWNLOADING") return "正在下载"
  if (job.state === "PROBING") return "正在探测"
  return "等待开始"
}

function mediaLine(job: DownloadJob) {
  const url = job.url.toLowerCase()
  const kind = url.includes(".m3u8") ? "HLS" : url.includes(".mpd") ? "DASH" : "文件"
  const matched = job.filename.match(/(\d{3,4}p|\d+x\d+)/i)
  const resolution = matched ? matched[1].toUpperCase() : ""
  const ext = (job.filename.split(".").pop() || "mp4").toUpperCase()
  return [kind, resolution, ext].filter(Boolean).join(" · ")
}
</script>

<template>
  <div v-if="store.trayOpen" class="tray-back">
    <section class="tray" data-testid="tray">
      <button type="button" class="tray-close" aria-label="关闭" @click="store.trayOpen = false">×</button>
      <article v-if="!store.downloads.length" class="job" data-testid="job" data-state="EMPTY">
        <header>
          <h2>流媒体下载任务</h2>
          <span class="state-pill">暂无</span>
        </header>
        <p class="job-empty">还没有下载任务。</p>
      </article>
      <article
        v-for="job in store.downloads"
        :key="job.id"
        class="job"
        :data-state="job.state"
        data-testid="job"
      >
        <header>
          <h2>流媒体下载任务</h2>
          <span class="state-pill" :data-state="job.state">{{ statusLabel(job.state) }}</span>
        </header>
        <div class="job-main">
          <span class="film" aria-hidden="true">
            <svg viewBox="0 0 24 24">
              <rect x="4" y="4" width="16" height="16" rx="2" />
              <path d="M8 4v16M16 4v16M4 9h4M4 15h4M16 9h4M16 15h4" />
            </svg>
          </span>
          <div>
            <strong>{{ job.filename }}</strong>
            <p>{{ mediaLine(job) }}</p>
          </div>
        </div>
        <div class="bar"><i :style="{ width: `${Math.max(0, Math.min(100, job.progress))}%` }" /></div>
        <div class="job-meta">
          <span>{{ statusDetail(job) }}</span>
          <span>{{ Math.round(job.progress) }}%</span>
        </div>
        <div class="job-actions">
          <button v-if="!terminal.has(job.state)" type="button" data-testid="cancel-job" @click="store.cancel(job.id)">
            取消
          </button>
          <a v-if="job.state === 'COMPLETED'" :href="store.fileUrl(job.id)">保存文件</a>
        </div>
      </article>
    </section>
  </div>
</template>
