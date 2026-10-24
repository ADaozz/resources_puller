<script setup lang="ts">
import Hls from "hls.js"
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue"
import { playable, useSessionStore } from "../stores/session"
import type { Group, Variant } from "../types"

const store = useSessionStore()
const chosen = ref<Record<string, string>>({})
const rendition = ref<Record<string, number>>({})
const videoEl = ref<HTMLVideoElement | null>(null)
const activeGroupId = ref<string | null>(null)
let hls: Hls | null = null

const groups = computed(() =>
  store.modalKind === "image" ? store.imageGroups : store.modalKind === "video" ? store.videoGroups : [],
)
const ids = computed(() => groups.value.map((group) => group.id))
const allSelected = computed(() => ids.value.length > 0 && ids.value.every((id) => store.selected[id]))
const activeGroup = computed(() => groups.value.find((group) => group.id === activeGroupId.value) || groups.value[0] || null)

function currentVariant(group: Group): Variant {
  const list = playable(group)
  return list.find((variant) => variant.id === chosen.value[group.id]) || list[0]
}

const activeVariant = computed(() => (activeGroup.value ? currentVariant(activeGroup.value) : null))

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

watch([activeVariant, videoEl, () => store.modalKind], async () => {
  await nextTick()
  bindPlayer(activeVariant.value)
})
watch(
  () => store.modalKind,
  (kind) => {
    if (!kind) {
      hls?.destroy()
      hls = null
      return
    }
    activeGroupId.value = groups.value[0]?.id || null
  },
)

onBeforeUnmount(() => hls?.destroy())

function downloadOne(group: Group) {
  const variant = currentVariant(group)
  const index = variant.renditions?.length ? rendition.value[variant.id] ?? 0 : null
  void store.download([{ variant_id: variant.id, rendition_index: index }])
}

function downloadSelected() {
  const items = groups.value
    .filter((group) => store.selected[group.id])
    .map((group) => {
      const variant = currentVariant(group)
      return {
        variant_id: variant.id,
        rendition_index: variant.renditions?.length ? rendition.value[variant.id] ?? 0 : null,
      }
    })
  void store.download(items)
}
</script>

<template>
  <div v-if="store.modalKind" class="modal-back" data-testid="modal" @click.self="store.modalKind = null">
    <section class="modal" role="dialog" aria-modal="true">
      <header>
        <h2>{{ store.modalKind === "image" ? "图片" : "视频" }}</h2>
        <div class="modal-actions">
          <button type="button" data-testid="select-all" @click="store.selectAll(ids, !allSelected)">
            {{ allSelected ? "取消全选" : "全选" }}
          </button>
          <button type="button" data-testid="download-batch" @click="downloadSelected">批量下载</button>
          <button type="button" data-testid="close-modal" @click="store.modalKind = null">关闭</button>
        </div>
      </header>
      <div class="modal-body">
        <ul class="rows">
          <li
            v-for="group in groups"
            :key="group.id"
            data-testid="group-row"
            :data-group-id="group.id"
            :class="{ active: activeGroup?.id === group.id }"
            @click="activeGroupId = group.id"
          >
            <label>
              <input
                type="checkbox"
                data-testid="group-check"
                :data-group-id="group.id"
                :checked="!!store.selected[group.id]"
                @click.stop
                @change="store.toggle(group.id)"
              />
              <span>{{ group.title }}</span>
            </label>
            <select
              :value="currentVariant(group).id"
              @click.stop
              @change="chosen[group.id] = ($event.target as HTMLSelectElement).value"
            >
              <option v-for="variant in playable(group)" :key="variant.id" :value="variant.id">
                {{ variant.label || "原始" }} · {{ variant.occurrence_count }} 次
              </option>
            </select>
            <button type="button" data-testid="copy" @click.stop="store.copy(currentVariant(group).url)">
              复制原始链接
            </button>
            <button type="button" data-testid="download-one" @click.stop="downloadOne(group)">下载</button>
          </li>
        </ul>
        <div class="preview" data-testid="preview">
          <template v-if="activeVariant && store.modalKind === 'image'">
            <img :src="store.previewUrl(activeVariant.id)" alt="" />
          </template>
          <template v-else-if="activeVariant">
            <video ref="videoEl" controls playsinline />
            <select
              v-if="activeVariant.renditions.length > 1"
              :value="rendition[activeVariant.id] ?? 0"
              @change="rendition[activeVariant.id] = Number(($event.target as HTMLSelectElement).value)"
            >
              <option v-for="(item, index) in activeVariant.renditions" :key="item.label" :value="index">
                {{ item.label }}
              </option>
            </select>
          </template>
          <p class="url-line">{{ activeVariant?.url }}</p>
          <p data-testid="copied" :data-last-copied="store.lastCopied">
            {{ store.lastCopied ? "已复制" : "选择一条资源后可以复制原始链接" }}
          </p>
        </div>
      </div>
    </section>
  </div>
</template>
