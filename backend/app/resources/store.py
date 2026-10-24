from __future__ import annotations

import json
import threading
import time
import uuid
from urllib.parse import urldefrag

from app.db import DB
from app.hub import Hub
from app.resources.classify import classify
from app.resources.normalize import identity_for
from app.resources.playlist import parse_m3u8, parse_mpd


def _clean_url(url: str) -> str:
    clean, _frag = urldefrag(url.strip())
    return clean


class Store:
    def __init__(self, db: DB, hub: Hub) -> None:
        self.db = db
        self.hub = hub
        self._lock = threading.Lock()
        self._urls: dict[str, dict[str, str]] = {}
        self._skip: dict[str, set[str]] = {}

    def create_session(self, session_id: str) -> None:
        now = time.time()
        with self._lock:
            self.db.execute(
                "INSERT INTO sessions(id, created_at, state) VALUES(?,?,?)",
                (session_id, now, "open"),
            )
            self._urls[session_id] = {}
            self._skip[session_id] = set()

    def delete_session(self, session_id: str) -> list[str]:
        with self._lock:
            variant_ids = [
                row["id"]
                for row in self.db.query(
                    """
                    SELECT variants.id FROM variants
                    JOIN groups ON groups.id = variants.group_id
                    WHERE groups.session_id = ?
                    """,
                    (session_id,),
                )
            ]
            paths = [
                row["path"]
                for row in self.db.query(
                    "SELECT path FROM downloads WHERE session_id=? AND path IS NOT NULL",
                    (session_id,),
                )
            ]
            if variant_ids:
                marks = ",".join("?" for _ in variant_ids)
                self.db.execute(
                    f"DELETE FROM occurrences WHERE variant_id IN ({marks})",
                    tuple(variant_ids),
                )
            self.db.execute("DELETE FROM downloads WHERE session_id=?", (session_id,))
            self.db.execute(
                """
                DELETE FROM variants WHERE group_id IN (
                    SELECT id FROM groups WHERE session_id=?
                )
                """,
                (session_id,),
            )
            self.db.execute("DELETE FROM groups WHERE session_id=?", (session_id,))
            self.db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            self._urls.pop(session_id, None)
            self._skip.pop(session_id, None)
            return [path for path in paths if path] + [
                f"poster:{variant_id}" for variant_id in variant_ids
            ]

    def session_exists(self, session_id: str) -> bool:
        return self.db.query_one("SELECT id FROM sessions WHERE id=?", (session_id,)) is not None

    def ingest(
        self,
        session_id: str,
        *,
        url: str,
        mime: str | None,
        status: int | None,
        via: str,
        hint: str | None = None,
        body: str | None = None,
    ) -> None:
        url = _clean_url(url)
        if not url:
            return
        with self._lock:
            if session_id not in self._urls:
                return
            if url in self._skip.get(session_id, set()):
                return
            kind = classify(url, mime, hint)
            if kind in {"ignore", "segment"}:
                return
            if kind == "playlist":
                self._ingest_playlist(session_id, url, mime, status, via, body or "")
                return
            self._ingest_media(
                session_id,
                url,
                mime,
                status,
                via,
                kind,
                label=None,
                width=None,
                height=None,
                is_playlist=False,
                renditions=None,
                group_key=None,
                title=None,
            )

    def _ingest_playlist(
        self,
        session_id: str,
        url: str,
        mime: str | None,
        status: int | None,
        via: str,
        body: str,
    ) -> None:
        lowered = url.lower().split("?", 1)[0]
        text_head = body.lstrip()[:200].lower()
        if lowered.endswith(".mpd") or "dash+xml" in (mime or "") or text_head.startswith("<?xml") or "<mpd" in text_head:
            parsed = parse_mpd(url, body)
            self._ingest_dash(session_id, url, mime, status, via, parsed)
            return
        parsed = parse_m3u8(url, body)
        if parsed["kind"] == "master":
            self._ingest_hls_master(session_id, url, mime, status, via, parsed)
            return
        for segment in parsed.get("segments") or []:
            self._skip[session_id].add(_clean_url(segment))
        self._ingest_media(
            session_id,
            url,
            mime or "application/vnd.apple.mpegurl",
            status,
            via,
            "video",
            label=None,
            width=None,
            height=None,
            is_playlist=True,
            renditions=None,
            group_key=None,
            title=None,
        )

    def _ingest_hls_master(
        self,
        session_id: str,
        url: str,
        mime: str | None,
        status: int | None,
        via: str,
        parsed: dict,
    ) -> None:
        key = f"video|hls|{url}"
        title = url.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0]
        group_id, new_group = self._ensure_group(session_id, key, "video", title or "hls")
        new_variant = False
        master_id, created = self._ensure_variant(
            session_id,
            group_id,
            url,
            mime or "application/vnd.apple.mpegurl",
            None,
            None,
            "主清单",
            True,
            None,
        )
        new_variant = new_variant or created
        self._remember(session_id, url, master_id)
        self._add_occurrence(master_id, url, mime, status, via)
        for item in parsed.get("variants") or []:
            variant_id, created = self._ensure_variant(
                session_id,
                group_id,
                item["url"],
                "application/vnd.apple.mpegurl",
                item.get("width"),
                item.get("height"),
                item.get("label") or "variant",
                True,
                None,
            )
            new_variant = new_variant or created
            self._remember(session_id, item["url"], variant_id)
            self._move_variant_to_group(session_id, variant_id, group_id)
        self._emit_group(
            session_id,
            group_id,
            new_group,
            new_variant,
            {
                "id": uuid.uuid4().hex,
                "url": url,
                "mime": mime,
                "status": status,
                "via": via,
                "created_at": time.time(),
            },
        )

    def _ingest_dash(
        self,
        session_id: str,
        url: str,
        mime: str | None,
        status: int | None,
        via: str,
        parsed: dict,
    ) -> None:
        key = f"video|dash|{url}"
        title = url.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0] or "dash"
        group_id, new_group = self._ensure_group(session_id, key, "video", title)
        renditions = [
            item
            for item in (parsed.get("renditions") or [])
            if item.get("width") or item.get("height")
        ]
        master_id, created = self._ensure_variant(
            session_id,
            group_id,
            url,
            mime or "application/dash+xml",
            None,
            None,
            "主清单",
            True,
            renditions,
        )
        new_variant = created
        self._remember(session_id, url, master_id)
        self._add_occurrence(master_id, url, mime, status, via)
        for item in parsed.get("variants") or []:
            variant_id, created = self._ensure_variant(
                session_id,
                group_id,
                item["url"],
                None,
                item.get("width"),
                item.get("height"),
                item.get("label") or "dash",
                bool(item.get("is_playlist")),
                None,
            )
            new_variant = new_variant or created
            self._remember(session_id, item["url"], variant_id)
            self._move_variant_to_group(session_id, variant_id, group_id)
        self._emit_group(
            session_id,
            group_id,
            new_group,
            new_variant,
            {
                "id": uuid.uuid4().hex,
                "url": url,
                "mime": mime,
                "status": status,
                "via": via,
                "created_at": time.time(),
            },
        )

    def _ingest_media(
        self,
        session_id: str,
        url: str,
        mime: str | None,
        status: int | None,
        via: str,
        kind: str,
        *,
        label: str | None,
        width: int | None,
        height: int | None,
        is_playlist: bool,
        renditions: list | None,
        group_key: str | None,
        title: str | None,
    ) -> None:
        known = self._urls[session_id].get(url)
        if known:
            self._add_occurrence(known, url, mime, status, via)
            group_id = self.db.query_one("SELECT group_id FROM variants WHERE id=?", (known,))
            if group_id:
                self._touch(group_id["group_id"])
                self._emit_group(
                    session_id,
                    group_id["group_id"],
                    False,
                    False,
                    self._last_occurrence(known),
                )
            return
        key, auto_title, auto_label, auto_w, auto_h = identity_for(url, kind)
        group_id, new_group = self._ensure_group(
            session_id, group_key or key, kind, title or auto_title
        )
        variant_id, created = self._ensure_variant(
            session_id,
            group_id,
            url,
            mime,
            width if width is not None else auto_w,
            height if height is not None else auto_h,
            label or auto_label or kind,
            is_playlist,
            renditions,
        )
        self._remember(session_id, url, variant_id)
        self._add_occurrence(variant_id, url, mime, status, via)
        self._emit_group(
            session_id,
            group_id,
            new_group,
            created and not new_group,
            self._last_occurrence(variant_id),
        )

    def _ensure_group(self, session_id: str, key: str, kind: str, title: str) -> tuple[str, bool]:
        row = self.db.query_one(
            "SELECT id FROM groups WHERE session_id=? AND group_key=?",
            (session_id, key),
        )
        if row:
            return row["id"], False
        group_id = uuid.uuid4().hex
        now = time.time()
        self.db.execute(
            """
            INSERT INTO groups(id, session_id, kind, group_key, title, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?)
            """,
            (group_id, session_id, kind, key, title[:120] or kind, now, now),
        )
        return group_id, True

    def _ensure_variant(
        self,
        session_id: str,
        group_id: str,
        url: str,
        mime: str | None,
        width: int | None,
        height: int | None,
        label: str | None,
        is_playlist: bool,
        renditions: list | None,
    ) -> tuple[str, bool]:
        row = self.db.query_one(
            "SELECT id FROM variants WHERE group_id=? AND url=?",
            (group_id, url),
        )
        if row:
            if renditions:
                self.db.execute(
                    "UPDATE variants SET renditions=? WHERE id=?",
                    (json.dumps(renditions, ensure_ascii=False), row["id"]),
                )
            return row["id"], False
        # 同一个会话里 URL 已挂在别的组（先看到分轨、后看到主清单）时直接复用。
        existing = self.db.query_one(
            """
            SELECT variants.id FROM variants
            JOIN groups ON groups.id = variants.group_id
            WHERE variants.url=? AND groups.session_id=?
            """,
            (url, session_id),
        )
        if existing:
            return existing["id"], False
        variant_id = uuid.uuid4().hex
        self.db.execute(
            """
            INSERT INTO variants(
                id, group_id, url, mime, width, height, label, is_playlist, renditions, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                variant_id,
                group_id,
                url,
                mime,
                width,
                height,
                label,
                1 if is_playlist else 0,
                json.dumps(renditions, ensure_ascii=False) if renditions else None,
                time.time(),
            ),
        )
        return variant_id, True

    def _remember(self, session_id: str, url: str, variant_id: str) -> None:
        self._urls.setdefault(session_id, {})[_clean_url(url)] = variant_id

    def _move_variant_to_group(self, session_id: str, variant_id: str, group_id: str) -> None:
        row = self.db.query_one("SELECT group_id FROM variants WHERE id=?", (variant_id,))
        if not row or row["group_id"] == group_id:
            return
        old = row["group_id"]
        self.db.execute("UPDATE variants SET group_id=? WHERE id=?", (group_id, variant_id))
        left = self.db.query_one("SELECT COUNT(*) AS n FROM variants WHERE group_id=?", (old,))
        if left and int(left["n"]) == 0:
            self.db.execute("DELETE FROM groups WHERE id=?", (old,))
            self.hub.publish(session_id, {"type": "resource.removed", "group_id": old})
        else:
            self._emit_group(session_id, old, False, False, None)

    def _add_occurrence(
        self,
        variant_id: str,
        url: str,
        mime: str | None,
        status: int | None,
        via: str,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO occurrences(id, variant_id, url, mime, status, via, created_at)
            VALUES(?,?,?,?,?,?,?)
            """,
            (uuid.uuid4().hex, variant_id, url, mime, status, via, time.time()),
        )
        if mime:
            self.db.execute(
                "UPDATE variants SET mime=COALESCE(mime, ?) WHERE id=?",
                (mime, variant_id),
            )

    def _last_occurrence(self, variant_id: str) -> dict | None:
        row = self.db.query_one(
            """
            SELECT id, url, mime, status, via, created_at
            FROM occurrences WHERE variant_id=? ORDER BY created_at DESC LIMIT 1
            """,
            (variant_id,),
        )
        return dict(row) if row else None

    def _touch(self, group_id: str) -> None:
        self.db.execute("UPDATE groups SET updated_at=? WHERE id=?", (time.time(), group_id))

    def _emit_group(
        self,
        session_id: str,
        group_id: str,
        new_group: bool,
        new_variant: bool,
        occurrence: dict | None,
    ) -> None:
        self._touch(group_id)
        payload = self._group_payload(group_id)
        if not payload:
            return
        if new_group:
            event_type = "resource.created"
        elif new_variant:
            event_type = "variant.added"
        else:
            event_type = "resource.updated"
        self.hub.publish(
            session_id,
            {"type": event_type, "group": payload, "occurrence": occurrence},
        )

    def list_groups(self, session_id: str) -> list[dict]:
        with self._lock:
            rows = self.db.query(
                "SELECT id FROM groups WHERE session_id=? ORDER BY created_at ASC",
                (session_id,),
            )
            return [item for row in rows if (item := self._group_payload(row["id"]))]

    def get_variant(self, session_id: str, variant_id: str) -> dict | None:
        with self._lock:
            row = self.db.query_one(
                """
                SELECT variants.*, groups.session_id, groups.kind, groups.title AS group_title
                FROM variants JOIN groups ON groups.id = variants.group_id
                WHERE variants.id=? AND groups.session_id=?
                """,
                (variant_id, session_id),
            )
            if not row:
                return None
            data = dict(row)
            data["is_playlist"] = bool(data["is_playlist"])
            data["renditions"] = json.loads(data["renditions"]) if data.get("renditions") else []
            return data

    def known_or_playlist_origin(self, session_id: str, url: str) -> bool:
        url = _clean_url(url)
        with self._lock:
            if url in self._urls.get(session_id, {}):
                return True
            if url in self._skip.get(session_id, set()):
                return True
            origin = _origin(url)
            if not origin:
                return False
            for known in self._urls.get(session_id, {}):
                row = self.db.query_one(
                    "SELECT is_playlist FROM variants WHERE id=?",
                    (self._urls[session_id][known],),
                )
                if row and row["is_playlist"] and _origin(known) == origin:
                    return True
            return False

    def _group_payload(self, group_id: str) -> dict | None:
        group = self.db.query_one("SELECT * FROM groups WHERE id=?", (group_id,))
        if not group:
            return None
        variants = []
        for row in self.db.query(
            "SELECT * FROM variants WHERE group_id=? ORDER BY created_at ASC",
            (group_id,),
        ):
            count = self.db.query_one(
                "SELECT COUNT(*) AS n FROM occurrences WHERE variant_id=?",
                (row["id"],),
            )
            variants.append(
                {
                    "id": row["id"],
                    "url": row["url"],
                    "mime": row["mime"],
                    "width": row["width"],
                    "height": row["height"],
                    "label": row["label"],
                    "is_playlist": bool(row["is_playlist"]),
                    "renditions": json.loads(row["renditions"]) if row["renditions"] else [],
                    "occurrence_count": int(count["n"] if count else 0),
                }
            )
        variants.sort(key=lambda item: ((item["width"] or 0) * (item["height"] or 0)), reverse=True)
        return {
            "id": group["id"],
            "kind": group["kind"],
            "title": group["title"],
            "created_at": group["created_at"],
            "updated_at": group["updated_at"],
            "variants": variants,
        }

    def list_downloads(self, session_id: str) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM downloads WHERE session_id=? ORDER BY created_at DESC",
            (session_id,),
        )
        return [_download_dict(row) for row in rows]

    def get_download(self, session_id: str, job_id: str) -> dict | None:
        row = self.db.query_one(
            "SELECT * FROM downloads WHERE session_id=? AND id=?",
            (session_id, job_id),
        )
        return _download_dict(row) if row else None

    def insert_download(
        self,
        *,
        job_id: str,
        session_id: str,
        variant_id: str,
        rendition_index: int | None,
        url: str,
        filename: str,
    ) -> dict:
        now = time.time()
        self.db.execute(
            """
            INSERT INTO downloads(
                id, session_id, variant_id, rendition_index, url, state, progress,
                error, path, filename, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job_id,
                session_id,
                variant_id,
                rendition_index,
                url,
                "PENDING",
                0,
                None,
                None,
                filename,
                now,
                now,
            ),
        )
        job = self.get_download(session_id, job_id)
        assert job is not None
        return job

    def update_download(self, session_id: str, job_id: str, **fields: object) -> dict | None:
        current = self.get_download(session_id, job_id)
        if not current:
            return None
        allowed = {"state", "progress", "error", "path"}
        sets = []
        params: list[object] = []
        for key, value in fields.items():
            if key in allowed:
                sets.append(f"{key}=?")
                params.append(value)
        sets.append("updated_at=?")
        params.append(time.time())
        params.extend([session_id, job_id])
        self.db.execute(
            f"UPDATE downloads SET {', '.join(sets)} WHERE session_id=? AND id=?",
            tuple(params),
        )
        job = self.get_download(session_id, job_id)
        if job:
            self.hub.publish(session_id, {"type": "download.updated", "download": job})
        return job


def _download_dict(row: sqlite3_row) -> dict:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "variant_id": row["variant_id"],
        "rendition_index": row["rendition_index"],
        "url": row["url"],
        "state": row["state"],
        "progress": row["progress"],
        "error": row["error"],
        "path": row["path"],
        "filename": row["filename"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _origin(url: str) -> str | None:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


# 供类型注释使用，避免在运行时再导入 sqlite3.Row 失败时影响上面的函数。
sqlite3_row = object
