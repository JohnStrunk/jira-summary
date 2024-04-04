from typing import Any, Iterator, List

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.outputs import GenerationChunk
from langchain_together import Together
from together import Complete


class StreamingTogether(Together):
    def _stream(
        self,
        prompt: str,
        stop: List[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[GenerationChunk]:
        stream = Complete.create_streaming(
            prompt=prompt, model=self.model, stop=stop, **kwargs
        )
        for chunk in stream:
            if isinstance(chunk, str):
                out = chunk
            else:
                out = chunk.generated_text or ""
            gen_chunk = GenerationChunk(text=out)
            yield gen_chunk
            if run_manager:
                run_manager.on_llm_new_token(
                    gen_chunk.text,
                    chunk=gen_chunk,
                    verbose=self.verbose,
                )
