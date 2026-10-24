export type Rendition = {
  label: string
  width: number | null
  height: number | null
  bandwidth?: number | null
}

export type Variant = {
  id: string
  url: string
  mime: string | null
  width: number | null
  height: number | null
  label: string | null
  is_playlist: boolean
  renditions: Rendition[]
  occurrence_count: number
}

export type Group = {
  id: string
  kind: "image" | "video"
  title: string
  created_at: number
  updated_at: number
  variants: Variant[]
}

export type DownloadJob = {
  id: string
  variant_id: string
  rendition_index: number | null
  url: string
  state: string
  progress: number
  error: string | null
  filename: string
  path?: string | null
}

export type CaptureEntry = {
  url: string
  via: string
  at: number
}
