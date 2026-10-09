# Custom Prompts Guide

## Overview

The AI Agent Gateway allows you to customize **system prompts** while keeping **built-in tools** non-negotiable. This gives you control over agent behavior while ensuring core functionality remains intact.

## Architecture

### What You Control ✅
- **System Prompts**: Customize how the agent thinks and responds
  - Router prompt (routing decisions)
  - Planner prompt (task decomposition)
  - Executor prompt (task execution)
  - Reviewer prompt (quality checking)
  - Direct prompt (simple answers)
  - Finalizer prompt (answer composition)

### What's Non-Negotiable 🔒
- **Built-in Tools**: Always available, cannot be disabled
  - `read_artifact` - Read stored artifacts
  - `list_artifacts` - List all artifacts
  - `remember_fact` - Store facts for the session
  - `get_session_memory` - Retrieve previous facts
  - `store_kv` - Persistent key-value storage
  - `get_kv` - Read stored values
  - `get_conversation_history` - Access chat history
  - `kb_search` - Search knowledge bases (if attached)

## Default Prompts

### Router Prompt
```
Decide how to handle a user request.
Answer "direct" if it can be done in one pass with at most one or two tool calls.
Answer "plan" if it needs several dependent steps, multiple tools, or research followed by synthesis.
```

### Planner Prompt
```
You are a planner. Break the user's goal into at most {max_steps} steps.
Rules:
- Each step must be doable by a worker that only sees the step goal, its inputs, and the tools you assign.
- Use ids s1, s2, ... On a REPLAN use NEW ids that continue the numbering; never reuse an existing id.
- depends_on: step ids that must finish first. Steps with no dependencies run in parallel.
... (etc)
```

### Executor Prompt
```
You execute ONE step of a larger task. Do only this step.
Use the provided tools when needed. Large tool results are stored as artifacts: call read_artifact(id) to read more.
If a tool call fails, read the error and fix the arguments, or try another approach.
...
```

### Direct Prompt
```
Answer the user's request. Use tools if they help. If a tool fails, adjust and retry or explain the limitation. Be concise and accurate.
```

### Reviewer Prompt
```
You review completed steps against their success criteria.
For each step return a verdict:
- ok: the output satisfies the success criteria.
- retry_step: fixable by running the same step again
- replan: the step is impossible as written or the plan is wrong.
- abort: the overall task cannot be completed.
```

### Finalizer Prompt
```
Write the final answer to the user from the step results and facts provided.
Be concise and direct. Do not mention internal steps, ids, or tools.
If the run stopped early, say clearly what is incomplete.
```

## Customizing Prompts

### Example 1: Custom Direct Prompt (Friendly Chatbot)

```json
POST /v1/agents/
{
  "name": "Friendly Assistant",
  "provider_id": "nvidia",
  "models": {
    "planner": "meta-llama/llama-3.1-70b-instruct",
    "executor": "meta-llama/llama-3.1-70b-instruct",
    "reviewer": "meta-llama/llama-3.1-8b-instruct"
  },
  "system_prompt": "You are a friendly, helpful assistant named Buddy.",
  
  "direct_prompt": "Answer the user's question in a warm, friendly tone. Use emojis when appropriate! 😊 If you need to use tools like get_conversation_history to check what they asked before, feel free to do so. Always be positive and encouraging!",
  
  "temperature": 0.7,
  "tools": [],
  "knowledge_base_ids": [],
  "limits": {
    "max_iterations": 5,
    "max_tokens_per_run": 50000,
    "timeout_s": 120
  }
}
```

### Example 2: Custom Planner (Detailed Planning)

```json
{
  "name": "Research Agent",
  "provider_id": "nvidia",
  "models": "meta-llama/llama-3.1-70b-instruct",
  "system_prompt": "You are a research assistant. Be thorough and methodical.",
  
  "planner_prompt": "You are an expert planner for research tasks. Break down the user's goal into at most {max_steps} detailed steps.\\n\\nGuidelines:\\n- Each step should be specific and measurable\\n- Use parallel execution when steps are independent\\n- Always use kb_search for finding information\\n- Prefer more granular steps over fewer broad ones\\n- Each step MUST have clear success criteria\\n\\nAvailable tools will be provided. Use them wisely.",
  
  "executor_prompt": "Execute this ONE research step carefully. Use the tools provided:\\n- kb_search: Search the knowledge base\\n- get_conversation_history: Check what user asked before\\n- store_kv: Save important findings\\n\\nBe thorough and cite sources when possible.",
  
  "temperature": 0.3,
  "tools": ["web_search"],
  "knowledge_base_ids": ["kb-uuid-123"],
  "limits": {
    "max_iterations": 10,
    "max_steps": 15,
    "max_tokens_per_run": 100000,
    "timeout_s": 300
  }
}
```

### Example 3: Custom Executor (Code-Focused)

```json
{
  "name": "Code Helper",
  "provider_id": "openai",
  "models": {
    "planner": "gpt-4",
    "executor": "gpt-4",
    "reviewer": "gpt-3.5-turbo"
  },
  "system_prompt": "You are a software engineering assistant specialized in Python.",
  
  "executor_prompt": "Execute this coding task step. \\n\\nAvailable tools:\\n- read_artifact(id): Read previous code/outputs\\n- list_artifacts(): See what's been created\\n- store_kv(key, value): Save variables/config\\n- get_kv(key): Retrieve saved values\\n\\nWhen writing code:\\n1. Always include docstrings\\n2. Add type hints\\n3. Include error handling\\n4. Write tests if requested\\n\\nReturn your output as JSON with kind='code' for code artifacts.",
  
  "reviewer_prompt": "Review the code step:\\n- Does it follow Python best practices?\\n- Are there type hints?\\n- Is error handling present?\\n- Does it meet the success criteria?\\n\\nBe strict about code quality but flexible about style choices.",
  
  "temperature": 0.2,
  "tools": ["run_python", "read_file"],
  "limits": {
    "max_iterations": 8,
    "max_tool_turns": 6
  }
}
```

## Best Practices

### 1. **Always Mention Available Tools**
Even though built-in tools are automatic, remind the agent in custom prompts:

```
"Use these tools when helpful:
- get_conversation_history: Check previous messages
- store_kv/get_kv: Save and retrieve data
- kb_search: Search your knowledge base (if available)"
```

### 2. **Be Specific About Behavior**
Instead of:
```
"Be helpful"
```

Use:
```
"Answer concisely in 2-3 sentences. Use bullet points for lists. If you don't know something, admit it and suggest how to find the answer."
```

### 3. **Include Format Instructions**
```
"When providing code, use markdown code blocks. When listing items, use numbered lists. When explaining concepts, start with a one-sentence summary."
```

### 4. **Set Tone and Personality**
```
"You are a patient teacher. Explain concepts using analogies. Always encourage the user and celebrate their progress."
```

### 5. **Leverage Built-in Tools**
```
"Before answering questions about previous conversations, ALWAYS use get_conversation_history to check what was discussed. This ensures accuracy."
```

## Viewing Built-In Tools

To see all built-in tools and their descriptions:

```bash
GET /v1/runs/builtin-tools
```

Response:
```json
{
  "builtin_tools": [
    {
      "name": "read_artifact",
      "description": "Read the full content of a previously stored artifact by its ID.",
      "category": "internal",
      "always_available": true
    },
    {
      "name": "get_conversation_history",
      "description": "Retrieve conversation history from previous runs in this session.",
      "category": "memory",
      "always_available": true
    },
    ...
  ],
  "total_count": 8,
  "note": "These tools are automatically available to all agents and cannot be disabled."
}
```

## Template Variables

Some prompts support template variables:

### Planner Prompt
- `{max_steps}`: Maximum steps allowed (from limits)

### All Prompts
- Access to agent's `system_prompt` (persona)
- Access to `tools` list (user-defined tools)
- Access to `knowledge_base_ids`

## Prompt Composition

The final prompt sent to the LLM is composed as:

```
[Agent's system_prompt/persona]

[Role-specific prompt (router/planner/executor/etc)]

[Context: available tools, variables, facts, etc.]

[User input]
```

## Testing Custom Prompts

1. **Create agent with custom prompts**
2. **Run a test query**
3. **Check logs** to see prompts being used:
   ```
   🤖 [ROUTER] System prompt: 250 chars, User prompt: 180 chars
   ```
4. **Iterate** based on agent behavior

## Migration from Defaults

If you want to customize just one prompt, provide only that field. Others will use defaults:

```json
{
  "name": "My Agent",
  "provider_id": "nvidia",
  "models": "meta-llama/llama-3.1-70b-instruct",
  "system_prompt": "You are helpful.",
  
  "direct_prompt": "My custom direct prompt here",
  // router_prompt, planner_prompt, etc. will use defaults
  
  "tools": [],
  "temperature": 0.7
}
```

## Common Use Cases

### Customer Service Bot
- Friendly direct_prompt
- Focus on get_conversation_history
- Quick, concise answers

### Research Assistant
- Detailed planner_prompt
- Thorough executor_prompt
- Strict reviewer_prompt
- Heavy kb_search usage

### Code Assistant
- Technical executor_prompt
- Code quality reviewer_prompt
- Use store_kv for config/state

### Data Analyst
- Structured planner_prompt
- Data-focused executor_prompt
- Use artifacts for large datasets

## Next Steps

1. Review default prompts in `app/engine/prompts.py`
2. Experiment with custom prompts for your use case
3. Check logs to see how prompts affect behavior
4. Iterate based on results

Remember: Built-in tools are always there to help your agent be more capable!
