"""WebSearchTool + WebFetchTool — Web search and URL fetching."""
import re
from tool_registry import BaseTool, ToolResult, ToolContext

MAX_FETCH_SIZE = 500_000


class WebSearchTool(BaseTool):
    name = "web_search"
    description = "Search the web using DuckDuckGo. Returns titles, URLs, and snippets."
    is_read_only = True

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "max_results": {"type": "integer", "description": "Max results (default: 5)."},
            },
            "required": ["query"],
        }

    def needs_confirmation(self, params, config): return False

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        query = params.get("query", "")
        max_results = params.get("max_results", 5)
        if not query.strip(): return ToolResult(error="Empty query", is_error=True)
        try:
            from duckduckgo_search import DDGS
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    results.append(r)
            if not results: return ToolResult(output=f"No results for: {query}")
            parts = [f"Search results for: {query}\n"]
            for i, r in enumerate(results, 1):
                parts.append(f"{i}. {r.get('title','')}\n   URL: {r.get('href', r.get('link',''))}\n   {r.get('body', r.get('snippet',''))}\n")
            return ToolResult(output="\n".join(parts))
        except ImportError:
            return ToolResult(error="duckduckgo-search not installed", is_error=True)
        except Exception as e:
            return ToolResult(error=f"Search failed: {e}", is_error=True)


class WebFetchTool(BaseTool):
    name = "web_fetch"
    description = "Fetch web page content at a URL. Returns text content stripped of HTML."
    is_read_only = True

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch."},
                "max_length": {"type": "integer", "description": f"Max chars (default: {MAX_FETCH_SIZE})."},
            },
            "required": ["url"],
        }

    def needs_confirmation(self, params, config): return False

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        url = params.get("url", "")
        max_length = params.get("max_length", MAX_FETCH_SIZE)
        if not url.strip(): return ToolResult(error="Empty URL", is_error=True)
        try:
            import httpx
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0,
                                         headers={"User-Agent": "Compagnon/2.0"}) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                ct = resp.headers.get("content-type", "")
                text = resp.text
                if "html" in ct:
                    text = self._html_to_text(text)
                if len(text) > max_length:
                    text = text[:max_length] + f"\n\n... [truncated at {max_length} chars]"
                return ToolResult(output=f"[Fetched {url}]\n\n{text}")
        except ImportError:
            return ToolResult(error="httpx not installed", is_error=True)
        except Exception as e:
            return ToolResult(error=f"Fetch failed: {e}", is_error=True)

    def _html_to_text(self, html: str) -> str:
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL|re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL|re.IGNORECASE)
        html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
        html = re.sub(r'</(p|div|h[1-6]|li|tr)>', '\n', html, flags=re.IGNORECASE)
        html = re.sub(r'<[^>]+>', '', html)
        html = html.replace('&amp;','&').replace('&lt;','<').replace('&gt;','>').replace('&quot;','"').replace('&#39;',"'").replace('&nbsp;',' ')
        return '\n'.join(l.strip() for l in html.split('\n') if l.strip())
