"""Send four dependent prompts through one agent session and stream each reply."""
import ast
import json
import time
from pathlib import Path

import requests


def _run_test_credentials() -> tuple[str, str]:
    """Read API_KEY and BASE_URL constants without executing the full test script."""
    script_path = Path(__file__).with_name("test_agent_run.py")
    tree = ast.parse(script_path.read_text(encoding="utf-8"))
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in {"API_KEY", "BASE_URL"}:
                values[target.id] = ast.literal_eval(node.value)
    return values["API_KEY"], values["BASE_URL"]


def _poll_for_result(base_url: str, run_id: str, headers: dict) -> str:
    """Keep polling as a fallback when Redis-backed SSE is unavailable."""
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        response = requests.get(
            f"{base_url}/v1/runs/{run_id}", headers=headers, timeout=15
        )
        response.raise_for_status()
        run = response.json()
        if run["status"] == "done":
            answer = run.get("result", {}).get("answer", "")
            print(answer)
            return answer
        if run["status"] in {"failed", "cancelled"}:
            raise RuntimeError(run.get("error_message") or run.get("result"))
        print(".", end="", flush=True)
        time.sleep(1)
    raise TimeoutError("The agent run did not finish within 120 seconds.")


def _stream_run(base_url: str, run_id: str, headers: dict) -> str:
    """Print visible progress and streamed answer text from the authenticated SSE endpoint."""
    response = requests.get(
        f"{base_url}/v1/runs/{run_id}/stream",
        headers=headers,
        stream=True,
        timeout=(15, 320),
    )
    if response.status_code == 503:
        response.close()
        print("Live stream unavailable; falling back to polling.")
        return _poll_for_result(base_url, run_id, headers)
    response.raise_for_status()
    response.encoding = "utf-8"

    current_event = "message"
    data_lines = []
    streamed_text = ""
    final_text = ""
    print("Agent is working…", flush=True)

    def handle_event(event_type: str, raw_data: str) -> bool:
        nonlocal streamed_text, final_text
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            return False

        if event_type == "progress_snapshot":
            if data.get("mode"):
                print(f"Mode: {data['mode']}")
            for step in data.get("steps", []):
                print(f"[{step.get('status', 'pending')}] {step.get('description', '')}")
        elif event_type == "plan_created":
            for step in data.get("steps", []):
                print(f"Planned: {step.get('description', '')}")
        elif event_type == "step_start":
            print(f"Working on: {data.get('description', data.get('step_id', 'task'))}")
        elif event_type == "answer.delta":
            text = data.get("text", "")
            streamed_text += text
            print(text, end="", flush=True)
        elif event_type == "answer.reset":
            print("\n[Restarting answer after tool use]", flush=True)
            streamed_text = ""
        elif event_type == "run_completed":
            final_text = data.get("output") or (data.get("result") or {}).get("answer", "")
            if final_text and not streamed_text:
                print(final_text, end="", flush=True)
            elif final_text and final_text.startswith(streamed_text):
                print(final_text[len(streamed_text):], end="", flush=True)
            elif final_text and final_text != streamed_text:
                print(f"\n{final_text}", end="", flush=True)
            print()
            return True
        elif event_type == "run_failed":
            raise RuntimeError(data.get("error") or data.get("result") or "Agent run failed")
        return False

    with response:
        for raw_line in response.iter_lines(decode_unicode=True):
            line = raw_line or ""
            if not line:
                if data_lines and handle_event(current_event, "\n".join(data_lines)):
                    break
                current_event = "message"
                data_lines = []
            elif line.startswith("event:"):
                current_event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())

    if final_text:
        return final_text
    if streamed_text:
        return streamed_text
    raise RuntimeError("The run stream ended without a completed answer.")


def main() -> None:
    api_key, base_url = _run_test_credentials()
    # Keep all four runs in one stable session for conversation-history testing.
    end_user_id = "test_100"
    session_id = "quick-test-session"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-End-User-ID": end_user_id,
        "X-Session-ID": session_id,
    }
    agents_response = requests.get(
        f"{base_url}/v1/agents/", headers=headers, timeout=15
    )
    agents_response.raise_for_status()
    agents = agents_response.json()
    if not agents:
        raise RuntimeError("No agent is configured for this API key.")

    agent_id = agents[0]["id"]
    questions = [
        "I'm planning a short trip to Japan. Recommend one city to visit and explain why.",
        None,  # Built from the first answer so this question depends on it.
        None,  # Built from the second answer so this question depends on it.
        "List all the questions I have asked in this conversation, in order.",
    ]

    answers = []
    for index in range(4):
        if index == 1:
            prompt = (
                "Based on your city recommendation, name one specific attraction to visit "
                f"there. Your previous answer was: {answers[0]}"
            )
        elif index == 2:
            prompt = (
                "Based on the city and attraction from your previous answers, recommend "
                "one local food I should try there. City recommendation: "
                f"{answers[0]} Attraction recommendation: {answers[1]}"
            )
        else:
            prompt = questions[index]

        print(f"\nQuestion {index + 1}/4: {prompt}", flush=True)
        run_response = requests.post(
            f"{base_url}/v1/agents/{agent_id}/run",
            headers=headers,
            json={"input": prompt, "external_session_id": session_id},
            timeout=30,
        )
        run_response.raise_for_status()
        run_id = run_response.json()["run_id"]
        answers.append(_stream_run(base_url, run_id, headers))


if __name__ == "__main__":
    main()
