"""Automated conversation-memory and history-retrieval smoke test.

Credentials are read by the existing helper in test_agent_quick.py, which in
turn reads API_KEY and BASE_URL from test_agent_run.py without executing it.
"""

from __future__ import annotations

import json
import time

import requests

from test_agent_quick import _poll_for_result, _run_test_credentials


END_USER_ID = "test4"
SESSION_ID = "session id 5"
MARKER = "BLUE-COMET-42"


def run_and_collect(base_url: str, agent_id: str, headers: dict, prompt: str) -> tuple[str, list[str] | None]:
    response = requests.post(
        f"{base_url}/v1/agents/{agent_id}/run",
        headers=headers,
        json={"input": prompt, "external_session_id": SESSION_ID},
        timeout=30,
    )
    response.raise_for_status()
    run_id = response.json()["run_id"]
    print(f"Run ID: {run_id}")

    stream = requests.get(
        f"{base_url}/v1/runs/{run_id}/stream",
        headers=headers,
        stream=True,
        timeout=(15, 320),
    )
    if stream.status_code == 503:
        stream.close()
        print("SSE unavailable; polling for the result. Tool-call details unavailable.")
        return _poll_for_result(base_url, run_id, headers), None
    stream.raise_for_status()
    stream.encoding = "utf-8"

    event_name = "message"
    data_lines: list[str] = []
    tool_calls: list[str] = []
    answer_parts: list[str] = []
    final_answer = ""

    def consume_event(name: str, payload: str) -> bool:
        nonlocal final_answer
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return False
        if name == "tool_executed":
            tool = data.get("tool")
            if tool:
                tool_calls.append(tool)
                print(f"  Tool used: {tool}")
        elif name == "answer.delta":
            answer_parts.append(data.get("text", ""))
        elif name == "run_completed":
            final_answer = data.get("output") or (data.get("result") or {}).get("answer", "")
            return True
        elif name == "run_failed":
            raise RuntimeError(data.get("error") or data.get("result") or "Agent run failed")
        return False

    with stream:
        for raw_line in stream.iter_lines(decode_unicode=True):
            line = raw_line or ""
            if not line:
                if data_lines and consume_event(event_name, "\n".join(data_lines)):
                    break
                event_name, data_lines = "message", []
            elif line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())

    answer = final_answer or "".join(answer_parts)
    if not answer:
        # Handle a stream that disconnected after the database record completed.
        for _ in range(10):
            result_response = requests.get(
                f"{base_url}/v1/runs/{run_id}", headers=headers, timeout=15
            )
            result_response.raise_for_status()
            run = result_response.json()
            if run.get("status") == "done":
                answer = (run.get("result") or {}).get("answer", "")
                break
            if run.get("status") in {"failed", "cancelled"}:
                raise RuntimeError(run.get("error_message") or run.get("result"))
            time.sleep(1)
    if not answer:
        raise RuntimeError(f"Run {run_id} ended without a completed answer.")
    return answer, tool_calls


def main() -> int:
    api_key, base_url = _run_test_credentials()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-End-User-ID": END_USER_ID,
        "X-Session-ID": SESSION_ID,
    }
    agents_response = requests.get(f"{base_url}/v1/agents/", headers=headers, timeout=15)
    agents_response.raise_for_status()
    agents = agents_response.json()
    if not agents:
        raise RuntimeError("No agent is configured for the API key in test_agent_run.py.")
    agent_id = agents[0]["id"]

    print(f"Testing agent: {agents[0].get('name', agent_id)} ({agent_id})")
    print(f"End user: {END_USER_ID} | Session: {SESSION_ID}")

    cases = [
        (
            "Seed session memory",
            f"For this session-memory test, remember the exact marker {MARKER}. Reply only with: stored",
        ),
        (
            "Recall from the conversation prompt",
            "What exact marker did I ask you to remember in my previous message? "
            "Answer with only the marker and do not use tools.",
        ),
        (
            "Retrieve the questions already asked in this session today",
            "List the questions I have asked in this session today, verbatim when possible. "
            "Exclude this message.",
        ),
    ]

    results: list[tuple[str, str, list[str] | None]] = []
    for label, prompt in cases:
        print(f"\n=== {label} ===\nYou: {prompt}")
        answer, tools = run_and_collect(base_url, agent_id, headers, prompt)
        print(f"Agent: {answer}")
        results.append((label, answer, tools))

    recall_answer = results[1][1]
    recall_passed = MARKER.lower() in recall_answer.lower()
    history_answer = results[2][1].lower()
    history_question_seen = "what exact marker did i ask you to remember" in history_answer

    print("\n=== Summary ===")
    print(f"Prompt conversation recall: {'PASS' if recall_passed else 'FAIL'}")
    print(f"History includes the earlier test question: {'PASS' if history_question_seen else 'CHECK ANSWER'}")
    history_tools = results[2][2]
    if history_tools is None:
        print("History lookup tool usage: unavailable (polling fallback)")
    else:
        print(f"History lookup tools called: {history_tools or 'none'}")
        if "get_conversation_history" in history_tools:
            print("Note: the agent called get_conversation_history even though recent turns are also in its prompt.")

    return 0 if recall_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
