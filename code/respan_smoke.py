"""Smoke test for Respan tracing + gateway wiring (not the agent itself).

- Gateway path: OpenAI client pointed at https://api.respan.ai/api/ (needs org
  credits or a provider key configured in Respan for the requested model).
- Tracing path: any OpenAI-SDK call is auto-instrumented once Respan() is
  initialized, regardless of the base_url the client uses.
"""

import os

from dotenv import load_dotenv
from openai import OpenAI
from respan import Respan, workflow

load_dotenv()

respan = Respan()  # reads RESPAN_API_KEY


@workflow(name="ostryn_smoke_traced")
def traced_call() -> str:
    client = OpenAI(
        base_url=os.environ.get("OPENMODEL_BASE_URL", "https://api.openmodel.ai/v1"),
        api_key=os.environ["OPENMODEL_API_KEY"],
    )
    response = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "mindai/macaron-v1-tall"),
        messages=[{"role": "user", "content": "Reply with the word ok."}],
        max_tokens=5,
    )
    return response.choices[0].message.content


def gateway_call() -> str:
    client = OpenAI(
        base_url="https://api.respan.ai/api/",
        api_key=os.environ["RESPAN_API_KEY"],
    )
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Reply with the word ok."}],
        max_tokens=5,
    )
    return response.choices[0].message.content


if __name__ == "__main__":
    print("traced:", traced_call())
    try:
        print("gateway:", gateway_call())
    except Exception as e:
        print("gateway: FAILED -", e)
    respan.flush()
