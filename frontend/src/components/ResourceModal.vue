<script setup lang="ts">
import Hls from "hls.js"
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue"
import { playable, useSessionStore } from "../stores/session"
import type { Group, Variant } from "../types"

const store = useSessionStore()
const chosen = ref<Record<string, string>>({})
const rendition = ref<Record<string, number>>({})
const query = ref("")
const videoEl = ref<HTMLVideoElement | null>(null)
const activeGroupId = ref<string | null>(null)
const showPreview = ref(false)
let hls: Hls | null = null

const groups = computed(() =>
  store.modalKind === "image" ? store.imageGroups : store.modalKind === "video" ? store.videoGroups : [],
)
const visible = computed(() => {
  const text = query.value.trim().toLowerCase()
  if (!text) return groups.value
  return groups.value.filter((group) => group.title.toLowerCase().includes(text) || fileName(group).toLowerCase().includes(text))
})
const ids = computed(() => groups.value.map((group) => group.id))
const visibleIds = computed(() => visible.value.map((group) => group.id))
const allSelected = computed(() => visibleIds.value.length > 0 && visibleIds.value.every((id) => store.selected[id]))
const selectedCount = computed(() => groups.value.filter((group) => store.selected[group.id]).length)
const activeGroup = computed(() => groups.value.find((group) => group.id === activeGroupId.value) || null)
const activeVariant = computed(() => (activeGroup.value ? currentVariant(activeGroup.value) : null))

function currentVariant(group: Group): Variant {
  const list = playable(group)
  return list.find((variant) => variant.id === chosen.value[group.id]) || list[0]
}

function resolutionText(group: Group) {
  return playable(group).length > 1 ? `${playable(group).length}个分辨率` : "原始分辨率"
}

function fileName(group: Group) {
  const variant = currentVariant(group)
  try {
    const base = decodeURIComponent(new URL(variant.url).pathname.split("/").pop() || "")
    if (base) return base
  } catch {
    /* 使用组名 */
  }
  return group.title
}

function isM3u8(variant: Variant) {
  return variant.is_playlist && /\.m3u8(\?|$)/i.test(variant.url)
}

function bindPlayer(variant: Variant | null) {
  const video = videoEl.value
  hls?.destroy()
  hls = null
  if (!video || !variant || store.modalKind !== "video") return
  const src = store.previewUrl(variant.id)
  if (isM3u8(variant) && Hls.isSupported()) {
    hls = new Hls()
    hls.loadSource(src)
    hls.attachMedia(video)
    return
  }
  video.src = src
}

function openPreview(group: Group) {
  activeGroupId.value = group.id
  showPreview.value = true
}

watch([activeVariant, videoEl, showPreview, () => store.modalKind], async () => {
  await nextTick()
  if (showPreview.value) bindPlayer(activeVariant.value)
})
watch(
  () => store.modalKind,
  (kind) => {
    query.value = ""
    showPreview.value = false
    if (!kind) {
      hls?.destroy()
      hls = null
      return
    }
    activeGroupId.value = null
  },
)

onBeforeUnmount(() => hls?.destroy())

function downloadOne(group: Group) {
  const variant = currentVariant(group)
  const index = variant.renditions?.length ? (rendition.value[variant.id] ?? 0) : null
  void store.download([{ variant_id: variant.id, rendition_index: index }])
}

function downloadSelected() {
  const items = groups.value
    .filter((group) => store.selected[group.id])
    .map((group) => {
      const variant = currentVariant(group)
      return {
        variant_id: variant.id,
        rendition_index: variant.renditions?.length ? (rendition.value[variant.id] ?? 0) : null,
      }
    })
  void store.download(items)
}
</script>

<template>
  <div v-if="store.modalKind" class="modal-back" data-testid="modal" @click.self="store.modalKind = null">
    <section class="modal" role="dialog" aria-modal="true" aria-labelledby="resource-title">
      <header class="modal-title">
        <h2 id="resource-title">资源列表</h2>
        <button type="button" data-testid="close-modal" aria-label="关闭" @click="store.modalKind = null">×</button>
      </header>
      <div class="segments" role="tablist">
        <button type="button" :class="{ on: store.modalKind === 'image' }" @click="store.modalKind = 'image'">图片</button>
        <button type="button" :class="{ on: store.modalKind === 'video' }" @click="store.modalKind = 'video'">视频</button>
      </div>
      <div class="search-row">
        <label class="search">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="11" cy="11" r="6" />
            <path d="m16 16 4 4" />
          </svg>
          <input v-model="query" type="search" placeholder="搜索资源名称" />
        </label>
        <button type="button" class="total-pill" data-testid="select-all" @click="store.selectAll(visibleIds, !allSelected)">
          共 {{ visible.length }} 项
        </button>
      </div>
      <div v-if="showPreview && activeVariant" class="preview" data-testid="preview">
        <img v-if="store.modalKind === 'image'" :src="store.previewUrl(activeVariant.id)" alt="" />
        <video v-else ref="videoEl" controls playsinline />
        <select
          v-if="store.modalKind === 'video' && activeVariant.renditions.length > 1"
          :value="rendition[activeVariant.id] ?? 0"
          @change="rendition[activeVariant.id] = Number(($event.target as HTMLSelectElement).value)"
        >
          <option v-for="(item, index) in activeVariant.renditions" :key="`${item.label}-${index}`" :value="index">
            {{ item.label }}
          </option>
        </select>
      </div>
      <div v-else class="preview preview-idle" data-testid="preview"></div>
      <div class="grid">
        <article
          v-for="group in visible"
          :key="group.id"
          data-testid="group-row"
          :data-group-id="group.id"
          :class="{ picked: !!store.selected[group.id] }"
        >
          <header>
            <label class="check">
              <input
                type="checkbox"
                data-testid="group-check"
                :data-group-id="group.id"
                :checked="!!store.selected[group.id]"
                @change="store.toggle(group.id)"
              />
              <span class="mark"></span>
            </label>
            <div class="res-wrap">
              <span>{{ resolutionText(group) }}</span>
              <select
                v-if="playable(group).length > 1"
                class="res-hit"
                :value="currentVariant(group).id"
                :aria-label="`${group.title} 分辨率`"
                @change="chosen[group.id] = ($event.target as HTMLSelectElement).value"
              >
                <option v-for="variant in playable(group)" :key="variant.id" :value="variant.id">
                  {{ variant.label || "原始" }}
                </option>
              </select>
            </div>
          </header>
          <button type="button" class="thumb" @click="openPreview(group)">
            <img
              v-if="store.modalKind === 'image'"
              :src="store.previewUrl(currentVariant(group).id)"
              alt=""
            />
            <img v-else :src="store.posterUrl(currentVariant(group).id)" alt="" />
          </button>
          <p class="file-name">{{ fileName(group) }}</p>
          <div class="icon-row">
            <button type="button" aria-label="预览" @click="openPreview(group)">
              <svg viewBox="0 0 24 24"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z" /><circle cx="12" cy="12" r="2.5" /></svg>
            </button>
            <button type="button" data-testid="copy" aria-label="复制原始链接" @click="store.copy(currentVariant(group).url)">
              <svg viewBox="0 0 24 24"><rect x="8" y="8" width="11" height="11" rx="2" /><path d="M5 15V5h10" /></svg>
            </button>
            <button type="button" data-testid="download-one" aria-label="下载" @click="downloadOne(group)">
              <svg viewBox="0 0 24 24"><path d="M12 4v10" /><path d="m8 10 4 4 4-4" /><path d="M5 19h14" /></svg>
            </button>
          </div>
        </article>
        <p v-if="!visible.length" class="grid-empty">没有匹配的资源</p>
      </div>
      <footer>
        <p>
          已选 {{ selectedCount }} 项
          <span data-testid="copied" :data-last-copied="store.lastCopied">{{ store.lastCopied ? "已复制" : "" }}</span>
        </p>
        <button type="button" class="batch" data-testid="download-batch" @click="downloadSelected">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4v10" /><path d="m8 10 4 4 4-4" /><path d="M5 19h14" /></svg>
          批量下载
        </button>
      </footer>
    </section>
  </div>
</template>
