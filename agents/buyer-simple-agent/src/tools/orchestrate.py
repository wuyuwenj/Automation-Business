"""Mindra workflow orchestration for multi-seller research.

Triggers a Mindra workflow via their API and streams results back
via SSE. Used for complex queries that benefit from coordinated
multi-agent execution.
"""

import httpx

from ..log import get_logger, log

_logger = get_logger("buyer.orchestrate")

MINDRA_BASE_URL = "https://api.mindra.co"


def run_workflow_impl(
    mindra_api_key: str,
    workflow_slug: str,
    query: str,
    metadata: dict | None = None,
) -> dict:
    """Trigger a Mindra workflow and stream results.

    Args:
        mindra_api_key: Mindra API key for authentication.
        workflow_slug: The workflow slug from Mindra console.
        query: The task/query to send to the workflow.
        metadata: Optional metadata to pass to the workflow.

    Returns:
        Dict with workflow result or error.
    """
    if not mindra_api_key:
        return {
            "status": "error",
            "content": [{"text": "MINDRA_API_KEY not configured. Set it in .env to use orchestration."}],
        }

    url = f"{MINDRA_BASE_URL}/v1/workflows/{workflow_slug}/run"
    headers = {
        "x-api-key": mindra_api_key,
        "Content-Type": "application/json",
    }
    body = {
        "task": query,
        "metadata": metadata or {"source": "buyer-agent"},
    }

    log(_logger, "MINDRA", "TRIGGER", f"workflow={workflow_slug} query='{query[:60]}'")

    # Step 1: Trigger the workflow
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            run_data = resp.json()
    except httpx.HTTPStatusError as e:
        log(_logger, "MINDRA", "ERROR", f"trigger failed: HTTP {e.response.status_code}")
        return {
            "status": "error",
            "content": [{"text": f"Mindra workflow trigger failed: HTTP {e.response.status_code}"}],
        }
    except Exception as e:
        log(_logger, "MINDRA", "ERROR", f"connection failed: {e}")
        return {
            "status": "error",
            "content": [{"text": f"Failed to connect to Mindra: {e}"}],
        }

    execution_id = run_data.get("execution_id", "")
    stream_url = run_data.get("stream_url", "")
    log(_logger, "MINDRA", "STARTED", f"execution_id={execution_id}")

    if not stream_url:
        return {
            "status": "error",
            "content": [{"text": f"Workflow started (id={execution_id}) but no stream URL returned."}],
        }

    # Step 2: Stream events until done
    full_stream_url = f"{MINDRA_BASE_URL}{stream_url}" if stream_url.startswith("/") else stream_url
    result_text = ""
    tools_executed = []

    try:
        with httpx.Client(timeout=120.0) as client:
            with client.stream("GET", full_stream_url, headers={"x-api-key": mindra_api_key}) as stream:
                buffer = ""
                current_event = ""

                for chunk in stream.iter_text():
                    buffer += chunk
                    lines = buffer.split("\n")
                    buffer = lines.pop()

                    for line in lines:
                        line = line.strip()
                        if line.startswith("event:"):
                            current_event = line[6:].strip()
                        elif line.startswith("data:"):
                            data_str = line[5:].strip()
                            if not data_str:
                                continue
                            try:
                                import json
                                data = json.loads(data_str)
                            except (ValueError, TypeError):
                                continue

                            if current_event == "chunk":
                                content = data.get("content", "")
                                result_text += content

                            elif current_event == "tool_executing":
                                tool_name = data.get("tool_name", "unknown")
                                tools_executed.append(tool_name)
                                log(_logger, "MINDRA", "TOOL",
                                    f"executing: {tool_name}")

                            elif current_event == "tool_result":
                                tool_name = data.get("tool_name", "unknown")
                                log(_logger, "MINDRA", "TOOL_RESULT",
                                    f"completed: {tool_name}")

                            elif current_event == "done":
                                final = data.get("final_answer", "")
                                if final:
                                    result_text = final
                                log(_logger, "MINDRA", "COMPLETED",
                                    f"execution_id={execution_id} "
                                    f"tools={len(tools_executed)} "
                                    f"result={len(result_text)} chars")

    except Exception as e:
        log(_logger, "MINDRA", "ERROR", f"stream error: {e}")
        if result_text:
            log(_logger, "MINDRA", "PARTIAL", "returning partial result")
        else:
            return {
                "status": "error",
                "content": [{"text": f"Mindra stream failed: {e}"}],
            }

    if not result_text:
        return {
            "status": "error",
            "content": [{"text": "Workflow completed but returned no result."}],
        }

    lines = [
        f"Mindra workflow '{workflow_slug}' completed.",
        f"Tools executed: {', '.join(tools_executed) if tools_executed else 'none'}",
        "",
        "Result:",
        result_text,
    ]

    return {
        "status": "success",
        "content": [{"text": "\n".join(lines)}],
        "response": result_text,
        "execution_id": execution_id,
        "tools_executed": tools_executed,
    }
