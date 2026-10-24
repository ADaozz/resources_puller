<script setup lang="ts">
import { computed, reactive } from "vue"
import { playable, useSessionStore } from "../stores/session"
import type { Group } from "../types"

const props = defineProps<{ kind: "image" | "video" }>()
const store = useSessionStore()
const failed = reactive<Record<string, boolean>>({})
const groups = computed(() => (props.kind === "image" ? store.imageGroups : store.videoGroups))
const shown = computed(() => groups.value.slice(-4))
const title = computed(() => (props.kind === "image" ? "图片仓" : "视频仓"))
const empty = computed(() => (props.kind === "image" ? "暂无图片" : "暂无视频"))

function cover(group: Group) {
  return playable(group)[0]
}
</script>

<template>
  <section class="deck" :data-testid="`deck-${kind}`">
    <header>
      <h2>{{ title }}</h2>
      <strong data-testid="deck-count">{{ groups.length }}</strong>
    </header>
    <p v-if="!groups.length" class="empty">{{ empty }}</p>
    <button v-else class="stack" type="button" :data-count="shown.length" @click="store.modalKind = kind">
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
        <b>{{ group.title }}</b>
      </span>
      <em v-if="groups.length > 4" class="badge" data-testid="badge">{{ groups.length }}</em>
    </button>
  </section>
</template>
