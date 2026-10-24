"""Smoke test: Neon AI Gateway model access + Respan tracing.

- Model calls go through the Neon AI Gateway (OpenAI-compatible, branch-scoped
  bearer token, billed to Neon prepaid credits).
- Respan() auto-instruments the OpenAI SDK, so every call is traced in Respan
  regardless of the client's base_url.
"""

import os

from dotenv import load_dotenv
from openai import OpenAI
from respan import Respan, workflow

load_dotenv()

respan = Respan()  # reads RESPAN_API_KEY

client = OpenAI(
    base_url=f"{os.environ['NEON_AI_GATEWAY_BASE_URL']}/v1",
    api_key=os.environ["NEON_AI_GATEWAY_TOKEN"],
)


@workflow(name="ostryn_smoke")
def smoke(model: str = "kimi-k3") -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Reply with the word ok."}],
        max_tokens=10,
    )
    return response.choices[0].message.content


if __name__ == "__main__":
    print(smoke())
    respan.flush()
