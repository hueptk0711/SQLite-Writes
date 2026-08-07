from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class GenerationRequest:
    sample_id: str
    prompt: str


@dataclass(slots=True)
class GenerationResult:
    sample_id: str
    raw_output: str
    status: str = "success"
    error: str | None = None
    latency_sec: float = 0.0
    input_tokens: int | None = None
    original_input_tokens: int | None = None
    used_input_tokens: int | None = None
    input_truncated: bool = False
    output_tokens: int | None = None
    hit_max_new_tokens: bool = False
    oom_fallback_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Generator(Protocol):
    def generate(
        self,
        requests: list[GenerationRequest],
        *,
        batch_size: int = 1,
    ) -> list[GenerationResult]:
        ...

    def metadata(self) -> dict[str, Any]:
        ...


class MockGenerator:
    def __init__(
        self,
        responses: dict[str, str] | None = None,
        default_response: str = "{}",
    ):
        self.responses = responses or {}
        self.default_response = default_response

    def generate(
        self,
        requests: list[GenerationRequest],
        *,
        batch_size: int = 1,
    ) -> list[GenerationResult]:
        del batch_size
        return [
            GenerationResult(
                sample_id=request.sample_id,
                raw_output=self.responses.get(
                    request.sample_id,
                    self.default_response,
                ),
                input_tokens=len(request.prompt.split()),
                original_input_tokens=len(request.prompt.split()),
                used_input_tokens=len(request.prompt.split()),
                output_tokens=len(
                    self.responses.get(
                        request.sample_id,
                        self.default_response,
                    ).split()
                ),
                hit_max_new_tokens=False,
            )
            for request in requests
        ]

    def metadata(self) -> dict[str, Any]:
        return {"backend": "mock", "response_count": len(self.responses)}


def create_generator(config: dict[str, Any]) -> Generator:
    backend = str(config.get("backend") or "hf").lower()
    if backend == "mock":
        return MockGenerator(
            responses={
                str(key): str(value)
                for key, value in (config.get("mock_responses") or {}).items()
            },
            default_response=str(config.get("mock_default_response") or "{}"),
        )
    if backend in {"hf", "huggingface"}:
        from .hf_runner import HuggingFaceGenerator

        return HuggingFaceGenerator(config)
    raise ValueError(f"Unsupported inference backend: {backend}")
