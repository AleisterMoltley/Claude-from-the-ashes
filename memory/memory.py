"""Memory System — Persistent memory across sessions with search."""
import json
from datetime import datetime
from pathlib import Path
from tool_registry import BaseTool, ToolResult, ToolContext


class MemoryStore:
    def __init__(self, memory_dir: str):
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.memory_dir / "_index.json"
        self._index: dict = self._load_index()

    def _load_index(self) -> dict:
        if self._index_path.exists():
            try: return json.loads(self._index_path.read_text())
            except Exception: pass
        return {"memories": {}}

    def _save_index(self):
        self._index_path.write_text(json.dumps(self._index, indent=2, ensure_ascii=False))

    def save(self, key: str, content: str, tags: list[str] = None) -> str:
        safe_key = key.replace("/", "_").replace(" ", "_")[:100]
        filepath = self.memory_dir / f"{safe_key}.md"
        header = f"---\nkey: {key}\ntags: {', '.join(tags or [])}\nupdated: {datetime.now().isoformat()}\n---\n\n"
        filepath.write_text(header + content, encoding="utf-8")
        self._index["memories"][key] = {"file": f"{safe_key}.md", "tags": tags or [],
                                         "updated": datetime.now().isoformat(), "preview": content[:200]}
        self._save_index()
        return f"Saved: {key}"

    def get(self, key: str) -> str | None:
        entry = self._index.get("memories", {}).get(key)
        if not entry: return None
        fp = self.memory_dir / entry["file"]
        return fp.read_text() if fp.exists() else None

    def search(self, query: str) -> list[dict]:
        q = query.lower()
        results = []
        for key, entry in self._index.get("memories", {}).items():
            score = 0
            if q in key.lower(): score += 10
            if any(q in t.lower() for t in entry.get("tags", [])): score += 5
            if q in entry.get("preview", "").lower(): score += 3
            if score > 0: results.append({**entry, "key": key, "score": score})
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:10]

    def list_all(self) -> list[dict]:
        return [{"key": k, **v} for k, v in self._index.get("memories", {}).items()]

    def delete(self, key: str) -> bool:
        entry = self._index.get("memories", {}).pop(key, None)
        if entry:
            (self.memory_dir / entry["file"]).unlink(missing_ok=True)
            self._save_index(); return True
        return False

    def get_prompt_context(self) -> str:
        memories = self.list_all()
        if not memories: return ""
        parts = ["<memories>"]
        for m in memories:
            parts.append(f"- {m['key']}: {m.get('preview','')[:100]}")
        parts.append("</memories>")
        return "\n".join(parts)


class MemoryReadTool(BaseTool):
    name = "memory_read"
    description = "Read from persistent memory. Search or retrieve memories by key."
    is_read_only = True
    def __init__(self, store: MemoryStore): self._store = store
    def get_input_schema(self) -> dict:
        return {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["get", "search", "list"]},
            "key": {"type": "string"}, "query": {"type": "string"},
        }, "required": ["action"]}
    def needs_confirmation(self, params, config): return False
    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        action = params.get("action", "list")
        if action == "get":
            c = self._store.get(params.get("key", ""))
            return ToolResult(output=c) if c else ToolResult(error="Not found", is_error=True)
        elif action == "search":
            r = self._store.search(params.get("query", ""))
            if r: return ToolResult(output="\n".join(f"- {x['key']} ({x['score']}): {x.get('preview','')[:100]}" for x in r))
            return ToolResult(output="No results.")
        else:
            m = self._store.list_all()
            if m: return ToolResult(output="\n".join(f"- {x['key']} [{','.join(x.get('tags',[]))}]" for x in m))
            return ToolResult(output="No memories.")


class MemoryWriteTool(BaseTool):
    name = "memory_write"
    description = "Write to persistent memory. Save important info across sessions."
    is_read_only = False
    def __init__(self, store: MemoryStore): self._store = store
    def get_input_schema(self) -> dict:
        return {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["save", "delete"]},
            "key": {"type": "string"}, "content": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        }, "required": ["action", "key"]}
    def needs_confirmation(self, params, config): return False
    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        if params.get("action") == "save":
            return ToolResult(output=self._store.save(params["key"], params.get("content",""), params.get("tags",[])))
        elif params.get("action") == "delete":
            return ToolResult(output=f"Deleted: {params['key']}") if self._store.delete(params["key"]) else ToolResult(error="Not found", is_error=True)
        return ToolResult(error="Unknown action", is_error=True)
