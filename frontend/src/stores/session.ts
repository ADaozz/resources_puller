import { defineStore } from "pinia"
import { computed, ref } from "vue"
import { request } from "../api"
import type { AdapterStatus, CaptureEntry, DownloadJob, Group, Variant } from "../types"

type SessionPayload = {
  id: string
  novnc_url: string
  current_url: string
  browser_pid: number | null
  launch_count: number
}

export function playable(group: Group): Variant[] {
  const concrete = group.variants.filter((variant) => variant.label !== "主清单")
  return concrete.length ? concrete : group.variants
}

export const useSessionStore = defineStore("session", () => {
  const sessionId = ref<string | null>(null)
  const novncUrl = ref(
    "/novnc/vnc.html?autoconnect=1&resize=scale&reconnect=1&reconnect_delay=1000&show_dot=1&path=websockify",
  )
  const currentUrl = ref("http://127.0.0.1:8080/demo/")
  const wsState = ref<"connecting" | "open" | "closed">("closed")
  const groups = ref<Group[]>([])
  const downloads = ref<DownloadJob[]>([])
  const log = ref<CaptureEntry[]>([])
  const browserPid = ref<number | null>(null)
  const launchCount = ref<number | null>(null)
  const error = ref("")
  const adapterStatus = ref<AdapterStatus | null>(null)
  const selected = ref<Record<string, boolean>>({})
  const lastCopied = ref("")
  const modalKind = ref<"image" | "video" | null>(null)
  const trayOpen = ref(false)
  let socket: WebSocket | null = null
  let delay = 400
  let stopped = false

  const wsLabel = computed(() =>
    wsState.value === "open" ? "已连接" : wsState.value === "connecting" ? "正在连接" : "已断开",
  )
  const imageGroups = computed(() => groups.value.filter((group) => group.kind === "image"))
  const videoGroups = computed(() => groups.value.filter((group) => group.kind === "video"))

  function upsert(group: Group) {
    const index = groups.value.findIndex((item) => item.id === group.id)
    if (index === -1) {
      groups.value = [...groups.value, group]
      return
    }
    const next = groups.value.slice()
    next[index] = group
    groups.value = next
  }

  function remember(url: string, via: string, at?: number) {
    log.value = [{ url, via, at: at || Date.now() / 1000 }, ...log.value].slice(0, 80)
  }

  function apply(event: {
    type: string
    group?: Group
    group_id?: string
    occurrence?: { url?: string; via?: string; created_at?: number }
    url?: string
    download?: DownloadJob
    groups?: Group[]
    downloads?: DownloadJob[]
    current_url?: string
    adapter_status?: AdapterStatus | null
    browser_pid?: number
  }) {
    if (event.type === "adapter.status") {
      adapterStatus.value = event.adapter_status || null
      return
    }
    if (event.type === "snapshot") {
      adapterStatus.value = event.adapter_status || null
      groups.value = event.groups || []
      downloads.value = event.downloads || []
      if (!log.value.length) {
        log.value = groups.value
          .flatMap((group) =>
            group.variants.map((variant) => ({
              url: variant.url,
              via: "已捕获",
              at: group.updated_at,
            })),
          )
          .slice(0, 80)
      }
      if (event.current_url && event.current_url !== "about:blank") currentUrl.value = event.current_url
      if (event.browser_pid) browserPid.value = event.browser_pid
      return
    }
    if (event.type === "resource.removed" && event.group_id) {
      groups.value = groups.value.filter((group) => group.id !== event.group_id)
      return
    }
    if (
      event.type === "resource.created" ||
      event.type === "variant.added" ||
      event.type === "resource.updated"
    ) {
      if (event.group) upsert(event.group)
      if (event.occurrence?.url) remember(event.occurrence.url, event.occurrence.via || "network", event.occurrence.created_at)
      return
    }
    if (event.type === "page.url" && event.url) {
      currentUrl.value = event.url
      return
    }
    if (event.type === "download.updated" && event.download) {
      const index = downloads.value.findIndex((item) => item.id === event.download?.id)
      if (index === -1) downloads.value = [event.download, ...downloads.value]
      else {
        const next = downloads.value.slice()
        next[index] = event.download
        downloads.value = next
      }
      trayOpen.value = true
    }
  }

  function connect() {
    if (!sessionId.value || stopped) return
    wsState.value = "connecting"
    document.body.dataset.ws = "connecting"
    const proto = location.protocol === "https:" ? "wss" : "ws"
    socket = new WebSocket(`${proto}://${location.host}/api/sessions/${sessionId.value}/events`)
    ;(window as unknown as { __rpDebug?: { closeWs: () => void } }).__rpDebug = {
      closeWs: () => socket?.close(),
    }
    socket.onopen = () => {
      wsState.value = "open"
      delay = 400
      document.body.dataset.ws = "open"
    }
    socket.onmessage = (message: MessageEvent<string>) => {
      apply(JSON.parse(message.data))
    }
    socket.onclose = () => {
      wsState.value = "closed"
      document.body.dataset.ws = "closed"
      if (!stopped) {
        window.setTimeout(connect, delay)
        delay = Math.min(delay * 2, 4000)
      }
    }
  }

  async function boot() {
    stopped = false
    error.value = ""
    try {
      const health = await request<{ browser_pid: number | null; launch_count: number }>("/api/health")
      browserPid.value = health.browser_pid
      launchCount.value = health.launch_count
      const session = await request<SessionPayload>("/api/sessions", { method: "POST" })
      sessionId.value = session.id
      novncUrl.value = session.novnc_url
      browserPid.value = session.browser_pid
      launchCount.value = session.launch_count
      if (session.current_url && session.current_url !== "about:blank") currentUrl.value = session.current_url
      connect()
    } catch (err) {
      error.value = err instanceof Error ? err.message : "无法建立会话"
    }
  }

  async function navigate(url: string) {
    if (!sessionId.value) return
    error.value = ""
    try {
      const result = await request<{ url: string }>(`/api/sessions/${sessionId.value}/navigate`, {
        method: "POST",
        body: JSON.stringify({ url }),
      })
      currentUrl.value = result.url
    } catch (err) {
      error.value = err instanceof Error ? err.message : "打开失败"
    }
  }

  async function history(action: "back" | "forward" | "reload") {
    if (!sessionId.value) return
    error.value = ""
    try {
      const result = await request<{ url: string }>(`/api/sessions/${sessionId.value}/${action}`, {
        method: "POST",
      })
      currentUrl.value = result.url
    } catch (err) {
      error.value = err instanceof Error ? err.message : "操作失败"
    }
  }

  function toggle(id: string) {
    const next = { ...selected.value }
    if (next[id]) delete next[id]
    else next[id] = true
    selected.value = next
  }

  function selectAll(ids: string[], on: boolean) {
    const next = { ...selected.value }
    for (const id of ids) {
      if (on) next[id] = true
      else delete next[id]
    }
    selected.value = next
  }

  async function copy(url: string) {
    lastCopied.value = url
    try {
      await navigator.clipboard.writeText(url)
    } catch {
      const area = document.createElement("textarea")
      area.value = url
      document.body.appendChild(area)
      area.select()
      document.execCommand("copy")
      area.remove()
    }
  }

  async function download(items: { variant_id: string; rendition_index?: number | null }[]) {
    if (!sessionId.value || !items.length) return
    error.value = ""
    try {
      await request(`/api/sessions/${sessionId.value}/downloads`, {
        method: "POST",
        body: JSON.stringify({ items }),
      })
      trayOpen.value = true
    } catch (err) {
      error.value = err instanceof Error ? err.message : "下载失败"
    }
  }

  async function cancel(jobId: string) {
    if (!sessionId.value) return
    await request(`/api/sessions/${sessionId.value}/downloads/${jobId}/cancel`, { method: "POST" })
  }

  function previewUrl(variantId: string) {
    return `/api/sessions/${sessionId.value}/variants/${variantId}/preview`
  }

  function posterUrl(variantId: string) {
    return `/api/sessions/${sessionId.value}/variants/${variantId}/poster`
  }

  function fileUrl(jobId: string) {
    return `/api/sessions/${sessionId.value}/downloads/${jobId}/file`
  }

  return {
    sessionId,
    novncUrl,
    currentUrl,
    wsState,
    wsLabel,
    groups,
    downloads,
    log,
    browserPid,
    launchCount,
    error,
    adapterStatus,
    selected,
    lastCopied,
    modalKind,
    trayOpen,
    imageGroups,
    videoGroups,
    boot,
    navigate,
    history,
    toggle,
    selectAll,
    copy,
    download,
    cancel,
    previewUrl,
    posterUrl,
    fileUrl,
  }
})
