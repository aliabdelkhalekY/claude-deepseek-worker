from pathlib import Path

from deepseek_worker import config as config_module


def make_config(**overrides):
    env = {"DEEPSEEK_WORKER_API_KEY": "test-key", "DEEPSEEK_WORKER_BASE_URL": "https://gateway.test/anthropic",
           "DEEPSEEK_WORKER_MODEL": "test-model", "DEEPSEEK_WORKER_LOG": "/nonexistent/log.jsonl"}
    env.update(overrides)
    return config_module.load(env)


def write_files(root, files):
    for relative, text in files.items():
        path = Path(root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def tool_use(block_id, name, **args):
    return {"type": "tool_use", "id": block_id, "name": name, "input": args}


def text(value):
    return {"type": "text", "text": value}


class ScriptedGateway:
    """Returns prepared responses in order and keeps what it was sent."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create_message(self, system, messages, tools):
        self.calls.append({"system": system, "messages": [dict(m) for m in messages]})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
