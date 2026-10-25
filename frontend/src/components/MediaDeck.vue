<script setup lang="ts">
import { computed, reactive } from "vue"
import { playable, useSessionStore } from "../stores/session"
import type { Group } from "../types"

const props = defineProps<{ kind: "image" | "video" }>()
const store = useSessionStore()
const failed = reactive<Record<string, boolean>>({})
const groups = computed(() => (props.kind === "image" ? store.imageGroups : store.videoGroups))
const shown = computed(() => groups.value.slice(-4))
const title = computed(() => (props.kind === "image" ? "图片" : "视频"))

function cover(group: Group) {
  return playable(group)[0]
}

function open() {
  store.modalKind = props.kind
}
</script>

<template>
  <section class="deck" :data-testid="`deck-${kind}`">
    <header class="deck-head">
      <h2>
        <svg v-if="kind === 'image'" viewBox="0 0 24 24" aria-hidden="true">
          <rect x="4" y="5" width="16" height="14" rx="2" />
          <circle cx="9" cy="10" r="1.4" />
          <path d="m8 16 3.2-3.2L14 15l2-2 2 3" />
        </svg>
        <svg v-else viewBox="0 0 24 24" aria-hidden="true">
          <rect x="3.5" y="6" width="12" height="12" rx="2" />
          <path d="m16 10 4.5-2.2v8.4L16 14" />
        </svg>
        {{ title }}
      </h2>
      <span class="count-pill" data-testid="badge">
        <strong data-testid="deck-count">{{ groups.length }}</strong>
      </span>
    </header>
    <button v-if="groups.length" class="stack" type="button" :data-count="shown.length" @click="open">
      <span
        v-for="(group, index) in shown"
        :key="group.id"
        class="plate"
        :style="{ '--i': String(index) }"
      >
        <img
          v-if="kind === 'image'"
          :src="store.previewUrl(cover(group).id)"
          alt=""
        />
        <img
          v-else-if="!failed[group.id]"
          :src="store.posterUrl(cover(group).id)"
          alt=""
          @error="failed[group.id] = true"
        />
        <span v-if="kind === 'video' && failed[group.id]" class="video-fallback">
          <i></i>
          视频封面
        </span>
      </span>
    </button>
    <div v-else class="thumb-empty">
      <template v-if="kind === 'video'">
        <span class="video-fallback static">
          <i></i>
          视频封面
        </span>
      </template>
      <template v-else>暂无图片</template>
    </div>
    <button class="deck-foot" type="button" @click="open">
      <span>悬停展开资源堆叠</span>
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="m6 14 6-6 6 6" />
      </svg>
    </button>
  </section>
</template>
