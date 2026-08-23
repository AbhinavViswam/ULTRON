import json
import inspect
import time
from typing import Callable, Any

def run_browser_agent(
    goal: str,
    complete: Callable,
    tool_functions: dict,
    tools_schema: list,
    output_manager: Any = None,
    is_local_model: bool = False
) -> str:
    """Runs a dedicated loop for complex browser automation tasks.
    
    This agent runs synchronously, printing its thoughts and actions to the console,
    and returns a final summary back to the main Ultron brain.
    """
    
    system_prompt = (
        "You are Ultron's dedicated Browser Agent. Your ONLY job is to achieve the user's browser goal.\n\n"
        "You have full interactive control over the user's Chrome browser. Everything you do is visible on screen.\n"
        "To accomplish the goal, you must PLAN and EXECUTE step by step.\n"
        "1. Write out your step-by-step plan before taking action.\n"
        "2. Call the provided browser tools to navigate, click, type, and read the page.\n"
        "3. Observe the results and adjust your plan if needed.\n"
        "4. When the goal is achieved, output a final summary of what you found or accomplished.\n\n"
        "RULES:\n"
        "- Do not make up information. Use chrome_read_page to read the actual page content.\n"
        "- If an element isn't found, try scrolling or searching for a different selector.\n"
        "- Only use the tools provided.\n"
    )

    # Filter tools to only those needed by the browser agent
    allowed_tools = {
        "chrome_navigate", "chrome_click", "chrome_type", "chrome_scroll",
        "chrome_read_page", "chrome_screenshot", "chrome_go_back",
        "chrome_go_forward", "chrome_new_tab", "chrome_close_tab", "chrome_press_key"
    }
    
    agent_tools_schema = [t for t in tools_schema if t.get("function", {}).get("name") in allowed_tools]

    if is_local_model:
        tools_str = json.dumps(agent_tools_schema, indent=2)
        system_prompt += (
            f"\n\nAVAILABLE TOOLS:\n{tools_str}\n\n"
            "To use a tool, you MUST output a raw JSON block wrapped in <tool_call> tags.\n"
            "Example:\n"
            "<tool_call>\n"
            '{"name": "chrome_navigate", "arguments": {"url_or_query": "amazon"}}\n'
            "</tool_call>\n"
        )
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Browser Goal: {goal}"}
    ]
    
    print(f"\n[Browser Agent] Starting task: {goal}")
    if output_manager:
        output_manager.enqueue("I am starting the browser agent to handle this task.", source="system")
        
    for round_num in range(15):
        print(f"[Browser Agent] Thinking (Round {round_num + 1}/15)...")
        
        try:
            if is_local_model:
                response = complete(messages=messages)
                raw_text = response.choices[0].message.content or ""
                messages.append({"role": "assistant", "content": raw_text})
                
                # We need to parse local tool calls here. 
                import re
                tool_calls = []
                # Handle cases where the model might forget the closing tag, and handle nested JSON braces
                for match in re.finditer(r'<tool_call>\s*(.*?)(?:</tool_call>|$)', raw_text, re.DOTALL):
                    try:
                        json_str = match.group(1).strip()
                        parsed = json.loads(json_str)
                        if "name" in parsed:
                            tool_calls.append(parsed)
                    except Exception as e:
                        print(f"Failed to parse tool call JSON: {e}")
                
                # Print the thoughts so user can see what it's planning
                clean_text = re.sub(r'<tool_call>.*?</tool_call>', '', raw_text, flags=re.DOTALL).strip()
                if clean_text:
                    print(f"\n[Browser Agent Plan/Thought]:\n{clean_text}\n")
                    
            else:
                response = complete(
                    messages=messages,
                    tools=agent_tools_schema,
                    tool_choice="auto"
                )
                if not response or not response.choices:
                    return "Browser agent failed to get a response from the API."
                
                msg = response.choices[0].message
                # Append raw message directly for cloud models
                messages.append(msg.model_dump(exclude_none=True))
                
                tool_calls = []
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    for tc in msg.tool_calls:
                        func_name = tc.function.name
                        try:
                            args = json.loads(tc.function.arguments)
                        except:
                            args = tc.function.arguments
                        tool_calls.append({
                            "name": func_name,
                            "arguments": args,
                            "id": tc.id
                        })
                
                if msg.content:
                    print(f"\n[Browser Agent Plan/Thought]:\n{msg.content}\n")

            if not tool_calls:
                # No tool calls, means it thinks it's done.
                final_answer = raw_text if is_local_model else msg.content
                print("\n[Browser Agent] Task Complete.")
                return f"Browser Agent completed the task.\n\nFinal Report:\n{final_answer}"
                
            # Execute tool calls
            results_text_parts = []
            cloud_tool_msgs = []
            
            for tc in tool_calls:
                func_name = tc.get("name", "")
                func_args = tc.get("arguments")
                tc_id = tc.get("id", None)
                
                # Local model fallback: if 'arguments' key is missing, assume args are at root
                if func_args is None:
                    func_args = {k: v for k, v in tc.items() if k not in ["name", "id", "type"]}
                
                if func_name not in allowed_tools or func_name not in tool_functions:
                    res = f"Error: {func_name} is not a valid browser tool."
                else:
                    print(f"  -> [Action] {func_name}({func_args})")
                    try:
                        func = tool_functions[func_name]
                        # We don't have coerce_tool_args here, so we just pass kwargs
                        if isinstance(func_args, dict):
                            res = func(**func_args)
                        else:
                            # For single string arg from weak models
                            import inspect
                            sig = inspect.signature(func)
                            if len(sig.parameters) == 1:
                                param_name = list(sig.parameters.keys())[0]
                                res = func(**{param_name: func_args})
                            else:
                                res = f"Error: invalid arguments {func_args}"
                    except Exception as e:
                        res = f"Error executing {func_name}: {e}"
                        
                print(f"  <- [Result] {str(res)[:100]}...")
                
                if is_local_model:
                    results_text_parts.append(f"[Tool Result for {func_name}]: {res}")
                else:
                    cloud_tool_msgs.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "name": func_name,
                        "content": str(res)
                    })
                    
            # Feed results back
            if is_local_model:
                messages.append({
                    "role": "user",
                    "content": f"Tool results:\n{chr(10).join(results_text_parts)}\n\nObserve results and continue."
                })
            else:
                messages.extend(cloud_tool_msgs)
                
        except Exception as e:
            return f"Browser agent encountered a fatal error: {e}"
            
    return "Browser agent reached the maximum number of steps (15) and was forced to stop."
