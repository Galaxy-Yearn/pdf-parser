from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import json
import urllib.error
import urllib.request


@dataclass
class ChatResult:
    content: str
    raw: Dict[str, Any]
    usage: Optional[Dict[str, Any]]
    tool_calls: Optional[List[Dict[str, Any]]]
    model: str


@dataclass
class EmbeddingsResult:
    embeddings: List[List[float]]
    raw: Dict[str, Any]
    usage: Optional[Dict[str, Any]]
    model: str


@dataclass
class ResponsesResult:
    output_text: str
    raw: Dict[str, Any]
    usage: Optional[Dict[str, Any]]
    model: str


@dataclass
class OcrResult:
    markdown: str
    raw: Dict[str, Any]
    usage: Optional[Dict[str, Any]]
    request_id: Optional[str]


@dataclass
class RerankItem:
    index: int
    relevance_score: float
    document: Optional[Any] = None


@dataclass
class RerankResult:
    results: List[RerankItem]
    raw: Dict[str, Any]
    usage: Optional[Dict[str, Any]]
    model: str
    request_id: Optional[str] = None


class OpenAICompatProvider:
    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def _chat_url(self) -> str:
        if self._base_url.endswith("/v1"):
            return f"{self._base_url}/chat/completions"
        return f"{self._base_url}/v1/chat/completions"

    def _responses_url(self) -> str:
        if self._base_url.endswith("/v1"):
            return f"{self._base_url}/responses"
        return f"{self._base_url}/v1/responses"

    def _embeddings_url(self) -> str:
        if self._base_url.endswith("/v1"):
            return f"{self._base_url}/embeddings"
        return f"{self._base_url}/v1/embeddings"

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: Optional[float] = None,
        stream: bool = False,
        timeout_s: int = 60,
        extra: Optional[Dict[str, Any]] = None,
    ) -> ChatResult:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if stream:
            payload["stream"] = True
        if extra:
            payload.update(extra)

        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        request = urllib.request.Request(
            self._chat_url(),
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                if stream:
                    content, tool_calls, events = self._read_chat_stream(response)
                    raw = {"events": events}
                    usage = None
                else:
                    raw_bytes = response.read()
                    raw_text = raw_bytes.decode("utf-8", errors="ignore")
                    raw = json.loads(raw_text)
                    choices = raw.get("choices") or []
                    content = ""
                    tool_calls = None
                    if choices:
                        message = choices[0].get("message") or {}
                        content = message.get("content") or choices[0].get("text") or ""
                        tool_calls = message.get("tool_calls")
                    usage = raw.get("usage")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"Chat request failed: HTTP {exc.code} {exc.reason}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Chat request failed: {exc}") from exc

        return ChatResult(
            content=content,
            raw=raw,
            usage=usage,
            tool_calls=tool_calls,
            model=model,
        )

    def embeddings(
        self,
        model: str,
        inputs: str | List[str],
        timeout_s: int = 60,
        extra: Optional[Dict[str, Any]] = None,
    ) -> EmbeddingsResult:
        payload: Dict[str, Any] = {
            "model": model,
            "input": inputs,
        }
        if extra:
            payload.update(extra)

        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        request = urllib.request.Request(
            self._embeddings_url(),
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                raw_bytes = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"Embeddings request failed: HTTP {exc.code} {exc.reason}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Embeddings request failed: {exc}") from exc

        raw_text = raw_bytes.decode("utf-8", errors="ignore")
        raw = json.loads(raw_text)
        data_list = raw.get("data") or []
        embeddings: List[List[float]] = []
        for item in data_list:
            if not isinstance(item, dict):
                continue
            emb = item.get("embedding")
            if isinstance(emb, list):
                embeddings.append([float(x) for x in emb])
        usage = raw.get("usage")

        return EmbeddingsResult(
            embeddings=embeddings,
            raw=raw,
            usage=usage,
            model=model,
        )

    def responses(
        self,
        model: str,
        input_text: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
        timeout_s: int = 60,
        extra: Optional[Dict[str, Any]] = None,
    ) -> ResponsesResult:
        def _extract_output_text(obj: Dict[str, Any]) -> str:
            # Prefer the convenience field if present.
            out = obj.get("output_text")
            if isinstance(out, str) and out.strip():
                return out

            pieces: List[str] = []
            output = obj.get("output")
            if isinstance(output, list):
                for item in output:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") != "message":
                        continue
                    content = item.get("content") or []
                    if not isinstance(content, list):
                        continue
                    for c in content:
                        if not isinstance(c, dict):
                            continue
                        if c.get("type") == "output_text" and isinstance(c.get("text"), str):
                            pieces.append(c["text"])
            return "".join(pieces).strip()

        payload: Dict[str, Any] = {
            "model": model,
            "input": input_text,
        }
        if tools:
            payload["tools"] = tools
        if stream:
            payload["stream"] = True
        if extra:
            payload.update(extra)

        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        request = urllib.request.Request(
            self._responses_url(),
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                if stream:
                    output_text, events = self._read_stream(response)
                    raw = {"events": events}
                    usage = None
                else:
                    raw_bytes = response.read()
                    raw_text = raw_bytes.decode("utf-8", errors="ignore")
                    raw = json.loads(raw_text)
                    output_text = _extract_output_text(raw)
                    usage = raw.get("usage")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"Responses request failed: HTTP {exc.code} {exc.reason}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Responses request failed: {exc}") from exc

        return ResponsesResult(
            output_text=output_text,
            raw=raw,
            usage=usage,
            model=model,
        )

    def _read_stream(self, response: Any) -> tuple[str, List[Dict[str, Any]]]:
        output_text = ""
        events: List[Dict[str, Any]] = []
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            events.append(obj)
            if isinstance(obj, dict):
                if "output_text" in obj and isinstance(obj["output_text"], str):
                    output_text = obj["output_text"]
                elif "delta" in obj and isinstance(obj["delta"], str):
                    output_text += obj["delta"]
                elif obj.get("type") == "response.output_text.done" and isinstance(
                    obj.get("text"), str
                ):
                    output_text = obj["text"]
        return output_text, events

    def _read_chat_stream(
        self, response: Any
    ) -> tuple[str, Optional[List[Dict[str, Any]]], List[Dict[str, Any]]]:
        content_parts: List[str] = []
        events: List[Dict[str, Any]] = []
        tool_calls_map: Dict[int, Dict[str, Any]] = {}

        for raw_line in response:
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            events.append(obj)

            for choice in obj.get("choices", []):
                delta = choice.get("delta") or {}
                if isinstance(delta.get("content"), str):
                    content_parts.append(delta["content"])

                for call in delta.get("tool_calls") or []:
                    index = call.get("index", 0)
                    entry = tool_calls_map.setdefault(
                        index,
                        {"id": None, "type": None, "function": {"name": "", "arguments": ""}},
                    )
                    if call.get("id"):
                        entry["id"] = call.get("id")
                    if call.get("type"):
                        entry["type"] = call.get("type")
                    function = call.get("function") or {}
                    if function.get("name"):
                        entry["function"]["name"] = function.get("name")
                    if isinstance(function.get("arguments"), str):
                        entry["function"]["arguments"] += function.get("arguments")

        tool_calls = None
        if tool_calls_map:
            tool_calls = [tool_calls_map[idx] for idx in sorted(tool_calls_map.keys())]
        return "".join(content_parts), tool_calls, events
