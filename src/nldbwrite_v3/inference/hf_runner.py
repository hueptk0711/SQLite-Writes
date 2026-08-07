from __future__ import annotations

import gc
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from nldbwrite_v3.inference.generation import GenerationRequest, GenerationResult
from nldbwrite_v3.inference.model_manifest import verify_local_model


def _json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class HuggingFaceGenerator:
    """Lazy Hugging Face runner with batching, deterministic seeds, and OOM fallback."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        model_name = str(config.get("model_name_or_path") or "")
        if not model_name:
            raise ValueError("model_name_or_path is required for backend=hf")
        revision = str(config.get("revision") or "")
        local_model = Path(model_name).exists()
        configured_hash = str(config.get("model_hash") or "")
        local_manifest: dict[str, Any] | None = None
        if local_model:
            if not re.fullmatch(r"[0-9a-fA-F]{64}", configured_hash):
                raise ValueError(
                    "A 64-character model_hash is required for a local model"
                )
            local_manifest = verify_local_model(model_name, configured_hash)
        elif not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
            raise ValueError(
                "Remote Hugging Face models require a pinned 40-character "
                "commit revision"
            )
        try:
            import torch
            from transformers import (
                AutoModelForCausalLM,
                AutoTokenizer,
                BitsAndBytesConfig,
            )
        except ImportError as exc:
            raise RuntimeError(
                "Hugging Face inference requires torch and transformers. "
                "Install requirements-inference.txt."
            ) from exc
        self.torch = torch
        tokenizer_kwargs = {
            "trust_remote_code": bool(config.get("trust_remote_code", False)),
        }
        if not local_model:
            tokenizer_kwargs["revision"] = revision
        self.tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            **tokenizer_kwargs,
        )
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.tokenizer.padding_side = "left"
        model_kwargs: dict[str, Any] = {
            "trust_remote_code": bool(config.get("trust_remote_code", False)),
            "device_map": config.get("device_map", "auto"),
        }
        if not local_model:
            model_kwargs["revision"] = revision
        quantization = str(config.get("quantization") or "").lower()
        if quantization in {"4bit", "4-bit"}:
            model_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=getattr(
                    torch,
                    str(config.get("compute_dtype") or "float16"),
                ),
            )
        elif quantization in {"8bit", "8-bit"}:
            model_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_8bit=True
            )
        elif torch.cuda.is_available():
            model_kwargs["torch_dtype"] = getattr(
                torch,
                str(config.get("torch_dtype") or "float16"),
            )
        self.model = AutoModelForCausalLM.from_pretrained(
            model_name,
            **model_kwargs,
        )
        self.model.eval()
        self.model_name = model_name
        self.revision = revision
        self.model_hash = configured_hash if local_model else revision
        tokenizer_identity = {
            "class": type(self.tokenizer).__name__,
            "vocab_sha256": _json_sha256(self.tokenizer.get_vocab()),
            "special_tokens_map": self.tokenizer.special_tokens_map,
            "chat_template": getattr(self.tokenizer, "chat_template", None),
            "model_max_length": self.tokenizer.model_max_length,
            "padding_side": self.tokenizer.padding_side,
        }
        configuration_identity = {
            "model_config": self.model.config.to_dict(),
            "generation_config": self.model.generation_config.to_dict(),
        }
        self.model_manifest = {
            **(local_manifest or {}),
            "source": "local" if local_model else "huggingface_commit",
            "model_name_or_path": model_name,
            "revision": revision or None,
            "aggregate_sha256": self.model_hash,
            "tokenizer_sha256": _json_sha256(tokenizer_identity),
            "chat_template_sha256": _json_sha256(
                tokenizer_identity["chat_template"]
            ),
            "model_config_sha256": _json_sha256(
                configuration_identity["model_config"]
            ),
            "generation_config_sha256": _json_sha256(
                configuration_identity["generation_config"]
            ),
        }

    def _chat_prompt(self, prompt: str) -> str:
        if hasattr(self.tokenizer, "apply_chat_template"):
            return self.tokenizer.apply_chat_template(
                [{"role": "user", "content": prompt}],
                tokenize=False,
                add_generation_prompt=True,
            )
        return prompt

    def _generate_batch(
        self,
        requests: list[GenerationRequest],
        *,
        oom_fallback: bool,
    ) -> list[GenerationResult]:
        torch = self.torch
        prompts = [self._chat_prompt(request.prompt) for request in requests]
        max_input_tokens = int(self.config.get("max_input_tokens") or 8192)
        original_lengths = [
            len(
                self.tokenizer(
                    prompt,
                    add_special_tokens=True,
                    truncation=False,
                )["input_ids"]
            )
            for prompt in prompts
        ]
        oversized = [
            index
            for index, length in enumerate(original_lengths)
            if length > max_input_tokens
        ]
        truncation_policy = str(
            self.config.get("input_truncation_policy") or "error"
        ).casefold()
        if oversized and truncation_policy == "error":
            oversized_set = set(oversized)
            safe_requests = [
                request
                for index, request in enumerate(requests)
                if index not in oversized_set
            ]
            output = (
                self._generate_batch(
                    safe_requests,
                    oom_fallback=oom_fallback,
                )
                if safe_requests
                else []
            )
            by_id = {result.sample_id: result for result in output}
            for index in oversized:
                request = requests[index]
                by_id[request.sample_id] = GenerationResult(
                    sample_id=request.sample_id,
                    raw_output="",
                    status="input_truncation_error",
                    error=(
                        f"Prompt has {original_lengths[index]} tokens, above "
                        f"max_input_tokens={max_input_tokens}; generation "
                        "was not attempted"
                    ),
                    input_tokens=max_input_tokens,
                    original_input_tokens=original_lengths[index],
                    used_input_tokens=max_input_tokens,
                    input_truncated=True,
                )
            return [by_id[request.sample_id] for request in requests]
        if oversized and truncation_policy not in {"allow", "truncate"}:
            raise ValueError(
                "input_truncation_policy must be 'error' or 'allow'"
            )
        started = time.perf_counter()
        encoded = self.tokenizer(
            prompts,
            return_tensors="pt",
            padding=True,
            truncation=bool(oversized),
            max_length=max_input_tokens,
        )
        device = next(self.model.parameters()).device
        encoded = {key: value.to(device) for key, value in encoded.items()}
        seed = int(self.config.get("seed") or 42)
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        max_new_tokens = int(self.config.get("max_new_tokens") or 2048)
        generation_kwargs = {
            "max_new_tokens": max_new_tokens,
            "do_sample": bool(self.config.get("do_sample", False)),
            "temperature": float(self.config.get("temperature") or 1.0),
            "top_p": float(self.config.get("top_p") or 1.0),
            "pad_token_id": self.tokenizer.pad_token_id,
            "eos_token_id": self.tokenizer.eos_token_id,
        }
        if not generation_kwargs["do_sample"]:
            generation_kwargs.pop("temperature")
            generation_kwargs.pop("top_p")
        with torch.inference_mode():
            generated = self.model.generate(**encoded, **generation_kwargs)
        elapsed = time.perf_counter() - started
        input_lengths = encoded["attention_mask"].sum(dim=1).tolist()
        results: list[GenerationResult] = []
        eos_token_ids = self.tokenizer.eos_token_id
        eos_ids = (
            {int(item) for item in eos_token_ids}
            if isinstance(eos_token_ids, list)
            else (
                {int(eos_token_ids)}
                if eos_token_ids is not None
                else set()
            )
        )
        for index, request in enumerate(requests):
            output_ids = generated[index, encoded["input_ids"].shape[1] :]
            token_ids = [int(item) for item in output_ids.tolist()]
            effective_length = len(token_ids)
            for position, token_id in enumerate(token_ids):
                if token_id in eos_ids:
                    effective_length = position + 1
                    break
            token_ids = token_ids[:effective_length]
            raw_output = self.tokenizer.decode(
                token_ids,
                skip_special_tokens=True,
            )
            used_tokens = int(input_lengths[index])
            results.append(
                GenerationResult(
                    sample_id=request.sample_id,
                    raw_output=raw_output,
                    latency_sec=elapsed / max(len(requests), 1),
                    input_tokens=used_tokens,
                    original_input_tokens=original_lengths[index],
                    used_input_tokens=used_tokens,
                    input_truncated=original_lengths[index] > used_tokens,
                    output_tokens=effective_length,
                    hit_max_new_tokens=effective_length >= max_new_tokens,
                    oom_fallback_used=oom_fallback,
                )
            )
        return results

    def generate(
        self,
        requests: list[GenerationRequest],
        *,
        batch_size: int = 1,
    ) -> list[GenerationResult]:
        output: list[GenerationResult] = []
        cursor = 0
        current_batch_size = max(int(batch_size), 1)
        while cursor < len(requests):
            batch = requests[cursor : cursor + current_batch_size]
            try:
                output.extend(
                    self._generate_batch(
                        batch,
                        oom_fallback=current_batch_size < batch_size,
                    )
                )
                cursor += len(batch)
            except RuntimeError as exc:
                if "out of memory" not in str(exc).lower():
                    output.extend(
                        GenerationResult(
                            sample_id=request.sample_id,
                            raw_output="",
                            status="generation_error",
                            error=str(exc),
                        )
                        for request in batch
                    )
                    cursor += len(batch)
                    continue
                if self.torch.cuda.is_available():
                    self.torch.cuda.empty_cache()
                gc.collect()
                if current_batch_size > 1:
                    current_batch_size = max(current_batch_size // 2, 1)
                    continue
                output.append(
                    GenerationResult(
                        sample_id=batch[0].sample_id,
                        raw_output="",
                        status="oom",
                        error=str(exc),
                        oom_fallback_used=True,
                    )
                )
                cursor += 1
        return output

    def metadata(self) -> dict[str, Any]:
        torch = self.torch
        return {
            "backend": "hf",
            "model_name_or_path": self.model_name,
            "revision": self.revision or None,
            "model_hash": self.model_hash,
            "model_manifest": self.model_manifest,
            "padding_side": self.tokenizer.padding_side,
            "input_truncation_policy": (
                self.config.get("input_truncation_policy") or "error"
            ),
            "transformers_version": __import__("transformers").__version__,
            "torch_version": torch.__version__,
            "cuda_version": torch.version.cuda,
            "gpu": (
                torch.cuda.get_device_name(0)
                if torch.cuda.is_available()
                else None
            ),
        }
