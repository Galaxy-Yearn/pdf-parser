from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple
import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from config import get_default_model, get_default_provider, get_provider_config, load_dotenv
from providers import (
    ChatResult,
    EmbeddingsResult,
    OcrResult,
    OpenAICompatProvider,
    RerankItem,
    RerankResult,
    ResponsesResult,
)
from usage_tracker import record_usage


_PLACEHOLDERS = {
    "",
    "YOUR_API_KEY",
    "example-chat-model",
    "example-embed-model",
    "example-ocr-model",
    "example-vision-model",
}


def _is_placeholder(value: str) -> bool:
    return value.strip() in _PLACEHOLDERS


def _provider_key(name: str, base_url: str) -> Tuple[str, str]:
    return (name.lower(), base_url.rstrip("/"))


class ModelGateway:
    def __init__(self, env_path: Optional[str] = None) -> None:
        load_dotenv(env_path)
        self._providers: Dict[Tuple[str, str], OpenAICompatProvider] = {}

    def _get_provider(self, name: str, base_url: str, api_key: str) -> OpenAICompatProvider:
        key = _provider_key(name, base_url)
        provider_client = self._providers.get(key)
        if not provider_client:
            provider_client = OpenAICompatProvider(
                base_url=base_url,
                api_key=api_key,
            )
            self._providers[key] = provider_client
        return provider_client

    def chat(
        self,
        messages: List[Dict[str, Any]],
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = 0.2,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        timeout_s: int = 60,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        enable_search: Optional[bool] = None,
        search_options: Optional[Dict[str, Any]] = None,
        enable_thinking: Optional[bool] = None,
        thinking_budget: Optional[int] = None,
        parallel_tool_calls: Optional[bool] = None,
        extra: Optional[Dict[str, Any]] = None,
        usage_category: str = "chat",
    ) -> ChatResult:
        provider_name = (provider or get_default_provider("chat")).strip().lower()
        if not provider_name:
            raise ValueError("Chat provider not set. Check DEFAULT_CHAT_PROVIDER.")

        if provider_name == "mock":
            raise ValueError("Mock provider is disabled. Use a real provider in .env.")

        config = get_provider_config(provider_name)
        if _is_placeholder(config.base_url):
            raise ValueError(f"Missing base URL for provider: {provider_name}")
        if _is_placeholder(config.api_key):
            raise ValueError(
                f"Missing API key for provider: {provider_name}. Update .env first."
            )

        chosen_model = model or get_default_model("chat") or config.chat_model
        if _is_placeholder(chosen_model):
            raise ValueError(
                f"Missing chat model for provider: {provider_name}. Update .env first."
            )

        # Kimi K2.x parameters are stricter than generic OpenAI-compatible APIs.
        # The official docs recommend relying on model defaults, so we omit
        # temperature unless the caller explicitly asks for 1.0.
        if provider_name == "kimi" and temperature is not None:
            try:
                if float(temperature) != 1.0:
                    temperature = None
            except Exception:  # noqa: BLE001
                temperature = None

        request_extra: Dict[str, Any] = {}
        if extra:
            request_extra.update(extra)
        # Phase 9 / Slice H: avoid accidental truncation for agentic JSON outputs.
        # Default to a high cap (64k) but retry with smaller values if a provider rejects it.
        if max_tokens is None:
            try:
                max_tokens = int(os.getenv("DEFAULT_MAX_TOKENS", "64000") or "64000")
            except ValueError:
                max_tokens = 64000
        if isinstance(max_tokens, int) and max_tokens > 0 and "max_tokens" not in request_extra:
            request_extra["max_tokens"] = int(max_tokens)
        if tools is not None:
            request_extra["tools"] = tools
        if tool_choice is not None:
            request_extra["tool_choice"] = tool_choice

        # Provider-specific option mapping:
        # - Qwen (DashScope) supports these extra routing fields in compatible-mode.
        # - Kimi (Moonshot) uses a different knob for "thinking" (instant mode).
        if provider_name == "qwen":
            if enable_search is not None:
                request_extra["enable_search"] = enable_search
            if search_options is not None:
                request_extra["search_options"] = search_options
            if enable_thinking is not None:
                request_extra["enable_thinking"] = enable_thinking
            if thinking_budget is not None:
                request_extra["thinking_budget"] = thinking_budget
            if parallel_tool_calls is not None:
                request_extra["parallel_tool_calls"] = parallel_tool_calls
        elif provider_name == "kimi":
            # Best-effort: only set when explicitly disabling thinking, to avoid
            # sending unsupported/unknown fields.
            if enable_thinking is False:
                request_extra["thinking"] = {"type": "disabled"}

        provider_client = self._get_provider(
            provider_name,
            config.base_url,
            config.api_key,
        )

        def _looks_like_token_error(msg: str) -> bool:
            s = (msg or "").lower()
            return any(
                k in s
                for k in (
                    "max_tokens",
                    "maximum context",
                    "context length",
                    "context_length",
                    "too many tokens",
                    "token limit",
                    "exceed",
                )
            )

        try:
            res = provider_client.chat(
                chosen_model,
                messages,
                temperature=temperature,
                stream=stream,
                timeout_s=timeout_s,
                extra=request_extra or None,
            )
        except RuntimeError as exc:
            # If a provider rejects an overly-large max_tokens, retry with smaller caps.
            if "max_tokens" in request_extra and _looks_like_token_error(str(exc)):
                orig = int(request_extra.get("max_tokens") or 0)
                fallback = [32768, 16384, 8192, 4096, 2048, 1024]
                tried: set[int] = set()
                for mt in fallback:
                    if orig and mt >= orig:
                        continue
                    if mt in tried:
                        continue
                    tried.add(mt)
                    request_extra["max_tokens"] = int(mt)
                    try:
                        res = provider_client.chat(
                            chosen_model,
                            messages,
                            temperature=temperature,
                            stream=stream,
                            timeout_s=timeout_s,
                            extra=request_extra or None,
                        )
                        break
                    except RuntimeError:
                        continue
                else:
                    raise
            else:
                raise
        record_usage(category=usage_category, model=chosen_model, usage=res.usage)
        return res

    def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tool_handlers: Dict[str, Callable[[Dict[str, Any]], Any]],
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = 0.2,
        stream: bool = False,
        timeout_s: int = 60,
        tool_choice: Optional[Any] = None,
        enable_search: Optional[bool] = None,
        search_options: Optional[Dict[str, Any]] = None,
        enable_thinking: Optional[bool] = None,
        thinking_budget: Optional[int] = None,
        parallel_tool_calls: Optional[bool] = None,
        extra: Optional[Dict[str, Any]] = None,
        max_rounds: int = 5,
    ) -> ChatResult:
        provider_name = (provider or get_default_provider("chat")).strip().lower()
        working_messages = list(messages)
        for _ in range(max_rounds):
            result = self.chat(
                messages=working_messages,
                provider=provider,
                model=model,
                temperature=temperature,
                stream=stream,
                timeout_s=timeout_s,
                tools=tools,
                tool_choice=tool_choice,
                enable_search=enable_search,
                search_options=search_options,
                enable_thinking=enable_thinking,
                thinking_budget=thinking_budget,
                parallel_tool_calls=parallel_tool_calls,
                extra=extra,
            )

            tool_calls = result.tool_calls or []
            if not tool_calls:
                return result

            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": result.content or "",
                "tool_calls": tool_calls,
            }
            # Kimi requires preserving `reasoning_content` in tool-call turns when thinking is enabled.
            if provider_name == "kimi":
                try:
                    choices = (result.raw or {}).get("choices") or []
                    msg0 = (choices[0].get("message") or {}) if choices else {}
                    rc = msg0.get("reasoning_content")
                    assistant_msg["reasoning_content"] = rc if isinstance(rc, str) else ""
                except Exception:  # noqa: BLE001
                    assistant_msg["reasoning_content"] = ""

            working_messages.append(assistant_msg)

            for call in tool_calls:
                function = call.get("function") or {}
                name = function.get("name")
                arguments_raw = function.get("arguments") or "{}"
                if not name or name not in tool_handlers:
                    raise ValueError(f"Tool handler not found for: {name}")
                try:
                    if isinstance(arguments_raw, str):
                        arguments = json.loads(arguments_raw)
                    else:
                        arguments = arguments_raw
                except json.JSONDecodeError:
                    arguments = {"_raw": arguments_raw}

                tool_output = tool_handlers[name](arguments)
                if not isinstance(tool_output, str):
                    tool_output = json.dumps(tool_output, ensure_ascii=False)

                tool_msg: Dict[str, Any] = {
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": tool_output,
                }
                # Kimi's built-in tools (e.g. $web_search) require the tool name in the tool result message.
                if provider_name == "kimi":
                    tool_msg["name"] = name

                working_messages.append(
                    tool_msg
                )

        raise RuntimeError("Tool calling exceeded max rounds.")

    def responses(
        self,
        input_text: str,
        tools: Optional[List[Dict[str, Any]]] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        stream: bool = False,
        timeout_s: int = 60,
        extra: Optional[Dict[str, Any]] = None,
        max_tokens: Optional[int] = None,
        usage_category: str = "responses",
    ) -> ResponsesResult:
        provider_name = (provider or get_default_provider("chat")).strip().lower()
        if not provider_name:
            raise ValueError("Responses provider not set. Check DEFAULT_CHAT_PROVIDER.")

        config = get_provider_config(provider_name)
        if _is_placeholder(config.responses_base_url):
            raise ValueError(f"Missing responses base URL for provider: {provider_name}")
        if _is_placeholder(config.api_key):
            raise ValueError(
                f"Missing API key for provider: {provider_name}. Update .env first."
            )

        chosen_model = model or get_default_model("chat") or config.chat_model
        if _is_placeholder(chosen_model):
            raise ValueError(
                f"Missing chat model for provider: {provider_name}. Update .env first."
            )

        provider_client = self._get_provider(
            provider_name,
            config.responses_base_url,
            config.api_key,
        )

        request_extra: Dict[str, Any] = {}
        if extra:
            request_extra.update(extra)

        # Phase 9 / Slice H: avoid accidental truncation for long evidence extraction
        # and agentic maintenance jobs routed through the Responses API.
        #
        # OpenAI Responses uses `max_output_tokens`. Some OpenAI-compatible providers still use
        # `max_tokens` on /responses. Prefer `max_output_tokens` and fall back if rejected.
        if max_tokens is None:
            try:
                max_tokens = int(os.getenv("DEFAULT_MAX_TOKENS", "64000") or "64000")
            except ValueError:
                max_tokens = 64000
        if isinstance(max_tokens, int) and max_tokens > 0:
            if "max_output_tokens" not in request_extra and "max_tokens" not in request_extra:
                request_extra["max_output_tokens"] = int(max_tokens)

        def _looks_like_token_error(msg: str) -> bool:
            s = (msg or "").lower()
            return any(
                k in s
                for k in (
                    "max_output_tokens",
                    "max_tokens",
                    "maximum context",
                    "context length",
                    "context_length",
                    "too many tokens",
                    "token limit",
                    "exceed",
                )
            )

        def _looks_like_unknown_param(msg: str, param: str) -> bool:
            s = (msg or "").lower()
            return param.lower() in s and any(k in s for k in ("unknown", "unrecognized", "unexpected", "invalid"))

        try:
            res = provider_client.responses(
                chosen_model,
                input_text,
                tools=tools,
                stream=stream,
                timeout_s=timeout_s,
                extra=request_extra or None,
            )
        except RuntimeError as exc:
            msg = str(exc)

            # Compatibility fallback: if /responses rejects max_output_tokens, retry with max_tokens.
            if "max_output_tokens" in request_extra and _looks_like_unknown_param(msg, "max_output_tokens"):
                swapped = dict(request_extra)
                cap = int(swapped.pop("max_output_tokens") or 0)
                if cap > 0 and "max_tokens" not in swapped:
                    swapped["max_tokens"] = cap
                res = provider_client.responses(
                    chosen_model,
                    input_text,
                    tools=tools,
                    stream=stream,
                    timeout_s=timeout_s,
                    extra=swapped or None,
                )
            # Token-limit fallback: progressively shrink caps.
            elif ("max_output_tokens" in request_extra or "max_tokens" in request_extra) and _looks_like_token_error(msg):
                field = "max_output_tokens" if "max_output_tokens" in request_extra else "max_tokens"
                orig = int(request_extra.get(field) or 0)
                fallback = [32768, 16384, 8192, 4096, 2048, 1024]
                tried: set[int] = set()
                for mt in fallback:
                    if orig and mt >= orig:
                        continue
                    if mt in tried:
                        continue
                    tried.add(mt)
                    request_extra[field] = int(mt)
                    try:
                        res = provider_client.responses(
                            chosen_model,
                            input_text,
                            tools=tools,
                            stream=stream,
                            timeout_s=timeout_s,
                            extra=request_extra or None,
                        )
                        break
                    except RuntimeError:
                        continue
                else:
                    raise
            else:
                raise
        record_usage(category=usage_category, model=chosen_model, usage=res.usage)
        return res

    def embeddings(
        self,
        inputs: str | List[str],
        provider: Optional[str] = None,
        model: Optional[str] = None,
        timeout_s: int = 60,
        extra: Optional[Dict[str, Any]] = None,
    ) -> EmbeddingsResult:
        return self.embeddings_text(
            inputs=inputs,
            provider=provider,
            model=model,
            timeout_s=timeout_s,
            extra=extra,
        )

    def ocr(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("Use ocr_image instead.")

    def vision(
        self,
        image_path: str,
        prompt: str = "",
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = 0.2,
        stream: bool = False,
        timeout_s: int = 60,
        extra: Optional[Dict[str, Any]] = None,
    ) -> ChatResult:
        return self.vision_image(
            image_path=image_path,
            prompt=prompt,
            provider=provider,
            model=model,
            temperature=temperature,
            stream=stream,
            timeout_s=timeout_s,
            extra=extra,
        )

    def embeddings_text(
        self,
        inputs: str | List[str],
        provider: Optional[str] = None,
        model: Optional[str] = None,
        timeout_s: int = 60,
        extra: Optional[Dict[str, Any]] = None,
    ) -> EmbeddingsResult:
        provider_name = (provider or get_default_provider("embeddings")).strip().lower()
        if not provider_name:
            raise ValueError(
                "Embeddings provider not set. Check DEFAULT_EMBEDDINGS_PROVIDER."
            )

        if provider_name == "mock":
            raise ValueError("Mock provider is disabled. Use a real provider in .env.")

        config = get_provider_config(provider_name)
        if _is_placeholder(config.base_url):
            raise ValueError(f"Missing base URL for provider: {provider_name}")
        if _is_placeholder(config.api_key):
            raise ValueError(
                f"Missing API key for provider: {provider_name}. Update .env first."
            )

        chosen_model = model or get_default_model("embeddings") or config.embed_model
        if _is_placeholder(chosen_model):
            raise ValueError(
                f"Missing embeddings model for provider: {provider_name}. Update .env first."
            )

        provider_client = self._get_provider(
            provider_name,
            config.base_url,
            config.api_key,
        )

        res = provider_client.embeddings(
            chosen_model,
            inputs,
            timeout_s=timeout_s,
            extra=extra,
        )
        record_usage(category="embeddings", model=chosen_model, usage=res.usage)
        return res

    def rerank(
        self,
        query: str,
        documents: List[str] | List[Dict[str, Any]],
        provider: Optional[str] = None,
        model: Optional[str] = None,
        top_n: Optional[int] = None,
        instruct: Optional[str] = None,
        timeout_s: int = 60,
        return_documents: bool = False,
        extra: Optional[Dict[str, Any]] = None,
    ) -> RerankResult:
        """
        Text/Multimodal rerank.

        Notes:
        - For Qwen (DashScope), we use the official rerank service endpoint:
          POST https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank
        - qwen3-rerank uses top-level fields: query/documents/top_n/instruct
        - qwen3-vl-rerank uses: input={query, documents=[{text,...}]} and parameters={top_n, return_documents}
        """

        provider_name = (provider or get_default_provider("rerank") or "qwen").strip().lower()
        if not provider_name:
            provider_name = "qwen"

        if provider_name == "mock":
            raise ValueError("Mock provider is disabled. Use a real provider in .env.")

        config = get_provider_config(provider_name)
        if _is_placeholder(config.api_key):
            raise ValueError(
                f"Missing API key for provider: {provider_name}. Update .env first."
            )

        chosen_model = (model or get_default_model("rerank") or "").strip()
        if not chosen_model:
            # Default to Qwen rerank model per Phase 8 plan.
            chosen_model = "qwen3-rerank"
        if _is_placeholder(chosen_model):
            raise ValueError(
                f"Missing rerank model for provider: {provider_name}. Update .env first."
            )

        if provider_name != "qwen":
            raise NotImplementedError("Rerank is currently implemented for Qwen provider only.")

        # DashScope currently exposes:
        # - qwen3-rerank via OpenAI-compatible rerank endpoint
        # - qwen3-vl-rerank (and others) via the service endpoint
        if chosen_model == "qwen3-rerank":
            url = "https://dashscope.aliyuncs.com/compatible-api/v1/reranks"
        else:
            url = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"
        payload: Dict[str, Any] = {"model": chosen_model}
        if extra:
            payload.update(extra)

        # qwen3-rerank: text-only convenience API
        if chosen_model == "qwen3-rerank":
            if not isinstance(documents, list) or (documents and not isinstance(documents[0], str)):
                raise TypeError("qwen3-rerank expects documents: List[str]")
            payload["query"] = query
            payload["documents"] = documents
            if top_n is not None:
                payload["top_n"] = int(top_n)
            if instruct:
                payload["instruct"] = instruct
        else:
            # Multimodal rerank models (e.g. qwen3-vl-rerank). Allow text-only docs via {"text": ...}.
            input_docs: List[Dict[str, Any]] = []
            if documents and isinstance(documents[0], str):
                for d in documents:  # type: ignore[assignment]
                    input_docs.append({"text": str(d)})
            else:
                for d in documents:  # type: ignore[assignment]
                    if not isinstance(d, dict):
                        raise TypeError("Multimodal rerank expects documents: List[Dict[str, Any]] or List[str]")
                    input_docs.append(d)

            payload["input"] = {"query": query, "documents": input_docs}
            params: Dict[str, Any] = {}
            if top_n is not None:
                params["top_n"] = int(top_n)
            if return_documents:
                params["return_documents"] = True
            if params:
                payload["parameters"] = params

        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        }
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                raw_bytes = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"Rerank request failed: HTTP {exc.code} {exc.reason}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Rerank request failed: {exc}") from exc

        raw_text = raw_bytes.decode("utf-8", errors="ignore")
        raw = json.loads(raw_text)

        # Compatible endpoint returns `results` at top-level; service endpoint nests under `output.results`.
        if isinstance(raw.get("results"), list):
            results_raw = raw.get("results") or []
        else:
            out = raw.get("output") or {}
            results_raw = out.get("results") or []
        results: List[RerankItem] = []
        for item in results_raw:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            score = item.get("relevance_score")
            if idx is None or score is None:
                continue
            try:
                idx_i = int(idx)
                score_f = float(score)
            except (TypeError, ValueError):
                continue
            results.append(
                RerankItem(
                    index=idx_i,
                    relevance_score=score_f,
                    document=item.get("document"),
                )
            )

        usage = raw.get("usage") if isinstance(raw, dict) else None
        record_usage(category="rerank", model=chosen_model, usage=usage if isinstance(usage, dict) else None)

        return RerankResult(
            results=results,
            raw=raw,
            usage=usage,
            model=chosen_model,
            request_id=raw.get("request_id") or raw.get("id"),
        )

    def vision_image(
        self,
        image_path: str,
        prompt: str = "",
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = 0.2,
        stream: bool = False,
        timeout_s: int = 60,
        extra: Optional[Dict[str, Any]] = None,
    ) -> ChatResult:
        # Modern chat models (e.g. `qwen3.5-plus`, `kimi-k2.5`) are natively multimodal:
        # route vision through chat by default.
        provider_name = (provider or get_default_provider("chat")).strip().lower()
        if not provider_name:
            raise ValueError("Chat provider not set. Check DEFAULT_CHAT_PROVIDER.")

        if provider_name == "mock":
            raise ValueError("Mock provider is disabled. Use a real provider in .env.")

        config = get_provider_config(provider_name)
        if _is_placeholder(config.base_url):
            raise ValueError(f"Missing base URL for provider: {provider_name}")
        if _is_placeholder(config.api_key):
            raise ValueError(
                f"Missing API key for provider: {provider_name}. Update .env first."
            )

        chosen_model = model or get_default_model("chat") or config.chat_model
        if _is_placeholder(chosen_model):
            raise ValueError(
                f"Missing chat model for provider: {provider_name}. Update .env first."
            )

        file_bytes = Path(image_path).read_bytes()
        suffix = Path(image_path).suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            mime = "image/jpeg"
        elif suffix == ".png":
            mime = "image/png"
        elif suffix == ".webp":
            mime = "image/webp"
        else:
            mime = "application/octet-stream"

        prompt_text = str(prompt or "").strip()
        if not prompt_text:
            # Avoid hard-coded prompt defaults; load from repo prompt assets.
            from prompt_templates import read_prompt  # lazy import

            prompt_text = read_prompt("vision_default_prompt.md").strip()

        data_uri = f"data:{mime};base64,{base64.b64encode(file_bytes).decode('ascii')}"
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ]

        return self.chat(
            messages=messages,
            provider=provider_name,
            model=chosen_model,
            temperature=temperature,
            stream=stream,
            timeout_s=timeout_s,
            extra=extra,
            usage_category="vision",
        )

    def ocr_image(
        self,
        image_path: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        timeout_s: int = 60,
        return_crop_images: bool = False,
        need_layout_visualization: bool = False,
        extra: Optional[Dict[str, Any]] = None,
    ) -> OcrResult:
        provider_name = (provider or get_default_provider("ocr")).strip().lower()
        if not provider_name:
            raise ValueError("OCR provider not set. Check DEFAULT_OCR_PROVIDER.")

        if provider_name == "mock":
            raise ValueError("Mock OCR provider is disabled. Use a real provider in .env.")

        config = get_provider_config(provider_name)
        if _is_placeholder(config.base_url):
            raise ValueError(f"Missing base URL for provider: {provider_name}")
        if _is_placeholder(config.api_key):
            raise ValueError(
                f"Missing API key for provider: {provider_name}. Update .env first."
            )

        chosen_model = model or get_default_model("ocr") or config.ocr_model
        if _is_placeholder(chosen_model):
            raise ValueError(
                f"Missing OCR model for provider: {provider_name}. Update .env first."
            )

        if provider_name != "glm":
            raise NotImplementedError("OCR is currently implemented for GLM provider only.")

        url = config.base_url.rstrip("/") + "/layout_parsing"
        file_bytes = Path(image_path).read_bytes()
        suffix = Path(image_path).suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            mime = "image/jpeg"
        elif suffix == ".png":
            mime = "image/png"
        elif suffix == ".pdf":
            mime = "application/pdf"
        else:
            mime = "application/octet-stream"

        payload: Dict[str, Any] = {
            "model": chosen_model,
            "file": f"data:{mime};base64,{base64.b64encode(file_bytes).decode('ascii')}",
            "return_crop_images": return_crop_images,
            "need_layout_visualization": need_layout_visualization,
        }
        if extra:
            payload.update(extra)

        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        }
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                raw_bytes = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"OCR request failed: HTTP {exc.code} {exc.reason}: {detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OCR request failed: {exc}") from exc

        raw_text = raw_bytes.decode("utf-8", errors="ignore")
        raw = json.loads(raw_text)
        markdown = _extract_ocr_markdown(raw)
        usage = raw.get("usage") or raw.get("data_info", {}).get("usage")
        request_id = raw.get("request_id") or raw.get("id")
        record_usage(category="ocr", model=chosen_model, usage=usage if isinstance(usage, dict) else None)

        return OcrResult(
            markdown=markdown,
            raw=raw,
            usage=usage,
            request_id=request_id,
        )


def _extract_ocr_markdown(raw: Dict[str, Any]) -> str:
    if isinstance(raw.get("content"), str):
        return raw["content"]
    if isinstance(raw.get("result"), str):
        return raw["result"]
    if isinstance(raw.get("output_text"), str):
        return raw["output_text"]
    layout = raw.get("layout_details")
    if isinstance(layout, list) and layout:
        page = layout[0] if isinstance(layout[0], list) else layout
        parts: list[str] = []
        if isinstance(page, list):
            for block in page:
                if not isinstance(block, dict):
                    continue
                content = block.get("content")
                if isinstance(content, str) and content.strip():
                    parts.append(content.strip())
        if parts:
            return "\n\n".join(parts)
    data = raw.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            if isinstance(first.get("content"), str):
                return first["content"]
            if isinstance(first.get("text"), str):
                return first["text"]
    return ""
